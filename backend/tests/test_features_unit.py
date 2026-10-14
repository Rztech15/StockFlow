"""Forecasting, SKU formats, plans, CSV safety and API-key authentication. No database."""

from uuid import uuid4

from app.auth.repository import ResolvedSession
from app.auth.sessions import hash_token
from app.forecast import average_lead_days, build_insights, forecast_row
from app.permissions import permissions_for_scope
from app.plans import PLAN_LIMITS, RESOURCES, limit_for
from app.routes.integrations import csv_safe, to_csv
from app.workspace import format_sku
from tests.test_auth_unit import give_session, make_client

LEAD = 7.0


def product(**overrides):
    base = dict(id=uuid4(), sku="S-1", name="Widget", unit="piece", quantity=10,
                minimum_stock=5, out_total=60, out_recent=30, on_order=0)
    return {**base, **overrides}


def test_forecast_rate_cover_and_suggested_reorder():
    row = forecast_row(product(), days=60, horizon=30, lead_days=LEAD)
    assert row["avg_daily_demand"] == 1.0
    assert row["forecast_units"] == 30
    assert row["days_of_cover"] == 10.0
    assert row["suggested_reorder"] == 9  # 1/day * (7 + 7) + 5 minimum - 10 on hand
    assert row["status"] == "reorder-soon" and row["trend"] == "steady"


def test_running_out_before_delivery_is_urgent_unless_already_on_order():
    urgent = forecast_row(product(quantity=3), 60, 30, LEAD)
    assert urgent["status"] == "reorder-now" and urgent["suggested_reorder"] == 16
    covered = forecast_row(product(quantity=3, on_order=16), 60, 30, LEAD)
    assert covered["suggested_reorder"] == 0 and covered["status"] == "ok"


def test_idle_stock_and_trends():
    idle = forecast_row(product(out_total=0, out_recent=0), 60, 30, LEAD)
    assert idle["status"] == "idle" and idle["trend"] == "none" and idle["days_of_cover"] is None
    assert forecast_row(product(out_recent=50), 60, 30, LEAD)["trend"] == "rising"
    assert forecast_row(product(out_recent=10), 60, 30, LEAD)["trend"] == "falling"


def test_lead_time_average_and_default():
    suppliers = [
        {"avg_lead_days": 4.0, "received": 2},
        {"avg_lead_days": 10.0, "received": 1},
        {"avg_lead_days": None, "received": 0},
    ]
    assert average_lead_days(suppliers) == 6.0
    assert average_lead_days([]) == 7.0


def test_insights_are_plain_language_and_flag_urgent_items():
    rows = [forecast_row(product(quantity=3, name="Bolts"), 60, 30, LEAD)]
    messages = build_insights(rows, [], 60)
    assert messages[0]["severity"] == "high" and "Bolts" in messages[0]["message"]
    healthy = build_insights([forecast_row(product(quantity=500), 60, 30, LEAD)], [], 60)
    assert healthy[0]["message"].startswith("No issues")


def test_sku_convention_formatting():
    assert format_sku("SF-", 5, 7) == "SF-00007"
    assert format_sku("", 3, 123456) == "123456"


def test_plan_limits_fail_closed():
    assert limit_for("free", "products") == 100
    assert limit_for("business", "products") is None
    assert limit_for("unknown-plan", "api_keys") == PLAN_LIMITS["free"]["api_keys"]
    assert all(resource in PLAN_LIMITS["pro"] for resource in RESOURCES)


def test_csv_neutralises_spreadsheet_formulas():
    assert csv_safe("=1+1") == "'=1+1" and csv_safe("@cmd") == "'@cmd"
    assert csv_safe("-5") == "'-5" and csv_safe("normal") == "normal" and csv_safe(5) == 5
    body = to_csv(["name"], [["=HYPERLINK(\"http://x\")"], ["ok"]])
    assert "'=HYPERLINK" in body and body.startswith("name")


def test_api_key_scopes_never_include_members_or_settings():
    read, readwrite = permissions_for_scope("read"), permissions_for_scope("readwrite")
    assert "catalog.write" not in read and "catalog.read" in read
    assert {"catalog.write", "inventory.write", "purchasing.write"} <= readwrite
    for scope in (read, readwrite):
        assert not any(p.startswith(("members.", "settings.")) for p in scope)
    assert permissions_for_scope("admin") == frozenset()
    assert permissions_for_scope(None) == frozenset()


def test_api_keys_authenticate_with_limited_permissions():
    client, repo = make_client()
    workspace = str(uuid4())
    repo.api_keys = {
        hash_token("sf_read-key"): ResolvedSession(str(uuid4()), workspace, "read"),
        hash_token("sf_write-key"): ResolvedSession(str(uuid4()), workspace, "readwrite"),
    }
    repo.resolve_api_key = lambda key_hash: repo.api_keys.get(key_hash)
    bearer = lambda key: {"authorization": f"Bearer {key}"}  # noqa: E731
    with client:
        ok = client.get("/api/v1/session", headers=bearer("sf_read-key"))
        read_cannot_write = client.post(
            "/api/v1/products", json={"name": "x"}, headers=bearer("sf_read-key")
        )
        write_cannot_manage = client.post(
            "/api/v1/members",
            json={"email": "a@b.co", "role": "viewer"},
            headers=bearer("sf_write-key"),
        )
        write_cannot_configure = client.put(
            "/api/v1/settings/sku", json={"prefix": "", "padding": 5, "next_number": 1},
            headers=bearer("sf_write-key"),
        )
        unknown = client.get("/api/v1/session", headers=bearer("sf_not-a-key"))
        no_prefix = client.get("/api/v1/session", headers=bearer("plain-token"))
        no_cookie_routes = client.get("/api/v1/workspaces", headers=bearer("sf_read-key"))
    assert ok.status_code == 200 and ok.json()["tenant_id"] == workspace
    assert read_cannot_write.status_code == write_cannot_manage.status_code == 403
    assert write_cannot_configure.status_code == 403
    assert unknown.status_code == no_prefix.status_code == no_cookie_routes.status_code == 401


def test_new_routes_require_the_right_role():
    client, repo = make_client()
    give_session(repo, "tok-staff", "staff")
    give_session(repo, "tok-viewer", "viewer")
    headers = lambda t: {"cookie": f"stockflow_session={t}"}  # noqa: E731
    with client:
        staff_keys = client.get("/api/v1/api-keys", headers=headers("tok-staff"))
        staff_sku = client.put(
            "/api/v1/settings/sku",
            json={"prefix": "", "padding": 5, "next_number": 1},
            headers=headers("tok-staff"),
        )
        viewer_category = client.post(
            "/api/v1/categories", json={"name": "Tools"}, headers=headers("tok-viewer")
        )
        staff_insights = client.get("/api/v1/insights", headers=headers("tok-staff"))
        anonymous = client.get("/api/v1/export/products.csv")
    for res in (staff_keys, staff_sku, viewer_category, staff_insights):
        assert res.status_code == 403
    assert anonymous.status_code == 401
