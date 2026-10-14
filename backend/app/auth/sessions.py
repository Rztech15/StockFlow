"""Session tokens. The browser holds a random opaque token; the database stores only its SHA-256."""

import hashlib
import secrets

from app.config import Settings

SESSION_COOKIE = "stockflow_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_TOKEN_LENGTH = 128


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def cookie_is_secure(settings: Settings) -> bool:
    return settings.is_production or settings.app_base_url.startswith("https://")
