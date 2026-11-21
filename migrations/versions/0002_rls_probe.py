"""THROWAWAY table that proves the RLS design (tests in tests/test_rls_isolation.py).
Phase 1 drops it with a new migration. It is not a business table.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        """CREATE TABLE public.rls_probe (
             id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
             tenant_id  uuid NOT NULL,
             payload    text NOT NULL,
             created_at timestamptz NOT NULL DEFAULT now()
           )""",
        "COMMENT ON TABLE public.rls_probe IS 'THROWAWAY Phase 0 RLS proof table. Drop in Phase 1.'",
        "ALTER TABLE public.rls_probe ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE public.rls_probe FORCE ROW LEVEL SECURITY",
        # One FOR ALL policy covers SELECT, INSERT, UPDATE and DELETE.
        # USING filters existing rows; WITH CHECK stops writing/moving rows into another tenant.
        """CREATE POLICY tenant_isolation ON public.rls_probe
             USING (tenant_id = app.current_tenant_id())
             WITH CHECK (tenant_id = app.current_tenant_id())""",
        # Explicit minimal grants; there are no default privileges for future tables.
        "GRANT SELECT, INSERT, UPDATE, DELETE ON public.rls_probe TO stockflow_app",
    ):
        op.execute(sa.text(statement))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
