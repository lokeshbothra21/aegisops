# AegisOps developer interface. CI calls these same targets (PROJECT.md §14, §15).
.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help install lint format typecheck test check api demo-up demo-down clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install all workspace packages and git hooks
	uv sync --all-packages
	uv run pre-commit install

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-fix lint and formatting
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy --strict on all source
	uv run mypy apps/api/src packages/*/src

test: ## Unit + integration tests with coverage
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck test ## Everything CI runs

api: ## Run the API locally with reload
	uv run uvicorn aegisops_api.main:app --app-dir apps/api/src --reload --port 8000

DEMO_DIR := ../opentelemetry-demo
demo-up: ## Start the OTel Demo (minimal) with our overrides
	cd $(DEMO_DIR) && make start-minimal
	docker update --memory 400m --memory-swap 400m grafana >/dev/null && docker restart grafana >/dev/null
	@echo "store http://localhost:8080  flags /feature  jaeger /jaeger/ui  grafana /grafana"

demo-down: ## Stop the OTel Demo
	cd $(DEMO_DIR) && make stop

clean: ## Remove caches
	rm -rf .venv .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
