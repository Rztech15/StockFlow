
"""The ONLY module that talks to PostgreSQL (enforced by a ruff rule).

tenant_tx(ctx) is the single way application code gets a connection:
    BEGIN; set_config('app.tenant_id', ..., true);
    set_config('app.user_id', ..., true);
    set_config('row_security', 'on', true);
    ...your queries...; COMMIT
    (ROLLBACK if an exception escapes)

The true argument makes settings transaction-local, so PostgreSQL resets
them at COMMIT/ROLLBACK and pooled connections do not retain tenant context.
Row Level Security policies read the tenant through app.current_tenant_id().
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text

from app.context import TenantContext

__all__ = ["Connection", "Database", "InvalidTenantContextError", "text"]

_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class InvalidTenantContextError(ValueError):
    pass


def _uuid(name: str, value: object) -> str:
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        raise InvalidTenantContextError(f"{name} must be a UUID")
    return value.lower()


def _sqlalchemy_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
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

        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "SELECT set_config('app.tenant_id', :tenant, true), "
                    "set_config('app.user_id', :user, true), "
                    "set_config('row_security', 'on', true)"
                ),
                {"tenant": tenant_id, "user": user_id},
            )
            yield conn

    def dispose(self) -> None:
        self._engine.dispose()