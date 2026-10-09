"""Create the database roles and the database (idempotent). Needs an ADMIN connection.

    python -m scripts.bootstrap_db

  stockflow_owner  owns every object, used ONLY by migrations (MIGRATION_DATABASE_URL)
  stockflow_app    runtime role: NOBYPASSRLS, owns nothing, no DDL. It only gets the
                   privileges that migrations explicitly GRANT.
No passwords are printed or committed.
"""

import re
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from app.config import BootstrapSettings

OWNER, APP = "stockflow_owner", "stockflow_app"
ATTRS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"


def upsert_role(cur: psycopg.Cursor, role: str, password: str, inherit: str) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(
        sql.SQL("{} ROLE {} WITH {} {} PASSWORD {}").format(
            sql.SQL(verb), sql.Identifier(role), sql.SQL(ATTRS), sql.SQL(inherit), sql.Literal(password)
        )
    )


def main() -> None:
    s = BootstrapSettings()  # type: ignore[call-arg]
    db_name = s.stockflow_db_name
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", db_name):
        raise SystemExit("STOCKFLOW_DB_NAME must match [a-z][a-z0-9_]{0,62}")
    admin_url = s.bootstrap_database_url.get_secret_value()

    with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
        upsert_role(cur, OWNER, s.stockflow_owner_password.get_secret_value(), "INHERIT")
        upsert_role(cur, APP, s.stockflow_app_password.get_secret_value(), "NOINHERIT")
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if not cur.fetchone():
            cur.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db_name), sql.Identifier(OWNER)
                )
            )
        cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name)))
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(db_name), sql.Identifier(APP)
            )
        )

    parts = urlsplit(admin_url)
    target_url = urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, ""))
    with psycopg.connect(target_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(OWNER)))
        cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(APP)))

    print(f"Bootstrap complete: roles {OWNER}, {APP} and database '{db_name}' are ready.")


if __name__ == "__main__":
    main()
