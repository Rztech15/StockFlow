"""Authentication routes: register, login, logout (cookie sessions)."""

import re

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.auth.deps import Passwords, Repo, require_trusted_origin
from app.auth.repository import EmailAlreadyRegistered
from app.auth.sessions import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    cookie_is_secure,
    hash_token,
    new_token,
)
from app.errors import conflict, forbidden, unauthorized

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["auth"],
    dependencies=[Depends(require_trusted_origin)],
)

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
GENERIC_LOGIN_ERROR = "Invalid email or password."


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    workspace_name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip().lower()
        if _EMAIL.fullmatch(value) is None:
            raise ValueError("must be a valid email address")
        return value

    @field_validator("workspace_name")
    @classmethod
    def _workspace_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return value.strip().lower()


def _start_session(
    request: Request, response: Response, repo: Repo, user_id: str, workspace_id: str
) -> None:
    token = new_token()
    repo.create_session(user_id, workspace_id, hash_token(token), SESSION_TTL_SECONDS)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=cookie_is_secure(request.app.state.settings),
        samesite="lax",
        path="/",
    )


@router.post("/register", status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    repo: Repo,
    passwords: Passwords,
) -> dict[str, str]:
    password_hash = passwords.hash(payload.password)
    try:
        user_id, workspace_id = repo.register(
            payload.email, password_hash, payload.workspace_name
        )
    except EmailAlreadyRegistered:
        raise conflict("Unable to register with these details.") from None
    _start_session(request, response, repo, user_id, workspace_id)
    return {"user_id": user_id, "workspace_id": workspace_id}


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    repo: Repo,
    passwords: Passwords,
) -> dict[str, str]:
    record = repo.get_login(payload.email)
    if record is None or record.locked:
        # Same work and same message whether the account is missing, locked or wrong.
        passwords.verify_dummy(payload.password)
        raise unauthorized(GENERIC_LOGIN_ERROR)
    if not passwords.verify(record.password_hash, payload.password):
        repo.record_login_failure(record.user_id)
        raise unauthorized(GENERIC_LOGIN_ERROR)

    repo.record_login_success(record.user_id)
    workspace_id = repo.default_workspace(record.user_id)
    if workspace_id is None:
        raise forbidden("This account does not belong to any workspace.")
    _start_session(request, response, repo, record.user_id, workspace_id)
    return {"user_id": record.user_id, "workspace_id": workspace_id}


@router.post("/logout", status_code=204)
def logout(request: Request, repo: Repo) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        repo.revoke_session(hash_token(token))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
