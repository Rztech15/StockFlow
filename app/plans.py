"""Subscription plans and usage limits. No billing provider is connected: a workspace's plan is
changed by an administrator in the database (column workspace_settings.plan)."""

PLAN_LIMITS: dict[str, dict[str, int | None]] = {
    "free": {"products": 100, "members": 5, "suppliers": 20, "api_keys": 2},
    "pro": {"products": 5000, "members": 25, "suppliers": 500, "api_keys": 10},
    "business": {"products": None, "members": None, "suppliers": None, "api_keys": None},
}
RESOURCES = ("products", "members", "suppliers", "api_keys")


def limit_for(plan: str, resource: str) -> int | None:
    """None means unlimited. Unknown plans fall back to the free limits (fail closed)."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])[resource]
