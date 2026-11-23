"""Async Postgres connection pool (psycopg 3).

Why a pool: opening a Postgres connection costs a TCP + TLS + auth handshake (tens of
ms, more over the network to Cloud SQL). A pool keeps a few connections open and lends
them out per request.

Why psycopg 3 (not asyncpg): the same driver serves the API, the pgvector adapter and
LangGraph's Postgres checkpointer, so there is one driver's behaviour to understand.

Sizing matters later (M14): Cloud Run instances × max_size must stay under Cloud SQL's
connection limit, or the 21st clinic gets "too many connections".
"""

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.config import Settings


def create_pool(settings: Settings) -> AsyncConnectionPool:
    pool: AsyncConnectionPool | None = None

    async def check(conn: AsyncConnection) -> None:
        """Test a connection before lending it out (see docs/FAILURES.md, 2026-11-23).

        1. Without any check, after a DB restart or failover the first request gets a
           dead connection and fails even though the database is back.
        2. With psycopg_pool's plain check, dead connections are found one at a time,
           with 0 s, 1 s, 2 s, 4 s… of backoff between them, so a pool of k dead
           connections makes the next request wait about 2^(k-1) - 1 seconds.

        So: if one connection is dead, assume they all are (that's what a restart
        does) and purge every idle connection in one pass before retrying.
        """
        try:
            await AsyncConnectionPool.check_connection(conn)
        except Exception:
            if pool is not None:
                await pool.check()  # tests idle conns, drops dead ones, refills
            raise  # the pool discards `conn` and immediately retries with a fresh one

    pool = AsyncConnectionPool(
        conninfo=str(settings.database_url),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        # Opened explicitly in the app lifespan, not at import time.
        open=False,
        check=check,
        kwargs={
            "connect_timeout": settings.db_connect_timeout_s,
            "application_name": "medguard-api",  # shows up in pg_stat_activity
        },
        name="medguard",
    )
    return pool
