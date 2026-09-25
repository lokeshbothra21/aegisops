"""FastAPI application factory.

`create_app()` builds a fresh app so tests can construct one per test with
different settings. `app` at module level is what uvicorn imports.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from aegisops_agent.actions import ActionExecutor, AuditWriter, LiveBackend, ReplayBackend
from aegisops_agent.llm import LLMClient, RecordedLLM, Router, load_models_config
from aegisops_agent.remediation import load_policy
from aegisops_agent.run import psycopg_url
from aegisops_api import __version__
from aegisops_api.alerts.evaluator import Evaluator, ensure_default_rules, load_rules_file
from aegisops_api.db import create_engine, create_session_factory, session_scope
from aegisops_api.errors import install_error_handlers
from aegisops_api.jobs.container_watcher import ContainerWatcher
from aegisops_api.jobs.flag_watcher import FlagWatcher
from aegisops_api.jobs.retention import run_retention
from aegisops_api.jobs.runner import Job, JobRunner
from aegisops_api.jobs.service_edges import derive_recent_hours
from aegisops_api.logging import configure_logging
from aegisops_api.routes import admin, health, incidents, ingest, runs, scenarios
from aegisops_api.runs.service import RunManager
from aegisops_api.settings import Settings, get_settings
from aegisops_api.targets import load_target

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: logging + database engine. Shutdown: return connections to the pool."""
    settings: Settings = app.state.settings
    configure_logging(settings)
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    if settings.alerts_enabled:
        await seed_rules(app)
    runner = build_job_runner(app)
    app.state.jobs = runner
    saver_cm = None
    if settings.agent_enabled:
        saver_cm = AsyncPostgresSaver.from_conn_string(psycopg_url(settings.database_url))
        try:
            saver = await saver_cm.__aenter__()
            await saver.setup()
        except Exception as exc:
            log.warning("agent.checkpointer_unavailable", error=repr(exc))
            saver_cm, saver = None, None
        if saver is not None:
            app.state.runs = build_run_manager(app, engine, saver)
            log.info(
                "agent.ready",
                public_mode=settings.public_mode,
                recorded=bool(settings.recorded_llm_path),
            )
    if settings.jobs_enabled:
        runner.start()
    log.info("api.start", env=settings.env, version=__version__, jobs=settings.jobs_enabled)
    try:
        yield
    finally:
        await runner.stop()
        mgr = getattr(app.state, "runs", None)
        if mgr is not None:
            await mgr.stop()
        if saver_cm is not None:
            await saver_cm.__aexit__(None, None, None)
        watcher = getattr(app.state, "container_watcher", None)
        if watcher is not None:
            await watcher.aclose()
        await engine.dispose()
        log.info("api.stop")


async def seed_rules(app: FastAPI) -> None:
    """Insert missing default alert rules. Startup must not depend on the database
    (Cloud Run boots before Supabase exists), so a failure here is a warning; the
    evaluator will simply find no rules until the next restart."""
    settings: Settings = app.state.settings
    try:
        async with session_scope(app.state.session_factory) as session:
            added = await ensure_default_rules(
                session, load_rules_file(settings.alerts_config_path)
            )
        log.info("alerts.rules_seeded", added=added)
    except Exception as exc:
        log.warning("alerts.rules_seed_failed", error=repr(exc))


def build_run_manager(app: FastAPI, engine: Any, saver: AsyncPostgresSaver) -> RunManager:
    settings: Settings = app.state.settings
    models = load_models_config(settings.models_config_path)

    def llm_factory() -> LLMClient:
        if settings.recorded_llm_path:
            return RecordedLLM.from_file(settings.recorded_llm_path)
        return Router.from_env(models)

    policy = load_policy(settings.policy_config_path)
    live = None
    if settings.execute_enabled and not settings.public_mode and settings.flagd_config_path:
        live = LiveBackend(
            flagd_path=Path(settings.flagd_config_path),
            docker_socket=settings.docker_socket,
            compose_project=settings.docker_compose_project,
        )
    executor = ActionExecutor(
        policy=policy,
        replay=ReplayBackend(),
        live=live,
        audit=AuditWriter(app.state.session_factory),
    )
    return RunManager(
        executor=executor,
        verify_delay_s=settings.verify_delay_s,
        engine=engine,
        factory=app.state.session_factory,
        llm_factory=llm_factory,
        policy=policy,
        checkpointer=saver,
        prices=models.prices,
        public_mode=settings.public_mode,
    )


def build_job_runner(app: FastAPI) -> JobRunner:
    settings: Settings = app.state.settings
    retention = timedelta(hours=settings.retention_hours)
    runner = JobRunner(
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
    if settings.flagd_config_path:
        watcher = FlagWatcher(
            path=Path(settings.flagd_config_path), target=load_target(settings.target_config_path)
        )
        runner.jobs.append(Job("flag_watcher", watcher.tick, settings.flag_watch_interval_s))
    if settings.docker_socket:
        containers = ContainerWatcher(
            socket_path=settings.docker_socket, project=settings.docker_compose_project
        )
        app.state.container_watcher = containers
        runner.jobs.append(
            Job("container_watcher", containers.tick, settings.container_watch_interval_s)
        )
    if settings.alerts_enabled:
        evaluator = Evaluator(recovery_windows=settings.alert_recovery_windows)
        app.state.evaluator = evaluator
        runner.jobs.append(Job("alerts", evaluator.tick, settings.alert_interval_s))
    return runner


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
    app.include_router(incidents.router)
    app.include_router(scenarios.router)
    app.include_router(runs.router)
    return app


app = create_app()
