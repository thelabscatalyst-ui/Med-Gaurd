"""Health endpoints, used by Cloud Run probes and by humans.

Two endpoints, because they answer two different questions:

  /health/live   "Is the process alive?"  No dependencies are checked. If this fails,
                 the platform should RESTART the container.

  /health/ready  "Can it do useful work right now?"  Checks the database and that the
                 pgvector extension is installed. If this fails, the platform should
                 stop sending traffic — but NOT restart: a 30-second database blip must
                 not turn into every instance restarting at once (a restart storm).

Error details are reduced to the exception class name: a public health endpoint must
not leak hostnames or connection strings.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    settings = get_settings()
    pool = request.app.state.db_pool
    checks: dict[str, str] = {}
    try:
        async with pool.connection(timeout=settings.health_db_timeout_s) as conn:
            cur = await conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = await cur.fetchone()
        checks["database"] = "ok"
        checks["pgvector"] = row[0] if row else "missing"
    except Exception as exc:  # any failure means "not ready", never a 500
        log.warning("readiness check failed: %s", type(exc).__name__)
        checks["database"] = f"error: {type(exc).__name__}"

    is_ready = checks.get("database") == "ok" and checks.get("pgvector") != "missing"
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ok" if is_ready else "unavailable", "checks": checks},
    )
