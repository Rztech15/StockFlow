"""Versioned StockFlow API routes."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.activity import log_activity
from app.auth.deps import require_trusted_origin, tenant_with
from app.context import TenantContext
from app.db import Database, text
from app.errors import conflict, not_found
from app.workspace import check_limit, next_sku

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])


def get_db(request: Request) -> Database:
    return request.app.state.db


# Every dependency below derives the tenant from the verified session, then checks the role's
# permission. A request without a valid session gets 401; without the permission, 403.
AnyMember = Annotated[TenantContext, Depends(tenant_with(None))]
CatalogRead = Annotated[TenantContext, Depends(tenant_with("catalog.read"))]
CatalogWrite = Annotated[TenantContext, Depends(tenant_with("catalog.write"))]
InventoryRead = Annotated[TenantContext, Depends(tenant_with("inventory.read"))]
InventoryWrite = Annotated[TenantContext, Depends(tenant_with("inventory.write"))]
ReportsRead = Annotated[TenantContext, Depends(tenant_with("reports.read"))]
DB = Annotated[Database, Depends(get_db)]


class ProductCreate(BaseModel):
    # Leave `sku` empty to generate one from the workspace SKU convention.
    sku: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    minimum_stock: int = Field(default=0, ge=0)
    unit: str = Field(default="unit", min_length=1, max_length=24)
    category_id: UUID | None = None


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    minimum_stock: int | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=24)
    category_id: UUID | None = None


class MovementCreate(BaseModel):
    product_id: UUID
    movement_type: str = Field(pattern="^(IN|OUT)$")
    quantity: int = Field(gt=0, le=1_000_000)
    note: str = Field(default="", max_length=1000)


@router.get("/session")
def session_status(tenant: AnyMember) -> dict[str, object]:
    return {"authenticated": True, "tenant_id": tenant.tenant_id}


@router.get("/products")
def list_products(
    tenant: CatalogRead,
    db: DB,
    search: str = Query(default="", max_length=100),
    stock: str = Query(default="all", pattern="^(all|low|out)$"),
    category: str = Query(default="", max_length=36),
) -> list[dict[str, object]]:
    query = """
        SELECT p.id, p.sku, p.name, p.description, p.quantity, p.minimum_stock, p.unit,
               p.created_at, p.updated_at, p.category_id, c.name AS category
        FROM public.products p
        LEFT JOIN public.categories c
          ON c.id = p.category_id AND c.tenant_id = p.tenant_id
        WHERE p.tenant_id = :tenant_id
          AND (:search = '' OR p.sku ILIKE :pattern OR p.name ILIKE :pattern)
          AND (
              :stock = 'all'
              OR (:stock = 'low' AND p.quantity > 0 AND p.quantity <= p.minimum_stock)
              OR (:stock = 'out' AND p.quantity = 0)
          )
          AND (:category = '' OR CAST(p.category_id AS text) = :category)
        ORDER BY p.name
    """
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text(query),
            {
                "tenant_id": tenant.tenant_id,
                "search": search,
                "pattern": f"%{search}%",
                "stock": stock,
                "category": category,
            },
        ).mappings().all()
    return [dict(row) for row in rows]


INSERT_PRODUCT = """
    INSERT INTO public.products
        (id, tenant_id, sku, name, description, minimum_stock, unit, category_id)
    VALUES
        (:id, :tenant_id, :sku, :name, :description, :minimum_stock, :unit,
         CAST(:category_id AS uuid))
    RETURNING id, sku, name, description, quantity, minimum_stock, unit,
              category_id, created_at, updated_at
"""


@router.post("/products", status_code=201)
def create_product(payload: ProductCreate, tenant: CatalogWrite, db: DB):
    product_id = str(uuid4())
    manual_sku = (payload.sku or "").strip()
    data = payload.model_dump()
    data["category_id"] = str(payload.category_id) if payload.category_id else None
    base = {"id": product_id, "tenant_id": tenant.tenant_id, **data}
    try:
        with db.tenant_tx(tenant) as conn:
            check_limit(conn, tenant, "products")
            if manual_sku:
                row = conn.execute(
                    text(INSERT_PRODUCT), {**base, "sku": manual_sku}
                ).mappings().one()
            else:
                row = None
                for _ in range(5):  # a generated SKU may collide with a hand-typed one
                    try:
                        with conn.begin_nested():
                            row = conn.execute(
                                text(INSERT_PRODUCT), {**base, "sku": next_sku(conn, tenant)}
                            ).mappings().one()
                        break
                    except Exception as exc:
                        if "products_tenant_sku_unique" not in str(exc):
                            raise
                if row is None:
                    raise conflict("No free SKU found. Raise the next number in Settings.")
            log_activity(conn, tenant, "created", "product", product_id, row["sku"])
        return dict(row)
    except Exception as exc:
        if "products_tenant_sku_unique" in str(exc):
            raise conflict("A product with this SKU already exists.") from None
        if "products_category_same_tenant" in str(exc):
            raise not_found("Category not found.") from None
        raise


@router.patch("/products/{product_id}")
def update_product(
    product_id: UUID, payload: ProductUpdate, tenant: CatalogWrite, db: DB
):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise conflict("Provide at least one field to update.")

    if changes.get("category_id") is not None:
        changes["category_id"] = str(changes["category_id"])
    assignments = ", ".join(
        f"{key} = CAST(:{key} AS uuid)" if key == "category_id" else f"{key} = :{key}"
        for key in changes
    )
    changes.update(
        tenant_id=tenant.tenant_id,
        product_id=str(product_id),
    )

    try:
        with db.tenant_tx(tenant) as conn:
            row = conn.execute(
                text(f"""
                    UPDATE public.products
                    SET {assignments}, updated_at = now()
                    WHERE tenant_id = :tenant_id AND id = :product_id
                    RETURNING id, sku, name, description, quantity,
                              minimum_stock, unit, category_id, created_at, updated_at
                """),
                changes,
            ).mappings().first()
            if row is not None:
                log_activity(conn, tenant, "updated", "product", str(product_id), row["sku"])
        if row is None:
            raise not_found("Product not found.")
        return dict(row)
    except Exception as exc:
        if "products_tenant_sku_unique" in str(exc):
            raise conflict("A product with this SKU already exists.") from None
        if "products_category_same_tenant" in str(exc):
            raise not_found("Category not found.") from None
        raise


@router.get("/movements")
def list_movements(
    tenant: InventoryRead,
    db: DB,
    limit: int = Query(default=100, ge=1, le=500),
):
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT m.id, m.product_id, p.sku, p.name AS product_name,
                       m.movement_type, m.quantity, m.note,
                       m.created_by, m.created_at
                FROM public.stock_movements m
                JOIN public.products p
                  ON p.id = m.product_id AND p.tenant_id = m.tenant_id
                WHERE m.tenant_id = :tenant_id
                ORDER BY m.created_at DESC
                LIMIT :limit
            """),
            {"tenant_id": tenant.tenant_id, "limit": limit},
        ).mappings().all()
    return [dict(row) for row in rows]


@router.post("/movements", status_code=201)
def create_movement(payload: MovementCreate, tenant: InventoryWrite, db: DB):
    movement_id = str(uuid4())

    with db.tenant_tx(tenant) as conn:
        product = conn.execute(
            text("""
                SELECT id, quantity
                FROM public.products
                WHERE tenant_id = :tenant_id AND id = :product_id
                FOR UPDATE
            """),
            {
                "tenant_id": tenant.tenant_id,
                "product_id": str(payload.product_id),
            },
        ).mappings().first()

        if product is None:
            raise not_found("Product not found.")

        current = product["quantity"]
        if payload.movement_type == "OUT" and payload.quantity > current:
            raise conflict("Insufficient stock for this issue.")

        delta = payload.quantity if payload.movement_type == "IN" else -payload.quantity

        conn.execute(
            text("""
                UPDATE public.products
                SET quantity = quantity + :delta, updated_at = now()
                WHERE tenant_id = :tenant_id AND id = :product_id
            """),
            {
                "delta": delta,
                "tenant_id": tenant.tenant_id,
                "product_id": str(payload.product_id),
            },
        )

        row = conn.execute(
            text("""
                INSERT INTO public.stock_movements
                    (id, tenant_id, product_id, movement_type,
                     quantity, note, created_by)
                VALUES
                    (:id, :tenant_id, :product_id, :movement_type,
                     :quantity, :note, :created_by)
                RETURNING id, product_id, movement_type, quantity, note, created_at
            """),
            {
                "id": movement_id,
                "tenant_id": tenant.tenant_id,
                "product_id": str(payload.product_id),
                "movement_type": payload.movement_type,
                "quantity": payload.quantity,
                "note": payload.note,
                "created_by": tenant.user_id,
            },
        ).mappings().one()
        log_activity(
            conn, tenant, "stock_in" if payload.movement_type == "IN" else "stock_out",
            "product", str(payload.product_id), f"{payload.quantity} units",
        )

    return dict(row)


@router.get("/dashboard")
def dashboard_summary(tenant: ReportsRead, db: DB):
    with db.tenant_tx(tenant) as conn:
        summary = conn.execute(
            text("""
                SELECT
                    count(*) AS products,
                    coalesce(sum(quantity), 0) AS units_in_stock,
                    count(*) FILTER (
                        WHERE quantity > 0 AND quantity <= minimum_stock
                    ) AS low_stock,
                    count(*) FILTER (WHERE quantity = 0) AS out_of_stock
                FROM public.products
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant.tenant_id},
        ).mappings().one()

        restock = conn.execute(
            text("""
                SELECT id, sku, name, quantity, minimum_stock, unit
                FROM public.products
                WHERE tenant_id = :tenant_id
                  AND quantity <= minimum_stock
                ORDER BY quantity ASC, name
                LIMIT 20
            """),
            {"tenant_id": tenant.tenant_id},
        ).mappings().all()

    return {"summary": dict(summary), "restock": [dict(row) for row in restock]}
