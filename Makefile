# AegisOps developer interface. CI calls these same targets (PROJECT.md §14, §15).
.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help install lint format typecheck test check api db-up db-down db-migrate db-revision demo-check demo-up demo-down demo-config demo-logs flag clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install all workspace packages and git hooks
	uv sync --all-packages
	uv run pre-commit install

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-fix lint and formatting (format first so line-length fixes land before lint)
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy --strict on all source
	uv run mypy apps/api/src packages/*/src

test: ## Unit + integration tests with coverage
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck test ## Everything CI runs

api: ## Run the API locally with reload
	uv run uvicorn aegisops_api.main:app --app-dir apps/api/src --reload --port 8000

db-up: ## Start local Postgres (pgvector) on port 5433
	docker compose up -d --wait postgres

db-down: ## Stop local Postgres (data kept in the aegis-pgdata volume)
	docker compose stop postgres

db-migrate: ## Apply all migrations (alembic upgrade head)
	cd apps/api && uv run alembic upgrade head

db-revision: ## Create a migration from model changes: make db-revision m="add foo"
	cd apps/api && uv run alembic revision --autogenerate -m "$(m)"

# --- OpenTelemetry Demo (the target system), pinned in infra/otel-demo/VERSION ---------
# The demo checkout is untouched: our collector layer, compose override and the
# version pin live in infra/otel-demo and are passed as extra --env-file / -f.
DEMO_DIR := ../opentelemetry-demo
INFRA := $(CURDIR)/infra/otel-demo
DEMO_COMPOSE := docker compose --env-file .env --env-file .env.override --env-file $(INFRA)/aegisops.env \
	-f compose.yaml -f compose.observability.yaml -f compose.extras.yaml -f $(INFRA)/compose.aegisops.yaml

demo-check: ## Verify the demo checkout matches infra/otel-demo/VERSION and the pin agrees
	@want=$$(cat $(INFRA)/VERSION); have=$$(cd $(DEMO_DIR) && git describe --tags --exact-match 2>/dev/null || echo unknown); \
	pin=$$(sed -n 's/^DEMO_VERSION=//p' $(INFRA)/aegisops.env); \
	if [ "$$have" != "$$want" ] || [ "$$pin" != "$$want" ]; then \
	  echo "demo checkout=$$have pin=$$pin expected=$$want (cd $(DEMO_DIR) && git checkout $$want)"; exit 1; fi; \
	echo "demo $$want ok"

demo-up: demo-check ## Start the OTel Demo (minimal) with our collector layer and overrides
	cd $(DEMO_DIR) && $(DEMO_COMPOSE) up --force-recreate --remove-orphans --detach
	@echo "store http://localhost:8080  flags /feature  jaeger /jaeger/ui  grafana /grafana"
	@echo "collector -> $$(sed -n 's/^AEGISOPS_INGEST_ENDPOINT=//p' $(INFRA)/aegisops.env)  (run 'make api' on the host)"

demo-down: ## Stop and remove the OTel Demo containers
	cd $(DEMO_DIR) && $(DEMO_COMPOSE) down --remove-orphans

demo-config: ## Print the merged compose config (debugging overrides)
	cd $(DEMO_DIR) && $(DEMO_COMPOSE) config

demo-logs: ## Tail the collector's logs (export errors show here)
	docker logs -f --since 2m otel-collector

flag: ## Set a demo feature flag: make flag name=paymentFailure variant=100%   (variant=off to clear)
	@test -n "$(name)" -a -n "$(variant)" || { echo "usage: make flag name=<flag> variant=<variant>"; exit 1; }
	@python3 $(INFRA)/flag.py $(DEMO_DIR)/src/flagd/demo.flagd.json "$(name)" "$(variant)"

clean: ## Remove caches
	rm -rf .venv .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
