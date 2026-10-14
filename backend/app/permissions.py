"""Permission catalogue and role mapping. Unknown roles get no permissions (fail closed)."""

PERMISSIONS: tuple[str, ...] = (
    "catalog.read",
    "catalog.write",
    "inventory.read",
    "inventory.write",
    "purchasing.read",
    "purchasing.write",
    "reports.read",
    "members.read",
    "members.manage",
    "settings.manage",
)


def is_permission(value: object) -> bool:
    return isinstance(value, str) and value in PERMISSIONS


_READ_ONLY = frozenset(
    p for p in PERMISSIONS if p.endswith(".read") and not p.startswith("members.")
)

# Roles are stored in memberships.role (CHECK constraint in migration 0004).
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset(PERMISSIONS),
    "admin": frozenset(PERMISSIONS),
    "manager": frozenset(PERMISSIONS) - {"members.manage", "settings.manage"},
    # Staff can look things up and record stock movements, but cannot edit the catalogue
    # or see reports.
    "staff": frozenset({"catalog.read", "inventory.read", "inventory.write", "purchasing.read"}),
    "viewer": _READ_ONLY,
}


def permissions_for_role(role: object) -> frozenset[str]:
    if not isinstance(role, str):
        return frozenset()
    return ROLE_PERMISSIONS.get(role, frozenset())


# API keys act with a fixed, limited set of permissions (never members or settings).
API_SCOPES: dict[str, frozenset[str]] = {
    "read": _READ_ONLY,
    "readwrite": _READ_ONLY | {"catalog.write", "inventory.write", "purchasing.write"},
}


def permissions_for_scope(scope: object) -> frozenset[str]:
    if not isinstance(scope, str):
        return frozenset()
    return API_SCOPES.get(scope, frozenset())
