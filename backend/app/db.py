"""The ONLY module that talks to PostgreSQL (enforced by a ruff rule).

tenant_tx(ctx) is the single way application code gets a connection:
    BEGIN; set_config('app.tenant_id', ..., true); set_config('app.user_id', ..., true);
    ...your queries...; COMMIT   (ROLLBACK if an exception escapes)
`true` makes the settings transaction-local: PostgreSQL discards them at COMMIT/ROLLBACK, so a
pooled connection never carries tenant context into its next use. Row Level Security policies
then read the context through app.current_tenant_id().
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text

from app.context import TenantContext

__all__ = ["Connection", "Database", "InvalidTenantContextError", "text"]

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


class InvalidTenantContextError(ValueError):
    pass


def _uuid(name: str, value: object) -> str:
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        raise InvalidTenantContextError(f"{name} must be a UUID")
    return value.lower()


def _sqlalchemy_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class Database:
    def __init__(self, url: str, *, pool_size: int = 10) -> None:
        self._engine: Engine = create_engine(
            _sqlalchemy_url(url),
            pool_size=pool_size,
            max_overflow=0,
            pool_pre_ping=True,
            connect_args={
                "application_name": "stockflow",
                "options": "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000",
            },
        )

    @property
    def engine(self) -> Engine:
        """For tests only (e.g. to inspect the same pooled connections)."""
        return self._engine

    @contextmanager
    def tenant_tx(self, ctx: TenantContext) -> Iterator[Connection]:
        tenant_id = _uuid("tenant_id", ctx.tenant_id)
        user_id = _uuid("user_id", ctx.user_id)
        with self._engine.begin() as conn:  # commits on success, rolls back on exception
            conn.execute(
                text(
                    "SELECT set_config('app.tenant_id', :tenant, true), "
                    "set_config('app.user_id', :user, true)"
                ),
                {"tenant": tenant_id, "user": user_id},
            )
            yield conn

    def dispose(self) -> None:
        self._engine.dispose()
