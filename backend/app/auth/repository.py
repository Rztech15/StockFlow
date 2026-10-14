"""Database access for authentication.

Only calls the app.auth_* SECURITY DEFINER functions through Database.auth_tx(). The runtime
role has no privileges on the authentication tables, so nothing else is possible here.
CAST(...) is used instead of the :: operator because SQLAlchemy text() mishandles ":name::type".
"""

from dataclasses import dataclass

from app.db import Database, text


class EmailAlreadyRegistered(Exception):
    pass


@dataclass(frozen=True)
class LoginRecord:
    user_id: str
    password_hash: str
    locked: bool


@dataclass(frozen=True)
class ResolvedSession:
    user_id: str
    workspace_id: str | None
    role: str | None


class AuthRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def register(self, email: str, password_hash: str, workspace_name: str) -> tuple[str, str]:
        try:
            with self._db.auth_tx() as conn:
                row = conn.execute(
                    text(
                        "SELECT o_user_id, o_workspace_id FROM app.auth_register("
                        "CAST(:email AS text), CAST(:hash AS text), CAST(:name AS text))"
                    ),
                    {"email": email, "hash": password_hash, "name": workspace_name},
                ).one()
        except Exception as exc:
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if sqlstate == "23505" and "users_email_unique" in str(exc):
                raise EmailAlreadyRegistered from None
            raise
        return str(row[0]), str(row[1])

    def get_login(self, email: str) -> LoginRecord | None:
        with self._db.auth_tx() as conn:
            row = conn.execute(
                text(
                    "SELECT o_user_id, o_password_hash, o_locked "
                    "FROM app.auth_get_login(CAST(:email AS text))"
                ),
                {"email": email},
            ).first()
        if row is None:
            return None
        return LoginRecord(str(row[0]), row[1], bool(row[2]))

    def record_login_failure(self, user_id: str) -> None:
        with self._db.auth_tx() as conn:
            conn.execute(
                text("SELECT app.auth_record_login_failure(CAST(:user AS uuid))"),
                {"user": user_id},
            )

    def record_login_success(self, user_id: str) -> None:
        with self._db.auth_tx() as conn:
            conn.execute(
                text("SELECT app.auth_record_login_success(CAST(:user AS uuid))"),
                {"user": user_id},
            )

    def default_workspace(self, user_id: str) -> str | None:
        with self._db.auth_tx() as conn:
            value = conn.execute(
                text("SELECT app.auth_default_workspace(CAST(:user AS uuid))"),
                {"user": user_id},
            ).scalar()
        return None if value is None else str(value)

    def create_session(
        self, user_id: str, workspace_id: str | None, token_hash: bytes, ttl_seconds: int
    ) -> None:
        with self._db.auth_tx() as conn:
            conn.execute(
                text(
                    "SELECT o_session_id FROM app.auth_create_session("
                    "CAST(:user AS uuid), CAST(:ws AS uuid), CAST(:hash AS bytea), "
                    "CAST(:ttl AS integer))"
                ),
                {"user": user_id, "ws": workspace_id, "hash": token_hash, "ttl": ttl_seconds},
            ).one()

    def resolve_session(self, token_hash: bytes) -> ResolvedSession | None:
        with self._db.auth_tx() as conn:
            row = conn.execute(
                text(
                    "SELECT o_user_id, o_workspace_id, o_role "
                    "FROM app.auth_resolve_session(CAST(:hash AS bytea))"
                ),
                {"hash": token_hash},
            ).first()
        if row is None:
            return None
        workspace_id = None if row[1] is None else str(row[1])
        return ResolvedSession(str(row[0]), workspace_id, row[2])

    def revoke_session(self, token_hash: bytes) -> None:
        with self._db.auth_tx() as conn:
            conn.execute(
                text("SELECT app.auth_revoke_session(CAST(:hash AS bytea))"),
                {"hash": token_hash},
            )

    def list_workspaces(self, token_hash: bytes) -> list[dict[str, object]]:
        with self._db.auth_tx() as conn:
            rows = conn.execute(
                text(
                    "SELECT o_workspace_id, o_name, o_role, o_active "
                    "FROM app.auth_list_workspaces(CAST(:hash AS bytea))"
                ),
                {"hash": token_hash},
            ).all()
        return [
            {"workspace_id": str(r[0]), "name": r[1], "role": r[2], "active": bool(r[3])}
            for r in rows
        ]

    def switch_workspace(self, token_hash: bytes, workspace_id: str) -> bool:
        with self._db.auth_tx() as conn:
            result = conn.execute(
                text(
                    "SELECT app.auth_switch_workspace("
                    "CAST(:hash AS bytea), CAST(:ws AS uuid))"
                ),
                {"hash": token_hash, "ws": workspace_id},
            ).scalar()
        return bool(result)

    def create_workspace(self, token_hash: bytes, name: str) -> str:
        with self._db.auth_tx() as conn:
            value = conn.execute(
                text(
                    "SELECT app.auth_create_workspace("
                    "CAST(:hash AS bytea), CAST(:name AS text))"
                ),
                {"hash": token_hash, "name": name},
            ).scalar_one()
        return str(value)

    def resolve_api_key(self, key_hash: bytes) -> ResolvedSession | None:
        """For API keys, ResolvedSession.role carries the key's scope ('read' / 'readwrite')."""
        with self._db.auth_tx() as conn:
            row = conn.execute(
                text(
                    "SELECT o_user_id, o_workspace_id, o_scope "
                    "FROM app.auth_resolve_apikey(CAST(:hash AS bytea))"
                ),
                {"hash": key_hash},
            ).first()
        if row is None:
            return None
        return ResolvedSession(str(row[0]), str(row[1]), row[2])
