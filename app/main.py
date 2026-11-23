"""FastAPI entry point.

`create_app()` builds the app; the module-level `app` is what uvicorn serves.
Building it in a function lets tests create a fresh app with their own settings.

Startup and shutdown happen in `lifespan` (FastAPI's replacement for on_event):
anything opened there — the DB pool now, model clients later — is closed there too.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings
from app.db.pool import create_pool

log = logging.getLogger("medguard")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    pool = create_pool(settings)
    # wait=False: start serving even if the DB is down. /health/live stays green,
    # /health/ready reports the outage, and the pool keeps retrying in the background.
    await pool.open(wait=False)
    app.state.db_pool = pool
    log.info("medguard started (environment=%s)", settings.environment)
    try:
        yield
    finally:
        await pool.close()
        log.info("medguard stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="MedGuard AI", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
