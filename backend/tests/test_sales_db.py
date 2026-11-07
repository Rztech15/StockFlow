"""Customers and sales orders against the real dev database. DESTRUCTIVE (TRUNCATE); guarded by
TEST_DATABASE_HOST like the other db tests."""

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
            "TRUNCATE TABLE public.sales_order_items, public.sales_orders, public.customers, "
            "public.api_keys, public.activity_log, public.user_preferences, "
            "public.workspace_settings, public.categories, public.purchase_order_items, "
            "public.purchase_orders, public.suppliers, public.sessions, public.memberships, "
            "public.workspaces, public.users"
        )


@pytest.fixture
def app():
    reset_all()
    database = Database(app_url(), pool_size=4)
    application = create_app(make_settings(database_url=app_url()), database)
    yield application
    database.dispose()


def signup(app, name: str) -> TestClient:
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
    return client


def stocked_product(client: TestClient, sku: str, units: int) -> dict:
    product = client.post("/api/v1/products", json={"sku": sku, "name": f"Item {sku}"}).json()
    moved = client.post(
        "/api/v1/movements",
        json={"product_id": product["id"], "movement_type": "IN", "quantity": units},
    )
    assert moved.status_code == 201, moved.text
    return product


def place(client: TestClient, customer: dict, product: dict, quantity: int):
    return client.post(
        "/api/v1/sales-orders",
        json={
            "customer_id": customer["id"],
            "items": [{"product_id": product["id"], "quantity": quantity}],
        },
    )


def stock_of(client: TestClient, sku: str) -> dict:
    return {p["sku"]: p for p in client.get("/api/v1/sales/availability").json()}[sku]


def test_an_open_order_reserves_stock_and_cannot_oversell(app):
    alice = signup(app, "alice")
    lamp = stocked_product(alice, "L-1", 10)
    acme = alice.post("/api/v1/customers", json={"name": "Acme"}).json()
    assert alice.post("/api/v1/customers", json={"name": "Acme"}).status_code == 409

    assert place(alice, acme, lamp, 6).status_code == 201
    row = stock_of(alice, "L-1")
    assert (row["quantity"], row["reserved"], row["available"]) == (10, 6, 4)

    oversell = place(alice, acme, lamp, 5)
    assert oversell.status_code == 409 and "Only 4" in oversell.json()["detail"]
    assert place(alice, acme, lamp, 4).status_code == 201  # exactly what is left
    assert stock_of(alice, "L-1")["available"] == 0
    assert alice.get("/api/v1/customers").json()[0]["open_orders"] == 2


def test_shipping_books_an_out_movement_and_cancelling_frees_the_reservation(app):
    alice = signup(app, "alice")
    lamp = stocked_product(alice, "L-1", 10)
    acme = alice.post("/api/v1/customers", json={"name": "Acme"}).json()
    first = place(alice, acme, lamp, 6).json()["id"]
    second = place(alice, acme, lamp, 4).json()["id"]

    assert alice.post(f"/api/v1/sales-orders/{first}/ship").status_code == 200
    row = stock_of(alice, "L-1")
    assert (row["quantity"], row["reserved"], row["available"]) == (4, 4, 0)
    out = [m for m in alice.get("/api/v1/movements").json() if m["movement_type"] == "OUT"]
    assert [m["quantity"] for m in out] == [6]
    assert alice.post(f"/api/v1/sales-orders/{first}/ship").status_code == 409  # already shipped
    assert alice.post(f"/api/v1/sales-orders/{first}/cancel").status_code == 409

    assert alice.post(f"/api/v1/sales-orders/{second}/cancel").status_code == 200
    row = stock_of(alice, "L-1")
    assert (row["quantity"], row["reserved"], row["available"]) == (4, 0, 4)
    assert alice.post(f"/api/v1/sales-orders/{second}/cancel").status_code == 409
    statuses = {o["id"]: o["status"] for o in alice.get("/api/v1/sales-orders").json()}
    assert statuses == {first: "shipped", second: "cancelled"}


def test_an_order_cannot_ship_if_stock_was_taken_out_since(app):
    alice = signup(app, "alice")
    lamp = stocked_product(alice, "L-1", 10)
    acme = alice.post("/api/v1/customers", json={"name": "Acme"}).json()
    order_id = place(alice, acme, lamp, 8).json()["id"]
    taken = alice.post(
        "/api/v1/movements", json={"product_id": lamp["id"], "movement_type": "OUT", "quantity": 5}
    )
    assert taken.status_code == 201
    shipped = alice.post(f"/api/v1/sales-orders/{order_id}/ship")
    assert shipped.status_code == 409 and "Not enough" in shipped.json()["detail"]
    assert stock_of(alice, "L-1")["quantity"] == 5  # nothing was booked


def test_workspaces_cannot_see_or_use_each_others_sales_data(app):
    alice, bob = signup(app, "alice"), signup(app, "bob")
    lamp = stocked_product(alice, "L-1", 10)
    acme = alice.post("/api/v1/customers", json={"name": "Acme"}).json()
    order_id = place(alice, acme, lamp, 2).json()["id"]

    assert bob.get("/api/v1/customers").json() == []
    assert bob.get("/api/v1/sales-orders").json() == []
    assert bob.get("/api/v1/sales/availability").json() == []
    assert place(bob, acme, lamp, 1).status_code == 404  # Alice's customer is invisible to Bob
    assert bob.post(f"/api/v1/sales-orders/{order_id}/ship").status_code == 404
    assert bob.post(f"/api/v1/sales-orders/{order_id}/cancel").status_code == 404
