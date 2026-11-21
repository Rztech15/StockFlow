"""API keys (programmatic access) and CSV exports for accounting and e-commerce tools."""

import csv
import io
import secrets
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.activity import log_activity
from app.auth.deps import DB, require_trusted_origin, tenant_with
from app.auth.sessions import hash_token
from app.context import TenantContext
from app.db import text
from app.routes.dberrors import raise_for_db_error
from app.workspace import check_limit

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])

SettingsManage = Annotated[TenantContext, Depends(tenant_with("settings.manage"))]
CatalogRead = Annotated[TenantContext, Depends(tenant_with("catalog.read"))]
InventoryRead = Annotated[TenantContext, Depends(tenant_with("inventory.read"))]

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: Literal["read", "readwrite"] = "read"


def csv_safe(value: object) -> object:
    """Neutralise spreadsheet formula injection: a leading = + - @ becomes literal text."""
    if isinstance(value, str) and value.startswith(_DANGEROUS):
        return "'" + value
    return value


def to_csv(header: list[str], rows: list[list[object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_safe(cell) for cell in row])
    return out.getvalue()


def _csv_response(body: str, filename: str) -> Response:
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api-keys")
def list_api_keys(tenant: SettingsManage, db: DB) -> list[dict[str, object]]:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text(
                "SELECT o_id, o_name, o_prefix, o_scope, o_created_at, o_last_used_at, o_revoked "
                "FROM app.apikey_list()"
            )
        ).all()
    return [
        {
            "id": str(r[0]), "name": r[1], "prefix": r[2], "scope": r[3],
            "created_at": r[4], "last_used_at": r[5], "revoked": bool(r[6]),
        }
        for r in rows
    ]


@router.post("/api-keys", status_code=201)
def create_api_key(payload: ApiKeyCreate, tenant: SettingsManage, db: DB) -> dict[str, object]:
    key = "sf_" + secrets.token_urlsafe(32)
    try:
        with db.tenant_tx(tenant) as conn:
            check_limit(conn, tenant, "api_keys")
            key_id = conn.execute(
                text(
                    "SELECT app.apikey_create(CAST(:name AS text), CAST(:scope AS text), "
                    "CAST(:hash AS bytea), CAST(:prefix AS text))"
                ),
                {
                    "name": payload.name,
                    "scope": payload.scope,
                    "hash": hash_token(key),
                    "prefix": key[:10],
                },
            ).scalar_one()
            log_activity(conn, tenant, "created", "api_key", str(key_id), payload.name)
    except Exception as exc:
        raise_for_db_error(exc)
    # The full key is shown exactly once; only its hash is stored.
    return {"id": str(key_id), "name": payload.name, "scope": payload.scope, "key": key}


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(key_id: UUID, tenant: SettingsManage, db: DB) -> Response:
    try:
        with db.tenant_tx(tenant) as conn:
            conn.execute(
                text("SELECT app.apikey_revoke(CAST(:id AS uuid))"), {"id": str(key_id)}
            )
            log_activity(conn, tenant, "revoked", "api_key", str(key_id))
    except Exception as exc:
        raise_for_db_error(exc)
    return Response(status_code=204)


@router.get("/export/products.csv")
def export_products(tenant: CatalogRead, db: DB) -> Response:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT p.sku, p.name, coalesce(c.name, ''), p.description, p.quantity,
                       p.minimum_stock, p.unit, p.created_at, p.updated_at
                FROM public.products p
                LEFT JOIN public.categories c
                  ON c.id = p.category_id AND c.tenant_id = p.tenant_id
                WHERE p.tenant_id = :tenant_id
                ORDER BY p.sku
            """),
            {"tenant_id": tenant.tenant_id},
        ).all()
    header = ["sku", "name", "category", "description", "quantity", "minimum_stock", "unit",
              "created_at", "updated_at"]
    return _csv_response(to_csv(header, [list(r) for r in rows]), "products.csv")


@router.get("/export/movements.csv")
def export_movements(tenant: InventoryRead, db: DB) -> Response:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT m.created_at, p.sku, p.name, m.movement_type, m.quantity, m.note
                FROM public.stock_movements m
                JOIN public.products p ON p.id = m.product_id AND p.tenant_id = m.tenant_id
                WHERE m.tenant_id = :tenant_id
                ORDER BY m.created_at DESC
                LIMIT 50000
            """),
            {"tenant_id": tenant.tenant_id},
        ).all()
    header = ["created_at", "sku", "name", "type", "quantity", "note"]
    return _csv_response(to_csv(header, [list(r) for r in rows]), "movements.csv")
