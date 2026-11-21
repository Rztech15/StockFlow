import pytest

from tests.catalog_guard import (
    APP_ROLE, APPEND_ONLY_TABLES, append_only_violations, tenant_table_violations,
)  # fmt: skip
from tests.helpers import owner_conn

pytestmark = [pytest.mark.db, pytest.mark.guard]


def test_every_tenant_table_has_forced_rls_and_a_policy():
    with owner_conn() as conn:
        assert tenant_table_violations(conn) == {}


def test_registered_append_only_tables_grant_no_update_delete_truncate():
    with owner_conn() as conn:
        assert append_only_violations(conn, APPEND_ONLY_TABLES) == {}


def test_guard_detects_an_unprotected_tenant_table():
    """Proof of effectiveness: build bad tables inside a transaction that is rolled back."""
    with owner_conn() as conn:
        conn.autocommit = False
        try:
            conn.execute("CREATE TABLE public.guard_bad (id int, tenant_id uuid)")
            find = lambda: tenant_table_violations(conn).get("public.guard_bad")  # noqa: E731

            assert find() == [
                "ROW LEVEL SECURITY is not enabled",
                "FORCE ROW LEVEL SECURITY is not enabled",
                f"no permissive RLS policy applies to role {APP_ROLE}",
            ]
            conn.execute("ALTER TABLE public.guard_bad ENABLE ROW LEVEL SECURITY")
            conn.execute("ALTER TABLE public.guard_bad FORCE ROW LEVEL SECURITY")
            conn.execute("CREATE POLICY p ON public.guard_bad FOR SELECT USING (true)")
            assert find() == ["no policy covers: INSERT, UPDATE, DELETE"]

            conn.execute("DROP POLICY p ON public.guard_bad")
            conn.execute(
                "CREATE POLICY p ON public.guard_bad USING (tenant_id = app.current_tenant_id()) "
                "WITH CHECK (tenant_id = app.current_tenant_id())"
            )
            assert find() is None
        finally:
            conn.rollback()


def test_guard_detects_forbidden_append_only_privileges():
    with owner_conn() as conn:
        conn.autocommit = False
        try:
            conn.execute("CREATE TABLE public.guard_append_only (id int)")
            conn.execute(f"GRANT SELECT, INSERT ON public.guard_append_only TO {APP_ROLE}")
            assert append_only_violations(conn, ("public.guard_append_only",)) == {}

            conn.execute(f"GRANT UPDATE, DELETE ON public.guard_append_only TO {APP_ROLE}")
            bad = append_only_violations(conn, ("public.guard_append_only",))
            assert bad["public.guard_append_only"] == [
                f"{APP_ROLE} holds UPDATE",
                f"{APP_ROLE} holds DELETE",
            ]
            missing = append_only_violations(conn, ("public.does_not_exist",))
            assert missing["public.does_not_exist"] == [
                "registered append-only table does not exist"
            ]
        finally:
            conn.rollback()
