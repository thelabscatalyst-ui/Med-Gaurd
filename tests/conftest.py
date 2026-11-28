import os

import pytest
from fastapi.testclient import TestClient

# A port nothing listens on: connection is refused immediately, so "database down"
# tests are fast and need no Docker.
UNREACHABLE_DB = "postgresql://medguard:medguard@127.0.0.1:1/medguard"
LOCAL_DB = os.environ.get(
    "MEDGUARD_TEST_DATABASE_URL", "postgresql://medguard:medguard@localhost:5433/medguard"
)


@pytest.fixture(scope="session")
def rls_db() -> str:
    """A fresh `medguard_test` database: every migration applied, test world seeded.

    Built once per test session, and only when an integration test asks for it.
    """
    from tests import pg

    url = pg.recreate_database("medguard_test")
    pg.alembic(url, "upgrade", "head")
    pg.seed(url)
    return url


@pytest.fixture
def make_client(monkeypatch):
    """Build a fresh app against a given database URL, with its lifespan running."""
    clients = []

    def _make(database_url: str) -> TestClient:
        monkeypatch.setenv("DATABASE_URL", database_url)
        monkeypatch.setenv("HEALTH_DB_TIMEOUT_S", "0.5")
        from app.config import get_settings

        get_settings.cache_clear()  # settings are cached per process; reset per test
        from app.main import create_app

        client = TestClient(create_app())
        client.__enter__()  # runs the lifespan (opens the pool)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)
