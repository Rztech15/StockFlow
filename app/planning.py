"""Inventory planning for large catalogues: ABC and XYZ classes, safety stock, reorder points.

Plain statistics on units issued (OUT movements), NOT machine learning:

* ABC ranks products by units issued (Pareto): A is the top 80 percent of volume, B the next 15,
  C the rest.
* XYZ rates how steady the daily demand is: X steady, Y variable, Z erratic (coefficient of
  variation).
* Safety stock = z * sqrt(L * sd_d^2 + mean_d^2 * sd_L^2), where z comes from the service level,
  L is the supplier lead time in days and sd_L its spread. This protects against both demand and
  delivery variability.
* Reorder point = mean_d * L + safety stock. Order-up-to level = reorder point + mean_d * review
  days. When the stock position (on hand plus on order) falls to the reorder point, order up to
  that level.

Pure functions with no database access, so they are unit-tested without one.
"""

from math import ceil, sqrt
from statistics import NormalDist

# Higher service for products that matter most. A caller can override it for every product.
SERVICE_BY_CLASS = {"A": 0.98, "B": 0.95, "C": 0.90, "N": 0.90}
ABC_A_SHARE = 0.80
ABC_B_SHARE = 0.95
XYZ_X_MAX_CV = 0.5
XYZ_Y_MAX_CV = 1.0
DEFAULT_LEAD_DAYS = 7.0
OVERSTOCK_FACTOR = 2.0  # on hand above twice the order-up-to level
URGENCY = {"reorder-now": 0, "reorder-soon": 1, "overstock": 2, "ok": 3, "idle": 4, "no-demand": 5}


def z_value(service_level: float) -> float:
    """Standard normal quantile for a service level such as 0.95."""
    return NormalDist().inv_cdf(min(max(service_level, 0.5), 0.999))


def daily_stats(days: int, total: float, sum_squares: float) -> tuple[float, float]:
    """Mean and sample standard deviation of daily units issued, zero days included."""
    mean = total / days
    if days < 2:
        return mean, 0.0
    variance = max(0.0, (sum_squares - days * mean * mean) / (days - 1))
    return mean, sqrt(variance)


def lead_stats(count: int, mean: float | None, std: float | None) -> tuple[float, float]:
    """Delivery lead time in days and its spread; defaults until there are two received orders."""
    if not count or mean is None:
        return DEFAULT_LEAD_DAYS, 0.0
    return float(mean), float(std) if count >= 2 and std is not None else 0.0


def xyz_class(mean: float, std: float) -> str:
    if mean <= 0:
        return "N"
    cv = std / mean
    return "X" if cv <= XYZ_X_MAX_CV else "Y" if cv <= XYZ_Y_MAX_CV else "Z"


def abc_classes(totals: dict[str, float]) -> dict[str, str]:
    """A, B or C by cumulative share of units issued; N for products with no demand at all."""
    grand = sum(t for t in totals.values() if t > 0)
    classes: dict[str, str] = {}
    running = 0.0
    for key, total in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        if total <= 0 or grand <= 0:
            classes[key] = "N"
            continue
        share = running / grand
        classes[key] = "A" if share < ABC_A_SHARE else "B" if share < ABC_B_SHARE else "C"
        running += total
    return classes


def safety_stock(z: float, mean: float, std: float, lead: float, lead_std: float) -> float:
    return z * sqrt(lead * std * std + mean * mean * lead_std * lead_std)


def plan_row(
    row: dict, days: int, lead: float, lead_std: float, review_days: int,
    abc: str, service_level: float | None = None,
) -> dict:
    quantity = int(row["quantity"])
    on_order = int(row["on_order"])
    mean, std = daily_stats(days, float(row["out_total"]), float(row["out_sq"]))
    xyz = xyz_class(mean, std)
    service = service_level if service_level is not None else SERVICE_BY_CLASS[abc]
    ss = safety_stock(z_value(service), mean, std, lead, lead_std) if mean > 0 else 0.0
    rop = mean * lead + ss
    order_up_to = rop + mean * review_days
    position = quantity + on_order
    cover = round(quantity / mean, 1) if mean > 0 else None

    if mean == 0:
        status = "idle" if quantity > 0 else "no-demand"
        suggested = 0
    elif position <= rop:
        status = "reorder-now" if quantity <= mean * lead else "reorder-soon"
        suggested = max(0, ceil(order_up_to - position - 1e-9))
    elif quantity > OVERSTOCK_FACTOR * order_up_to:
        status, suggested = "overstock", 0
    else:
        status, suggested = "ok", 0

    return {
        "product_id": str(row["id"]), "sku": row["sku"], "name": row["name"], "unit": row["unit"],
        "quantity": quantity, "on_order": on_order,
        "avg_daily_demand": round(mean, 2), "std_daily_demand": round(std, 2),
        "cv": round(std / mean, 2) if mean > 0 else None,
        "abc": abc, "xyz": xyz, "class": abc + xyz if mean > 0 else "N",
        "service_level": service, "lead_days": round(lead, 1),
        "safety_stock": ceil(ss - 1e-9), "reorder_point": ceil(rop - 1e-9),
        "order_up_to": ceil(order_up_to - 1e-9),
        "days_of_cover": cover, "suggested_order": suggested, "status": status,
    }


def build_plan(
    products: list[dict], days: int, lead_count: int, lead_mean: float | None,
    lead_std: float | None, review_days: int = 7, service_level: float | None = None,
) -> dict:
    """Plan every product and summarise the whole catalogue."""
    lead, lead_sd = lead_stats(lead_count, lead_mean, lead_std)
    classes = abc_classes({str(p["id"]): float(p["out_total"]) for p in products})
    rows = [
        plan_row(p, days, lead, lead_sd, review_days, classes[str(p["id"])], service_level)
        for p in products
    ]
    rows.sort(key=lambda r: (URGENCY[r["status"]], r["abc"], r["name"]))

    matrix: dict[str, int] = {}
    for r in rows:
        matrix[r["class"]] = matrix.get(r["class"], 0) + 1

    def count(status: str) -> int:
        return sum(1 for r in rows if r["status"] == status)

    summary = {
        "products": len(rows),
        "abc": {c: sum(1 for r in rows if r["abc"] == c) for c in "ABCN"},
        "matrix": matrix,
        "safety_stock_units": sum(r["safety_stock"] for r in rows),
        "reorder_now": count("reorder-now"), "reorder_soon": count("reorder-soon"),
        "overstock": count("overstock"), "idle": count("idle"),
        "units_to_order": sum(r["suggested_order"] for r in rows),
    }
    return {
        "parameters": {
            "days": days, "review_days": review_days, "lead_days": round(lead, 1),
            "lead_days_std": round(lead_sd, 1), "lead_orders": lead_count,
            "service_level": service_level,
        },
        "summary": summary,
        "rows": rows,
    }
