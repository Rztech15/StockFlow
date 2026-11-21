"""Create or update the StockFlow database roles and schema permissions (idempotent).

    python -m scripts.bootstrap_db

On Neon the database must already exist (create it in the console first). On a local
PostgreSQL (localhost) it is created for you, owned by stockflow_owner.
  stockflow_owner  owns every object, used ONLY by migrations (MIGRATION_DATABASE_URL)
  stockflow_app    runtime role: owns nothing, no DDL. It only gets the privileges that
                   migrations explicitly GRANT.
Refuses to run unless BOOTSTRAP_DATABASE_URL points at TEST_DATABASE_HOST. No passwords are printed.
"""

import re
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from app.config import BootstrapSettings, host_of, require_development_database

OWNER = "stockflow_owner"
APP = "stockflow_app"
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def upsert_role(cur: psycopg.Cursor, role: str, password: str, inherit: str) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(
        sql.SQL("{} ROLE {} WITH LOGIN {} PASSWORD {}").format(
            sql.SQL(verb), sql.Identifier(role), sql.SQL(inherit), sql.Literal(password)
        )
    )


def main() -> None:
    require_development_database(bootstrap=True)
    settings = BootstrapSettings()  # type: ignore[call-arg]
    db_name = settings.stockflow_db_name
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", db_name):
        raise SystemExit("STOCKFLOW_DB_NAME must match [a-z][a-z0-9_]{0,62}")
    admin_url = settings.bootstrap_database_url.get_secret_value()

    with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        missing = cur.fetchone() is None
        if missing and host_of(admin_url) not in LOCAL_HOSTS:
            raise SystemExit(f"Database '{db_name}' does not exist. Create it in Neon first.")
        upsert_role(cur, OWNER, settings.stockflow_owner_password.get_secret_value(), "INHERIT")
        upsert_role(cur, APP, settings.stockflow_app_password.get_secret_value(), "NOINHERIT")
        if missing:  # local PostgreSQL only: the owner must own it so migrations can create schemas
            cur.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db_name), sql.Identifier(OWNER)
                )
            )
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(db_name), sql.Identifier(APP)
            )
        )
        cur.execute(  # migrations create the "app" schema, which needs CREATE on the database
            sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                sql.Identifier(db_name), sql.Identifier(OWNER)
            )
        )

    parts = urlsplit(admin_url)
    target_url = urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, ""))
    with psycopg.connect(target_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(APP)))
        cur.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(OWNER))
        )

    print(f"Bootstrap complete for existing database '{db_name}'.")


if __name__ == "__main__":
    main()
