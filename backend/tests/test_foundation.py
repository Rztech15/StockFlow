"""Config, redaction, permission catalogue, error model and the API (no database needed)."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import REDACTED, Settings, explain
from app.db import Database
from app.errors import PROBLEM_CONTENT_TYPE, AppError, conflict, not_found
from app.main import create_app
from app.permissions import PERMISSIONS, is_permission
from tests.helpers import BASE, make_settings

PROD = dict(
    app_env="production",
    app_base_url="https://app.example.com",
    allowed_origins="https://app.example.com",
)


def problems(**overrides) -> list[str]:
    try:
        make_settings(**overrides)
    except ValidationError as error:
        return explain(error)
    return []


# --- configuration -------------------------------------------------------------------------
def test_valid_config_and_defaults():
    s = make_settings()
    assert s.api_port == 8000
    assert s.origins == ["http://localhost:5173"]
    assert s.smtp_user is None and s.migration_database_url is None


def test_empty_optionals_are_unset():
    s = make_settings(smtp_user="", smtp_password="", migration_database_url="")
    assert s.smtp_user is None and s.migration_database_url is None


def test_missing_and_invalid_values_are_reported_without_echoing_secrets():
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, database_url="mysql://user:hunter2@host/db", password_pepper_v1="short")  # type: ignore[call-arg]
    text = " ".join(explain(info.value))
    for name in ("APP_BASE_URL", "ALLOWED_ORIGINS", "DATABASE_URL", "PASSWORD_PEPPER_V1", "SMTP_HOST"):
        assert name in text
    assert "hunter2" not in text and "short" not in text


def test_origins_must_be_bare():
    assert any("ALLOWED_ORIGINS" in p for p in problems(allowed_origins="*"))
    assert any("ALLOWED_ORIGINS" in p for p in problems(allowed_origins="http://localhost:5173/"))


def test_runtime_must_not_be_the_owner_role():
    owner = "postgresql://stockflow_owner:not-a-real-password@localhost:5432/stockflow"
    assert any("stockflow_app" in p for p in problems(database_url=owner))


def test_production_rejects_migration_credentials_and_plain_http():
    assert problems(**PROD) == []
    owner = "postgresql://stockflow_owner:not-a-real-password@localhost:5432/stockflow"
    assert any("MIGRATION_DATABASE_URL" in p for p in problems(**PROD, migration_database_url=owner))
    assert any("https" in p for p in problems(**{**PROD, "app_base_url": "http://app.example.com"}))


def test_secrets_never_appear_in_repr_str_or_redacted_view():
    s = make_settings(smtp_user="mailer", smtp_password="smtp-secret-value")
    shown = " ".join([repr(s), str(s), str(s.redacted())])
    for secret in ("not-a-real-password", "not-a-real-pepper", "smtp-secret-value", "mailer"):
        assert secret not in shown
    assert REDACTED in str(s.redacted())


# --- permissions ---------------------------------------------------------------------------
def test_permission_catalogue():
    assert len(set(PERMISSIONS)) == len(PERMISSIONS)
    assert is_permission("inventory.write") and not is_permission("inventory.destroy")
    assert not is_permission(42)


# --- API -----------------------------------------------------------------------------------
def make_client(**overrides) -> TestClient:
    s = make_settings(**overrides)
    # Database() connects lazily, so these tests need no PostgreSQL.
    app = create_app(s, Database(s.database_url.get_secret_value(), pool_size=1))
    return TestClient(app, raise_server_exceptions=False)


def test_health_is_ok_and_exposes_nothing():
    with make_client() as client:
        res = client.get("/health")
    assert res.status_code == 200 and res.json() == {"status": "ok"}
    assert "postgres" not in res.text
    assert res.headers["x-request-id"] and res.headers["x-content-type-options"] == "nosniff"


def test_unknown_route_is_an_rfc9457_problem_with_request_id():
    with make_client() as client:
        res = client.get("/api/v1/nope", headers={"x-request-id": "req-12345678"})
    body = res.json()
    assert res.status_code == 404
    assert res.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert body["status"] == 404 and body["title"] == "Not Found"
    assert body["requestId"] == "req-12345678"
    assert body["instance"] == "urn:stockflow:request:req-12345678"


def test_malformed_request_id_is_replaced():
    with make_client() as client:
        res = client.get("/health", headers={"x-request-id": "bad id with spaces!"})
    assert "bad" not in res.headers["x-request-id"]


def test_app_error_maps_to_its_status():
    client = make_client()
    app = client.app

    @app.get("/__conflict")
    def _c():
        raise conflict("Already exists.")

    @app.get("/__missing")
    def _m():
        raise not_found("No such thing.")

    @app.get("/__custom")
    def _x():
        raise AppError(418, "teapot", "I'm a teapot")

    with client:
        assert client.get("/__conflict").status_code == 409
        assert client.get("/__conflict").json()["detail"] == "Already exists."
        assert client.get("/__missing").status_code == 404
        assert client.get("/__custom").json()["type"] == "urn:stockflow:problem:teapot"


def test_production_hides_internal_errors():
    client = make_client(**PROD)
    app = client.app

    @app.get("/__boom")
    def _boom():
        raise RuntimeError("connection to postgresql://stockflow_app:hunter2@db failed")

    with client:
        res = client.get("/__boom")
    assert res.status_code == 500
    for leak in ("hunter2", "postgres", "Traceback", "RuntimeError", "File "):
        assert leak not in res.text
    assert res.json()["detail"] == "An unexpected error occurred."
    assert client.get("/docs").status_code == 404  # API docs are off in production


def test_development_shows_message_but_never_a_traceback():
    client = make_client()
    app = client.app

    @app.get("/__boom")
    def _boom():
        raise RuntimeError("visible in development")

    with client:
        res = client.get("/__boom")
    assert res.status_code == 500
    assert res.json()["detail"] == "visible in development"
    assert "Traceback" not in res.text


def test_validation_errors_do_not_echo_values():
    client = make_client()
    app = client.app

    @app.post("/__typed")
    def _typed(payload: dict[str, int]):
        return payload

    with client:
        res = client.post("/__typed", json={"a": "secret-text"})
    assert res.status_code == 422
    assert "secret-text" not in res.text


def test_base_settings_fixture_is_complete():
    assert set(BASE) >= {"database_url", "password_pepper_v1", "smtp_from"}
