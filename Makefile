UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
NPM_CACHE_DIR ?= $(CURDIR)/.cache/npm
export UV_CACHE_DIR

.PHONY: install check test db up down api web schemas migrate research-demo
install:
	uv sync --frozen
	npm --prefix apps/web ci --cache $(NPM_CACHE_DIR)
check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen mypy
	uv run --frozen pytest
	npm --prefix apps/web run lint
	npm --prefix apps/web run typecheck
	npm --prefix apps/web test
	npm --prefix apps/web run build
test:
	uv run --frozen pytest
db:
	docker compose -f infra/docker-compose.yml up -d --wait db
up:
	docker compose -f infra/docker-compose.yml up -d --build --wait
down:
	docker compose -f infra/docker-compose.yml down
api:
	LIVE_TRADING_ENABLED=false TRADING_MODE=PAPER uv run --frozen uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload
web:
	npm --prefix apps/web run dev
schemas:
	uv run --frozen python -m scripts.generate_schemas

migrate:
	uv run --frozen alembic upgrade head

# Use a new output path on each run; existing reports are never overwritten.
research-demo:
	uv run --frozen python -m scripts.run_backtest --demo --output $(OUTPUT)

# Dedicated synthetic PAPER account and new output directory are mandatory.
paper-demo:
	uv run --frozen python -m scripts.run_paper --demo --policy configs/paper-sandbox.example.yaml --account $(ACCOUNT) --output $(OUTPUT)
