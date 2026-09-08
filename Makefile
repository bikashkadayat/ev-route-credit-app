# EV-RCA — developer entry points (Doc 15 §15.6)
# Windows users: run these from Git Bash, or copy the command out of the recipe.

SHELL := /bin/bash
PY    := backend/.venv/Scripts/python.exe          # backend/.venv/bin/python on Linux/macOS
BE    := cd backend &&

.DEFAULT_GOAL := help

.PHONY: help
help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- environment
.PHONY: venv
venv: ## create the backend virtualenv and install dependencies
	python -m venv backend/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r backend/requirements-dev.txt

.PHONY: up
up: ## start PostgreSQL 16 (add PROFILE=full for Redis too)
	docker compose up -d $(if $(PROFILE),--profile $(PROFILE),)
	@echo "waiting for the database to become healthy ..."
	@until docker compose exec -T db pg_isready -U evrca -d evrca >/dev/null 2>&1; \
	  do sleep 1; done
	@echo "database ready on localhost:$${POSTGRES_PORT:-5432}"

.PHONY: down
down: ## stop the stack (keeps volumes)
	docker compose down

.PHONY: reset-db
reset-db: ## destroy and recreate the database volume
	docker compose down -v
	$(MAKE) up

# ---------------------------------------------------------------- database
.PHONY: migrate
migrate: ## apply all migrations
	$(BE) DATABASE_URL="$${DATABASE_URL:-postgresql+psycopg://evrca:evrca@localhost:5432/evrca}" \
	  .venv/Scripts/alembic upgrade head

.PHONY: migrate-down
migrate-down: ## roll the schema back to empty
	$(BE) DATABASE_URL="$${DATABASE_URL:-postgresql+psycopg://evrca:evrca@localhost:5432/evrca}" \
	  .venv/Scripts/alembic downgrade base

.PHONY: seed
seed: ## load reference data (required in every environment)
	docker compose exec -T db psql -U evrca -d evrca -v ON_ERROR_STOP=1 \
	  < db/seed/01_reference_data.sql

.PHONY: seed-demo
seed-demo: seed ## load reference data AND the demo pack (non-production only)
	docker compose exec -T db psql -U evrca -d evrca -v ON_ERROR_STOP=1 \
	  < db/seed/02_demo_data.sql

.PHONY: verify-db
verify-db: ## apply schema+seeds to a throwaway PostgreSQL and assert their behaviour
	$(BE) ../$(PY) scripts/verify_schema.py

# ---------------------------------------------------------------- generation
.PHONY: score-routes
score-routes: ## re-score the demo corridors with the real engine
	$(BE) PYTHONPATH=. ../$(PY) scripts/compute_demo_routes.py

.PHONY: regen-seed
regen-seed: score-routes ## regenerate db/seed/02_demo_data.sql from engine output
	$(BE) ../$(PY) scripts/generate_demo_seed.py

# ---------------------------------------------------------------- quality
.PHONY: test
test: ## run the whole test suite
	$(BE) ../$(PY) -m pytest tests -q

.PHONY: test-unit
test-unit: ## unit tests only (fast, no database)
	$(BE) ../$(PY) -m pytest tests/unit -q

.PHONY: coverage
coverage: ## test with coverage, enforcing the engine and overall gates
	$(BE) ../$(PY) -m pytest tests -q \
	  --cov=app --cov-report=term-missing --cov-fail-under=70

.PHONY: coverage-engines
coverage-engines: ## coverage of the pure engines only (gate: 90%)
	$(BE) ../$(PY) -m pytest tests -q \
	  --cov=app/engines --cov=app/risk --cov-report=term-missing --cov-fail-under=90

.PHONY: lint
lint: ## ruff + mypy
	$(BE) ../$(PY) -m ruff check app tests scripts
	$(BE) ../$(PY) -m mypy app/engines app/risk

.PHONY: fmt
fmt: ## auto-fix what ruff can
	$(BE) ../$(PY) -m ruff check --fix app tests scripts

.PHONY: check
check: lint test verify-db ## everything CI runs
	@echo "all gates passed"
