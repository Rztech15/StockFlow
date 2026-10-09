"""Reusable PostgreSQL catalog guard (inspects pg_catalog only; no table names hard-coded).

  * every table with a tenant_id column must have RLS enabled, FORCE RLS and permissive
    policies that apply to the runtime role and cover every command it can use;
  * registered append-only tables must not grant UPDATE/DELETE/TRUNCATE to the runtime role.
Add append-only tables (e.g. 'public.inventory_ledger') to APPEND_ONLY_TABLES in later phases.
"""

import psycopg

APP_ROLE = "stockflow_app"
OWNER_ROLE = "stockflow_owner"
APPEND_ONLY_TABLES: tuple[str, ...] = ()

_NAMES = {"r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE"}

_TENANT_TABLES = """
SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p')
   AND n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND n.nspname NOT LIKE 'pg\\_toast%%'
   AND EXISTS (SELECT 1 FROM pg_attribute a
                WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                  AND a.attnum > 0 AND NOT a.attisdropped)
 ORDER BY 1, 2
"""

_POLICIES = """
SELECT n.nspname, c.relname, p.polcmd::text
  FROM pg_policy p
  JOIN pg_class c ON c.oid = p.polrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE p.polpermissive
   AND (p.polroles @> ARRAY[0::oid]
        OR EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname = %s AND r.oid = ANY (p.polroles)))
"""


def tenant_table_violations(
    conn: psycopg.Connection,
    app_role: str = APP_ROLE,
    append_only: tuple[str, ...] = APPEND_ONLY_TABLES,
) -> dict[str, list[str]]:
    tables = conn.execute(_TENANT_TABLES).fetchall()
    policies = conn.execute(_POLICIES, (app_role,)).fetchall()
    result: dict[str, list[str]] = {}
    for schema, table, rls, forced in tables:
        name = f"{schema}.{table}"
        problems: list[str] = []
        if not rls:
            problems.append("ROW LEVEL SECURITY is not enabled")
        if not forced:
            problems.append("FORCE ROW LEVEL SECURITY is not enabled")
        covered: set[str] = set()
        for p_schema, p_table, cmd in policies:
            if (p_schema, p_table) == (schema, table):
                covered.update("rawd" if cmd == "*" else cmd)
        if not covered:
            problems.append(f"no permissive RLS policy applies to role {app_role}")
        else:
            required = "ra" if name in append_only else "rawd"
            missing = [_NAMES[c] for c in required if c not in covered]
            if missing:
                problems.append(f"no policy covers: {', '.join(missing)}")
        if problems:
            result[name] = problems
    return result


def append_only_violations(
    conn: psycopg.Connection,
    tables: tuple[str, ...] = APPEND_ONLY_TABLES,
    app_role: str = APP_ROLE,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for table in tables:
        row = conn.execute(
            """SELECT to_regclass(%(t)s::text) IS NOT NULL,
                      COALESCE(has_table_privilege(%(r)s::text, to_regclass(%(t)s::text), 'UPDATE'), false),
                      COALESCE(has_table_privilege(%(r)s::text, to_regclass(%(t)s::text), 'DELETE'), false),
                      COALESCE(has_table_privilege(%(r)s::text, to_regclass(%(t)s::text), 'TRUNCATE'), false),
                      COALESCE(has_any_column_privilege(%(r)s::text, to_regclass(%(t)s::text), 'UPDATE'), false)""",
            {"t": table, "r": app_role},
        ).fetchone()
        assert row is not None
        present, update, delete, truncate, col_update = row
        problems: list[str] = []
        if not present:
            problems.append("registered append-only table does not exist")
        else:
            if update or col_update:
                problems.append(f"{app_role} holds UPDATE")
            if delete:
                problems.append(f"{app_role} holds DELETE")
            if truncate:
                problems.append(f"{app_role} holds TRUNCATE")
        if problems:
            result[table] = problems
    return result
