# Common tasks. `make check` is what CI runs; run it before you push.
.PHONY: help install check format lint types security test test-pg web e2e docs gen-docs serve

help:            ## Show this help
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:         ## Install backend, docs and web dependencies
	uv sync --all-groups --all-extras
	cd web && npm ci

check: lint types security test web gen-docs docs  ## Everything CI checks (except e2e and PostgreSQL)

format:          ## Format the code
	uv run ruff format src tests scripts examples

lint:            ## Formatting and lint
	uv run ruff format --check src tests scripts examples
	uv run ruff check src tests scripts examples

types:           ## Static types
	uv run mypy

security:        ## Security lint and dependency audits
	uv run bandit -q -r src -c pyproject.toml
	uv run pip-audit
	cd web && npm audit --omit=dev --audit-level=high

test:            ## Backend tests (SQLite)
	uv run pytest

test-pg:         ## Backend tests on PostgreSQL (set ZENTRAL_TEST_DATABASE_URL)
	@test -n "$$ZENTRAL_TEST_DATABASE_URL" || (echo "set ZENTRAL_TEST_DATABASE_URL" && exit 1)
	uv run pytest

web:             ## Web client: types, unit tests, build
	cd web && npm test && npm run build

e2e:             ## Browser end-to-end test against a throw-away instance
	scripts/e2e.sh

gen-docs:        ## Check that generated reference tables are current
	uv run python scripts/gen_docs.py --check

docs:            ## Build the documentation (strict)
	NO_MKDOCS_2_WARNING=1 uv run mkdocs build --strict

serve:           ## Run a local development server
	uv run zentral serve
