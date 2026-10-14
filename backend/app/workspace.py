"""Per-workspace settings: SKU convention, plan and usage limits."""

from fastapi import status

from app.context import TenantContext
from app.db import Connection, text
from app.errors import AppError
from app.plans import RESOURCES, limit_for

_USAGE_SQL = {
    "products": "SELECT count(*) FROM public.products WHERE tenant_id = :tenant_id",
    "suppliers": "SELECT count(*) FROM public.suppliers WHERE tenant_id = :tenant_id",
    "members": "SELECT count(*) FROM app.members_list()",
    "api_keys": "SELECT count(*) FROM app.apikey_list() WHERE NOT o_revoked",
}


def format_sku(prefix: str, padding: int, number: int) -> str:
    return f"{prefix}{number:0{padding}d}"


def ensure_settings(conn: Connection, tenant: TenantContext) -> None:
    conn.execute(
        text(
            "INSERT INTO public.workspace_settings (tenant_id) VALUES (:tenant_id) "
            "ON CONFLICT (tenant_id) DO NOTHING"
        ),
        {"tenant_id": tenant.tenant_id},
    )


def get_settings(conn: Connection, tenant: TenantContext) -> dict[str, object]:
    ensure_settings(conn, tenant)
    row = conn.execute(
        text(
            "SELECT sku_prefix, sku_padding, sku_next, plan "
            "FROM public.workspace_settings WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": tenant.tenant_id},
    ).mappings().one()
    return dict(row)


def next_sku(conn: Connection, tenant: TenantContext) -> str:
    """Atomically take the next number of the workspace's SKU convention."""
    ensure_settings(conn, tenant)
    row = conn.execute(
        text(
            "UPDATE public.workspace_settings SET sku_next = sku_next + 1 "
            "WHERE tenant_id = :tenant_id RETURNING sku_prefix, sku_padding, sku_next"
        ),
        {"tenant_id": tenant.tenant_id},
    ).one()
    return format_sku(row[0], row[1], row[2] - 1)


def usage(conn: Connection, tenant: TenantContext) -> dict[str, int]:
    params = {"tenant_id": tenant.tenant_id}
    return {
        name: int(conn.execute(text(_USAGE_SQL[name]), params).scalar_one())
        for name in RESOURCES
    }


def check_limit(conn: Connection, tenant: TenantContext, resource: str) -> None:
    plan = str(get_settings(conn, tenant)["plan"])
    limit = limit_for(plan, resource)
    if limit is None:
        return
    params = {"tenant_id": tenant.tenant_id}
    current = int(conn.execute(text(_USAGE_SQL[resource]), params).scalar_one())
    if current >= limit:
        raise AppError(
            status.HTTP_402_PAYMENT_REQUIRED,
            "Plan Limit Reached",
            f"The {plan} plan allows up to {limit} {resource.replace('_', ' ')}. "
            "Ask the workspace administrator to upgrade the plan.",
            "plan-limit",
        )
