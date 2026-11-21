"""Demand forecast, reorder suggestions, supplier lead times and rule-based insights."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.deps import DB, require_trusted_origin, tenant_with
from app.context import TenantContext
from app.db import text
from app.forecast import average_lead_days, build_insights, forecast_row

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])

ReportsRead = Annotated[TenantContext, Depends(tenant_with("reports.read"))]

DEMAND_SQL = """
    SELECT p.id, p.sku, p.name, p.unit, p.quantity, p.minimum_stock,
           coalesce(sum(m.quantity) FILTER (
               WHERE m.movement_type = 'OUT'
                 AND m.created_at >= now() - make_interval(days => CAST(:days AS integer))
           ), 0) AS out_total,
           coalesce(sum(m.quantity) FILTER (
               WHERE m.movement_type = 'OUT'
                 AND m.created_at >= now() - make_interval(days => CAST(:half AS integer))
           ), 0) AS out_recent,
           coalesce((
               SELECT sum(i.quantity)
               FROM public.purchase_order_items i
               JOIN public.purchase_orders o
                 ON o.id = i.order_id AND o.tenant_id = i.tenant_id
               WHERE i.tenant_id = p.tenant_id AND i.product_id = p.id AND o.status = 'ordered'
           ), 0) AS on_order
    FROM public.products p
    LEFT JOIN public.stock_movements m
      ON m.product_id = p.id AND m.tenant_id = p.tenant_id
     AND m.created_at >= now() - make_interval(days => CAST(:days AS integer))
    WHERE p.tenant_id = :tenant_id
    GROUP BY p.id
    ORDER BY p.name
"""

SUPPLIER_SQL = """
    SELECT s.id, s.name,
           count(o.id) AS orders,
           count(o.id) FILTER (WHERE o.status = 'received') AS received,
           avg(extract(epoch FROM (o.received_at - o.created_at)) / 86400.0)
               FILTER (WHERE o.status = 'received') AS avg_lead_days,
           min(extract(epoch FROM (o.received_at - o.created_at)) / 86400.0)
               FILTER (WHERE o.status = 'received') AS min_lead_days,
           max(extract(epoch FROM (o.received_at - o.created_at)) / 86400.0)
               FILTER (WHERE o.status = 'received') AS max_lead_days
    FROM public.suppliers s
    LEFT JOIN public.purchase_orders o
      ON o.supplier_id = s.id AND o.tenant_id = s.tenant_id
    WHERE s.tenant_id = :tenant_id
    GROUP BY s.id, s.name
    ORDER BY s.name
"""


def _round(value: object) -> float | None:
    return None if value is None else round(float(value), 1)


@router.get("/insights")
def insights(
    tenant: ReportsRead,
    db: DB,
    days: int = Query(default=60, ge=14, le=180),
    horizon: int = Query(default=30, ge=7, le=90),
) -> dict[str, object]:
    params = {"tenant_id": tenant.tenant_id, "days": days, "half": days // 2}
    with db.tenant_tx(tenant) as conn:
        products = conn.execute(text(DEMAND_SQL), params).mappings().all()
        supplier_rows = conn.execute(
            text(SUPPLIER_SQL), {"tenant_id": tenant.tenant_id}
        ).mappings().all()

    suppliers = [
        {
            "supplier_id": str(r["id"]),
            "name": r["name"],
            "orders": int(r["orders"]),
            "received": int(r["received"]),
            "avg_lead_days": _round(r["avg_lead_days"]),
            "min_lead_days": _round(r["min_lead_days"]),
            "max_lead_days": _round(r["max_lead_days"]),
        }
        for r in supplier_rows
    ]
    lead_days = average_lead_days(suppliers)
    rows = [forecast_row(dict(p), days, horizon, lead_days) for p in products]
    order = {"reorder-now": 0, "reorder-soon": 1, "idle": 2, "ok": 3}
    rows.sort(key=lambda r: (order[r["status"]], r["name"]))
    return {
        "days": days,
        "horizon": horizon,
        "lead_days": lead_days,
        "method": "moving average of units issued; not machine learning",
        "rows": rows,
        "suppliers": suppliers,
        "insights": build_insights(rows, suppliers, days),
    }
