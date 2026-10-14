"""Categories, SKU conventions, plans, notification preferences and an activity log.

All tables carry tenant_id and use the same forced-RLS pattern as 0003. workspace_settings uses
COLUMN-level grants so the runtime role can never change `plan` (plans are changed by an admin
with the owner connection, because no billing provider is connected).

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE public.categories (
         id          uuid PRIMARY KEY,
         tenant_id   uuid NOT NULL,
         name        varchar(80) NOT NULL,
         created_at  timestamptz NOT NULL DEFAULT now(),
         CONSTRAINT categories_tenant_name_unique UNIQUE (tenant_id, name),
         CONSTRAINT categories_tenant_id_unique UNIQUE (tenant_id, id)
       )""",
    "ALTER TABLE public.products ADD COLUMN category_id uuid",
    """ALTER TABLE public.products
         ADD CONSTRAINT products_category_same_tenant
         FOREIGN KEY (tenant_id, category_id)
         REFERENCES public.categories (tenant_id, id) ON DELETE SET NULL (category_id)""",
    """CREATE TABLE public.workspace_settings (
         tenant_id    uuid PRIMARY KEY,
         sku_prefix   varchar(12) NOT NULL DEFAULT '',
         sku_padding  integer NOT NULL DEFAULT 5,
         sku_next     integer NOT NULL DEFAULT 1,
         plan         varchar(12) NOT NULL DEFAULT 'free',
         CONSTRAINT ws_settings_plan_valid CHECK (plan IN ('free', 'pro', 'business')),
         CONSTRAINT ws_settings_padding_valid CHECK (sku_padding BETWEEN 1 AND 12),
         CONSTRAINT ws_settings_prefix_valid CHECK (sku_prefix ~ '^[A-Za-z0-9_-]*$'),
         CONSTRAINT ws_settings_next_positive CHECK (sku_next >= 1)
       )""",
    """CREATE TABLE public.user_preferences (
         tenant_id            uuid NOT NULL,
         user_id              uuid NOT NULL,
         notify_low_stock     boolean NOT NULL DEFAULT true,
         notify_order_events  boolean NOT NULL DEFAULT true,
         PRIMARY KEY (tenant_id, user_id)
       )""",
    """CREATE TABLE public.activity_log (
         id          uuid PRIMARY KEY,
         tenant_id   uuid NOT NULL,
         user_id     uuid NOT NULL,
         action      varchar(40) NOT NULL,
         entity      varchar(40) NOT NULL,
         entity_id   uuid,
         detail      varchar(300) NOT NULL DEFAULT '',
         created_at  timestamptz NOT NULL DEFAULT now()
       )""",
    "CREATE INDEX activity_tenant_created_idx ON public.activity_log (tenant_id, created_at DESC)",
)

TENANT = "tenant_id = app.current_tenant_id()"
POLICIES = (
    ("categories", "categories_tenant_isolation", TENANT),
    ("workspace_settings", "workspace_settings_tenant_isolation", TENANT),
    ("activity_log", "activity_log_tenant_isolation", TENANT),
    # Preferences are private to the user as well as the tenant.
    ("user_preferences", "user_preferences_isolation", f"{TENANT} AND user_id = app.current_user_id()"),
)

GRANTS = (
    "GRANT SELECT, INSERT, UPDATE, DELETE ON public.categories TO stockflow_app",
    "GRANT SELECT, INSERT (tenant_id), UPDATE (sku_prefix, sku_padding, sku_next) "
    "ON public.workspace_settings TO stockflow_app",
    "GRANT SELECT, INSERT, UPDATE ON public.user_preferences TO stockflow_app",
    "GRANT SELECT, INSERT ON public.activity_log TO stockflow_app",  # append-only ledger
)


def upgrade() -> None:
    for statement in TABLES:
        op.execute(sa.text(statement))
    for table, policy, condition in POLICIES:
        op.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {policy} ON public.{table} "
                f"USING ({condition}) WITH CHECK ({condition})"
            )
        )
    for statement in GRANTS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
