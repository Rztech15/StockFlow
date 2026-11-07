"""Bulk product import. The browser parses the file; this route validates and applies the rows.

Needs both catalog.write and inventory.write: the import creates products and records their
opening stock (or a correction) in the append-only stock ledger.
"""

from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.activity import log_activity
from app.auth.deps import DB, require_trusted_origin, tenant_with
from app.context import TenantContext
from app.db import Connection, text
from app.errors import AppError
from app.importing import MAX_ROWS, CleanRow, check_rows
from app.plans import limit_for
from app.routes.v1 import INSERT_PRODUCT
from app.workspace import get_settings, next_sku

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])


Importer = Annotated[TenantContext, Depends(tenant_with("catalog.write", "inventory.write"))]


class ImportRequest(BaseModel):
    # Rows stay loose on purpose: a bad row is reported back, it does not reject the whole file.
    rows: list[dict[str, object]] = Field(min_length=1, max_length=MAX_ROWS)
    dry_run: bool = False


def _record_movement(
    conn: Connection, tenant: TenantContext, product_id: str, new_quantity: int, old_quantity: int,
    note: str,
) -> None:
    delta = new_quantity - old_quantity
    if delta == 0:
        return
    conn.execute(
        text("UPDATE public.products SET quantity = :q, updated_at = now() "
             "WHERE tenant_id = :tenant_id AND id = :product_id"),
        {"q": new_quantity, "tenant_id": tenant.tenant_id, "product_id": product_id},
    )
    conn.execute(
        text("""
            INSERT INTO public.stock_movements
                (id, tenant_id, product_id, movement_type, quantity, note, created_by)
            VALUES (:id, :tenant_id, :product_id, :kind, :quantity, :note, :created_by)
        """),
        {
            "id": str(uuid4()), "tenant_id": tenant.tenant_id, "product_id": product_id,
            "kind": "IN" if delta > 0 else "OUT", "quantity": abs(delta), "note": note,
            "created_by": tenant.user_id,
        },
    )


def _insert(conn: Connection, tenant: TenantContext, row: CleanRow) -> tuple[str, str]:
    """Create one product (with its opening stock). Returns (product id, sku)."""
    product_id = str(uuid4())
    base = {
        "id": product_id, "tenant_id": tenant.tenant_id, "name": row.name,
        "description": row.description or "", "minimum_stock": row.minimum_stock or 0,
        "unit": row.unit or "unit", "category_id": None,
    }
    if row.sku:
        created = conn.execute(text(INSERT_PRODUCT), {**base, "sku": row.sku}).mappings().one()
    else:
        created = None
        for _ in range(5):  # a generated SKU may collide with a hand-typed one
            try:
                with conn.begin_nested():
                    created = conn.execute(
                        text(INSERT_PRODUCT), {**base, "sku": next_sku(conn, tenant)}
                    ).mappings().one()
                break
            except Exception as exc:
                if "products_tenant_sku_unique" not in str(exc):
                    raise
        if created is None:
            raise AppError(409, "conflict", "Conflict", "No free SKU found. Raise the next number.")
    _record_movement(conn, tenant, product_id, row.quantity or 0, 0, "Opening stock from import")
    return product_id, str(created["sku"])


def _update(
    conn: Connection, tenant: TenantContext, row: CleanRow, current: dict[str, Any]
) -> bool:
    """Apply a row to an existing product. Returns True if anything changed."""
    changed = (
        row.name != current["name"]
        or (row.minimum_stock is not None and row.minimum_stock != current["minimum_stock"])
        or (row.unit is not None and row.unit != current["unit"])
        or (row.description is not None and row.description != current["description"])
    )
    if changed:
        conn.execute(
            text("""
                UPDATE public.products
                SET name = :name,
                    minimum_stock = COALESCE(CAST(:minimum_stock AS integer), minimum_stock),
                    unit = COALESCE(CAST(:unit AS text), unit),
                    description = COALESCE(CAST(:description AS text), description),
                    updated_at = now()
                WHERE tenant_id = :tenant_id AND id = :product_id
            """),
            {
                "name": row.name, "minimum_stock": row.minimum_stock, "unit": row.unit,
                "description": row.description, "tenant_id": tenant.tenant_id,
                "product_id": str(current["id"]),
            },
        )
    if row.quantity is not None and row.quantity != current["quantity"]:
        _record_movement(
            conn, tenant, str(current["id"]), row.quantity, current["quantity"],
            f"Stock set to {row.quantity} by import",
        )
        changed = True
    return changed


@router.post("/products/import")
def import_products(payload: ImportRequest, tenant: Importer, db: DB) -> dict[str, object]:
    checks = check_rows(list(payload.rows))
    valid = [c.row for c in checks if c.row is not None]
    skus = [r.sku for r in valid if r.sku]
    results: dict[int, dict[str, object]] = {}
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}

    def note(check_line: int, sku: str, name: str, state: str, errors: list[str]) -> None:
        results[check_line] = {
            "line": check_line, "sku": sku, "name": name, "status": state, "errors": errors,
        }

    with db.tenant_tx(tenant) as conn:
        existing: dict[str, dict[str, Any]] = {}
        if skus:
            found = conn.execute(
                text("""
                    SELECT id, sku, name, description, minimum_stock, unit, quantity
                    FROM public.products
                    WHERE tenant_id = :tenant_id AND sku = ANY(CAST(:skus AS text[]))
                    FOR UPDATE
                """),
                {"tenant_id": tenant.tenant_id, "skus": skus},
            ).mappings().all()
            existing = {str(r["sku"]): dict(r) for r in found}

        new_rows = [r for r in valid if not (r.sku and r.sku in existing)]
        plan = str(get_settings(conn, tenant)["plan"])
        limit = limit_for(plan, "products")
        if limit is not None and new_rows:
            current = int(conn.execute(
                text("SELECT count(*) FROM public.products WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant.tenant_id},
            ).scalar_one())
            if current + len(new_rows) > limit:
                raise AppError(
                    status.HTTP_402_PAYMENT_REQUIRED, "plan-limit", "Plan Limit Reached",
                    f"The {plan} plan allows up to {limit} products. You have {current} and "
                    f"this file would add {len(new_rows)}. Remove rows or upgrade the plan.",
                )

        for check in checks:
            row = check.row
            if row is None:
                counts["failed"] += 1
                note(check.line, check.sku, check.name, "error", check.errors)
                continue
            current_row = existing.get(row.sku) if row.sku else None
            if payload.dry_run:
                if current_row is None:
                    state = "will_create"
                else:
                    state = "will_update"
                counts["created" if current_row is None else "updated"] += 1
                note(check.line, row.sku or "", row.name, state, [])
                continue
            try:
                with conn.begin_nested():
                    if current_row is None:
                        _, sku = _insert(conn, tenant, row)
                        counts["created"] += 1
                        note(check.line, sku, row.name, "created", [])
                    elif _update(conn, tenant, row, current_row):
                        counts["updated"] += 1
                        note(check.line, row.sku or "", row.name, "updated", [])
                    else:
                        counts["unchanged"] += 1
                        note(check.line, row.sku or "", row.name, "unchanged", [])
            except Exception as exc:
                if "products_tenant_sku_unique" not in str(exc):
                    raise  # unexpected: abort the whole import rather than hide it
                counts["failed"] += 1
                note(check.line, row.sku or "", row.name, "error", ["SKU already exists"])

        if not payload.dry_run and (counts["created"] or counts["updated"]):
            log_activity(
                conn, tenant, "imported", "product", None,
                f"{counts['created']} created, {counts['updated']} updated",
            )

    return {
        "dry_run": payload.dry_run, **counts,
        "rows": [results[c.line] for c in checks if c.line in results],
    }
