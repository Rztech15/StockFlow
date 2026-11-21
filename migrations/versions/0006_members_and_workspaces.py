"""Workspace members and workspace switching.

Authorization lives in the database as well as in the API: every members_* function reads the
caller from the transaction-local context set by tenant_tx() (app.current_tenant_id() and
app.current_user_id()) and checks the caller's role itself. The pre-tenant auth_* functions
identify the user from the session token hash, never from a client-supplied user id.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

D = "SECURITY DEFINER SET search_path = pg_catalog, pg_temp"
ROLES = "('owner', 'admin', 'manager', 'staff', 'viewer')"

FUNCTIONS = (
    f"""CREATE FUNCTION app.caller_role()
        RETURNS text LANGUAGE sql STABLE {D} AS $$
          SELECT m.role::text FROM public.memberships m
          WHERE m.workspace_id = app.current_tenant_id() AND m.user_id = app.current_user_id()
        $$""",
    f"""CREATE FUNCTION app.members_list()
        RETURNS TABLE (o_user_id uuid, o_email text, o_role text, o_joined_at timestamptz)
        LANGUAGE sql STABLE {D} AS $$
          SELECT m.user_id, u.email, m.role::text, m.created_at
          FROM public.memberships m
          JOIN public.users u ON u.id = m.user_id
          WHERE m.workspace_id = app.current_tenant_id() AND app.caller_role() IS NOT NULL
          ORDER BY m.created_at, u.email
        $$""",
    f"""CREATE FUNCTION app.members_add(p_email text, p_role text)
        RETURNS uuid LANGUAGE plpgsql {D} AS $$
        DECLARE
          v_caller text DEFAULT app.caller_role();
          v_user uuid;
        BEGIN
          IF v_caller IS NULL OR v_caller NOT IN ('owner', 'admin') THEN
            RAISE EXCEPTION 'not allowed' USING ERRCODE = '42501';
          END IF;
          IF p_role NOT IN {ROLES} THEN
            RAISE EXCEPTION 'invalid role' USING ERRCODE = '22023';
          END IF;
          IF p_role = 'owner' AND v_caller <> 'owner' THEN
            RAISE EXCEPTION 'only an owner can add an owner' USING ERRCODE = '42501';
          END IF;
          SELECT u.id INTO v_user FROM public.users u
            WHERE u.email = lower(btrim(p_email)) AND u.disabled_at IS NULL;
          IF v_user IS NULL THEN
            RAISE EXCEPTION 'no such account' USING ERRCODE = 'P0002';
          END IF;
          INSERT INTO public.memberships (workspace_id, user_id, role)
            VALUES (app.current_tenant_id(), v_user, p_role);
          RETURN v_user;
        END
        $$""",
    f"""CREATE FUNCTION app.members_set_role(p_user_id uuid, p_role text)
        RETURNS void LANGUAGE plpgsql {D} AS $$
        DECLARE
          v_caller text DEFAULT app.caller_role();
          v_target text;
          v_owners integer;
        BEGIN
          IF v_caller IS NULL OR v_caller NOT IN ('owner', 'admin') THEN
            RAISE EXCEPTION 'not allowed' USING ERRCODE = '42501';
          END IF;
          IF p_role NOT IN {ROLES} THEN
            RAISE EXCEPTION 'invalid role' USING ERRCODE = '22023';
          END IF;
          PERFORM 1 FROM public.memberships m
            WHERE m.workspace_id = app.current_tenant_id() AND m.role = 'owner' FOR UPDATE;
          SELECT m.role::text INTO v_target FROM public.memberships m
            WHERE m.workspace_id = app.current_tenant_id() AND m.user_id = p_user_id;
          IF v_target IS NULL THEN
            RAISE EXCEPTION 'no such member' USING ERRCODE = 'P0002';
          END IF;
          IF (v_target = 'owner' OR p_role = 'owner') AND v_caller <> 'owner' THEN
            RAISE EXCEPTION 'only an owner can change owners' USING ERRCODE = '42501';
          END IF;
          IF v_target = 'owner' AND p_role <> 'owner' THEN
            SELECT count(*) INTO v_owners FROM public.memberships m
              WHERE m.workspace_id = app.current_tenant_id() AND m.role = 'owner';
            IF v_owners <= 1 THEN
              RAISE EXCEPTION 'a workspace needs at least one owner' USING ERRCODE = '22023';
            END IF;
          END IF;
          UPDATE public.memberships m SET role = p_role
            WHERE m.workspace_id = app.current_tenant_id() AND m.user_id = p_user_id;
        END
        $$""",
    f"""CREATE FUNCTION app.members_remove(p_user_id uuid)
        RETURNS void LANGUAGE plpgsql {D} AS $$
        DECLARE
          v_caller text DEFAULT app.caller_role();
          v_target text;
          v_owners integer;
        BEGIN
          IF v_caller IS NULL OR v_caller NOT IN ('owner', 'admin') THEN
            RAISE EXCEPTION 'not allowed' USING ERRCODE = '42501';
          END IF;
          PERFORM 1 FROM public.memberships m
            WHERE m.workspace_id = app.current_tenant_id() AND m.role = 'owner' FOR UPDATE;
          SELECT m.role::text INTO v_target FROM public.memberships m
            WHERE m.workspace_id = app.current_tenant_id() AND m.user_id = p_user_id;
          IF v_target IS NULL THEN
            RAISE EXCEPTION 'no such member' USING ERRCODE = 'P0002';
          END IF;
          IF v_target = 'owner' AND v_caller <> 'owner' THEN
            RAISE EXCEPTION 'only an owner can remove an owner' USING ERRCODE = '42501';
          END IF;
          IF v_target = 'owner' THEN
            SELECT count(*) INTO v_owners FROM public.memberships m
              WHERE m.workspace_id = app.current_tenant_id() AND m.role = 'owner';
            IF v_owners <= 1 THEN
              RAISE EXCEPTION 'a workspace needs at least one owner' USING ERRCODE = '22023';
            END IF;
          END IF;
          DELETE FROM public.memberships m
            WHERE m.workspace_id = app.current_tenant_id() AND m.user_id = p_user_id;
        END
        $$""",
    f"""CREATE FUNCTION app.auth_list_workspaces(p_token_hash bytea)
        RETURNS TABLE (o_workspace_id uuid, o_name text, o_role text, o_active boolean)
        LANGUAGE sql STABLE {D} AS $$
          SELECT w.id, w.name::text, m.role::text, coalesce(w.id = s.workspace_id, false)
          FROM public.sessions s
          JOIN public.memberships m ON m.user_id = s.user_id
          JOIN public.workspaces w ON w.id = m.workspace_id
          WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > now()
          ORDER BY w.name, w.id
        $$""",
    f"""CREATE FUNCTION app.auth_switch_workspace(p_token_hash bytea, p_workspace_id uuid)
        RETURNS boolean LANGUAGE plpgsql {D} AS $$
        BEGIN
          UPDATE public.sessions s SET workspace_id = p_workspace_id
          WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > now()
            AND EXISTS (SELECT 1 FROM public.memberships m
                        WHERE m.user_id = s.user_id AND m.workspace_id = p_workspace_id);
          RETURN FOUND;
        END
        $$""",
    f"""CREATE FUNCTION app.auth_create_workspace(p_token_hash bytea, p_name text)
        RETURNS uuid LANGUAGE plpgsql {D} AS $$
        DECLARE
          v_user uuid;
          v_ws uuid;
        BEGIN
          SELECT s.user_id INTO v_user FROM public.sessions s
            WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL AND s.expires_at > now();
          IF v_user IS NULL THEN
            RAISE EXCEPTION 'invalid session' USING ERRCODE = '42501';
          END IF;
          IF (SELECT count(*) FROM public.workspaces w WHERE w.created_by = v_user) >= 20 THEN
            RAISE EXCEPTION 'workspace limit reached' USING ERRCODE = '22023';
          END IF;
          INSERT INTO public.workspaces (name, created_by) VALUES (p_name, v_user)
            RETURNING id INTO v_ws;
          INSERT INTO public.memberships (workspace_id, user_id, role)
            VALUES (v_ws, v_user, 'owner');
          UPDATE public.sessions s SET workspace_id = v_ws WHERE s.token_hash = p_token_hash;
          RETURN v_ws;
        END
        $$""",
)

SIGNATURES = (
    "app.caller_role()",
    "app.members_list()",
    "app.members_add(text, text)",
    "app.members_set_role(uuid, text)",
    "app.members_remove(uuid)",
    "app.auth_list_workspaces(bytea)",
    "app.auth_switch_workspace(bytea, uuid)",
    "app.auth_create_workspace(bytea, text)",
)


def upgrade() -> None:
    for statement in FUNCTIONS:
        op.execute(sa.text(statement))
    for signature in SIGNATURES:
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        op.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO stockflow_app"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
