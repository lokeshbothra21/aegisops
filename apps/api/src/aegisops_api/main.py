"""FastAPI application factory.

`create_app()` builds a fresh app so tests can construct one per test with
different settings. `app` at module level is what uvicorn imports.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import structlog
from fastapi import FastAPI

from aegisops_api import __version__
from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.errors import install_error_handlers
from aegisops_api.jobs.retention import run_retention
from aegisops_api.jobs.runner import Job, JobRunner
from aegisops_api.jobs.service_edges import derive_recent_hours
from aegisops_api.logging import configure_logging
from aegisops_api.routes import admin, health, ingest
from aegisops_api.settings import Settings, get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: logging + database engine. Shutdown: return connections to the pool."""
    settings: Settings = app.state.settings
    configure_logging(settings)
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    runner = build_job_runner(app)
    app.state.jobs = runner
    if settings.jobs_enabled:
        runner.start()
    log.info("api.start", env=settings.env, version=__version__, jobs=settings.jobs_enabled)
    try:
        yield
    finally:
        await runner.stop()
        await engine.dispose()
        log.info("api.stop")


def build_job_runner(app: FastAPI) -> JobRunner:
    settings: Settings = app.state.settings
    retention = timedelta(hours=settings.retention_hours)
    return JobRunner(
        factory=app.state.session_factory,
        jobs=[
            Job(
                "retention",
                lambda s: run_retention(s, older_than=retention),
                settings.retention_interval_s,
            ),
            Job(
                "service_edges", lambda s: derive_recent_hours(s), settings.service_edges_interval_s
            ),
        ],
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="AegisOps API",
        version=__version__,
        docs_url="/docs" if settings.env != "prod" else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(admin.router)
    return app


app = create_app()
