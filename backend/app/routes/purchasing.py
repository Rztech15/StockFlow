"""Suppliers and purchase orders. Receiving an order books stock-in movements atomically."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.activity import log_activity
from app.auth.deps import DB, require_trusted_origin, tenant_with
from app.context import TenantContext
from app.db import text
from app.errors import conflict, not_found
from app.routes.dberrors import raise_for_db_error
from app.workspace import check_limit

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])

PurchasingRead = Annotated[TenantContext, Depends(tenant_with("purchasing.read"))]
PurchasingWrite = Annotated[TenantContext, Depends(tenant_with("purchasing.write"))]
# Receiving changes stock, so the role needs both permissions.
ReceiveOrder = Annotated[
    TenantContext, Depends(tenant_with("purchasing.write", "inventory.write"))
]


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=2000)


class OrderLine(BaseModel):
    product_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)


class OrderCreate(BaseModel):
    supplier_id: UUID
    note: str = Field(default="", max_length=1000)
    items: list[OrderLine] = Field(min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def _unique_products(cls, items: list[OrderLine]) -> list[OrderLine]:
        if len({line.product_id for line in items}) != len(items):
            raise ValueError("each product may appear only once")
        return items


@router.get("/suppliers")
def list_suppliers(tenant: PurchasingRead, db: DB) -> list[dict[str, object]]:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT id, name, email, phone, notes, created_at
                FROM public.suppliers
                WHERE tenant_id = :tenant_id
                ORDER BY name
            """),
            {"tenant_id": tenant.tenant_id},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/suppliers", status_code=201)
def create_supplier(payload: SupplierCreate, tenant: PurchasingWrite, db: DB):
    try:
        with db.tenant_tx(tenant) as conn:
            check_limit(conn, tenant, "suppliers")
            row = conn.execute(
                text("""
                    INSERT INTO public.suppliers (id, tenant_id, name, email, phone, notes)
                    VALUES (:id, :tenant_id, :name, :email, :phone, :notes)
                    RETURNING id, name, email, phone, notes, created_at
                """),
                {"id": str(uuid4()), "tenant_id": tenant.tenant_id, **payload.model_dump()},
            ).mappings().one()
            log_activity(conn, tenant, "created", "supplier", str(row["id"]), payload.name)
    except Exception as exc:
        raise_for_db_error(exc, duplicate="A supplier with this name already exists.")
    return dict(row)


@router.get("/purchase-orders")
def list_orders(tenant: PurchasingRead, db: DB) -> list[dict[str, object]]:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT o.id, o.status, o.note, o.created_at, o.received_at,
                       o.supplier_id, s.name AS supplier_name,
                       coalesce(
                           json_agg(
                               json_build_object(
                                   'product_id', i.product_id, 'sku', p.sku,
                                   'name', p.name, 'quantity', i.quantity)
                               ORDER BY p.name
                           ) FILTER (WHERE i.id IS NOT NULL),
                           CAST('[]' AS json)
                       ) AS items
                FROM public.purchase_orders o
                JOIN public.suppliers s
                  ON s.id = o.supplier_id AND s.tenant_id = o.tenant_id
                LEFT JOIN public.purchase_order_items i
                  ON i.order_id = o.id AND i.tenant_id = o.tenant_id
                LEFT JOIN public.products p
                  ON p.id = i.product_id AND p.tenant_id = i.tenant_id
                WHERE o.tenant_id = :tenant_id
                GROUP BY o.id, s.name
                ORDER BY o.created_at DESC
                LIMIT 100
            """),
            {"tenant_id": tenant.tenant_id},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/purchase-orders", status_code=201)
def create_order(payload: OrderCreate, tenant: PurchasingWrite, db: DB) -> dict[str, str]:
    order_id = str(uuid4())
    try:
        with db.tenant_tx(tenant) as conn:
            conn.execute(
                text("""
                    INSERT INTO public.purchase_orders
                        (id, tenant_id, supplier_id, note, created_by)
                    VALUES (:id, :tenant_id, :supplier_id, :note, :created_by)
                """),
                {
                    "id": order_id,
                    "tenant_id": tenant.tenant_id,
                    "supplier_id": str(payload.supplier_id),
                    "note": payload.note,
                    "created_by": tenant.user_id,
                },
            )
            for line in payload.items:
                conn.execute(
                    text("""
                        INSERT INTO public.purchase_order_items
                            (id, tenant_id, order_id, product_id, quantity)
                        VALUES (:id, :tenant_id, :order_id, :product_id, :quantity)
                    """),
                    {
                        "id": str(uuid4()),
                        "tenant_id": tenant.tenant_id,
                        "order_id": order_id,
                        "product_id": str(line.product_id),
                        "quantity": line.quantity,
                    },
                )
            log_activity(
                conn, tenant, "created", "purchase_order", order_id, f"{len(payload.items)} line(s)"
            )
    except Exception as exc:
        raise_for_db_error(exc)
    return {"id": order_id, "status": "ordered"}


def _lock_open_order(conn, tenant: TenantContext, order_id: UUID):
    order = conn.execute(
        text("""
            SELECT id, status FROM public.purchase_orders
            WHERE tenant_id = :tenant_id AND id = :id
            FOR UPDATE
        """),
        {"tenant_id": tenant.tenant_id, "id": str(order_id)},
    ).mappings().first()
    if order is None:
        raise not_found("Purchase order not found.")
    if order["status"] != "ordered":
        raise conflict("Only an open order can be changed.")


@router.post("/purchase-orders/{order_id}/receive")
def receive_order(order_id: UUID, tenant: ReceiveOrder, db: DB) -> dict[str, str]:
    with db.tenant_tx(tenant) as conn:
        _lock_open_order(conn, tenant, order_id)
        items = conn.execute(
            text("""
                SELECT product_id, quantity FROM public.purchase_order_items
                WHERE tenant_id = :tenant_id AND order_id = :order_id
                ORDER BY product_id
            """),
            {"tenant_id": tenant.tenant_id, "order_id": str(order_id)},
        ).mappings().all()
        for item in items:
            params = {
                "tenant_id": tenant.tenant_id,
                "product_id": str(item["product_id"]),
                "quantity": item["quantity"],
            }
            conn.execute(
                text("""
                    UPDATE public.products
                    SET quantity = quantity + :quantity, updated_at = now()
                    WHERE tenant_id = :tenant_id AND id = :product_id
                """),
                params,
            )
            conn.execute(
                text("""
                    INSERT INTO public.stock_movements
                        (id, tenant_id, product_id, movement_type, quantity, note, created_by)
                    VALUES
                        (:id, :tenant_id, :product_id, 'IN', :quantity, :note, :created_by)
                """),
                {
                    **params,
                    "id": str(uuid4()),
                    "note": f"Purchase order {str(order_id)[:8]}",
                    "created_by": tenant.user_id,
                },
            )
        conn.execute(
            text("""
                UPDATE public.purchase_orders SET status = 'received', received_at = now()
                WHERE tenant_id = :tenant_id AND id = :id
            """),
            {"tenant_id": tenant.tenant_id, "id": str(order_id)},
        )
        log_activity(conn, tenant, "received", "purchase_order", str(order_id))
    return {"id": str(order_id), "status": "received"}


@router.post("/purchase-orders/{order_id}/cancel")
def cancel_order(order_id: UUID, tenant: PurchasingWrite, db: DB) -> dict[str, str]:
    with db.tenant_tx(tenant) as conn:
        _lock_open_order(conn, tenant, order_id)
        conn.execute(
            text("""
                UPDATE public.purchase_orders SET status = 'cancelled'
                WHERE tenant_id = :tenant_id AND id = :id
            """),
            {"tenant_id": tenant.tenant_id, "id": str(order_id)},
        )
        log_activity(conn, tenant, "cancelled", "purchase_order", str(order_id))
    return {"id": str(order_id), "status": "cancelled"}
