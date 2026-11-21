"""Suppliers and purchase orders (tenant-isolated, same RLS pattern as 0003).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE public.suppliers (
         id          uuid PRIMARY KEY,
         tenant_id   uuid NOT NULL,
         name        varchar(160) NOT NULL,
         email       varchar(254) NOT NULL DEFAULT '',
         phone       varchar(40) NOT NULL DEFAULT '',
         notes       text NOT NULL DEFAULT '',
         created_at  timestamptz NOT NULL DEFAULT now(),
         CONSTRAINT suppliers_tenant_name_unique UNIQUE (tenant_id, name),
         CONSTRAINT suppliers_tenant_id_unique UNIQUE (tenant_id, id)
       )""",
    """CREATE TABLE public.purchase_orders (
         id           uuid PRIMARY KEY,
         tenant_id    uuid NOT NULL,
         supplier_id  uuid NOT NULL,
         status       varchar(12) NOT NULL DEFAULT 'ordered',
         note         text NOT NULL DEFAULT '',
         created_by   uuid NOT NULL,
         created_at   timestamptz NOT NULL DEFAULT now(),
         received_at  timestamptz,
         CONSTRAINT purchase_orders_status_valid
             CHECK (status IN ('ordered', 'received', 'cancelled')),
         CONSTRAINT purchase_orders_supplier_same_tenant
             FOREIGN KEY (tenant_id, supplier_id) REFERENCES public.suppliers (tenant_id, id),
         CONSTRAINT purchase_orders_tenant_id_unique UNIQUE (tenant_id, id)
       )""",
    """CREATE TABLE public.purchase_order_items (
         id          uuid PRIMARY KEY,
         tenant_id   uuid NOT NULL,
         order_id    uuid NOT NULL,
         product_id  uuid NOT NULL,
         quantity    integer NOT NULL,
         CONSTRAINT po_items_quantity_positive CHECK (quantity > 0),
         CONSTRAINT po_items_order_same_tenant
             FOREIGN KEY (tenant_id, order_id)
             REFERENCES public.purchase_orders (tenant_id, id) ON DELETE CASCADE,
         CONSTRAINT po_items_product_same_tenant
             FOREIGN KEY (tenant_id, product_id) REFERENCES public.products (tenant_id, id),
         CONSTRAINT po_items_order_product_unique UNIQUE (order_id, product_id)
       )""",
    "CREATE INDEX purchase_orders_tenant_created_idx "
    "ON public.purchase_orders (tenant_id, created_at DESC)",
    "CREATE INDEX po_items_tenant_order_idx ON public.purchase_order_items (tenant_id, order_id)",
)

POLICIES = tuple(
    statement
    for table, policy in (
        ("suppliers", "suppliers_tenant_isolation"),
        ("purchase_orders", "purchase_orders_tenant_isolation"),
        ("purchase_order_items", "po_items_tenant_isolation"),
    )
    for statement in (
        f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY",
        f"""CREATE POLICY {policy} ON public.{table}
              USING (tenant_id = app.current_tenant_id())
              WITH CHECK (tenant_id = app.current_tenant_id())""",
    )
)

GRANTS = (
    "GRANT SELECT, INSERT, UPDATE ON public.suppliers TO stockflow_app",
    "GRANT SELECT, INSERT, UPDATE ON public.purchase_orders TO stockflow_app",
    "GRANT SELECT, INSERT ON public.purchase_order_items TO stockflow_app",
)


def upgrade() -> None:
    for statement in (*TABLES, *POLICIES, *GRANTS):
        op.execute(sa.text(statement))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
