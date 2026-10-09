"""Tenant isolation against REAL PostgreSQL and the REAL stockflow_app role. No mocks."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.context import TenantContext
from app.db import Database, text
from tests.helpers import (
    TENANT_A, TENANT_B, USER_A, USER_B, app_url, reset_probe, sqlstate_of,
)  # fmt: skip

pytestmark = [pytest.mark.db, pytest.mark.rls]
CTX_A = TenantContext(TENANT_A, USER_A)
CTX_B = TenantContext(TENANT_B, USER_B)


@pytest.fixture
def db():
    database = Database(app_url(), pool_size=4)
    yield database
    database.dispose()


@pytest.fixture
def rows(db):
    """One row per tenant. Returns (id_of_A_row, id_of_B_row)."""
    reset_probe()
    ids = []
    for ctx, tenant, payload in ((CTX_A, TENANT_A, "a-secret"), (CTX_B, TENANT_B, "b-secret")):
        with db.tenant_tx(ctx) as conn:
            ids.append(
                conn.execute(
                    text("INSERT INTO rls_probe (tenant_id, payload) VALUES (:t, :p) RETURNING id"),
                    {"t": tenant, "p": payload},
                ).scalar_one()
            )
    return ids[0], ids[1]


def payloads(db, ctx) -> list[str]:
    with db.tenant_tx(ctx) as conn:
        return [r[0] for r in conn.execute(text("SELECT payload FROM rls_probe ORDER BY payload"))]


def test_1_tenant_a_cannot_read_tenant_b_rows(db, rows):
    _, b_id = rows
    assert payloads(db, CTX_A) == ["a-secret"]
    with db.tenant_tx(CTX_A) as conn:
        assert conn.execute(text("SELECT id FROM rls_probe WHERE id = :i"), {"i": b_id}).all() == []


def test_2_tenant_a_cannot_insert_rows_for_tenant_b(db, rows):
    def attempt():
        with db.tenant_tx(CTX_A) as conn:
            conn.execute(
                text("INSERT INTO rls_probe (tenant_id, payload) VALUES (:t, 'planted')"),
                {"t": TENANT_B},
            )

    assert sqlstate_of(attempt) == "42501"  # violates row-level security policy
    assert payloads(db, CTX_B) == ["b-secret"]


def test_3_tenant_a_cannot_update_tenant_b_rows_or_move_its_own(db, rows):
    a_id, b_id = rows
    with db.tenant_tx(CTX_A) as conn:
        result = conn.execute(text("UPDATE rls_probe SET payload = 'hacked' WHERE id = :i"), {"i": b_id})
        assert result.rowcount == 0

    def move():
        with db.tenant_tx(CTX_A) as conn:
            conn.execute(text("UPDATE rls_probe SET tenant_id = :t WHERE id = :i"), {"t": TENANT_B, "i": a_id})

    assert sqlstate_of(move) == "42501"
    assert payloads(db, CTX_B) == ["b-secret"]


def test_4_tenant_a_cannot_delete_tenant_b_rows(db, rows):
    _, b_id = rows
    with db.tenant_tx(CTX_A) as conn:
        assert conn.execute(text("DELETE FROM rls_probe WHERE id = :i"), {"i": b_id}).rowcount == 0
    assert payloads(db, CTX_B) == ["b-secret"]


def test_5_no_tenant_context_returns_no_rows(db, rows):
    with db.engine.connect() as conn:  # bare connection, no tenant_tx
        assert conn.execute(text("SELECT count(*) FROM rls_probe")).scalar_one() == 0
        conn.rollback()
    with db.engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        assert conn.execute(text("SELECT count(*) FROM rls_probe")).scalar_one() == 0
        conn.execute(text("SELECT set_config('app.tenant_id', 'not-a-uuid', true)"))
        assert conn.execute(text("SELECT count(*) FROM rls_probe")).scalar_one() == 0
        conn.rollback()

    def bare_insert():
        with db.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO rls_probe (tenant_id, payload) VALUES (:t, 'x')"), {"t": TENANT_A}
            )

    assert sqlstate_of(bare_insert) == "42501"


def test_6_context_disappears_after_commit_and_rollback(rows):
    single = Database(app_url(), pool_size=1)  # ONE connection: the next use is the same session
    try:
        with single.tenant_tx(CTX_A) as conn:
            assert conn.execute(text("SELECT count(*) FROM rls_probe")).scalar_one() == 1

        def current_setting() -> str | None:
            with single.engine.connect() as conn:
                value = conn.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar_one()
                count = conn.execute(text("SELECT count(*) FROM rls_probe")).scalar_one()
                conn.rollback()
            assert count == 0
            return value

        assert current_setting() in (None, "")  # after commit

        with pytest.raises(RuntimeError), single.tenant_tx(CTX_A) as conn:
            conn.execute(
                text("INSERT INTO rls_probe (tenant_id, payload) VALUES (:t, 'rolled-back')"),
                {"t": TENANT_A},
            )
            raise RuntimeError("boom")
        assert current_setting() in (None, "")  # after rollback
    finally:
        single.dispose()


def test_7_context_does_not_leak_between_pooled_connections(rows):
    small = Database(app_url(), pool_size=2)
    barrier = threading.Barrier(2, timeout=10)

    def job(i: int) -> tuple[list[str], str]:
        ctx, expected = (CTX_A, "a-secret") if i % 2 == 0 else (CTX_B, "b-secret")
        with small.tenant_tx(ctx) as conn:
            if i < 2:
                barrier.wait()  # first two jobs hold both connections at the same time
            time.sleep(0.005)
            seen = [r[0] for r in conn.execute(text("SELECT payload FROM rls_probe"))]
            setting = conn.execute(text("SELECT current_setting('app.tenant_id')")).scalar_one()
        assert setting == ctx.tenant_id
        return seen, expected

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for seen, expected in pool.map(job, range(40)):
                assert seen == [expected]
        with small.engine.connect() as conn:  # a bare connection from the same pool
            assert conn.execute(text("SELECT count(*) FROM rls_probe")).scalar_one() == 0
            conn.rollback()
    finally:
        small.dispose()
