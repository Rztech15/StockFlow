"""Permission catalogue (names only). Roles and authorization logic arrive in Phase 1."""

PERMISSIONS: tuple[str, ...] = (
    "catalog.read",
    "catalog.write",
    "inventory.read",
    "inventory.write",
    "purchasing.read",
    "purchasing.write",
    "reports.read",
)


def is_permission(value: object) -> bool:
    return isinstance(value, str) and value in PERMISSIONS
