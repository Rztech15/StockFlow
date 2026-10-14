"""Login support: brute-force lockout state and a default-workspace lookup.

Replaces app.auth_get_credentials (0004) with app.auth_get_login, which also reports whether
the account is currently locked. Same hardening as 0004: SECURITY DEFINER, fixed search_path,
EXECUTE only for stockflow_app, no table privileges for the runtime role.

Lockout rule: 5 consecutive failed logins lock the account for 15 minutes. (Trade-off: an
attacker can lock a victim out. Per-IP rate limiting is a separate, later step.)

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

DEFINER = "SECURITY DEFINER SET search_path = pg_catalog, pg_temp"

STATEMENTS = (
    "ALTER TABLE public.users "
    "ADD COLUMN failed_attempts integer NOT NULL DEFAULT 0, "
    "ADD COLUMN locked_until timestamptz",
    "DROP FUNCTION app.auth_get_credentials(text)",
    f"""CREATE FUNCTION app.auth_get_login(p_email text)
        RETURNS TABLE (o_user_id uuid, o_password_hash text, o_locked boolean)
        LANGUAGE sql STABLE {DEFINER} AS $$
          SELECT u.id, u.password_hash,
                 (u.locked_until IS NOT NULL AND u.locked_until > now())
          FROM public.users u
          WHERE u.email = p_email AND u.disabled_at IS NULL
        $$""",
    f"""CREATE FUNCTION app.auth_record_login_failure(p_user_id uuid)
        RETURNS void
        LANGUAGE plpgsql {DEFINER} AS $$
        DECLARE
          v_attempts integer;
        BEGIN
          UPDATE public.users u
          SET failed_attempts = CASE
                WHEN u.locked_until IS NOT NULL AND u.locked_until <= now() THEN 1
                ELSE u.failed_attempts + 1
              END
          WHERE u.id = p_user_id
          RETURNING u.failed_attempts INTO v_attempts;
          IF v_attempts >= 5 THEN
            UPDATE public.users u
            SET locked_until = now() + interval '15 minutes'
            WHERE u.id = p_user_id;
          END IF;
        END
        $$""",
    f"""CREATE FUNCTION app.auth_record_login_success(p_user_id uuid)
        RETURNS void
        LANGUAGE sql {DEFINER} AS $$
          UPDATE public.users u SET failed_attempts = 0, locked_until = NULL
          WHERE u.id = p_user_id
        $$""",
    f"""CREATE FUNCTION app.auth_default_workspace(p_user_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE {DEFINER} AS $$
          SELECT m.workspace_id
          FROM public.memberships m
          WHERE m.user_id = p_user_id
          ORDER BY m.created_at, m.workspace_id
          LIMIT 1
        $$""",
)

NEW_FUNCTIONS = (
    "app.auth_get_login(text)",
    "app.auth_record_login_failure(uuid)",
    "app.auth_record_login_success(uuid)",
    "app.auth_default_workspace(uuid)",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(sa.text(statement))
    for signature in NEW_FUNCTIONS:
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        op.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO stockflow_app"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
