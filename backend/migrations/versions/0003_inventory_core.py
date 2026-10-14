"""Create tenant-isolated StockFlow inventory tables.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS public.rls_probe;

        CREATE TABLE public.products (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            sku VARCHAR(64) NOT NULL,
            name VARCHAR(160) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 0,
            minimum_stock INTEGER NOT NULL DEFAULT 0,
            unit VARCHAR(24) NOT NULL DEFAULT 'unit',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT products_quantity_nonnegative CHECK (quantity >= 0),
            CONSTRAINT products_minimum_stock_nonnegative
                CHECK (minimum_stock >= 0),
            CONSTRAINT products_tenant_sku_unique UNIQUE (tenant_id, sku),
            CONSTRAINT products_tenant_id_unique UNIQUE (tenant_id, id)
        );

        CREATE TABLE public.stock_movements (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            product_id UUID NOT NULL,
            movement_type VARCHAR(3) NOT NULL,
            quantity INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_by UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT movements_quantity_positive CHECK (quantity > 0),
            CONSTRAINT movements_type_valid
                CHECK (movement_type IN ('IN', 'OUT')),
            CONSTRAINT movements_product_same_tenant
                FOREIGN KEY (tenant_id, product_id)
                REFERENCES public.products (tenant_id, id)
        );

        CREATE INDEX products_tenant_name_idx
            ON public.products (tenant_id, name);

        CREATE INDEX movements_tenant_created_idx
            ON public.stock_movements (tenant_id, created_at DESC);

        CREATE INDEX movements_tenant_product_idx
            ON public.stock_movements (tenant_id, product_id);

        ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.products FORCE ROW LEVEL SECURITY;

        CREATE POLICY products_tenant_isolation
            ON public.products
            USING (tenant_id = app.current_tenant_id())
            WITH CHECK (tenant_id = app.current_tenant_id());

        ALTER TABLE public.stock_movements ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.stock_movements FORCE ROW LEVEL SECURITY;

        CREATE POLICY movements_tenant_isolation
            ON public.stock_movements
            USING (tenant_id = app.current_tenant_id())
            WITH CHECK (tenant_id = app.current_tenant_id());

        GRANT SELECT, INSERT, UPDATE, DELETE
            ON public.products TO stockflow_app;

        GRANT SELECT, INSERT
            ON public.stock_movements TO stockflow_app;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS public.stock_movements;
        DROP TABLE IF EXISTS public.products;

        CREATE TABLE public.rls_probe (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            value TEXT NOT NULL DEFAULT ''
        );

        ALTER TABLE public.rls_probe ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.rls_probe FORCE ROW LEVEL SECURITY;

        CREATE POLICY rls_probe_tenant_isolation
            ON public.rls_probe
            USING (tenant_id = app.current_tenant_id())
            WITH CHECK (tenant_id = app.current_tenant_id());

        GRANT SELECT, INSERT, UPDATE, DELETE
            ON public.rls_probe TO stockflow_app;
    """)
