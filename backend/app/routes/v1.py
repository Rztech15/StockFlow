
"""Versioned StockFlow API routes."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.context import RequestContext, TenantContext, to_tenant_context
from app.db import Database, text
from app.errors import conflict, not_found, unauthorized

router = APIRouter(prefix="/api/v1")


def get_tenant_context(request: Request) -> TenantContext:
    """Require a trusted authenticated user and active workspace."""
    ctx = getattr(request.state, "ctx", None)
    if not isinstance(ctx, RequestContext):
        raise unauthorized("Request context is unavailable.")
    return to_tenant_context(ctx)


def get_db(request: Request) -> Database:
    return request.app.state.db


Tenant = Annotated[TenantContext, Depends(get_tenant_context)]
DB = Annotated[Database, Depends(get_db)]


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    minimum_stock: int = Field(default=0, ge=0)
    unit: str = Field(default="unit", min_length=1, max_length=24)


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    minimum_stock: int | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=24)


class MovementCreate(BaseModel):
    product_id: UUID
    movement_type: str = Field(pattern="^(IN|OUT)$")
    quantity: int = Field(gt=0, le=1_000_000)
    note: str = Field(default="", max_length=1000)


@router.get("/session")
def session_status(tenant: Tenant) -> dict[str, object]:
    return {"authenticated": True, "tenant_id": tenant.tenant_id}


@router.get("/products")
def list_products(
    tenant: Tenant,
    db: DB,
    search: str = Query(default="", max_length=100),
    stock: str = Query(default="all", pattern="^(all|low|out)$"),
) -> list[dict[str, object]]:
    query = """
        SELECT id, sku, name, description, quantity, minimum_stock, unit,
               created_at, updated_at
        FROM public.products
        WHERE tenant_id = :tenant_id
          AND (:search = '' OR sku ILIKE :pattern OR name ILIKE :pattern)
          AND (
              :stock = 'all'
              OR (:stock = 'low' AND quantity > 0 AND quantity <= minimum_stock)
              OR (:stock = 'out' AND quantity = 0)
          )
        ORDER BY name
    """
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text(query),
            {
                "tenant_id": tenant.tenant_id,
                "search": search,
                "pattern": f"%{search}%",
                "stock": stock,
            },
        ).mappings().all()
    return [dict(row) for row in rows]


@router.post("/products", status_code=201)
def create_product(payload: ProductCreate, tenant: Tenant, db: DB):
    product_id = str(uuid4())
    try:
        with db.tenant_tx(tenant) as conn:
            row = conn.execute(
                text("""
                    INSERT INTO public.products
                        (id, tenant_id, sku, name, description,
                         minimum_stock, unit)
                    VALUES
                        (:id, :tenant_id, :sku, :name, :description,
                         :minimum_stock, :unit)
                    RETURNING id, sku, name, description, quantity,
                              minimum_stock, unit, created_at, updated_at
                """),
                {
                    "id": product_id,
                    "tenant_id": tenant.tenant_id,
                    **payload.model_dump(),
                },
            ).mappings().one()
        return dict(row)
    except Exception as exc:
        if "products_tenant_sku_unique" in str(exc):
            raise conflict("A product with this SKU already exists.") from None
        raise


@router.patch("/products/{product_id}")
def update_product(
    product_id: UUID, payload: ProductUpdate, tenant: Tenant, db: DB
):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise conflict("Provide at least one field to update.")

    assignments = ", ".join(f"{key} = :{key}" for key in changes)
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
                              minimum_stock, unit, created_at, updated_at
                """),
                changes,
            ).mappings().first()
        if row is None:
            raise not_found("Product not found.")
        return dict(row)
    except Exception as exc:
        if "products_tenant_sku_unique" in str(exc):
            raise conflict("A product with this SKU already exists.") from None
        raise


@router.get("/movements")
def list_movements(
    tenant: Tenant,
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
def create_movement(payload: MovementCreate, tenant: Tenant, db: DB):
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

    return dict(row)


@router.get("/dashboard")
def dashboard_summary(tenant: Tenant, db: DB):
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
