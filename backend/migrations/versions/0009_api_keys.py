"""API keys for programmatic access.

Like the auth tables, public.api_keys gives the runtime role NO privileges; only narrow
SECURITY DEFINER functions are callable. Only the SHA-256 of a key is stored. A key belongs to
the member who created it: removing that member from the workspace deletes the key.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

D = "SECURITY DEFINER SET search_path = pg_catalog, pg_temp"

STATEMENTS = (
    """CREATE TABLE public.api_keys (
         id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
         workspace_id  uuid NOT NULL REFERENCES public.workspaces (id) ON DELETE CASCADE,
         created_by    uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
         name          varchar(80) NOT NULL,
         key_prefix    varchar(12) NOT NULL,
         key_hash      bytea NOT NULL,
         scope         varchar(12) NOT NULL,
         created_at    timestamptz NOT NULL DEFAULT now(),
         last_used_at  timestamptz,
         revoked_at    timestamptz,
         CONSTRAINT api_keys_hash_unique UNIQUE (key_hash),
         CONSTRAINT api_keys_hash_length CHECK (octet_length(key_hash) = 32),
         CONSTRAINT api_keys_scope_valid CHECK (scope IN ('read', 'readwrite')),
         CONSTRAINT api_keys_creator_member FOREIGN KEY (workspace_id, created_by)
             REFERENCES public.memberships (workspace_id, user_id) ON DELETE CASCADE
       )""",
    "CREATE INDEX api_keys_workspace_idx ON public.api_keys (workspace_id)",
    "ALTER TABLE public.api_keys ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON public.api_keys FROM PUBLIC, stockflow_app",
    f"""CREATE FUNCTION app.apikey_create(p_name text, p_scope text, p_hash bytea, p_prefix text)
        RETURNS uuid LANGUAGE plpgsql {D} AS $$
        DECLARE
          v_id uuid;
        BEGIN
          IF app.caller_role() IS NULL OR app.caller_role() NOT IN ('owner', 'admin') THEN
            RAISE EXCEPTION 'not allowed' USING ERRCODE = '42501';
          END IF;
          IF p_scope NOT IN ('read', 'readwrite') THEN
            RAISE EXCEPTION 'invalid scope' USING ERRCODE = '22023';
          END IF;
          INSERT INTO public.api_keys (workspace_id, created_by, name, key_prefix, key_hash, scope)
            VALUES (app.current_tenant_id(), app.current_user_id(), p_name, p_prefix, p_hash, p_scope)
            RETURNING id INTO v_id;
          RETURN v_id;
        END
        $$""",
    f"""CREATE FUNCTION app.apikey_list()
        RETURNS TABLE (o_id uuid, o_name text, o_prefix text, o_scope text,
                       o_created_at timestamptz, o_last_used_at timestamptz, o_revoked boolean)
        LANGUAGE sql STABLE {D} AS $$
          SELECT k.id, k.name::text, k.key_prefix::text, k.scope::text, k.created_at,
                 k.last_used_at, (k.revoked_at IS NOT NULL)
          FROM public.api_keys k
          WHERE k.workspace_id = app.current_tenant_id()
            AND app.caller_role() IN ('owner', 'admin')
          ORDER BY k.created_at DESC
        $$""",
    f"""CREATE FUNCTION app.apikey_revoke(p_id uuid)
        RETURNS void LANGUAGE plpgsql {D} AS $$
        BEGIN
          IF app.caller_role() IS NULL OR app.caller_role() NOT IN ('owner', 'admin') THEN
            RAISE EXCEPTION 'not allowed' USING ERRCODE = '42501';
          END IF;
          UPDATE public.api_keys k SET revoked_at = now()
            WHERE k.id = p_id AND k.workspace_id = app.current_tenant_id()
              AND k.revoked_at IS NULL;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'no such active key' USING ERRCODE = 'P0002';
          END IF;
        END
        $$""",
    f"""CREATE FUNCTION app.auth_resolve_apikey(p_hash bytea)
        RETURNS TABLE (o_user_id uuid, o_workspace_id uuid, o_scope text)
        LANGUAGE plpgsql {D} AS $$
        BEGIN
          RETURN QUERY
          SELECT k.created_by, k.workspace_id, k.scope::text
          FROM public.api_keys k
          JOIN public.users u ON u.id = k.created_by AND u.disabled_at IS NULL
          WHERE k.key_hash = p_hash AND k.revoked_at IS NULL;
          IF FOUND THEN
            UPDATE public.api_keys k SET last_used_at = now()
              WHERE k.key_hash = p_hash
                AND (k.last_used_at IS NULL OR k.last_used_at < now() - interval '1 minute');
          END IF;
        END
        $$""",
)

SIGNATURES = (
    "app.apikey_create(text, text, bytea, text)",
    "app.apikey_list()",
    "app.apikey_revoke(uuid)",
    "app.auth_resolve_apikey(bytea)",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(sa.text(statement))
    for signature in SIGNATURES:
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        op.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO stockflow_app"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only: use a new migration (expand/contract).")
