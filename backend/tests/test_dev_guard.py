"""The development-database guard. Pure unit tests: no database, no .env."""

import pytest

from app.config import (
    PRODUCTION_MIGRATION_OVERRIDE,
    DatabaseTargetError,
    DevGuardSettings,
    check_development_targets,
    require_development_database,
)

DEV = "ep-dev-branch-123.region.aws.neon.tech"
PROD = "ep-prod-main-456.region.aws.neon.tech"
PASSWORD = "not-a-real-password"


def url(role: str, host: str) -> str:
    return f"postgresql://{role}:{PASSWORD}@{host}:5432/stockflow"


def guard(**overrides: object) -> DevGuardSettings:
    base = dict(
        test_database_host=DEV,
        database_url=url("stockflow_app", DEV),
        migration_database_url=url("stockflow_owner", DEV),
        bootstrap_database_url=url("postgres", DEV),
        allow_production_migration="",
    )
    return DevGuardSettings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]


def test_accepts_the_development_host():
    require_development_database(guard())
    require_development_database(guard(), bootstrap=True)
    require_development_database(guard(test_database_host=DEV.upper()))


@pytest.mark.parametrize("field", ["database_url", "migration_database_url"])
def test_rejects_a_production_host(field):
    role = "stockflow_app" if field == "database_url" else "stockflow_owner"
    with pytest.raises(DatabaseTargetError):
        require_development_database(guard(**{field: url(role, PROD)}))


@pytest.mark.parametrize(
    "bad_host",
    [DEV + ".evil.example", "x" + DEV, "localhost", "10.0.0.1"],
)
def test_rejects_lookalike_and_unexpected_hosts(bad_host):
    with pytest.raises(DatabaseTargetError):
        require_development_database(guard(database_url=url("stockflow_app", bad_host)))


def test_credentials_in_the_url_cannot_fake_the_host():
    tricky = f"postgresql://stockflow_app:x@{DEV}@{PROD}:5432/stockflow"
    with pytest.raises(DatabaseTargetError):
        require_development_database(guard(database_url=tricky))


@pytest.mark.parametrize(
    "allowed", ["", "   ", "*", f"https://{DEV}", f"{DEV}:5432", f"{DEV}/db"]
)
def test_missing_or_malformed_allowlist_fails_closed(allowed):
    with pytest.raises(DatabaseTargetError):
        require_development_database(guard(test_database_host=allowed))


@pytest.mark.parametrize("field", ["database_url", "migration_database_url"])
def test_missing_url_fails_closed(field):
    with pytest.raises(DatabaseTargetError):
        require_development_database(guard(**{field: None}))


def test_bootstrap_url_is_checked_only_for_bootstrap():
    bad = guard(bootstrap_database_url=url("postgres", PROD))
    require_development_database(bad)
    with pytest.raises(DatabaseTargetError):
        require_development_database(bad, bootstrap=True)


def test_error_messages_never_reveal_hosts_urls_or_passwords():
    with pytest.raises(DatabaseTargetError) as info:
        require_development_database(guard(database_url=url("stockflow_app", PROD)))
    message = str(info.value)
    for secret in (PASSWORD, PROD, DEV, "postgresql://"):
        assert secret not in message


def test_pure_check_rejects_an_empty_url():
    with pytest.raises(DatabaseTargetError):
        check_development_targets(DEV, {"DATABASE_URL": ""})


def test_production_migration_override_is_explicit_and_migration_only():
    prod = guard(
        migration_database_url=url("stockflow_owner", PROD),
        allow_production_migration=PRODUCTION_MIGRATION_OVERRIDE,
    )
    # Honoured only when the caller (alembic's env.py) asks for it ...
    require_development_database(prod, allow_override=True)
    # ... never for tests or bootstrap, which do not pass allow_override.
    with pytest.raises(DatabaseTargetError):
        require_development_database(prod)
    with pytest.raises(DatabaseTargetError):
        require_development_database(prod, bootstrap=True)
    # A wrong or partial value is not an override.
    wrong = guard(
        migration_database_url=url("stockflow_owner", PROD), allow_production_migration="yes"
    )
    with pytest.raises(DatabaseTargetError):
        require_development_database(wrong, allow_override=True)
