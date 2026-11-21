"""Guards for the authentication schema: the runtime role must never touch these tables."""

import pytest

from tests.catalog_guard import APP_ROLE, OWNER_ROLE
from tests.helpers import app_conn, owner_conn, sqlstate_of

pytestmark = [pytest.mark.db, pytest.mark.guard]

AUTH_TABLES = (
    "public.users",
    "public.workspaces",
    "public.memberships",
    "public.sessions",
    "public.api_keys",
)
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
EXPECTED_FUNCTIONS = {
    "auth_register",
    "auth_get_login",
    "auth_create_session",
    "auth_resolve_session",
    "auth_revoke_session",
    "auth_record_login_failure",
    "auth_record_login_success",
    "auth_default_workspace",
    "auth_list_workspaces",
    "auth_switch_workspace",
    "auth_create_workspace",
    "caller_role",
    "members_list",
    "members_add",
    "members_set_role",
    "members_remove",
    "apikey_create",
    "apikey_list",
    "apikey_revoke",
    "auth_resolve_apikey",
}


def test_runtime_role_holds_no_privilege_on_auth_tables():
    with owner_conn() as conn:
        for table in AUTH_TABLES:
            for privilege in PRIVILEGES:
                row = conn.execute(
                    "SELECT has_table_privilege(%s::text, %s::text, %s::text)",
                    (APP_ROLE, table, privilege),
                ).fetchone()
                assert row == (False,), f"{APP_ROLE} holds {privilege} on {table}"
            row = conn.execute(
                "SELECT has_any_column_privilege(%s::text, %s::text, 'SELECT')",
                (APP_ROLE, table),
            ).fetchone()
            assert row == (False,), f"{APP_ROLE} holds a column privilege on {table}"


def test_direct_access_to_auth_tables_is_denied():
    with app_conn() as conn:
        for table in AUTH_TABLES:
            assert sqlstate_of(lambda t=table: conn.execute(f"SELECT * FROM {t}")) == "42501"
            assert sqlstate_of(lambda t=table: conn.execute(f"DELETE FROM {t}")) == "42501"


def test_row_level_security_is_enabled_on_auth_tables():
    with owner_conn() as conn:
        for table in AUTH_TABLES:
            row = conn.execute(
                "SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(%s)", (table,)
            ).fetchone()
            assert row == (True,), f"RLS is not enabled on {table}"


def test_auth_functions_are_hardened_and_the_expected_set_exists():
    with owner_conn() as conn:
        rows = conn.execute(
            """SELECT p.proname,
                      p.prosecdef,
                      pg_get_userbyid(p.proowner),
                      p.proconfig,
                      p.proacl IS NOT NULL,
                      EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                              WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'),
                      has_function_privilege(%s::text, p.oid, 'EXECUTE')
               FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE n.nspname = 'app'
                 AND p.proname NOT IN ('current_tenant_id', 'current_user_id')""",
            (APP_ROLE,),
        ).fetchall()
    assert {row[0] for row in rows} == EXPECTED_FUNCTIONS
    for name, definer, owner, config, has_acl, public_exec, app_exec in rows:
        assert definer is True, f"{name} must be SECURITY DEFINER"
        assert owner == OWNER_ROLE, f"{name} must be owned by {OWNER_ROLE}"
        assert any(c.startswith("search_path=pg_catalog") for c in (config or [])), name
        assert has_acl and not public_exec, f"{name} must not be executable by PUBLIC"
        assert app_exec, f"{APP_ROLE} must be able to execute {name}"
