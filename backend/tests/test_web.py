"""The dashboard is served with a strict CSP and without inline scripts. No database needed."""

from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from tests.helpers import make_settings


def make_client() -> TestClient:
    settings = make_settings()
    app = create_app(settings, Database(settings.database_url.get_secret_value(), pool_size=1))
    return TestClient(app, raise_server_exceptions=False)


def test_dashboard_page_has_a_strict_csp_and_no_inline_script():
    with make_client() as client:
        page = client.get("/")
        script = client.get("/app.js")
        styles = client.get("/styles.css")
    assert page.status_code == script.status_code == styles.status_code == 200
    assert "StockFlow" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "<script>" not in page.text and "onclick=" not in page.text
    assert "innerHTML" not in script.text  # user data is only ever inserted as text


def test_brand_assets_are_served_only_from_the_allow_list():
    with make_client() as client:
        logo = client.get("/brand/logo-horizontal.svg")
        icon = client.get("/brand/favicon.ico")
        secret = client.get("/brand/..%2f..%2fapp%2fconfig.py")
        other = client.get("/brand/not-listed.svg")
    assert logo.status_code == icon.status_code == 200
    assert logo.headers["content-type"].startswith("image/svg+xml")
    assert secret.status_code == other.status_code == 404
