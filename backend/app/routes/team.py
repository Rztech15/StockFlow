"""Workspaces (list / create / switch) and members (list / add / change role / remove).

Authorization is enforced twice: here by permission, and again inside the members_* SQL
functions, which check the caller's role from the transaction-local tenant context.
"""

import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator

from app.activity import log_activity
from app.auth.deps import (
    DB,
    Repo,
    Token,
    get_request_context,
    require_trusted_origin,
    tenant_with,
)
from app.auth.sessions import hash_token
from app.context import RequestContext, TenantContext
from app.db import text
from app.errors import forbidden
from app.routes.dberrors import raise_for_db_error
from app.workspace import check_limit

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_trusted_origin)])

Signed = Annotated[RequestContext, Depends(get_request_context)]
MembersRead = Annotated[TenantContext, Depends(tenant_with("members.read"))]
MembersManage = Annotated[TenantContext, Depends(tenant_with("members.manage"))]
Role = Literal["owner", "admin", "manager", "staff", "viewer"]
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class WorkspaceSwitch(BaseModel):
    workspace_id: UUID


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Role

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip().lower()
        if _EMAIL.fullmatch(value) is None:
            raise ValueError("must be a valid email address")
        return value


class MemberRole(BaseModel):
    role: Role


@router.get("/workspaces")
def list_workspaces(_: Signed, token: Token, repo: Repo) -> list[dict[str, object]]:
    return repo.list_workspaces(hash_token(token))


@router.post("/workspaces", status_code=201)
def create_workspace(
    payload: WorkspaceCreate, _: Signed, token: Token, repo: Repo
) -> dict[str, str]:
    try:
        workspace_id = repo.create_workspace(hash_token(token), payload.name)
    except Exception as exc:
        raise_for_db_error(exc)
    return {"workspace_id": workspace_id}


@router.post("/workspaces/switch")
def switch_workspace(
    payload: WorkspaceSwitch, _: Signed, token: Token, repo: Repo
) -> dict[str, str]:
    if not repo.switch_workspace(hash_token(token), str(payload.workspace_id)):
        raise forbidden("You are not a member of that workspace.")
    return {"workspace_id": str(payload.workspace_id)}


@router.get("/members")
def list_members(tenant: MembersRead, db: DB) -> list[dict[str, object]]:
    with db.tenant_tx(tenant) as conn:
        rows = conn.execute(
            text("SELECT o_user_id, o_email, o_role, o_joined_at FROM app.members_list()")
        ).all()
    return [
        {"user_id": str(r[0]), "email": r[1], "role": r[2], "joined_at": r[3]} for r in rows
    ]


@router.post("/members", status_code=201)
def add_member(payload: MemberAdd, tenant: MembersManage, db: DB) -> dict[str, str]:
    try:
        with db.tenant_tx(tenant) as conn:
            check_limit(conn, tenant, "members")
            user_id = conn.execute(
                text("SELECT app.members_add(CAST(:email AS text), CAST(:role AS text))"),
                {"email": payload.email, "role": payload.role},
            ).scalar_one()
            log_activity(conn, tenant, "added", "member", str(user_id), payload.role)
    except Exception as exc:
        raise_for_db_error(exc, duplicate="That person is already a member.")
    return {"user_id": str(user_id)}


@router.patch("/members/{user_id}")
def change_member_role(
    user_id: UUID, payload: MemberRole, tenant: MembersManage, db: DB
) -> dict[str, str]:
    try:
        with db.tenant_tx(tenant) as conn:
            conn.execute(
                text("SELECT app.members_set_role(CAST(:user AS uuid), CAST(:role AS text))"),
                {"user": str(user_id), "role": payload.role},
            )
            log_activity(conn, tenant, "role_changed", "member", str(user_id), payload.role)
    except Exception as exc:
        raise_for_db_error(exc)
    return {"user_id": str(user_id), "role": payload.role}


@router.delete("/members/{user_id}", status_code=204)
def remove_member(user_id: UUID, tenant: MembersManage, db: DB) -> Response:
    try:
        with db.tenant_tx(tenant) as conn:
            conn.execute(
                text("SELECT app.members_remove(CAST(:user AS uuid))"), {"user": str(user_id)}
            )
            log_activity(conn, tenant, "removed", "member", str(user_id))
    except Exception as exc:
        raise_for_db_error(exc)
    return Response(status_code=204)
