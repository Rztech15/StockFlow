"""Tenant isolation tests against real PostgreSQL and the stockflow_app role."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.context import TenantContext
from app.db import Database, text
from tests.helpers import (
    TENANT_A,
    TENANT_B,
    USER_A,
    USER_B,
    app_url,
    reset_inventory,
    sqlstate_of,
)

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
    """Create one product per tenant and return their IDs."""
    reset_inventory()
    ids = []

    for ctx, tenant_id, sku, name in (
        (CTX_A, TENANT_A, "RLS-A-TEST", "a-secret"),
        (CTX_B, TENANT_B, "RLS-B-TEST", "b-secret"),
    ):
        product_id = str(uuid4())

        with db.tenant_tx(ctx) as conn:
            conn.execute(
                text("""
                    INSERT INTO public.products
                        (id, tenant_id, sku, name)
                    VALUES
                        (:id, :tenant_id, :sku, :name)
                """),
                {
                    "id": product_id,
                    "tenant_id": tenant_id,
                    "sku": sku,
                    "name": name,
                },
            )

        ids.append(product_id)

    return ids[0], ids[1]


def product_names(db, ctx):
    with db.tenant_tx(ctx) as conn:
        return list(
            conn.execute(
                text("SELECT name FROM public.products ORDER BY name")
            ).scalars()
        )


def test_tenant_can_only_read_its_own_products(db, rows):
    a_id, b_id = rows

    with db.tenant_tx(CTX_A) as conn:
        assert conn.execute(
            text("SELECT id FROM public.products WHERE id = :id"),
            {"id": a_id},
        ).scalar_one() == __import__("uuid").UUID(a_id)

        assert conn.execute(
            text("SELECT id FROM public.products WHERE id = :id"),
            {"id": b_id},
        ).first() is None

    assert product_names(db, CTX_A) == ["a-secret"]
    assert product_names(db, CTX_B) == ["b-secret"]


def test_tenant_cannot_insert_for_another_tenant(db, rows):
    del rows
    product_id = str(uuid4())

    def attempt():
        with db.tenant_tx(CTX_A) as conn:
            conn.execute(
                text("""
                    INSERT INTO public.products (id, tenant_id, sku, name)
                    VALUES (:id, :tenant_id, :sku, :name)
                """),
                {
                    "id": product_id,
                    "tenant_id": TENANT_B,
                    "sku": "FORBIDDEN-TEST",
                    "name": "forbidden",
                },
            )

    assert sqlstate_of(attempt) == "42501"


def test_tenant_cannot_update_or_move_another_tenants_product(db, rows):
    a_id, b_id = rows

    with db.tenant_tx(CTX_A) as conn:
        result = conn.execute(
            text("""
                UPDATE public.products
                SET name = 'changed'
                WHERE id = :id
            """),
            {"id": b_id},
        )
        assert result.rowcount == 0

    def move_own_product():
        with db.tenant_tx(CTX_A) as conn:
            conn.execute(
                text("""
                    UPDATE public.products
                    SET tenant_id = :other_tenant
                    WHERE id = :id
                """),
                {"other_tenant": TENANT_B, "id": a_id},
            )

    assert sqlstate_of(move_own_product) == "42501"
    assert product_names(db, CTX_B) == ["b-secret"]


def test_tenant_cannot_delete_another_tenants_product(db, rows):
    _, b_id = rows

    with db.tenant_tx(CTX_A) as conn:
        result = conn.execute(
            text("DELETE FROM public.products WHERE id = :id"),
            {"id": b_id},
        )
        assert result.rowcount == 0

    assert product_names(db, CTX_B) == ["b-secret"]


def test_missing_context_cannot_read_or_insert_products(db, rows):
    del rows

    with db.engine.begin() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM public.products")
        ).scalar_one() == 0

    def attempt():
        with db.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO public.products (id, tenant_id, sku, name)
                    VALUES (:id, :tenant_id, :sku, :name)
                """),
                {
                    "id": str(uuid4()),
                    "tenant_id": TENANT_A,
                    "sku": "NO-CONTEXT-TEST",
                    "name": "forbidden",
                },
            )

    assert sqlstate_of(attempt) == "42501"


def test_context_does_not_leak_after_commit_or_rollback(db, rows):
    a_id, _ = rows

    with db.tenant_tx(CTX_A) as conn:
        assert conn.execute(
            text("SELECT id FROM public.products WHERE id = :id"),
            {"id": a_id},
        ).scalar_one() == __import__("uuid").UUID(a_id)

    with db.engine.begin() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM public.products")
        ).scalar_one() == 0

    with pytest.raises(RuntimeError):
        with db.tenant_tx(CTX_A) as conn:
            conn.execute(
                text("""
                    INSERT INTO public.products (id, tenant_id, sku, name)
                    VALUES (:id, :tenant_id, :sku, :name)
                """),
                {
                    "id": str(uuid4()),
                    "tenant_id": TENANT_A,
                    "sku": "ROLLBACK-TEST",
                    "name": "rollback",
                },
            )
            raise RuntimeError("force rollback")

    with db.engine.begin() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM public.products")
        ).scalar_one() == 0


def test_pooled_connections_do_not_leak_tenant_context(db, rows):
    del rows

    def read_for(ctx):
        with db.tenant_tx(ctx) as conn:
            return list(
                conn.execute(
                    text("SELECT name FROM public.products ORDER BY name")
                ).scalars()
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                read_for,
                [CTX_A, CTX_B, CTX_A, CTX_B] * 3,
            )
        )

    for index, result in enumerate(results):
        assert result == (
            ["a-secret"] if index % 2 == 0 else ["b-secret"]
        )