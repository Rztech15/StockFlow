"""Members, workspace switching, purchasing and reports against the real dev database.

DESTRUCTIVE (TRUNCATE) and guarded by TEST_DATABASE_HOST like the other db tests.
"""

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from tests.helpers import app_url, make_settings, owner_conn, reset_inventory

pytestmark = [pytest.mark.db, pytest.mark.rls]

PASSWORD = "correct horse battery"


def reset_all() -> None:
    reset_inventory()
    with owner_conn() as conn:
        conn.execute(
            "TRUNCATE TABLE public.purchase_order_items, public.purchase_orders, "
            "public.suppliers, public.sessions, public.memberships, "
            "public.workspaces, public.users"
        )
        # stock_movements/products were truncated by reset_inventory (before the line above)


@pytest.fixture
def app():
    reset_all()
    database = Database(app_url(), pool_size=4)
    application = create_app(make_settings(database_url=app_url()), database)
    yield application
    database.dispose()


def signup(app, name: str) -> tuple[TestClient, dict]:
    client = TestClient(app, raise_server_exceptions=False)
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{name}@example.com",
            "password": PASSWORD,
            "workspace_name": f"{name} Ltd",
        },
    )
    assert res.status_code == 201, res.text
    return client, res.json()


def test_purchasing_flow_updates_stock_and_is_tenant_isolated(app):
    alice, _ = signup(app, "alice")
    bob, _ = signup(app, "bob")

    product = alice.post("/api/v1/products", json={"sku": "P-1", "name": "Widget"}).json()
    supplier = alice.post("/api/v1/suppliers", json={"name": "Acme Supply"})
    assert supplier.status_code == 201
    order = alice.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier.json()["id"],
            "items": [{"product_id": product["id"], "quantity": 25}],
        },
    )
    assert order.status_code == 201
    order_id = order.json()["id"]

    # Bob sees nothing and cannot act on Alice's order or reference her records.
    assert bob.get("/api/v1/suppliers").json() == []
    assert bob.get("/api/v1/purchase-orders").json() == []
    assert bob.post(f"/api/v1/purchase-orders/{order_id}/receive").status_code == 404
    bob_product = bob.post("/api/v1/products", json={"sku": "B-1", "name": "Bob item"}).json()
    cross = bob.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier.json()["id"],
            "items": [{"product_id": bob_product["id"], "quantity": 1}],
        },
    )
    assert cross.status_code == 404

    # Receiving adds stock and books exactly one IN movement; it cannot be received twice.
    assert alice.post(f"/api/v1/purchase-orders/{order_id}/receive").status_code == 200
    assert alice.post(f"/api/v1/purchase-orders/{order_id}/receive").status_code == 409
    quantities = {p["sku"]: p["quantity"] for p in alice.get("/api/v1/products").json()}
    assert quantities == {"P-1": 25}
    moves = alice.get("/api/v1/movements").json()
    assert [(m["movement_type"], m["quantity"]) for m in moves] == [("IN", 25)]

    report = alice.get("/api/v1/reports/movements?days=7")
    assert report.status_code == 200
    assert report.json()["top_products"][0]["units_in"] == 25


def test_members_roles_and_workspace_switching(app):
    owner, owner_ids = signup(app, "owner")
    guest, guest_ids = signup(app, "guest")
    owner_workspace = owner_ids["workspace_id"]

    # Unknown account, then a real one.
    missing = owner.post("/api/v1/members", json={"email": "nobody@example.com", "role": "viewer"})
    assert missing.status_code == 404
    added = owner.post("/api/v1/members", json={"email": "guest@example.com", "role": "viewer"})
    assert added.status_code == 201
    duplicate = owner.post("/api/v1/members", json={"email": "guest@example.com", "role": "staff"})
    assert duplicate.status_code == 409

    # The guest can now switch into the owner's workspace, with viewer rights only.
    workspaces = {w["workspace_id"]: w for w in guest.get("/api/v1/workspaces").json()}
    assert workspaces[owner_workspace]["role"] == "viewer"
    switched = guest.post("/api/v1/workspaces/switch", json={"workspace_id": owner_workspace})
    assert switched.status_code == 200
    assert guest.get("/api/v1/products").status_code == 200
    assert guest.post("/api/v1/products", json={"sku": "X", "name": "x"}).status_code == 403
    assert guest.get("/api/v1/members").status_code == 403
    assert guest.post(
        "/api/v1/members", json={"email": "owner@example.com", "role": "owner"}
    ).status_code == 403

    # A workspace the guest does not belong to cannot be selected.
    stranger, stranger_ids = signup(app, "stranger")
    refused = guest.post(
        "/api/v1/workspaces/switch", json={"workspace_id": stranger_ids["workspace_id"]}
    )
    assert refused.status_code == 403

    # The last owner can neither be demoted nor removed.
    owner_user = owner_ids["user_id"]
    assert owner.patch(f"/api/v1/members/{owner_user}", json={"role": "viewer"}).status_code == 409
    assert owner.delete(f"/api/v1/members/{owner_user}").status_code == 409

    # Removing a member ends their access to that workspace immediately.
    assert owner.delete(f"/api/v1/members/{guest_ids['user_id']}").status_code == 204
    assert guest.get("/api/v1/session").status_code == 401


def test_a_user_can_create_and_use_an_additional_workspace(app):
    client, _ = signup(app, "multi")
    created = client.post("/api/v1/workspaces", json={"name": "Second shop"})
    assert created.status_code == 201
    names = sorted(w["name"] for w in client.get("/api/v1/workspaces").json())
    assert names == ["Second shop", "multi Ltd"]
    # The new workspace is active and empty.
    assert client.get("/api/v1/session").json()["tenant_id"] == created.json()["workspace_id"]
    assert client.get("/api/v1/products").json() == []
