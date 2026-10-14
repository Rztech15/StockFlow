"""Categories, SKU conventions, plan limits, API keys, activity log and insights against the real
dev database. DESTRUCTIVE (TRUNCATE); guarded by TEST_DATABASE_HOST like the other db tests."""

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from tests.helpers import app_conn, app_url, make_settings, owner_conn, reset_inventory, sqlstate_of

pytestmark = [pytest.mark.db, pytest.mark.rls]

PASSWORD = "correct horse battery"


def reset_all() -> None:
    reset_inventory()
    with owner_conn() as conn:
        conn.execute(
            "TRUNCATE TABLE public.api_keys, public.activity_log, public.user_preferences, "
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


def test_categories_and_sku_convention(app):
    alice, bob = signup(app, "alice"), signup(app, "bob")

    category = alice.post("/api/v1/categories", json={"name": "Tools"})
    assert category.status_code == 201
    assert alice.post("/api/v1/categories", json={"name": "Tools"}).status_code == 409
    assert bob.get("/api/v1/categories").json() == []

    saved = alice.put(
        "/api/v1/settings/sku", json={"prefix": "SF-", "padding": 4, "next_number": 7}
    )
    assert saved.status_code == 200 and saved.json()["example"] == "SF-0007"

    first = alice.post(
        "/api/v1/products", json={"name": "Hammer", "category_id": category.json()["id"]}
    )
    second = alice.post("/api/v1/products", json={"name": "Saw"})
    assert (first.json()["sku"], second.json()["sku"]) == ("SF-0007", "SF-0008")
    manual = alice.post("/api/v1/products", json={"sku": "MY-1", "name": "Custom"})
    assert manual.json()["sku"] == "MY-1"

    listing = alice.get("/api/v1/products", params={"category": category.json()["id"]}).json()
    assert [p["name"] for p in listing] == ["Hammer"] and listing[0]["category"] == "Tools"

    # Another workspace cannot use Alice's category.
    cross = bob.post(
        "/api/v1/products", json={"name": "Stolen", "category_id": category.json()["id"]}
    )
    assert cross.status_code == 404

    # Deleting a category keeps its products.
    assert alice.delete(f"/api/v1/categories/{category.json()['id']}").status_code == 204
    names = {p["name"]: p["category"] for p in alice.get("/api/v1/products").json()}
    assert names["Hammer"] is None


def test_plan_limits_and_the_plan_cannot_be_self_upgraded(app):
    alice = signup(app, "alice")
    settings = alice.get("/api/v1/settings").json()
    assert settings["plan"]["name"] == "free" and settings["plan"]["billing_connected"] is False

    # The runtime role may not touch the plan column at all.
    with app_conn() as conn:
        assert sqlstate_of(
            lambda: conn.execute("UPDATE public.workspace_settings SET plan = 'business'")
        ) == "42501"

    # Free plan allows 2 API keys; the third is refused with 402.
    for number in (1, 2):
        assert alice.post("/api/v1/api-keys", json={"name": f"key {number}"}).status_code == 201
    assert alice.post("/api/v1/api-keys", json={"name": "key 3"}).status_code == 402

    # An administrator upgrades the plan with the owner connection; the limit lifts.
    tenant_id = alice.get("/api/v1/session").json()["tenant_id"]
    with owner_conn() as conn:  # FORCE RLS applies to the owner too: select the workspace first
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        conn.execute("UPDATE public.workspace_settings SET plan = 'pro'")
    assert alice.post("/api/v1/api-keys", json={"name": "key 3"}).status_code == 201


def test_api_key_lifecycle_and_exports(app):
    alice = signup(app, "alice")
    alice.post("/api/v1/products", json={"sku": "=CMD", "name": "Formula"})

    created = alice.post("/api/v1/api-keys", json={"name": "sync", "scope": "readwrite"})
    assert created.status_code == 201
    key, key_id = created.json()["key"], created.json()["id"]
    assert key.startswith("sf_")
    listed = alice.get("/api/v1/api-keys").json()
    assert "key" not in listed[0] and listed[0]["prefix"] == key[:10]

    script = TestClient(app, raise_server_exceptions=False)  # no cookies: key only
    headers = {"authorization": f"Bearer {key}"}
    assert script.get("/api/v1/products", headers=headers).status_code == 200
    made = script.post("/api/v1/products", json={"name": "From API"}, headers=headers)
    assert made.status_code == 201
    assert script.get("/api/v1/members", headers=headers).status_code == 403
    assert script.get("/api/v1/workspaces", headers=headers).status_code == 401

    export = script.get("/api/v1/export/products.csv", headers=headers)
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    assert "'=CMD" in export.text  # formula injection neutralised

    assert alice.delete(f"/api/v1/api-keys/{key_id}").status_code == 204
    assert script.get("/api/v1/products", headers=headers).status_code == 401


def test_activity_log_preferences_notifications_and_insights(app):
    alice = signup(app, "alice")
    product = alice.post("/api/v1/products", json={"sku": "P-1", "name": "Widget"}).json()
    alice.post(
        "/api/v1/movements",
        json={"product_id": product["id"], "movement_type": "IN", "quantity": 50},
    )
    alice.post(
        "/api/v1/movements",
        json={"product_id": product["id"], "movement_type": "OUT", "quantity": 45},
    )

    feed = alice.get("/api/v1/activity").json()
    assert {"created", "stock_in", "stock_out"} <= {entry["action"] for entry in feed}
    assert all(entry["actor"] == "alice@example.com" for entry in feed)

    alice.patch(f"/api/v1/products/{product['id']}", json={"minimum_stock": 10})
    notes = alice.get("/api/v1/notifications").json()
    assert notes["count"] == 1 and notes["items"][0]["kind"] == "low_stock"
    saved = alice.put(
        "/api/v1/preferences", json={"notify_low_stock": False, "notify_order_events": True}
    )
    assert saved.status_code == 200
    assert alice.get("/api/v1/notifications").json()["count"] == 0

    report = alice.get("/api/v1/insights", params={"days": 30}).json()
    row = next(r for r in report["rows"] if r["sku"] == "P-1")
    assert row["avg_daily_demand"] == 1.5 and row["suggested_reorder"] > 0
    assert report["insights"] and report["suppliers"] == []
