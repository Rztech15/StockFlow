import pytest

from app.config import require_development_database


@pytest.fixture(autouse=True)
def _development_database_only(request: pytest.FixtureRequest) -> None:
    """Every test marked `db` refuses to start unless the host is the allow-listed dev host."""
    if request.node.get_closest_marker("db") is not None:
        require_development_database()
