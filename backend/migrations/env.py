"""Alembic environment. Runs with the OWNER connection (MIGRATION_DATABASE_URL), never the app's.
Migrations are plain SQL (no ORM models). The API never runs migrations at startup."""

from alembic import context
from sqlalchemy import create_engine

from app.config import MigrationSettings

url = MigrationSettings().migration_database_url.get_secret_value()  # type: ignore[call-arg]
for prefix in ("postgresql://", "postgres://"):
    if url.startswith(prefix):
        url = "postgresql+psycopg://" + url[len(prefix) :]

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
