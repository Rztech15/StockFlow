"""Customers and sales orders (tenant-isolated, same RLS pattern as 0007).

An open sales order reserves stock; shipping it books OUT movements. There are no prices yet:
the app has no workspace currency, so invoices and payments come with the money model.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE public.customers (
         id          uuid PRIMARY KEY,
         tenant_id   uuid NOT NULL,
         name        varchar(160) NOT NULL,
         email       varchar(254) NOT NULL DEFAULT '',
         phone       varchar(40) NOT NULL DEFAULT '',
         notes       text NOT NULL DEFAULT '',
         created_at  timestamptz NOT NULL DEFAULT now(),
         CONSTRAINT customers_tenant_name_unique UNIQUE (tenant_id, name),
         CONSTRAINT customers_tenant_id_unique UNIQUE (tenant_id, id)
       )""",
    """CREATE TABLE public.sales_orders (
         id            uuid PRIMARY KEY,
         tenant_id     uuid NOT NULL,
         customer_id   uuid NOT NULL,
         status        varchar(12) NOT NULL DEFAULT 'open',
         note          text NOT NULL DEFAULT '',
         created_by    uuid NOT NULL,
         created_at    timestamptz NOT NULL DEFAULT now(),
         shipped_at    timestamptz,
         cancelled_at  timestamptz,
         CONSTRAINT sales_orders_status_valid
             CHECK (status IN ('open', 'shipped', 'cancelled')),
         CONSTRAINT sales_orders_customer_same_tenant
             FOREIGN KEY (tenant_id, customer_id) REFERENCES public.customers (tenant_id, id),
         CONSTRAINT sales_orders_tenant_id_unique UNIQUE (tenant_id, id)
       )""",
    """CREATE TABLE public.sales_order_items (
         id          uuid PRIMARY KEY,
         tenant_id   uuid NOT NULL,
         order_id    uuid NOT NULL,
         product_id  uuid NOT NULL,
         quantity    integer NOT NULL,
         CONSTRAINT so_items_quantity_positive CHECK (quantity > 0),
         CONSTRAINT so_items_order_same_tenant
             FOREIGN KEY (tenant_id, order_id)
             REFERENCES public.sales_orders (tenant_id, id) ON DELETE CASCADE,
         CONSTRAINT so_items_product_same_tenant
             FOREIGN KEY (tenant_id, product_id) REFERENCES public.products (tenant_id, id),
         CONSTRAINT so_items_order_product_unique UNIQUE (order_id, product_id)
       )""",
    "CREATE INDEX sales_orders_tenant_created_idx "
    "ON public.sales_orders (tenant_id, created_at DESC)",
    "CREATE INDEX so_items_tenant_order_idx ON public.sales_order_items (tenant_id, order_id)",
    "CREATE INDEX so_items_tenant_product_idx ON public.sales_order_items (tenant_id, product_id)",
)

POLICIES = tuple(
    statement
    for table, policy in (
        ("customers", "customers_tenant_isolation"),
        ("sales_orders", "sales_orders_tenant_isolation"),
        ("sales_order_items", "so_items_tenant_isolation"),
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
    "GRANT SELECT, INSERT, UPDATE ON public.customers TO stockflow_app",
    "GRANT SELECT, INSERT, UPDATE ON public.sales_orders TO stockflow_app",
    "GRANT SELECT, INSERT ON public.sales_order_items TO stockflow_app",
)


def upgrade() -> None:
    for statement in (*TABLES, *POLICIES, *GRANTS):
        op.execute(sa.text(statement))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
