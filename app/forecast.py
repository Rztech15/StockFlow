"""Demand forecasting, reorder suggestions and rule-based insights.

Plain statistics, NOT machine learning: a moving average of units issued (OUT) over a window,
compared between the older and newer half of the window to detect a trend.
Pure functions, so they are unit-tested without a database.
"""

import math

DEFAULT_LEAD_DAYS = 7.0
REVIEW_DAYS = 7  # how often stock is reviewed; added to the lead time as cover


def _ceil(value: float) -> int:
    return int(math.ceil(round(value, 6)))


def forecast_row(
    row: dict, days: int, horizon: int, lead_days: float, review_days: int = REVIEW_DAYS
) -> dict:
    quantity = int(row["quantity"])
    minimum = int(row["minimum_stock"])
    out_total = int(row["out_total"])
    out_recent = int(row["out_recent"])
    on_order = int(row["on_order"])

    avg_daily = out_total / days
    half = days / 2
    recent_rate = out_recent / half
    older_rate = (out_total - out_recent) / half
    if recent_rate == 0 and older_rate == 0:
        trend = "none"
    elif older_rate == 0:
        trend = "rising"
    else:
        change = (recent_rate - older_rate) / older_rate
        trend = "rising" if change > 0.25 else "falling" if change < -0.25 else "steady"

    days_of_cover = round(quantity / avg_daily, 1) if avg_daily > 0 else None
    target = _ceil(avg_daily * (lead_days + review_days)) + minimum
    suggested = max(0, target - quantity - on_order)

    if suggested > 0 and days_of_cover is not None and days_of_cover <= lead_days:
        status = "reorder-now"
    elif suggested > 0:
        status = "reorder-soon"
    elif avg_daily == 0 and quantity > 0:
        status = "idle"
    else:
        status = "ok"

    return {
        "product_id": str(row["id"]),
        "sku": row["sku"],
        "name": row["name"],
        "unit": row["unit"],
        "quantity": quantity,
        "minimum_stock": minimum,
        "on_order": on_order,
        "avg_daily_demand": round(avg_daily, 2),
        "forecast_units": _ceil(avg_daily * horizon),
        "days_of_cover": days_of_cover,
        "trend": trend,
        "suggested_reorder": suggested,
        "status": status,
    }


def build_insights(rows: list[dict], suppliers: list[dict], days: int) -> list[dict]:
    """Short, rule-based observations in plain language, most urgent first."""
    insights: list[dict] = []

    def names(items: list[dict], limit: int = 3) -> str:
        shown = ", ".join(item["name"] for item in items[:limit])
        return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")

    urgent = [r for r in rows if r["status"] == "reorder-now"]
    if urgent:
        insights.append({
            "severity": "high",
            "message": f"{len(urgent)} product(s) will run out before a new order can arrive: "
                       f"{names(urgent)}.",
        })
    soon = [r for r in rows if r["status"] == "reorder-soon"]
    if soon:
        insights.append({
            "severity": "medium",
            "message": f"{len(soon)} product(s) need a reorder soon: {names(soon)}.",
        })
    rising = [r for r in rows if r["trend"] == "rising" and r["avg_daily_demand"] > 0]
    if rising:
        insights.append({
            "severity": "info",
            "message": f"Demand is rising for {names(rising)}.",
        })
    idle = [r for r in rows if r["status"] == "idle"]
    if idle:
        insights.append({
            "severity": "info",
            "message": f"{len(idle)} product(s) had no sales in the last {days} days: "
                       f"{names(idle)}. Consider a promotion or stopping reorders.",
        })
    measured = [s for s in suppliers if s["avg_lead_days"] is not None]
    if len(measured) >= 2:
        slowest = max(measured, key=lambda s: s["avg_lead_days"])
        fastest = min(measured, key=lambda s: s["avg_lead_days"])
        if slowest["avg_lead_days"] > fastest["avg_lead_days"] * 1.5:
            insights.append({
                "severity": "info",
                "message": (
                    f"{slowest['name']} averages {slowest['avg_lead_days']} days to deliver, "
                    f"versus {fastest['avg_lead_days']} days for {fastest['name']}."
                ),
            })
    if not insights:
        insights.append({"severity": "info", "message": "No issues found. Stock looks healthy."})
    return insights


def average_lead_days(suppliers: list[dict]) -> float:
    """Weighted average over received orders; DEFAULT_LEAD_DAYS when there is no history."""
    total = sum((s["avg_lead_days"] or 0) * s["received"] for s in suppliers if s["avg_lead_days"])
    count = sum(s["received"] for s in suppliers if s["avg_lead_days"])
    return round(total / count, 1) if count else DEFAULT_LEAD_DAYS
