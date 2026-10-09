import pytest

from app.context import TenantContext
from app.db import Database, InvalidTenantContextError, text
from tests.helpers import TENANT_A, USER_A, app_conn, app_url

pytestmark = pytest.mark.db
CTX = TenantContext(TENANT_A, USER_A)


@pytest.fixture
def db():
    database = Database(app_url(), pool_size=2)
    yield database
    database.dispose()


def test_context_is_visible_to_the_sql_helper_functions(db):
    with db.tenant_tx(CTX) as conn:
        row = conn.execute(
            text("SELECT app.current_tenant_id()::text, app.current_user_id()::text")
        ).one()
    assert tuple(row) == (TENANT_A, USER_A)


def test_helper_functions_fail_closed():
    with app_conn() as conn:
        assert conn.execute("SELECT app.current_tenant_id(), app.current_user_id()").fetchone() == (None, None)
    with app_conn() as conn:
        conn.autocommit = False
        conn.execute("SELECT set_config('app.tenant_id', 'garbage', true)")
        assert conn.execute("SELECT app.current_tenant_id()").fetchone() == (None,)
        conn.rollback()


def test_invalid_context_is_rejected_before_touching_the_database(db):
    with pytest.raises(InvalidTenantContextError), db.tenant_tx(TenantContext("nope", USER_A)):
        pass
    with pytest.raises(InvalidTenantContextError), db.tenant_tx(
        TenantContext(TENANT_A, "'; drop table x; --")
    ):
        pass


def test_commit_returns_callback_value(db):
    with db.tenant_tx(CTX) as conn:
        assert conn.execute(text("SELECT 42")).scalar_one() == 42
