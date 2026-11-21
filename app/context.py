from dataclasses import dataclass, field

from app.errors import unauthorized


@dataclass
class AuthenticatedUser:
    id: str


@dataclass
class RequestContext:
    """Per-request context. Phase 0 creates the anonymous shape; Phase 1 fills it after login."""

    request_id: str
    user: AuthenticatedUser | None = None
    tenant_id: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str


def to_tenant_context(ctx: RequestContext) -> TenantContext:
    """Needed by tenant_tx(). Rejects requests that are not authenticated into a workspace."""
    if ctx.user is None or ctx.tenant_id is None:
        raise unauthorized("Authentication with an active workspace is required.")
    return TenantContext(tenant_id=ctx.tenant_id, user_id=ctx.user.id)
