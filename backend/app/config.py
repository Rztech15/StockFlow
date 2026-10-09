"""The ONLY module that reads configuration. Everything else receives a validated Settings.

Values come from environment variables or the repo-root .env file (never committed).
Secrets use SecretStr, so printing a Settings object never shows them.
"""

from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
REDACTED = "[REDACTED]"


def _check_postgres_url(value: str) -> str:
    if not value.startswith(("postgres://", "postgresql://")):
        raise ValueError("must be a postgres:// or postgresql:// URL")
    return value


def redact_url(value: str) -> str:
    """Masks the password inside a connection URL."""
    try:
        parts = urlsplit(value)
        if parts.password:
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            netloc = f"{parts.username}:REDACTED@{host}{port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return value
    except ValueError:
        return REDACTED


class Settings(BaseSettings):
    """Runtime settings of the API / worker."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_env: str = "development"  # development | test | production
    app_base_url: str
    allowed_origins: str  # comma-separated bare origins
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "info"
    db_pool_max: int = 10

    # Runtime connection: restricted role stockflow_app.
    database_url: SecretStr
    # Owner credentials: migration/deploy jobs only. Forbidden in a production runtime.
    migration_database_url: SecretStr | None = None

    password_pepper_v1: SecretStr

    smtp_host: str
    smtp_port: int
    smtp_secure: bool = False
    smtp_user: SecretStr | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in {"development", "test", "production"}:
            raise ValueError("must be development, test or production")
        return v

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _pg(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None:
            _check_postgres_url(v.get_secret_value())
        return v

    @field_validator("migration_database_url", "smtp_user", "smtp_password", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if isinstance(v, str) and v.strip() == "" else v

    @field_validator("password_pepper_v1")
    @classmethod
    def _pepper(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("must be at least 32 characters")
        return v

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, v: str) -> str:
        entries = [e.strip() for e in v.split(",") if e.strip()]
        if not entries:
            raise ValueError("at least one origin is required")
        for entry in entries:
            parts = urlsplit(entry)
            bare = f"{parts.scheme}://{parts.netloc}"
            if parts.scheme not in {"http", "https"} or not parts.netloc or entry != bare:
                raise ValueError(
                    "entries must be bare origins like https://app.example.com "
                    "(no path, no trailing slash, no wildcard)"
                )
        return ",".join(entries)

    @model_validator(mode="after")
    def _security_rules(self) -> "Settings":
        user = unquote(urlsplit(self.database_url.get_secret_value()).username or "")
        if user == "stockflow_owner":
            raise ValueError("DATABASE_URL must use the restricted role stockflow_app, not the owner")
        if self.app_env == "production":
            if self.migration_database_url is not None:
                raise ValueError(
                    "MIGRATION_DATABASE_URL (owner credentials) must not be set in a production runtime"
                )
            if not self.app_base_url.startswith("https://"):
                raise ValueError("APP_BASE_URL must use https in production")
            if any(not o.startswith("https://") for o in self.origins):
                raise ValueError("ALLOWED_ORIGINS must all use https in production")
        return self

    @property
    def origins(self) -> list[str]:
        return self.allowed_origins.split(",")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def redacted(self) -> dict[str, object]:
        """Safe-to-log view: no secrets, no URL passwords."""
        data: dict[str, object] = {
            k: v for k, v in self.model_dump().items() if not isinstance(v, SecretStr)
        }
        data["database_url"] = redact_url(self.database_url.get_secret_value())
        data["migration_database_url"] = REDACTED if self.migration_database_url else None
        data["password_pepper_v1"] = REDACTED
        data["smtp_user"] = REDACTED if self.smtp_user else None
        data["smtp_password"] = REDACTED if self.smtp_password else None
        return data


class MigrationSettings(BaseSettings):
    """Used by Alembic only (owner connection)."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")
    migration_database_url: SecretStr

    @field_validator("migration_database_url")
    @classmethod
    def _pg(cls, v: SecretStr) -> SecretStr:
        _check_postgres_url(v.get_secret_value())
        return v


class BootstrapSettings(BaseSettings):
    """Used by scripts/bootstrap_db.py (admin connection)."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")
    bootstrap_database_url: SecretStr
    stockflow_db_name: str = "stockflow"
    stockflow_owner_password: SecretStr
    stockflow_app_password: SecretStr


def explain(error: ValidationError) -> list[str]:
    """Readable problem list: variable name + message, never the value."""
    return [
        f"{'.'.join(str(p) for p in e['loc']).upper() or '(settings)'}: {e['msg']}"
        for e in error.errors()
    ]


def load_settings_or_exit() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        lines = "\n".join(f"  - {line}" for line in explain(error))
        raise SystemExit(f"Invalid or missing configuration. Fix these variables:\n{lines}") from None
