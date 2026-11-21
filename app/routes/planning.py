"""Inventory planning: ABC and XYZ classes, safety stock, reorder points (see app/planning.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.deps import DB, require_trusted_origin, tenant_with
from app.context import TenantContext
from app.db import text
from app.planning import build_plan

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])

ReportsRead = Annotated[TenantContext, Depends(tenant_with("reports.read"))]

# Units issued per product and day are aggregated in the database, so only two numbers per product
# (the total and the sum of squares) travel to the application, however long the history is.
DEMAND_SQL = """
    WITH daily AS (
        SELECT product_id, date_trunc('day', created_at) AS day, sum(quantity) AS q
        FROM public.stock_movements
        WHERE tenant_id = :tenant_id AND movement_type = 'OUT'
          AND created_at >= now() - make_interval(days => CAST(:days AS integer))
        GROUP BY product_id, date_trunc('day', created_at)
    ), demand AS (
        SELECT product_id, sum(q) AS out_total, sum(q * q) AS out_sq
        FROM daily GROUP BY product_id
    )
    SELECT p.id, p.sku, p.name, p.unit, p.quantity,
           coalesce(d.out_total, 0) AS out_total, coalesce(d.out_sq, 0) AS out_sq,
           coalesce((
               SELECT sum(i.quantity)
               FROM public.purchase_order_items i
               JOIN public.purchase_orders o ON o.id = i.order_id AND o.tenant_id = i.tenant_id
               WHERE i.tenant_id = p.tenant_id AND i.product_id = p.id AND o.status = 'ordered'
           ), 0) AS on_order
    FROM public.products p
    LEFT JOIN demand d ON d.product_id = p.id
    WHERE p.tenant_id = :tenant_id
    ORDER BY p.name
"""

LEAD_SQL = """
    SELECT count(*) AS n, avg(x) AS mean, stddev_samp(x) AS std
    FROM (
        SELECT extract(epoch FROM (o.received_at - o.created_at)) / 86400.0 AS x
        FROM public.purchase_orders o
        WHERE o.tenant_id = :tenant_id AND o.status = 'received' AND o.received_at IS NOT NULL
    ) t
"""


@router.get("/planning")
def planning(
    tenant: ReportsRead,
    db: DB,
    days: int = Query(default=90, ge=14, le=365),
    review_days: int = Query(default=7, ge=1, le=60),
    service_level: float | None = Query(default=None, ge=0.5, le=0.999),
    status: str | None = Query(
        default=None, pattern="^(reorder-now|reorder-soon|overstock|ok|idle|no-demand)$"
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    with db.tenant_tx(tenant) as conn:
        products = [dict(r) for r in conn.execute(
            text(DEMAND_SQL), {"tenant_id": tenant.tenant_id, "days": days}
        ).mappings().all()]
        lead = conn.execute(text(LEAD_SQL), {"tenant_id": tenant.tenant_id}).mappings().one()
    plan = build_plan(
        products, days, int(lead["n"]), lead["mean"], lead["std"], review_days, service_level,
    )
    rows = [r for r in plan["rows"] if status is None or r["status"] == status]
    return {
        "method": "ABC and XYZ classes, safety stock and reorder points; not machine learning",
        "parameters": plan["parameters"], "summary": plan["summary"],
        "total": len(rows), "limit": limit, "offset": offset,
        "rows": rows[offset:offset + limit],
    }
