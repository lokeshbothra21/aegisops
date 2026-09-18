# AegisOps API image (E11.3). Multi-stage: uv resolves and installs into a venv in the
# builder; the runtime stage is python:3.13-slim with only that venv, non-root, port 8080.
# Build context is the repo root (uv workspace: apps/api + packages/*).

FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_FROZEN=1
WORKDIR /app
# Dependency layer first so it is cached while source changes.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/agent/pyproject.toml packages/agent/pyproject.toml
COPY packages/tools/pyproject.toml packages/tools/pyproject.toml
COPY packages/alerts/pyproject.toml packages/alerts/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --no-dev --no-install-workspace
# Now the source; install the workspace packages themselves.
COPY apps apps
COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --no-dev

FROM python:3.13-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1001 --create-home aegis
WORKDIR /app
COPY --from=builder --chown=aegis:aegis /app /app
COPY --chown=aegis:aegis apps/api/alembic.ini apps/api/alembic.ini
COPY --chmod=755 infra/docker/entrypoint.sh /entrypoint.sh
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PORT=8080 AEGIS_ENV=prod AEGIS_LOG_JSON=true
USER aegis
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD curl -fsS http://localhost:8080/healthz || exit 1
ENTRYPOINT ["/entrypoint.sh"]
