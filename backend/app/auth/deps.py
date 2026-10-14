"""FastAPI dependencies: session cookie -> RequestContext, permission checks, CSRF origin check.

The tenant ALWAYS comes from the verified session (database lookup of the hashed token and a
fresh membership check). Nothing the client sends (headers, body, query) can select a tenant.
"""

from collections.abc import Callable
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, Request

from app.auth.passwords import PasswordService
from app.auth.repository import AuthRepository
from app.auth.sessions import MAX_TOKEN_LENGTH, SESSION_COOKIE, hash_token
from app.context import AuthenticatedUser, RequestContext, TenantContext, to_tenant_context
from app.db import Database
from app.errors import forbidden, unauthorized
from app.permissions import is_permission, permissions_for_role, permissions_for_scope

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
API_KEY_PREFIX = "sf_"


def get_repo(request: Request) -> AuthRepository:
    return AuthRepository(request.app.state.db)


def get_passwords(request: Request) -> PasswordService:
    service = getattr(request.app.state, "passwords", None)
    if service is None:
        pepper = request.app.state.settings.password_pepper_v1.get_secret_value()
        service = PasswordService(pepper)
        request.app.state.passwords = service
    return service


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_session_token(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise unauthorized()
    return token


DB = Annotated[Database, Depends(get_db)]
Token = Annotated[str, Depends(get_session_token)]
Repo = Annotated[AuthRepository, Depends(get_repo)]
Passwords = Annotated[PasswordService, Depends(get_passwords)]


def require_trusted_origin(request: Request) -> None:
    """CSRF defence for cookie authentication.

    Unsafe methods must come from an allow-listed Origin. A request with no Origin header is
    accepted only if the browser did not mark it cross-site (curl, tests and server-to-server
    calls send neither header; a browser always sends Origin on a cross-site POST).
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        if request.headers.get("sec-fetch-site") in (None, "same-origin", "none"):
            return
        raise forbidden("Cross-site request blocked.")
    if origin not in request.app.state.settings.origins:
        raise forbidden("Origin not allowed.")


def get_request_context(request: Request, repo: Repo) -> RequestContext:
    existing = getattr(request.state, "ctx", None)
    request_id = existing.request_id if isinstance(existing, RequestContext) else str(uuid4())

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        key = header[7:].strip()
        if not key.startswith(API_KEY_PREFIX) or len(key) > MAX_TOKEN_LENGTH:
            raise unauthorized("API key is invalid or revoked.")
        resolved = repo.resolve_api_key(hash_token(key))
        if resolved is None:
            raise unauthorized("API key is invalid or revoked.")
        permissions = permissions_for_scope(resolved.role)
    else:
        token = get_session_token(request)
        resolved = repo.resolve_session(hash_token(token))
        if resolved is None:
            raise unauthorized("Session is invalid or has expired.")
        permissions = permissions_for_role(resolved.role)

    ctx = RequestContext(
        request_id=request_id,
        user=AuthenticatedUser(id=resolved.user_id),
        tenant_id=resolved.workspace_id,
        permissions=permissions,
    )
    request.state.ctx = ctx
    return ctx


def tenant_with(permission: str | None, *extra: str) -> Callable[..., TenantContext]:
    """Build a dependency that yields the TenantContext only if the role has ALL permissions."""
    required = tuple(p for p in (permission, *extra) if p is not None)
    for name in required:
        if not is_permission(name):
            raise ValueError(f"unknown permission: {name}")

    def dependency(ctx: Annotated[RequestContext, Depends(get_request_context)]) -> TenantContext:
        tenant = to_tenant_context(ctx)
        if any(name not in ctx.permissions for name in required):
            raise forbidden()
        return tenant

    return dependency
