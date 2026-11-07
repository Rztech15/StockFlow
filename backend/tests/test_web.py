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


def test_import_dialog_and_3d_stage_are_in_the_page_without_inline_or_remote_code():
    with make_client() as client:
        page = client.get("/")
        script = client.get("/app.js")
    for needle in ('id="import-dialog"', 'id="import-file"', 'id="stage"', 'id="import-open"'):
        assert needle in page.text
    assert "/api/v1/products/import" in script.text
    assert "eval(" not in script.text
    # The strict CSP allows no remote code, so the script must not reference any.
    assert "http://" not in script.text and "https://" not in script.text


def test_assistant_and_calculator_tabs_load_their_script_from_our_own_origin():
    with make_client() as client:
        page = client.get("/")
        tools = client.get("/tools.js")
    for needle in ('data-view="assistant"', 'data-view="calculator"', 'id="chat-form"',
                   'id="calc-grid"', '<script src="/tools.js"></script>'):
        assert needle in page.text
    assert tools.status_code == 200 and "javascript" in tools.headers["content-type"]
    assert "StockFlowTools" in tools.text and "innerHTML" not in tools.text
