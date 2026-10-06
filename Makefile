# Tycheon developer entrypoints. `make check` is the gate: it must pass before
# any claim that something works, and it is exactly what CI runs.
.DEFAULT_GOAL := help

UV      ?= uv
COMPOSE ?= docker compose -f docker-compose.dev.yml
# The dev environment includes the `kronos` extra (CPU torch): the fast tests run
# the real Kronos code path on a tiny randomly-initialised model, and mypy needs
# torch installed to give the same answer locally and in CI.
EXTRAS  ?= --extra kronos --extra report --extra agents
RUN     ?= $(UV) run $(EXTRAS)

.PHONY: help setup setup-all lock fmt lint format-check types test test-slow \
        check up down restart logs health hooks secrets-baseline \
        benchmark-small benchmark benchmark-render example docs docs-build build clean

help: ## Show this help
	@awk 'BEGIN{FS=":.*?## "} /^[a-zA-Z_-]+:.*?## /{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

# ------------------------------------------------------------------- setup
setup: ## Create the dev venv (with CPU torch for Kronos) and install git hooks
	$(UV) sync $(EXTRAS)
	$(UV) run pre-commit install
	@echo "ready — run 'make check'"

setup-all: ## Install every optional extra and the example/doc groups
	$(UV) sync --all-extras --group examples --group docs
	$(UV) run pre-commit install

lock: ## Refresh uv.lock (resolves base deps, all extras and all groups)
	$(UV) lock

# -------------------------------------------------------------------- gate
fmt: ## Format the codebase
	$(RUN) ruff format .

lint: ## Lint (ruff check)
	$(RUN) ruff check .

format-check: ## Verify formatting without writing
	$(RUN) ruff format --check .

types: ## Type-check src/ with mypy --strict
	$(RUN) mypy

test: ## Run the fast test suite with coverage
	$(RUN) pytest -m "not slow"

test-slow: ## Run the slow suite too (downloads Kronos-mini weights from Hugging Face)
	$(RUN) pytest

check: lint format-check types test ## Lint, format-check, type-check and fast tests
	@echo "make check: PASS"

# ---------------------------------------------------------- dev services
up: ## Start Postgres, Redis, MinIO and Jaeger, waiting for health
	$(COMPOSE) up -d --wait
	$(MAKE) health

down: ## Stop dev services and delete their volumes
	$(COMPOSE) down -v

restart: down up ## Recreate dev services from scratch

logs: ## Tail dev service logs
	$(COMPOSE) logs -f

health: ## Probe each dev service from the host
	$(COMPOSE) ps
	@echo "--- endpoint probes ---"
	@pg_isready -h localhost -p 5432 -U tycheon >/dev/null 2>&1 && echo "postgres :5432 ok" || echo "postgres :5432 UNREACHABLE (or pg_isready not installed)"
	@curl -fsS -o /dev/null http://localhost:9000/minio/health/live && echo "minio    :9000 ok" || echo "minio    :9000 UNREACHABLE"
	@curl -fsS -o /dev/null http://localhost:16686/ && echo "jaeger   :16686 ok" || echo "jaeger   :16686 UNREACHABLE"
	@curl -fsS -o /dev/null http://localhost:14269/ && echo "jaeger admin :14269 ok" || echo "jaeger admin :14269 UNREACHABLE"

# --------------------------------------------------------------- benchmarks
benchmark-small: ## Run the small benchmark on CPU (sample data) and render the leaderboard
	$(RUN) python -m benchmarks.run --config benchmarks/configs/small.yaml --execute

benchmark: ## Run the full benchmark (needs the timesfm/chronos extras and model downloads)
	$(UV) run $(EXTRAS) --extra timesfm --extra chronos python -m benchmarks.run --config benchmarks/configs/full.yaml --execute

benchmark-render: ## Re-render docs/leaderboard from the published results
	$(RUN) python -m benchmarks.render

example: ## Kronos-small forecast beside the random walk; saves examples/output/forecast.png
	$(UV) run $(EXTRAS) --group examples python examples/forecast.py

# -------------------------------------------------------------- hygiene
hooks: ## Run every pre-commit hook over all files
	$(RUN) pre-commit run --all-files

secrets-baseline: ## Regenerate the detect-secrets baseline
	$(UV) run detect-secrets scan --baseline .secrets.baseline

# ------------------------------------------------------------------ docs
docs: ## Serve the docs locally
	$(UV) run --group docs mkdocs serve

docs-build: ## Build the docs strictly (warnings are errors)
	$(UV) run --group docs mkdocs build --strict

build: ## Build the wheel and sdist
	$(UV) build

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis htmlcov site dist build
	rm -f .coverage coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
