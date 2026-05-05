.PHONY: sync run-frontend test lint lint-fix format format-check typecheck check db-migrate db-reset clean help

PROJECT_NAME=lcnc-a2a
SRC_DIR=src

## sync: Install dependencies with uv
sync:
	@uv sync

## run-frontend: Run the FastAPI app on http://localhost:8001
run-frontend: sync
	@uv run uvicorn lcnc_a2a.main:app --reload --host 0.0.0.0 --port 8001

## db-migrate: Run alembic migrations
db-migrate:
	@uv run alembic upgrade head

## db-reset: Drop and recreate the dev database (DEV ONLY)
db-reset:
	@dropdb --if-exists lcnc_a2a
	@createdb lcnc_a2a
	@uv run alembic upgrade head

## test: Run tests with pytest
test:
	@uv run pytest $(ARGS)

## lint: Check code with Ruff
lint:
	@uv run ruff check .

## lint-fix: Auto-fix lint issues
lint-fix:
	@uv run ruff check --fix .

## format: Format code with Ruff
format:
	@uv run ruff format .

## format-check: Check formatting
format-check:
	@uv run ruff format --check .

## typecheck: Run mypy
typecheck:
	@uv run mypy $(SRC_DIR)/

## check: Run lint + format-check + typecheck + test
check: lint format-check typecheck test

## clean: Clean caches and artifacts
clean:
	@rm -rf .pytest_cache .mypy_cache .ruff_cache dist build
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

## help: Show this help message
help:
	@echo "Available targets:"
	@grep -E '^##' Makefile | sed 's/##/ /'
