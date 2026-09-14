"""FastAPI application factory.

`create_app()` builds a fresh app so tests can construct one per test with
different settings. `app` at module level is what uvicorn imports.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from aegisops_api import __version__
from aegisops_api.logging import configure_logging
from aegisops_api.routes import health
from aegisops_api.settings import Settings, get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hook. Day 2: open and close the database engine here."""
    settings: Settings = app.state.settings
    configure_logging(settings)
    log.info("api.start", env=settings.env, version=__version__)
    yield
    log.info("api.stop")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="AegisOps API",
        version=__version__,
        docs_url="/docs" if settings.env != "prod" else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.include_router(health.router)
    return app


app = create_app()
