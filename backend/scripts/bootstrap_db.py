"""Configure StockFlow roles and schema permissions for Neon."""

import re
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from app.config import BootstrapSettings

OWNER = "stockflow_owner"
APP = "stockflow_app"


def upsert_role(cur, role, password, inherit):
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    exists = cur.fetchone() is not None

    statement = (
        sql.SQL("ALTER ROLE {} WITH LOGIN {} PASSWORD {}")
        if exists
        else sql.SQL("CREATE ROLE {} WITH LOGIN {} PASSWORD {}")
    )
    cur.execute(
        statement.format(
            sql.Identifier(role),
            sql.SQL(inherit),
            sql.Literal(password),
        )
    )


def main():
    settings = BootstrapSettings()
    db_name = settings.stockflow_db_name

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", db_name):
        raise SystemExit("Invalid STOCKFLOW_DB_NAME.")

    admin_url = settings.bootstrap_database_url.get_secret_value()

    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (db_name,),
            )
            if cur.fetchone() is None:
                raise SystemExit(
                    f"Database '{db_name}' does not exist. "
                    "Create it through Neon first."
                )

            upsert_role(
                cur, OWNER,
                settings.stockflow_owner_password.get_secret_value(),
                "INHERIT",
            )
            upsert_role(
                cur, APP,
                settings.stockflow_app_password.get_secret_value(),
                "NOINHERIT",
            )

            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(db_name), sql.Identifier(APP)
                )
            )

    parts = urlsplit(admin_url)
    target_url = urlunsplit(
        (parts.scheme, parts.netloc, f"/{db_name}", parts.query, "")
    )

    with psycopg.connect(target_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(APP)
                )
            )
            cur.execute(
                sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                    sql.Identifier(OWNER)
                )
            )

    print(f"Bootstrap complete for existing database '{db_name}'.")


if __name__ == "__main__":
    main()
