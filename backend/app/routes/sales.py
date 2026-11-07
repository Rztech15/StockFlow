"""Customers and sales orders.

An open order reserves stock: available = on hand - units on open orders. Shipping books OUT
movements in the append-only ledger. Cancelling just closes the order, which frees the
reservation. Product rows are locked in id order so concurrent orders cannot oversell.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.activity import log_activity
from app.auth.deps import DB, require_trusted_origin, tenant_with
from app.context import TenantContext
from app.db import Connection, text
from app.errors import conflict, not_found
from app.routes.dberrors import raise_for_db_error

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])

SalesRead = Annotated[TenantContext, Depends(tenant_with("sales.read"))]
SalesWrite = Annotated[TenantContext, Depends(tenant_with("sales.write"))]
# Shipping changes stock, so the role needs both permissions.
ShipOrder = Annotated[TenantContext, Depends(tenant_with("sales.write", "inventory.write"))]


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=2000)


class SalesLine(BaseModel):
    product_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)


class SalesOrderCreate(BaseModel):
    customer_id: UUID
    note: str = Field(default="", max_length=1000)
    items: list[SalesLine] = Field(min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def _unique_products(cls, items: list[SalesLine]) -> list[SalesLine]:
        if len({line.product_id for line in items}) != len(items):
            raise ValueError("each product may appear only once")
        return items


RESERVED_SQL = """
    SELECT i.product_id, CAST(sum(i.quantity) AS integer) AS reserved
    FROM public.sales_order_items i
    JOIN public.sales_orders o ON o.id = i.order_id AND o.tenant_id = i.tenant_id
    WHERE i.tenant_id = :tenant_id AND o.status = 'open'
      AND i.product_id = ANY(CAST(:ids AS uuid[]))
    GROUP BY i.product_id
"""

LOCK_PRODUCTS_SQL = """
    SELECT id, name, quantity FROM public.products
    WHERE tenant_id = :tenant_id AND id = ANY(CAST(:ids AS uuid[]))
    ORDER BY id
    FOR UPDATE
"""


@router.get("/customers")
def list_customers(tenant: SalesRead, db: DB) -> list[dict[str, object]]:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT c.id, c.name, c.email, c.phone, c.notes, c.created_at,
                       count(o.id) FILTER (WHERE o.status = 'open') AS open_orders
                FROM public.customers c
                LEFT JOIN public.sales_orders o
                  ON o.customer_id = c.id AND o.tenant_id = c.tenant_id
                WHERE c.tenant_id = :tenant_id
                GROUP BY c.id
                ORDER BY c.name
            """),
            {"tenant_id": tenant.tenant_id},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/customers", status_code=201)
def create_customer(payload: CustomerCreate, tenant: SalesWrite, db: DB):
    try:
        with db.tenant_tx(tenant) as conn:
            row = conn.execute(
                text("""
                    INSERT INTO public.customers (id, tenant_id, name, email, phone, notes)
                    VALUES (:id, :tenant_id, :name, :email, :phone, :notes)
                    RETURNING id, name, email, phone, notes, created_at
                """),
                {"id": str(uuid4()), "tenant_id": tenant.tenant_id, **payload.model_dump()},
            ).mappings().one()
            log_activity(conn, tenant, "created", "customer", str(row["id"]), payload.name)
    except Exception as exc:
        raise_for_db_error(exc, duplicate="A customer with this name already exists.")
    return dict(row)


@router.get("/sales/availability")
def availability(tenant: SalesRead, db: DB) -> list[dict[str, object]]:
    """Every product with its stock on hand, the units reserved by open orders and the rest."""
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT p.id, p.sku, p.name, p.unit, p.quantity,
                       CAST(coalesce(sum(i.quantity) FILTER (WHERE o.status = 'open'), 0)
                            AS integer) AS reserved
                FROM public.products p
                LEFT JOIN public.sales_order_items i
                  ON i.product_id = p.id AND i.tenant_id = p.tenant_id
                LEFT JOIN public.sales_orders o
                  ON o.id = i.order_id AND o.tenant_id = i.tenant_id
                WHERE p.tenant_id = :tenant_id
                GROUP BY p.id
                ORDER BY p.name
            """),
            {"tenant_id": tenant.tenant_id},
        ).mappings().all()
    return [{**dict(r), "available": r["quantity"] - r["reserved"]} for r in rows]


@router.get("/sales-orders")
def list_orders(tenant: SalesRead, db: DB) -> list[dict[str, object]]:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT o.id, o.status, o.note, o.created_at, o.shipped_at, o.cancelled_at,
                       o.customer_id, c.name AS customer_name,
                       coalesce(
                           json_agg(
                               json_build_object(
                                   'product_id', i.product_id, 'sku', p.sku,
                                   'name', p.name, 'quantity', i.quantity)
                               ORDER BY p.name
                           ) FILTER (WHERE i.id IS NOT NULL),
                           CAST('[]' AS json)
                       ) AS items
                FROM public.sales_orders o
                JOIN public.customers c
                  ON c.id = o.customer_id AND c.tenant_id = o.tenant_id
                LEFT JOIN public.sales_order_items i
                  ON i.order_id = o.id AND i.tenant_id = o.tenant_id
                LEFT JOIN public.products p
                  ON p.id = i.product_id AND p.tenant_id = i.tenant_id
                WHERE o.tenant_id = :tenant_id
                GROUP BY o.id, c.name
                ORDER BY o.created_at DESC
                LIMIT 100
            """),
            {"tenant_id": tenant.tenant_id},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/sales-orders", status_code=201)
def create_order(payload: SalesOrderCreate, tenant: SalesWrite, db: DB) -> dict[str, str]:
    order_id = str(uuid4())
    ids = [str(line.product_id) for line in payload.items]
    try:
        with db.tenant_tx(tenant) as conn:
            customer = conn.execute(
                text("SELECT id FROM public.customers WHERE tenant_id = :tenant_id AND id = :id"),
                {"tenant_id": tenant.tenant_id, "id": str(payload.customer_id)},
            ).first()
            if customer is None:
                raise not_found("Customer not found.")
            locked = conn.execute(
                text(LOCK_PRODUCTS_SQL), {"tenant_id": tenant.tenant_id, "ids": ids}
            ).mappings().all()
            products = {str(p["id"]): p for p in locked}
            if len(products) != len(ids):
                raise not_found("A product was not found.")
            reserved = {
                str(r["product_id"]): int(r["reserved"])
                for r in conn.execute(
                    text(RESERVED_SQL), {"tenant_id": tenant.tenant_id, "ids": ids}
                ).mappings()
            }
            for line in payload.items:
                key = str(line.product_id)
                free = products[key]["quantity"] - reserved.get(key, 0)
                if line.quantity > free:
                    raise conflict(f"Only {max(free, 0)} of {products[key]['name']} available.")
            conn.execute(
                text("""
                    INSERT INTO public.sales_orders
                        (id, tenant_id, customer_id, note, created_by)
                    VALUES (:id, :tenant_id, :customer_id, :note, :created_by)
                """),
                {
                    "id": order_id, "tenant_id": tenant.tenant_id,
                    "customer_id": str(payload.customer_id), "note": payload.note,
                    "created_by": tenant.user_id,
                },
            )
            for line in payload.items:
                conn.execute(
                    text("""
                        INSERT INTO public.sales_order_items
                            (id, tenant_id, order_id, product_id, quantity)
                        VALUES (:id, :tenant_id, :order_id, :product_id, :quantity)
                    """),
                    {
                        "id": str(uuid4()), "tenant_id": tenant.tenant_id, "order_id": order_id,
                        "product_id": str(line.product_id), "quantity": line.quantity,
                    },
                )
            log_activity(
                conn, tenant, "created", "sales_order", order_id, f"{len(payload.items)} line(s)"
            )
    except Exception as exc:
        raise_for_db_error(exc)
    return {"id": order_id, "status": "open"}


def _lock_open_order(conn: Connection, tenant: TenantContext, order_id: UUID) -> None:
    order = conn.execute(
        text("""
            SELECT id, status FROM public.sales_orders
            WHERE tenant_id = :tenant_id AND id = :id
            FOR UPDATE
        """),
        {"tenant_id": tenant.tenant_id, "id": str(order_id)},
    ).mappings().first()
    if order is None:
        raise not_found("Sales order not found.")
    if order["status"] != "open":
        raise conflict("Only an open order can be changed.")


@router.post("/sales-orders/{order_id}/ship")
def ship_order(order_id: UUID, tenant: ShipOrder, db: DB) -> dict[str, str]:
    with db.tenant_tx(tenant) as conn:
        _lock_open_order(conn, tenant, order_id)
        items = conn.execute(
            text("""
                SELECT product_id, quantity FROM public.sales_order_items
                WHERE tenant_id = :tenant_id AND order_id = :order_id
                ORDER BY product_id
            """),
            {"tenant_id": tenant.tenant_id, "order_id": str(order_id)},
        ).mappings().all()
        ids = [str(i["product_id"]) for i in items]
        locked = conn.execute(
            text(LOCK_PRODUCTS_SQL), {"tenant_id": tenant.tenant_id, "ids": ids}
        ).mappings().all()
        products = {str(p["id"]): p for p in locked}
        for item in items:
            product = products[str(item["product_id"])]
            if product["quantity"] < item["quantity"]:
                raise conflict(
                    f"Not enough {product['name']} in stock to ship "
                    f"({product['quantity']} left, {item['quantity']} needed)."
                )
        for item in items:
            params = {
                "tenant_id": tenant.tenant_id,
                "product_id": str(item["product_id"]),
                "quantity": item["quantity"],
            }
            conn.execute(
                text("""
                    UPDATE public.products
                    SET quantity = quantity - :quantity, updated_at = now()
                    WHERE tenant_id = :tenant_id AND id = :product_id
                """),
                params,
            )
            conn.execute(
                text("""
                    INSERT INTO public.stock_movements
                        (id, tenant_id, product_id, movement_type, quantity, note, created_by)
                    VALUES
                        (:id, :tenant_id, :product_id, 'OUT', :quantity, :note, :created_by)
                """),
                {
                    **params,
                    "id": str(uuid4()),
                    "note": f"Sales order {str(order_id)[:8]}",
                    "created_by": tenant.user_id,
                },
            )
        conn.execute(
            text("""
                UPDATE public.sales_orders SET status = 'shipped', shipped_at = now()
                WHERE tenant_id = :tenant_id AND id = :id
            """),
            {"tenant_id": tenant.tenant_id, "id": str(order_id)},
        )
        log_activity(conn, tenant, "shipped", "sales_order", str(order_id))
    return {"id": str(order_id), "status": "shipped"}


@router.post("/sales-orders/{order_id}/cancel")
def cancel_order(order_id: UUID, tenant: SalesWrite, db: DB) -> dict[str, str]:
    with db.tenant_tx(tenant) as conn:
        _lock_open_order(conn, tenant, order_id)
        conn.execute(
            text("""
                UPDATE public.sales_orders SET status = 'cancelled', cancelled_at = now()
                WHERE tenant_id = :tenant_id AND id = :id
            """),
            {"tenant_id": tenant.tenant_id, "id": str(order_id)},
        )
        log_activity(conn, tenant, "cancelled", "sales_order", str(order_id))
    return {"id": str(order_id), "status": "cancelled"}
