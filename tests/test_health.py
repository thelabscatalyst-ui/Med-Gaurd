import pytest

from tests.conftest import LOCAL_DB, UNREACHABLE_DB


def test_live_is_ok_even_when_database_is_down(make_client):
    client = make_client(UNREACHABLE_DB)
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_is_503_when_database_is_down(make_client):
    client = make_client(UNREACHABLE_DB)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"].startswith("error: ")
    # Never leak connection details from a public endpoint.
    assert "127.0.0.1" not in resp.text


def test_missing_database_url_fails_at_startup(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.integration
def test_ready_survives_killed_pool_connections(make_client, monkeypatch):
    """Regression (FAILURES.md 2026-11-23): after the DB drops ALL our connections
    (restart, failover), the very next request must succeed, and fast.

    The pool is filled to 4 connections first: with psycopg_pool's plain check, 4 dead
    connections cost ~1 + 2 = 3 s of backoff, far over the 0.5 s health timeout.
    """
    import time

    import psycopg

    monkeypatch.setenv("DB_POOL_MIN_SIZE", "4")
    client = make_client(LOCAL_DB)
    pool = client.app.state.db_pool
    deadline = time.monotonic() + 5
    # pool_available = connected and idle. (pool_size also counts connections that are
    # still being opened, so it can say 4 while only 3 exist in Postgres.)
    while pool.get_stats().get("pool_available", 0) < 4 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pool.get_stats().get("pool_available", 0) >= 4

    with psycopg.connect(LOCAL_DB, autocommit=True) as admin:
        killed = admin.execute(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE application_name = 'medguard-api' AND pid <> pg_backend_pid()"
        ).fetchone()[0]
    assert killed >= 4

    resp = client.get("/health/ready")  # 0.5 s DB timeout in tests
    assert resp.status_code == 200, resp.text


@pytest.mark.integration
def test_ready_is_200_with_pgvector_against_local_db(make_client):
    client = make_client(LOCAL_DB)
    resp = client.get("/health/ready")
    assert resp.status_code == 200, resp.text
    checks = resp.json()["checks"]
    assert checks["database"] == "ok"
    assert checks["pgvector"].startswith("0.")
