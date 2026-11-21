"""Authentication core: users, workspaces, memberships, sessions.

Design (see the review notes in the chat):
- The runtime role stockflow_app gets NO table privileges on these tables. It can only call
  the narrow SECURITY DEFINER functions below, which are owned by stockflow_owner.
- RLS is enabled with no policies as defense in depth: any role other than the table owner
  sees zero rows even if a grant were added by mistake.
- Columns are named workspace_id (not tenant_id) on purpose: these tables are reached only
  through the functions, so the tenant-table catalog guard does not apply to them.
- products / stock_movements are NOT given foreign keys here (existing tenant tests use
  fictitious tenant ids). That linkage is a separate, later migration.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

FUNCTION_DEFAULTS = "SECURITY DEFINER SET search_path = pg_catalog, pg_temp"

TABLES = (
    """CREATE TABLE public.users (
         id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
         email          text NOT NULL,
         email_verified boolean NOT NULL DEFAULT false,
         password_hash  text NOT NULL,
         disabled_at    timestamptz,
         created_at     timestamptz NOT NULL DEFAULT now(),
         CONSTRAINT users_email_unique UNIQUE (email),
         CONSTRAINT users_email_normalized
             CHECK (email = lower(btrim(email)) AND length(email) BETWEEN 3 AND 254),
         CONSTRAINT users_password_hash_present CHECK (length(password_hash) > 0)
       )""",
    """CREATE TABLE public.workspaces (
         id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
         name       varchar(120) NOT NULL,
         created_by uuid NOT NULL REFERENCES public.users (id),
         created_at timestamptz NOT NULL DEFAULT now(),
         CONSTRAINT workspaces_name_present CHECK (length(btrim(name)) > 0)
       )""",
    """CREATE TABLE public.memberships (
         workspace_id uuid NOT NULL REFERENCES public.workspaces (id) ON DELETE CASCADE,
         user_id      uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
         role         varchar(16) NOT NULL,
         created_at   timestamptz NOT NULL DEFAULT now(),
         PRIMARY KEY (workspace_id, user_id),
         CONSTRAINT memberships_role_valid
             CHECK (role IN ('owner', 'admin', 'manager', 'staff', 'viewer'))
       )""",
    "CREATE INDEX memberships_user_idx ON public.memberships (user_id)",
    """CREATE TABLE public.sessions (
         id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
         user_id      uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
         workspace_id uuid,
         token_hash   bytea NOT NULL,
         created_at   timestamptz NOT NULL DEFAULT now(),
         last_seen_at timestamptz NOT NULL DEFAULT now(),
         expires_at   timestamptz NOT NULL,
         revoked_at   timestamptz,
         CONSTRAINT sessions_token_hash_unique UNIQUE (token_hash),
         CONSTRAINT sessions_token_hash_length CHECK (octet_length(token_hash) = 32),
         -- A session can only point at a workspace its user belongs to.
         CONSTRAINT sessions_membership_fk FOREIGN KEY (workspace_id, user_id)
             REFERENCES public.memberships (workspace_id, user_id) ON DELETE CASCADE
       )""",
    "CREATE INDEX sessions_user_idx ON public.sessions (user_id)",
    "CREATE INDEX sessions_expires_idx ON public.sessions (expires_at)",
)

LOCK_DOWN = (
    "ALTER TABLE public.users ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON public.users, public.workspaces, public.memberships, public.sessions "
    "FROM PUBLIC, stockflow_app",
)

FUNCTIONS = (
    # Registration: user + first workspace + owner membership, atomically.
    f"""CREATE FUNCTION app.auth_register(
          p_email text, p_password_hash text, p_workspace_name text)
        RETURNS TABLE (o_user_id uuid, o_workspace_id uuid)
        LANGUAGE plpgsql {FUNCTION_DEFAULTS} AS $$
        DECLARE
          v_user uuid;
          v_ws uuid;
        BEGIN
          INSERT INTO public.users (email, password_hash)
            VALUES (p_email, p_password_hash) RETURNING id INTO v_user;
          INSERT INTO public.workspaces (name, created_by)
            VALUES (p_workspace_name, v_user) RETURNING id INTO v_ws;
          INSERT INTO public.memberships (workspace_id, user_id, role)
            VALUES (v_ws, v_user, 'owner');
          RETURN QUERY SELECT v_user, v_ws;
        END
        $$""",
    # Login step 1: fetch the stored hash. The application verifies the password.
    f"""CREATE FUNCTION app.auth_get_credentials(p_email text)
        RETURNS TABLE (o_user_id uuid, o_password_hash text)
        LANGUAGE sql STABLE {FUNCTION_DEFAULTS} AS $$
          SELECT u.id, u.password_hash
          FROM public.users u
          WHERE u.email = p_email AND u.disabled_at IS NULL
        $$""",
    # Login step 2: create a session. Only the SHA-256 of the token is stored.
    f"""CREATE FUNCTION app.auth_create_session(
          p_user_id uuid, p_workspace_id uuid, p_token_hash bytea, p_ttl_seconds integer)
        RETURNS TABLE (o_session_id uuid, o_expires_at timestamptz)
        LANGUAGE plpgsql {FUNCTION_DEFAULTS} AS $$
        DECLARE
          v_id uuid;
          v_exp timestamptz;
        BEGIN
          IF p_ttl_seconds NOT BETWEEN 60 AND 2592000 THEN
            RAISE EXCEPTION 'invalid session lifetime' USING ERRCODE = '22023';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.users u WHERE u.id = p_user_id AND u.disabled_at IS NULL
          ) THEN
            RAISE EXCEPTION 'unknown or disabled user' USING ERRCODE = '42501';
          END IF;
          IF p_workspace_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.memberships m
            WHERE m.workspace_id = p_workspace_id AND m.user_id = p_user_id
          ) THEN
            RAISE EXCEPTION 'not a member of this workspace' USING ERRCODE = '42501';
          END IF;
          INSERT INTO public.sessions (user_id, workspace_id, token_hash, expires_at)
            VALUES (p_user_id, p_workspace_id, p_token_hash,
                    now() + make_interval(secs => p_ttl_seconds))
            RETURNING id, expires_at INTO v_id, v_exp;
          RETURN QUERY SELECT v_id, v_exp;
        END
        $$""",
    # Per request: token hash -> user, active workspace and role. Membership is re-checked
    # every time, so removing a member takes effect immediately.
    f"""CREATE FUNCTION app.auth_resolve_session(p_token_hash bytea)
        RETURNS TABLE (o_user_id uuid, o_workspace_id uuid, o_role text)
        LANGUAGE plpgsql {FUNCTION_DEFAULTS} AS $$
        BEGIN
          RETURN QUERY
          SELECT s.user_id, s.workspace_id, m.role::text
          FROM public.sessions s
          JOIN public.users u ON u.id = s.user_id AND u.disabled_at IS NULL
          LEFT JOIN public.memberships m
            ON m.workspace_id = s.workspace_id AND m.user_id = s.user_id
          WHERE s.token_hash = p_token_hash
            AND s.revoked_at IS NULL
            AND s.expires_at > now()
            AND (s.workspace_id IS NULL OR m.user_id IS NOT NULL);
          IF FOUND THEN
            UPDATE public.sessions s SET last_seen_at = now()
            WHERE s.token_hash = p_token_hash AND s.last_seen_at < now() - interval '1 minute';
          END IF;
        END
        $$""",
    f"""CREATE FUNCTION app.auth_revoke_session(p_token_hash bytea)
        RETURNS void
        LANGUAGE sql {FUNCTION_DEFAULTS} AS $$
          UPDATE public.sessions s SET revoked_at = now()
          WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL
        $$""",
)

SIGNATURES = (
    "app.auth_register(text, text, text)",
    "app.auth_get_credentials(text)",
    "app.auth_create_session(uuid, uuid, bytea, integer)",
    "app.auth_resolve_session(bytea)",
    "app.auth_revoke_session(bytea)",
)


def upgrade() -> None:
    # Runs as stockflow_owner (MIGRATION_DATABASE_URL), so the functions are owned by it.
    for statement in (*TABLES, *LOCK_DOWN, *FUNCTIONS):
        op.execute(sa.text(statement))
    for signature in SIGNATURES:
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        op.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO stockflow_app"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
