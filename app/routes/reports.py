"""Read-only reports over the stock ledger (tenant-isolated by RLS and by explicit filters)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.deps import DB, require_trusted_origin, tenant_with
from app.context import TenantContext
from app.db import text

router = APIRouter(prefix="/api/v1/reports", dependencies=[Depends(require_trusted_origin)])

ReportsRead = Annotated[TenantContext, Depends(tenant_with("reports.read"))]


@router.get("/movements")
def movement_report(
    tenant: ReportsRead, db: DB, days: int = Query(default=30, ge=1, le=365)
) -> dict[str, object]:
    params = {"tenant_id": tenant.tenant_id, "days": days}
    with db.tenant_tx(tenant) as conn:
        daily = conn.execute(
            text("""
                SELECT CAST(date_trunc('day', created_at) AS date) AS day,
                       coalesce(sum(quantity) FILTER (WHERE movement_type = 'IN'), 0)
                           AS units_in,
                       coalesce(sum(quantity) FILTER (WHERE movement_type = 'OUT'), 0)
                           AS units_out
                FROM public.stock_movements
                WHERE tenant_id = :tenant_id
                  AND created_at >= now() - make_interval(days => CAST(:days AS integer))
                GROUP BY 1
                ORDER BY 1
            """),
            params,
        ).mappings().all()
        top = conn.execute(
            text("""
                SELECT p.sku, p.name,
                       coalesce(sum(m.quantity) FILTER (WHERE m.movement_type = 'IN'), 0)
                           AS units_in,
                       coalesce(sum(m.quantity) FILTER (WHERE m.movement_type = 'OUT'), 0)
                           AS units_out,
                       count(*) AS movements
                FROM public.stock_movements m
                JOIN public.products p ON p.id = m.product_id AND p.tenant_id = m.tenant_id
                WHERE m.tenant_id = :tenant_id
                  AND m.created_at >= now() - make_interval(days => CAST(:days AS integer))
                GROUP BY p.id, p.sku, p.name
                ORDER BY sum(m.quantity) DESC, p.name
                LIMIT 10
            """),
            params,
        ).mappings().all()
    return {"days": days, "daily": [dict(r) for r in daily], "top_products": [dict(r) for r in top]}
