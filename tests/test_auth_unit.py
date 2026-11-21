"""Authentication unit tests. No PostgreSQL: the repository is replaced by an in-memory fake."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth.deps import get_repo
from app.auth.passwords import PasswordService
from app.auth.repository import EmailAlreadyRegistered, LoginRecord, ResolvedSession
from app.auth.sessions import SESSION_COOKIE, hash_token, new_token
from app.db import Database
from app.main import create_app
from app.permissions import PERMISSIONS, ROLE_PERMISSIONS, is_permission, permissions_for_role
from tests.helpers import BASE, make_settings

PEPPER = BASE["password_pepper_v1"]
PASSWORD = "correct horse battery"
ALLOWED_ORIGIN = "http://localhost:5173"


# --- passwords -----------------------------------------------------------------------------


def test_password_roundtrip_and_format():
    service = PasswordService(PEPPER)
    stored = service.hash(PASSWORD)
    assert stored.startswith("pepper1$$argon2id$")
    assert PASSWORD not in stored
    assert service.verify(stored, PASSWORD)
    assert not service.verify(stored, PASSWORD + "x")
    assert service.hash(PASSWORD) != stored  # random salt


def test_password_hash_depends_on_the_pepper():
    stored = PasswordService(PEPPER).hash(PASSWORD)
    assert not PasswordService("another-pepper-0123456789abcdef0123456789").verify(stored, PASSWORD)


def test_garbage_or_unknown_version_hashes_never_verify():
    service = PasswordService(PEPPER)
    for stored in ("", "nonsense", "pepper9$$argon2id$v=19$x", "pepper1$not-an-argon2-hash"):
        assert service.verify(stored, PASSWORD) is False


# --- tokens and roles ----------------------------------------------------------------------


def test_tokens_are_random_and_hashed_to_32_bytes():
    first, second = new_token(), new_token()
    assert first != second and len(first) >= 40
    assert len(hash_token(first)) == 32
    assert hash_token(first) == hash_token(first) != hash_token(second)


def test_roles_map_to_valid_permissions_and_fail_closed():
    for permissions in ROLE_PERMISSIONS.values():
        assert all(is_permission(p) for p in permissions)
    assert permissions_for_role("owner") == frozenset(PERMISSIONS)
    assert "catalog.write" not in permissions_for_role("viewer")
    assert "inventory.write" not in permissions_for_role("viewer")
    assert "reports.read" not in permissions_for_role("staff")
    assert permissions_for_role("hacker") == frozenset()
    assert permissions_for_role(None) == frozenset()


# --- API with a fake repository ------------------------------------------------------------


class FakeRepo:
    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.sessions: dict[bytes, ResolvedSession] = {}
        self.failures: list[str] = []
        self.successes: list[str] = []
        self.revoked: list[bytes] = []
        self.allowed_workspace = ""

    def register(self, email, password_hash, workspace_name):
        if email in self.users:
            raise EmailAlreadyRegistered
        user_id, workspace_id = str(uuid4()), str(uuid4())
        self.users[email] = {
            "user_id": user_id,
            "hash": password_hash,
            "workspace_id": workspace_id,
            "locked": False,
        }
        return user_id, workspace_id

    def get_login(self, email):
        user = self.users.get(email)
        if user is None:
            return None
        return LoginRecord(user["user_id"], user["hash"], user["locked"])

    def record_login_failure(self, user_id):
        self.failures.append(user_id)

    def record_login_success(self, user_id):
        self.successes.append(user_id)

    def default_workspace(self, user_id):
        for user in self.users.values():
            if user["user_id"] == user_id:
                return user["workspace_id"]
        return None

    def create_session(self, user_id, workspace_id, token_hash, ttl_seconds):
        self.sessions[token_hash] = ResolvedSession(user_id, workspace_id, "owner")

    def resolve_session(self, token_hash):
        return self.sessions.get(token_hash)

    def revoke_session(self, token_hash):
        self.revoked.append(token_hash)
        self.sessions.pop(token_hash, None)

    def list_workspaces(self, token_hash):
        return [{"workspace_id": "w1", "name": "One", "role": "owner", "active": True}]

    def switch_workspace(self, token_hash, workspace_id):
        return workspace_id == self.allowed_workspace

    def create_workspace(self, token_hash, name):
        return "new-workspace-id"


def make_client() -> tuple[TestClient, FakeRepo]:
    settings = make_settings()
    app = create_app(settings, Database(settings.database_url.get_secret_value(), pool_size=1))
    repo = FakeRepo()
    app.dependency_overrides[get_repo] = lambda: repo
    return TestClient(app, raise_server_exceptions=False), repo


def cookie(token: str) -> dict[str, str]:
    return {"cookie": f"{SESSION_COOKIE}={token}"}


def give_session(repo: FakeRepo, token: str, role: str | None, workspace: str | None = None):
    workspace = workspace or str(uuid4())
    repo.sessions[hash_token(token)] = ResolvedSession(str(uuid4()), workspace, role)
    return workspace


def register_body(**overrides):
    body = {"email": " Owner@Example.com ", "password": PASSWORD, "workspace_name": " Acme "}
    return {**body, **overrides}


def test_register_normalizes_email_hashes_password_and_sets_a_safe_cookie():
    client, repo = make_client()
    with client:
        res = client.post("/api/v1/auth/register", json=register_body())
    assert res.status_code == 201
    assert set(res.json()) == {"user_id", "workspace_id"}
    assert PASSWORD not in res.text
    flags = res.headers["set-cookie"].lower()
    assert "httponly" in flags and "samesite=lax" in flags and "path=/" in flags
    stored = repo.users["owner@example.com"]["hash"]
    assert stored.startswith("pepper1$$argon2id$") and PASSWORD not in stored
    assert len(repo.sessions) == 1  # registered users are logged in


def test_register_rejects_weak_input_without_echoing_it():
    client, _ = make_client()
    with client:
        short = client.post("/api/v1/auth/register", json=register_body(password="short"))
        bad_email = client.post("/api/v1/auth/register", json=register_body(email="nope"))
        blank = client.post("/api/v1/auth/register", json=register_body(workspace_name="   "))
    assert short.status_code == bad_email.status_code == blank.status_code == 422
    assert "short" not in short.text.replace("too short", "")


def test_duplicate_registration_is_a_conflict():
    client, _ = make_client()
    with client:
        assert client.post("/api/v1/auth/register", json=register_body()).status_code == 201
        again = client.post("/api/v1/auth/register", json=register_body())
    assert again.status_code == 409


def test_login_success_and_identical_errors_for_every_failure():
    client, repo = make_client()
    with client:
        client.post("/api/v1/auth/register", json=register_body())
        good = client.post(
            "/api/v1/auth/login", json={"email": "OWNER@example.com", "password": PASSWORD}
        )
        wrong = client.post(
            "/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong-password"}
        )
        unknown = client.post(
            "/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
        )
        repo.users["owner@example.com"]["locked"] = True
        locked = client.post(
            "/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
        )
    assert good.status_code == 200 and SESSION_COOKIE in good.headers["set-cookie"]
    assert wrong.status_code == unknown.status_code == locked.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == locked.json()["detail"]
    assert len(repo.failures) == 1 and len(repo.successes) == 1


def test_logout_revokes_the_session_and_clears_the_cookie():
    client, repo = make_client()
    with client:
        give_session(repo, "tok-logout", "owner")
        res = client.post("/api/v1/auth/logout", headers=cookie("tok-logout"))
    assert res.status_code == 204
    assert hash_token("tok-logout") in repo.revoked
    assert "max-age=0" in res.headers["set-cookie"].lower()


def test_cross_site_origin_is_rejected_on_unsafe_methods():
    client, _ = make_client()
    body = {"email": "a@example.com", "password": PASSWORD}
    with client:
        evil = client.post(
            "/api/v1/auth/login", json=body, headers={"origin": "https://evil.example"}
        )
        site = client.post(
            "/api/v1/auth/login", json=body, headers={"sec-fetch-site": "cross-site"}
        )
        ok = client.post("/api/v1/auth/login", json=body, headers={"origin": ALLOWED_ORIGIN})
    assert evil.status_code == 403 and site.status_code == 403
    assert ok.status_code == 401  # reached the handler; unknown account


def test_protected_routes_need_a_valid_session():
    client, _ = make_client()
    with client:
        none = client.get("/api/v1/session")
        junk = client.get("/api/v1/session", headers=cookie("not-a-real-token"))
        huge = client.get("/api/v1/session", headers=cookie("x" * 500))
    assert none.status_code == junk.status_code == huge.status_code == 401


def test_tenant_comes_from_the_session_not_from_the_client():
    client, repo = make_client()
    workspace = give_session(repo, "tok-a", "owner")
    other_tenant = str(uuid4())
    with client:
        res = client.get(
            "/api/v1/session",
            headers={**cookie("tok-a"), "x-tenant-id": other_tenant},
            params={"tenant_id": other_tenant},
        )
    assert res.status_code == 200
    assert res.json() == {"authenticated": True, "tenant_id": workspace}


def test_session_without_a_workspace_cannot_reach_tenant_routes():
    client, repo = make_client()
    repo.sessions[hash_token("tok-nows")] = ResolvedSession(str(uuid4()), None, None)
    with client:
        res = client.get("/api/v1/session", headers=cookie("tok-nows"))
    assert res.status_code == 401


def test_permissions_are_enforced_before_any_database_access():
    client, repo = make_client()
    give_session(repo, "tok-viewer", "viewer")
    give_session(repo, "tok-staff", "staff")
    give_session(repo, "tok-unknown", "hacker")
    product = {"sku": "X-1", "name": "Widget"}
    movement = {"product_id": str(uuid4()), "movement_type": "IN", "quantity": 1}
    with client:
        viewer_write = client.post("/api/v1/products", json=product, headers=cookie("tok-viewer"))
        viewer_patch = client.patch(
            f"/api/v1/products/{uuid4()}", json={"name": "n"}, headers=cookie("tok-viewer")
        )
        viewer_move = client.post("/api/v1/movements", json=movement, headers=cookie("tok-viewer"))
        staff_report = client.get("/api/v1/dashboard", headers=cookie("tok-staff"))
        staff_catalog = client.post("/api/v1/products", json=product, headers=cookie("tok-staff"))
        unknown_read = client.get("/api/v1/products", headers=cookie("tok-unknown"))
    for res in (viewer_write, viewer_patch, viewer_move, staff_report, staff_catalog, unknown_read):
        assert res.status_code == 403


# --- members, workspaces, purchasing, reports (permission checks run before any database) ---


def test_member_and_purchasing_permissions_by_role():
    assert "members.manage" in permissions_for_role("owner")
    assert "members.manage" in permissions_for_role("admin")
    assert "members.manage" not in permissions_for_role("manager")
    assert "members.read" in permissions_for_role("manager")
    assert "members.read" not in permissions_for_role("viewer")
    assert "purchasing.write" not in permissions_for_role("staff")


def test_team_routes_require_the_right_role():
    client, repo = make_client()
    give_session(repo, "tok-manager", "manager")
    give_session(repo, "tok-staff", "staff")
    give_session(repo, "tok-viewer", "viewer")
    add = {"email": "new@example.com", "role": "viewer"}
    with client:
        manager_add = client.post("/api/v1/members", json=add, headers=cookie("tok-manager"))
        staff_list = client.get("/api/v1/members", headers=cookie("tok-staff"))
        viewer_remove = client.delete(f"/api/v1/members/{uuid4()}", headers=cookie("tok-viewer"))
        anonymous = client.get("/api/v1/members")
    assert manager_add.status_code == staff_list.status_code == viewer_remove.status_code == 403
    assert anonymous.status_code == 401


def test_purchasing_and_report_routes_require_the_right_role():
    client, repo = make_client()
    give_session(repo, "tok-staff", "staff")
    give_session(repo, "tok-viewer", "viewer")
    order = {
        "supplier_id": str(uuid4()),
        "items": [{"product_id": str(uuid4()), "quantity": 1}],
    }
    with client:
        staff_order = client.post(
            "/api/v1/purchase-orders", json=order, headers=cookie("tok-staff")
        )
        staff_receive = client.post(
            f"/api/v1/purchase-orders/{uuid4()}/receive", headers=cookie("tok-staff")
        )
        viewer_supplier = client.post(
            "/api/v1/suppliers", json={"name": "Acme"}, headers=cookie("tok-viewer")
        )
        staff_report = client.get("/api/v1/reports/movements", headers=cookie("tok-staff"))
    for res in (staff_order, staff_receive, viewer_supplier, staff_report):
        assert res.status_code == 403


def test_order_validation_rejects_duplicate_products_and_empty_orders():
    client, repo = make_client()
    give_session(repo, "tok-owner", "owner")
    product = str(uuid4())
    base = {"supplier_id": str(uuid4())}
    with client:
        duplicate = client.post(
            "/api/v1/purchase-orders",
            json={**base, "items": [{"product_id": product, "quantity": 1}] * 2},
            headers=cookie("tok-owner"),
        )
        empty = client.post(
            "/api/v1/purchase-orders", json={**base, "items": []}, headers=cookie("tok-owner")
        )
    assert duplicate.status_code == empty.status_code == 422


def test_workspace_switch_is_refused_for_foreign_workspaces():
    client, repo = make_client()
    give_session(repo, "tok-owner", "owner")
    mine, foreign = str(uuid4()), str(uuid4())
    repo.allowed_workspace = mine
    with client:
        ok = client.post(
            "/api/v1/workspaces/switch", json={"workspace_id": mine}, headers=cookie("tok-owner")
        )
        refused = client.post(
            "/api/v1/workspaces/switch", json={"workspace_id": foreign}, headers=cookie("tok-owner")
        )
        listing = client.get("/api/v1/workspaces", headers=cookie("tok-owner"))
        anonymous = client.get("/api/v1/workspaces")
    assert ok.status_code == 200 and refused.status_code == 403
    assert listing.status_code == 200 and listing.json()[0]["name"] == "One"
    assert anonymous.status_code == 401
