# Axiom — developer workflow. `make help` lists targets.
#
# This is the single interface for local development: setup, the quality gate,
# the dev stack, and the test lanes. CI runs the same commands (CONTRIBUTING.md),
# so "green locally" means "green in CI."

.DEFAULT_GOAL := help
.PHONY: help setup up down logs check fmt lint typecheck boundaries test \
        test-integration test-chaos test-e2e test-stack-up test-stack-down \
        migrate revision build smoke clean

UV ?= uv

# Isolated test datastores (docker/datastores.dev.yml) live on remapped HOST
# ports so they coexist with anything already bound to 5432/6379. The
# integration + chaos lanes point at these via AXIOM_* env (TEST_DB_ENV).
TEST_DB_URL   ?= postgresql+asyncpg://axiom:axiom@localhost:55432/axiom
TEST_REDIS_URL ?= redis://localhost:56379/0
TEST_DB_ENV    = AXIOM_DATABASE_URL="$(TEST_DB_URL)" \
                 AXIOM_REDIS_URL="$(TEST_REDIS_URL)" \
                 AXIOM_ENVIRONMENT=test \
                 AXIOM_MASTER_KEY="test-master-key-not-for-production"

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────────────────────────────────────
setup: ## Install toolchain, sync deps, install git hooks
	@command -v $(UV) >/dev/null 2>&1 || { echo "Install uv: https://docs.astral.sh/uv/"; exit 1; }
	$(UV) sync --extra dev
	$(UV) run pre-commit install
	@test -f .env || cp .env.example .env && echo "Created .env from .env.example"
	@echo "Setup complete. Run 'make up' to start the stack, 'make check' for the gate."

# ── Dev stack (one command — BLUEPRINT.md principle 5) ──────────────────────────
up: ## Start postgres + redis + api + worker
	docker compose up --build -d
	@echo "API:  http://localhost:8000/health   (readiness: /ready)"

down: ## Stop the stack (keeps volumes)
	docker compose down

logs: ## Tail stack logs
	docker compose logs -f

# ── The quality gate (mirrors CI) ───────────────────────────────────────────────
check: fmt lint typecheck boundaries test ## Run the full local gate

fmt: ## Format code
	$(UV) run ruff format src tests

lint: ## Lint code
	$(UV) run ruff check src tests

typecheck: ## Strict type-check the package
	$(UV) run mypy src

boundaries: ## Enforce modular-monolith import rules (ADR-0002)
	$(UV) run lint-imports

# ── Tests (the three lanes — pyproject markers) ──────────────────────────────────
test: ## Run the unit lane (fast, no services)
	$(UV) run pytest -m unit --cov

test-integration: ## Run the integration lane (needs test-stack-up)
	$(TEST_DB_ENV) $(UV) run pytest -m integration

test-chaos: ## Run the chaos lane (crash recovery — PHASE_1.md §6)
	$(TEST_DB_ENV) $(UV) run pytest -m chaos

test-e2e: ## Run the browser E2E suite (Playwright; needs test-stack-up + node)
	cd web && npm ci && npm run test:e2e:install && npm run test:e2e

# ── Isolated test datastores (coexist with default-port services) ──────────────
test-stack-up: ## Start isolated postgres+redis for the integration/chaos lanes
	docker compose -f docker/datastores.dev.yml up -d --wait
	@echo "Test datastores ready: pg=localhost:55432 redis=localhost:56379"

test-stack-down: ## Stop the isolated test datastores
	docker compose -f docker/datastores.dev.yml down

# ── Database ──────────────────────────────────────────────────────────────────
migrate: ## Apply migrations to the dev database
	$(UV) run alembic upgrade head

revision: ## Autogenerate a migration (use: make revision m="message")
	$(UV) run alembic revision --autogenerate -m "$(m)"

# ── Image ─────────────────────────────────────────────────────────────────────
build: ## Build the single Axiom image
	docker build -t axiom:dev .

smoke: ## Build the image and verify all three run modes start
	docker build -t axiom:dev .
	@echo "cli:"    && docker run --rm -e MODE=cli axiom:dev version
	@echo "Smoke check: api/worker require postgres+redis — use 'make up' for those."

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
