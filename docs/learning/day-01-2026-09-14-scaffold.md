# Day 1 · 14 Sep 2026 · Repository scaffold (PR #1, E11.1, E8.4)

## What we did
Created the monorepo, the FastAPI service with health probes, the Makefile, linting/typing/testing setup, pre-commit hooks, PR template and licence.

## Terms introduced

**Monorepo.** One git repository holding several packages/apps (`apps/api`, `packages/agent`, `packages/tools`, `packages/alerts`). Cross-package changes land in one PR; one CI; one version history.

**uv and uv workspace.** `uv` is a fast Python package/project manager (replaces pip, virtualenv, pip-tools, pyenv). A *workspace* is uv's way of managing several packages in one repo with one lockfile (`uv.lock`). `uv sync --all-packages` installs everything into one `.venv`.

**Lockfile.** An exact record of every dependency version (and hash) that was resolved. Committing `uv.lock` means CI, laptop and the container install identical bits. `UV_FROZEN=1` makes uv refuse to change it.

**pyproject.toml.** The standard Python project file: metadata, dependencies, build backend, and tool config (ruff, mypy, pytest, coverage all read from it).

**hatchling.** The build backend that turns a package directory into an installable wheel. Configured per package.

**FastAPI.** An async Python web framework. Request/response bodies are Pydantic models, so validation and the OpenAPI schema (`/docs`) come for free. **uvicorn** is the ASGI server that runs it.

**ASGI.** Asynchronous Server Gateway Interface, the async successor to WSGI: the contract between a Python web app and the server.

**Async / await.** Cooperative concurrency: while one request waits on the database, the event loop serves another. Our DB driver (`asyncpg`) and SQLAlchemy engine are async.

**Pydantic v2.** Data validation from type hints. Every boundary (HTTP body, settings, LLM output later) is a Pydantic model, so bad data fails fast with a precise error. `pydantic-settings` reads settings from environment variables.

**12-factor config.** Configuration comes from environment variables, never from code or committed files. `AEGIS_DATABASE_URL` etc.; `.env.example` is committed, `.env` is ignored.

**Application factory pattern.** `create_app(settings)` builds a fresh app instance; tests construct one per test with different settings instead of sharing a global.

**Lifespan.** FastAPI's startup/shutdown hook: create the DB engine on start, dispose it on stop.

**Liveness vs readiness probes.** `/livez` answers "the process is up" (restart me if not). `/readyz` answers "I can serve traffic" (dependencies OK; today: Postgres answers `SELECT 1`), returning 503 otherwise so the platform stops routing to a broken instance. Originally `/healthz`; renamed on Day 5 (ADR-015).

**structlog.** Structured logging: each log line is key=value pairs (or JSON in prod), so Cloud Logging can index `run_id`, `service`, etc. `bind_contextvars` attaches a request id to every line in that request.

**Ruff.** A very fast linter *and* formatter (replaces flake8, isort, black). Rules enabled: pyflakes, pycodestyle, isort, bugbear, pyupgrade, bandit-security, naming, async, ruff-specific.

**mypy --strict.** Static type checking with every optional strictness flag on: no untyped defs, no implicit Any, etc. Catches bugs before runtime and documents intent.

**pytest, pytest-asyncio, pytest-cov.** The test runner; async test support (`asyncio_mode = auto`); coverage measurement. Coverage gate for core packages is ≥ 80 %.

**pre-commit.** A framework that runs checks on `git commit`: whitespace, YAML/TOML syntax, large files, merge markers, private keys, ruff, and **detect-secrets** with a baseline file so known false positives are allow-listed (`# pragma: allowlist secret`).

**Makefile as developer interface.** `make check` = lint + typecheck + test. CI runs the same targets, so "green locally" means "green in CI".

**Conventional Commits.** `type(scope): message`, e.g. `feat(db): ...`, `fix(api): ...`, `ci: ...`, `docs: ...`, `chore(deps): ...`. Enables generated changelogs and semantic versioning.

**Squash merge.** All commits on a PR branch become one commit on main. History reads as one line per feature.

**CODEOWNERS / PR template.** GitHub conventions: who reviews which paths; a fixed PR body (What / Why / How tested / Checklist) so every PR is documented the same way.

**Apache-2.0.** A permissive open-source licence with an explicit patent grant. Chosen so anyone can reuse the project; it also drives the dependency-review rule that blocks copyleft licences.

## Interview questions
1. *Why uv over pip/poetry?* Speed (10–100×), one tool for Python versions, venvs and lockfiles, first-class workspaces.
2. *Why strict mypy in a solo project?* Types are documentation and a refactoring safety net; the agent code will have dozens of Pydantic schemas crossing node boundaries.
3. *Difference between liveness and readiness?* Liveness = restart me; readiness = don't send me traffic yet. Conflating them causes restart loops when a dependency is down.
4. *What does an application factory buy you?* Isolated tests with different settings, no import-time side effects.
