import pytest

from tests.catalog_guard import APP_ROLE, OWNER_ROLE
from tests.helpers import app_conn, owner_conn, sqlstate_of

pytestmark = pytest.mark.db


def test_roles_have_no_dangerous_attributes():
    with owner_conn() as conn:
        rows = conn.execute(
            """SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
                      rolreplication, rolcanlogin
                 FROM pg_roles WHERE rolname = ANY(%s)""",
            ([APP_ROLE, OWNER_ROLE],),
        ).fetchall()
    assert len(rows) == 2
    for _name, superuser, bypassrls, createrole, createdb, replication, canlogin in rows:
        assert (superuser, bypassrls, createrole, createdb, replication) == (False,) * 5
        assert canlogin is True


def test_runtime_role_owns_nothing():
    with owner_conn() as conn:
        counts = conn.execute(
            """SELECT
                 (SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = %(r)s),
                 (SELECT count(*) FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner WHERE r.rolname = %(r)s),
                 (SELECT count(*) FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner WHERE r.rolname = %(r)s)""",
            {"r": APP_ROLE},
        ).fetchone()
    assert counts == (0, 0, 0)


def test_runtime_connection_is_stockflow_app_and_cannot_run_ddl():
    with app_conn() as conn:
        assert conn.execute("SELECT current_user").fetchone() == (APP_ROLE,)
        for statement in (
            "CREATE TABLE public.should_fail (id int)",
            "CREATE SCHEMA should_fail",
            "DROP TABLE public.rls_probe",
            "ALTER TABLE public.rls_probe DISABLE ROW LEVEL SECURITY",
            "CREATE ROLE should_fail",
        ):
            assert sqlstate_of(lambda s=statement: conn.execute(s)) == "42501", statement


def test_runtime_role_cannot_switch_row_security_off():
    with app_conn() as conn:
        conn.execute("SET row_security = off")
        # Without BYPASSRLS this does not bypass policies; PostgreSQL raises an error instead.
        assert sqlstate_of(lambda: conn.execute("SELECT * FROM public.rls_probe")) == "42501"
