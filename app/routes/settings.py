"""Workspace settings (SKU convention, plan and usage), categories, notification preferences,
in-app notifications and the activity feed."""

import re
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator

from app.activity import log_activity
from app.auth.deps import DB, get_request_context, require_trusted_origin, tenant_with
from app.context import RequestContext, TenantContext, to_tenant_context
from app.db import text
from app.errors import not_found
from app.plans import PLAN_LIMITS
from app.routes.dberrors import raise_for_db_error
from app.workspace import format_sku, get_settings, usage

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])

AnyMember = Annotated[TenantContext, Depends(tenant_with(None))]
SettingsManage = Annotated[TenantContext, Depends(tenant_with("settings.manage"))]
CatalogRead = Annotated[TenantContext, Depends(tenant_with("catalog.read"))]
CatalogWrite = Annotated[TenantContext, Depends(tenant_with("catalog.write"))]
ReportsRead = Annotated[TenantContext, Depends(tenant_with("reports.read"))]
Signed = Annotated[RequestContext, Depends(get_request_context)]

_PREFIX = re.compile(r"[A-Za-z0-9_-]{0,12}")


class SkuSettings(BaseModel):
    prefix: str = Field(default="", max_length=12)
    padding: int = Field(ge=1, le=12)
    next_number: int = Field(ge=1, le=999_999_999)

    @field_validator("prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        if _PREFIX.fullmatch(value) is None:
            raise ValueError("may contain only letters, digits, - and _")
        return value


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class Preferences(BaseModel):
    notify_low_stock: bool
    notify_order_events: bool


@router.get("/settings")
def get_workspace_settings(tenant: AnyMember, db: DB) -> dict[str, object]:
    with db.tenant_tx(tenant) as conn:
        settings = get_settings(conn, tenant)
        used = usage(conn, tenant)
    plan = str(settings["plan"])
    return {
        "sku": {
            "prefix": settings["sku_prefix"],
            "padding": settings["sku_padding"],
            "next_number": settings["sku_next"],
            "example": format_sku(
                str(settings["sku_prefix"]), int(settings["sku_padding"]), int(settings["sku_next"])
            ),
        },
        "plan": {
            "name": plan,
            "limits": PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]),
            "usage": used,
            "billing_connected": False,
        },
    }


@router.put("/settings/sku")
def update_sku_settings(payload: SkuSettings, tenant: SettingsManage, db: DB) -> dict[str, str]:
    with db.tenant_tx(tenant) as conn:
        get_settings(conn, tenant)  # make sure the row exists
        conn.execute(
            text("""
                UPDATE public.workspace_settings
                SET sku_prefix = :prefix, sku_padding = :padding, sku_next = :next_number
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant.tenant_id, **payload.model_dump()},
        )
        log_activity(conn, tenant, "updated", "sku_convention", None, payload.prefix)
    return {"example": format_sku(payload.prefix, payload.padding, payload.next_number)}


@router.get("/categories")
def list_categories(tenant: CatalogRead, db: DB) -> list[dict[str, object]]:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT c.id, c.name,
                       (SELECT count(*) FROM public.products p
                        WHERE p.tenant_id = c.tenant_id AND p.category_id = c.id) AS products
                FROM public.categories c
                WHERE c.tenant_id = :tenant_id
                ORDER BY c.name
            """),
            {"tenant_id": tenant.tenant_id},
        ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/categories", status_code=201)
def create_category(payload: CategoryCreate, tenant: CatalogWrite, db: DB) -> dict[str, object]:
    category_id = str(uuid4())
    try:
        with db.tenant_tx(tenant) as conn:
            conn.execute(
                text(
                    "INSERT INTO public.categories (id, tenant_id, name) "
                    "VALUES (:id, :tenant_id, :name)"
                ),
                {"id": category_id, "tenant_id": tenant.tenant_id, "name": payload.name},
            )
            log_activity(conn, tenant, "created", "category", category_id, payload.name)
    except Exception as exc:
        raise_for_db_error(exc, duplicate="A category with this name already exists.")
    return {"id": category_id, "name": payload.name, "products": 0}


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: UUID, tenant: CatalogWrite, db: DB) -> Response:
    with db.tenant_tx(tenant) as conn:
        result = conn.execute(
            text("DELETE FROM public.categories WHERE tenant_id = :tenant_id AND id = :id"),
            {"tenant_id": tenant.tenant_id, "id": str(category_id)},
        )
        if result.rowcount == 0:
            raise not_found("Category not found.")
        log_activity(conn, tenant, "deleted", "category", str(category_id))
    return Response(status_code=204)


def _read_preferences(conn, tenant: TenantContext) -> dict[str, bool]:
    row = conn.execute(
        text("""
            SELECT notify_low_stock, notify_order_events FROM public.user_preferences
            WHERE tenant_id = :tenant_id AND user_id = CAST(:user_id AS uuid)
        """),
        {"tenant_id": tenant.tenant_id, "user_id": tenant.user_id},
    ).first()
    if row is None:
        return {"notify_low_stock": True, "notify_order_events": True}
    return {"notify_low_stock": bool(row[0]), "notify_order_events": bool(row[1])}


@router.get("/preferences")
def get_preferences(tenant: AnyMember, db: DB) -> dict[str, bool]:
    with db.tenant_tx(tenant) as conn:
        return _read_preferences(conn, tenant)


@router.put("/preferences")
def put_preferences(payload: Preferences, tenant: AnyMember, db: DB) -> dict[str, bool]:
    with db.tenant_tx(tenant) as conn:
        conn.execute(
            text("""
                INSERT INTO public.user_preferences
                    (tenant_id, user_id, notify_low_stock, notify_order_events)
                VALUES (:tenant_id, CAST(:user_id AS uuid), :low, :orders)
                ON CONFLICT (tenant_id, user_id) DO UPDATE
                SET notify_low_stock = EXCLUDED.notify_low_stock,
                    notify_order_events = EXCLUDED.notify_order_events
            """),
            {
                "tenant_id": tenant.tenant_id,
                "user_id": tenant.user_id,
                "low": payload.notify_low_stock,
                "orders": payload.notify_order_events,
            },
        )
    return payload.model_dump()


@router.get("/notifications")
def notifications(ctx: Signed, db: DB) -> dict[str, object]:
    """In-app notifications (no email is sent). Respects the member's preferences and role."""
    tenant = to_tenant_context(ctx)
    items: list[dict[str, object]] = []
    with db.tenant_tx(tenant) as conn:
        prefs = _read_preferences(conn, tenant)
        if prefs["notify_low_stock"] and "catalog.read" in ctx.permissions:
            low = conn.execute(
                text(
                    "SELECT count(*) FROM public.products "
                    "WHERE tenant_id = :tenant_id AND quantity <= minimum_stock"
                ),
                {"tenant_id": tenant.tenant_id},
            ).scalar_one()
            if low:
                items.append({
                    "kind": "low_stock",
                    "count": int(low),
                    "message": f"{low} product(s) are at or below their minimum stock.",
                })
        if prefs["notify_order_events"] and "purchasing.read" in ctx.permissions:
            open_orders = conn.execute(
                text(
                    "SELECT count(*) FROM public.purchase_orders "
                    "WHERE tenant_id = :tenant_id AND status = 'ordered'"
                ),
                {"tenant_id": tenant.tenant_id},
            ).scalar_one()
            if open_orders:
                items.append({
                    "kind": "open_orders",
                    "count": int(open_orders),
                    "message": f"{open_orders} purchase order(s) are waiting to be received.",
                })
    return {"count": len(items), "items": items}


@router.get("/activity")
def activity_feed(
    tenant: ReportsRead, db: DB, limit: int = Query(default=50, ge=1, le=200)
) -> list[dict[str, object]]:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("""
                SELECT a.id, a.action, a.entity, a.detail, a.created_at,
                       coalesce(m.o_email, 'former member') AS actor
                FROM public.activity_log a
                LEFT JOIN app.members_list() m ON m.o_user_id = a.user_id
                WHERE a.tenant_id = :tenant_id
                ORDER BY a.created_at DESC
                LIMIT :limit
            """),
            {"tenant_id": tenant.tenant_id, "limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]
