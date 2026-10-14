"""Auth tests against the real PostgreSQL dev branch and the real stockflow_app role.

DESTRUCTIVE to the auth and inventory tables (TRUNCATE). They only run if the dev-database
guard (TEST_DATABASE_HOST) passes; see tests/conftest.py.
"""

import pytest
from fastapi.testclient import TestClient

from app.auth.repository import AuthRepository, EmailAlreadyRegistered
from app.auth.sessions import SESSION_COOKIE, hash_token, new_token
from app.db import Database
from app.main import create_app
from tests.helpers import app_url, make_settings, owner_conn, reset_inventory, sqlstate_of

pytestmark = [pytest.mark.db, pytest.mark.rls]

PASSWORD = "correct horse battery"
FAKE_HASH = "pepper1$not-a-real-hash"


def reset_auth() -> None:
    reset_inventory()
    with owner_conn() as conn:
        conn.execute(
            "TRUNCATE TABLE public.sessions, public.memberships, "
            "public.workspaces, public.users"
        )


@pytest.fixture
def db():
    reset_auth()
    database = Database(app_url(), pool_size=4)
    yield database
    database.dispose()


@pytest.fixture
def repo(db):
    return AuthRepository(db)


def signup(repo: AuthRepository, email: str) -> tuple[str, str]:
    return repo.register(email, FAKE_HASH, f"Workspace of {email}")


def login(repo: AuthRepository, user_id: str, workspace_id: str) -> str:
    token = new_token()
    repo.create_session(user_id, workspace_id, hash_token(token), 3600)
    return token


def test_register_creates_an_owner_membership_and_rejects_duplicate_emails(repo):
    user_id, workspace_id = signup(repo, "a@example.com")
    token = login(repo, user_id, workspace_id)
    resolved = repo.resolve_session(hash_token(token))
    assert (resolved.user_id, resolved.workspace_id, resolved.role) == (
        user_id,
        workspace_id,
        "owner",
    )
    with pytest.raises(EmailAlreadyRegistered):
        signup(repo, "a@example.com")


def test_unknown_token_resolves_to_nothing(repo):
    assert repo.resolve_session(hash_token(new_token())) is None


def test_a_session_cannot_target_a_workspace_the_user_does_not_belong_to(repo):
    user_a, _ = signup(repo, "a@example.com")
    _, workspace_b = signup(repo, "b@example.com")
    def attempt():
        repo.create_session(user_a, workspace_b, hash_token(new_token()), 3600)

    assert sqlstate_of(attempt) == "42501"


def test_revoked_expired_disabled_and_removed_members_lose_access(repo):
    # revoked
    user, workspace = signup(repo, "revoke@example.com")
    token = login(repo, user, workspace)
    repo.revoke_session(hash_token(token))
    assert repo.resolve_session(hash_token(token)) is None

    # expired
    user, workspace = signup(repo, "expire@example.com")
    token = login(repo, user, workspace)
    with owner_conn() as conn:
        conn.execute(
            "UPDATE public.sessions SET expires_at = now() - interval '1 second' "
            "WHERE token_hash = %s",
            (hash_token(token),),
        )
    assert repo.resolve_session(hash_token(token)) is None

    # disabled user
    user, workspace = signup(repo, "disabled@example.com")
    token = login(repo, user, workspace)
    with owner_conn() as conn:
        conn.execute("UPDATE public.users SET disabled_at = now() WHERE id = %s::uuid", (user,))
    assert repo.resolve_session(hash_token(token)) is None
    assert repo.get_login("disabled@example.com") is None

    # removed from the workspace
    user, workspace = signup(repo, "removed@example.com")
    token = login(repo, user, workspace)
    with owner_conn() as conn:
        conn.execute("DELETE FROM public.memberships WHERE user_id = %s::uuid", (user,))
    assert repo.resolve_session(hash_token(token)) is None


def test_five_failures_lock_the_account_and_success_resets_it(repo):
    user, _ = signup(repo, "lock@example.com")
    for _ in range(4):
        repo.record_login_failure(user)
    assert repo.get_login("lock@example.com").locked is False
    repo.record_login_failure(user)
    assert repo.get_login("lock@example.com").locked is True
    repo.record_login_success(user)
    assert repo.get_login("lock@example.com").locked is False


def test_default_workspace_is_the_users_own(repo):
    user, workspace = signup(repo, "ws@example.com")
    assert repo.default_workspace(user) == workspace


def test_full_http_flow_with_cross_tenant_isolation(db):
    settings = make_settings(database_url=app_url())
    app = create_app(settings, db)
    alice = TestClient(app, raise_server_exceptions=False)
    bob = TestClient(app, raise_server_exceptions=False)
    replay = TestClient(app, raise_server_exceptions=False)

    def credentials(name: str) -> dict[str, str]:
        return {"email": f"{name}@example.com", "password": PASSWORD}

    reg_a = alice.post(
        "/api/v1/auth/register", json={**credentials("alice"), "workspace_name": "Alice Ltd"}
    )
    reg_b = bob.post(
        "/api/v1/auth/register", json={**credentials("bob"), "workspace_name": "Bob Ltd"}
    )
    assert reg_a.status_code == reg_b.status_code == 201
    alice_token = reg_a.cookies[SESSION_COOKIE]
    alice_workspace = reg_a.json()["workspace_id"]

    assert alice.get("/api/v1/session").json()["tenant_id"] == alice_workspace

    created = alice.post("/api/v1/products", json={"sku": "A-1", "name": "Alice secret"})
    assert created.status_code == 201
    product_id = created.json()["id"]
    assert [p["name"] for p in alice.get("/api/v1/products").json()] == ["Alice secret"]

    # Bob sees nothing of Alice's, cannot change it, and cannot pick her tenant.
    assert bob.get("/api/v1/products").json() == []
    assert bob.get("/api/v1/products", headers={"x-tenant-id": alice_workspace}).json() == []
    patched = bob.patch(f"/api/v1/products/{product_id}", json={"name": "hacked"})
    assert patched.status_code == 404
    assert alice.get("/api/v1/products").json()[0]["name"] == "Alice secret"

    # Wrong password is refused; logout kills the token server-side (replay fails).
    wrong = alice.post("/api/v1/auth/login", json={**credentials("alice"), "password": "wrong-pw"})
    assert wrong.status_code == 401
    assert alice.post("/api/v1/auth/logout").status_code == 204
    assert alice.get("/api/v1/session").status_code == 401
    stolen = {"cookie": f"{SESSION_COOKIE}={alice_token}"}
    assert replay.get("/api/v1/session", headers=stolen).status_code == 401

    # Logging in again works and sees the same data.
    assert alice.post("/api/v1/auth/login", json=credentials("alice")).status_code == 200
    assert [p["name"] for p in alice.get("/api/v1/products").json()] == ["Alice secret"]
