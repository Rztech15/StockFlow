"""Shared test helpers. Connection details come from app.config (the .env file), never os.environ."""

from collections.abc import Callable

import psycopg
from sqlalchemy.exc import DBAPIError

from app.config import MigrationSettings, Settings

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
USER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

BASE = dict(
    app_env="test",
    app_base_url="http://localhost:5173",
    allowed_origins="http://localhost:5173",
    database_url="postgresql://stockflow_app:not-a-real-password@localhost:5432/stockflow",
    password_pepper_v1="not-a-real-pepper-0123456789abcdef0123456789",
    smtp_host="localhost",
    smtp_port=1025,
    smtp_from="StockFlow <no-reply@localhost>",
)


def make_settings(**overrides: object) -> Settings:
    """Fully explicit fake settings; never reads .env or the environment (_env_file=None)."""
    return Settings(_env_file=None, **{**BASE, **overrides})  # type: ignore[arg-type]


def app_url() -> str:
    return Settings().database_url.get_secret_value()  # type: ignore[call-arg]


def owner_url() -> str:
    return MigrationSettings().migration_database_url.get_secret_value()  # type: ignore[call-arg]


def owner_conn() -> psycopg.Connection:
    return psycopg.connect(owner_url(), autocommit=True)


def app_conn() -> psycopg.Connection:
    return psycopg.connect(app_url(), autocommit=True)


def reset_probe() -> None:
    with owner_conn() as conn:  # TRUNCATE is not subject to RLS; the owner may run it
        conn.execute("TRUNCATE TABLE public.rls_probe")


def sqlstate_of(fn: Callable[[], object]) -> str | None:
    """Run fn; return the PostgreSQL SQLSTATE if it fails, else None."""
    try:
        fn()
    except DBAPIError as error:
        return getattr(error.orig, "sqlstate", None) or "no-sqlstate"
    except psycopg.Error as error:
        return error.sqlstate or "no-sqlstate"
    return None
