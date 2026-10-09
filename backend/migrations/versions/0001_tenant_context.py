"""Phase 0 baseline: tenant/user context helper functions. No business tables.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UUID_RE = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def _fn(name: str, setting: str) -> str:
    return f"""
CREATE FUNCTION app.{name}() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN s.v ~* '{UUID_RE}' THEN s.v::uuid END
  FROM (SELECT current_setting('{setting}', true) AS v) AS s
$$"""


def upgrade() -> None:
    # Requires the roles from scripts/bootstrap_db.py.
    for statement in (
        "CREATE SCHEMA IF NOT EXISTS app",
        "REVOKE ALL ON SCHEMA app FROM PUBLIC",
        "GRANT USAGE ON SCHEMA app TO stockflow_app",
        # Fail closed: unset, empty or malformed context returns NULL, which matches no row.
        _fn("current_tenant_id", "app.tenant_id"),
        _fn("current_user_id", "app.user_id"),
        "REVOKE ALL ON FUNCTION app.current_tenant_id() FROM PUBLIC",
        "REVOKE ALL ON FUNCTION app.current_user_id() FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION app.current_tenant_id() TO stockflow_app",
        "GRANT EXECUTE ON FUNCTION app.current_user_id() TO stockflow_app",
    ):
        op.execute(sa.text(statement))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
