# X-INSIGHT development loop (S01 bootstrap).
#
# Local ports:
#   backend API  http://localhost:8000  (health: /api/v1/health)
#   web dev      http://localhost:5173  (proxies /api to :8000)
#   db (local)   localhost:5432 (database x_insight)
#   db-test      localhost:5433 (database x_insight_test)
#
# Environment placeholders live in `.env.example` (copy to `.env`, never commit it).
# If host PostgreSQL occupies 5432/5433, rerun with overrides: DB_PORT=5442 TEST_DB_PORT=5443 make dev.
# Stop the stack with `make stop` (== `docker compose down`).
# Volumes are disposable: `docker compose down -v` drops local and test data.
#
# Pinned runtimes (verified by install smoke in S01):
#   Python 3.12.14, Node 22.23.2 / npm 10.9.8, PostgreSQL 16 (postgres:16-alpine; verified 16.15)
#   fastapi 0.142.2, uvicorn 0.54.0, pydantic 2.13.5, httpx 0.28.1,
#   mcp 2.3.0 (MCPServer API), pgmpy 1.1.2, pytest 9.1.1,
#   sqlalchemy 2.1.3, alembic 1.20.0, psycopg 3.3.6 (binary),
#   ruff 0.16.10, mypy 2.4.0,
#   react 19.3.0, vite 8.3.2, typescript 7.0.2,
#   @vitejs/plugin-react 6.1.1, @playwright/test 1.63.0, @types/node 26.6.4

.PHONY: setup dev stop migrate check test-backend test-web test-e2e test-recovery test-load verify help prod-build prod-up prod-migrate prod-stop prod-logs prod-check

TEST ?=
E2E_BASE_URL ?= http://localhost:5173

help:
	@echo "make setup        # locked dependencies and development prerequisites"
	@echo "make dev          # documented local stack (db + backend :8000 + web :5173)"
	@echo "make stop         # stop the local stack"
	@echo "make migrate      # apply schema + bundled approved DDI data (DATABASE_URL or .env)"
	@echo "make check        # formatting/lint/types/build, excluding network access"
	@echo "make test-backend # backend tests (TEST=tests/http/test_health.py for a selector)"
	@echo "make test-web     # web unit tests (no suite yet in S01)"
	@echo "make test-e2e     # browser smoke (TEST=e2e/smoke.spec.ts for a selector)"
	@echo "make test-recovery # recovery drill (pytest tests/recovery)"
	@echo "make test-load    # load check (no suite yet in S01)"
	@echo "make verify       # defined offline CI gate; no live provider credentials"
	@echo "make prod-build   # build pinned production images (app + edge)"
	@echo "make prod-up       # start one-host production stack (db + app + worker + edge)"
	@echo "make prod-migrate  # explicit production migration step (never destructive)"
	@echo "make prod-stop     # stop the production stack (keeps database volumes)"
	@echo "make prod-logs     # tail production app/worker/edge logs"
	@echo "make prod-check    # production packaging self-check (deploy/check.sh)"

setup:
	cd backend && uv sync --locked --group dev
	cd web && npm ci
	npm ci

dev: SHELL := /bin/bash
dev:
	@set -eu; \
	if [ ! -f .env ]; then echo "missing .env: copy .env.example to .env and set POSTGRES_PASSWORD" >&2; exit 1; fi; \
	declare -A dev_overrides; \
	for name in DATABASE_URL TEST_DATABASE_URL DB_PORT TEST_DB_PORT BACKEND_PORT WEB_PORT POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB TEST_POSTGRES_DB DDI_DEFAULT_DATASET_VERSION X_INSIGHT_PROVIDER_DATA_KEY X_INSIGHT_PROVIDER_DATA_KEY_FILE X_INSIGHT_PROVIDER_ALLOWED_HOSTS; do \
	if [[ -v $$name ]]; then dev_overrides[$$name]=$${!name}; fi; \
	done; \
	set -a; . ./.env; set +a; \
	for name in "$${!dev_overrides[@]}"; do export "$$name=$${dev_overrides[$$name]}"; done; \
	export DB_PORT=$${DB_PORT:-5432} TEST_DB_PORT=$${TEST_DB_PORT:-5433}; \
	export BACKEND_PORT=$${BACKEND_PORT:-8000} WEB_PORT=$${WEB_PORT:-5173}; \
	export API_PROXY_TARGET=$${API_PROXY_TARGET:-http://localhost:$$BACKEND_PORT}; \
	for pair in "$$DB_PORT:db" "$$TEST_DB_PORT:db-test"; do \
	port=$${pair%%:*}; svc=$${pair##*:}; \
	if (echo >/dev/tcp/127.0.0.1/$$port) 2>/dev/null && [ -z "$$(docker compose ps --status running -q "$$svc")" ]; then \
	echo "port $$port ($$svc) is occupied (host PostgreSQL likely); stop host PG or rerun with e.g. DB_PORT=5442 TEST_DB_PORT=5443 make dev" >&2; exit 1; \
	fi; \
	done; \
	docker compose up -d --wait db db-test; \
	(cd backend && uv run python -m x_insight.operations.migrate); \
	echo "database and approved DDI ready. Starting backend :$$BACKEND_PORT and web :$$WEB_PORT."; \
	echo "Stop with 'make stop' (Ctrl-C then 'make stop' if running foreground)."; \
	api_pid=; web_pid=; \
	cleanup() { \
	for pid in "$$api_pid" "$$web_pid"; do \
	if [ -n "$$pid" ]; then \
	kill "$$pid" 2>/dev/null || true; \
	kill -- "-$$pid" 2>/dev/null || true; \
	fi; \
	done; \
	for pid in "$$api_pid" "$$web_pid"; do \
	if [ -n "$$pid" ]; then wait "$$pid" 2>/dev/null || true; fi; \
	done; \
	}; \
	trap cleanup EXIT; \
	trap 'exit 130' INT; trap 'exit 143' TERM; \
	setsid bash -c 'cd backend && exec uv run uvicorn x_insight.app:app --host 0.0.0.0 --port "$$BACKEND_PORT" --reload' & api_pid=$$!; \
	setsid bash -c 'cd web && exec npm run dev -- --port "$$WEB_PORT" --strictPort' & web_pid=$$!; \
	wait -n "$$api_pid" "$$web_pid"

stop:
	docker compose down

migrate:
	set -a; if [ -f .env ]; then . ./.env; fi; cd backend && uv run python -m x_insight.operations.migrate

check:
	cd backend && uv run ruff format --check src tests
	cd backend && uv run ruff check src tests
	cd backend && uv run mypy src
	cd web && npm run build
	npx tsc --noEmit

test-backend:
ifdef TEST
	cd backend && uv run pytest $(TEST)
else
	cd backend && uv run pytest tests
endif

test-web:
	@echo "test-web: no web unit suite yet (S01 smoke is e2e/smoke.spec.ts via 'make test-e2e')." >&2
	@exit 1

test-e2e:
ifdef TEST
	npm --prefix web run build >/dev/null
	npm --prefix web run preview -- --port 5173 --strictPort >/tmp/x-insight-preview.log 2>&1 & sleep 3; E2E_BASE_URL=$(E2E_BASE_URL) npx playwright test --config playwright.config.ts $(TEST); STATUS=$$?; pkill -f "[v]ite preview" || true; exit $$STATUS
else
	npm --prefix web run build >/dev/null
	npm --prefix web run preview -- --port 5173 --strictPort >/tmp/x-insight-preview.log 2>&1 & sleep 3; E2E_BASE_URL=$(E2E_BASE_URL) npx playwright test --config playwright.config.ts; STATUS=$$?; pkill -f "[v]ite preview" || true; exit $$STATUS
endif

test-recovery:
	cd backend && uv run pytest tests/recovery

test-load:
	set -a; if [ -f .env ]; then . ./.env; fi; set +a; \
	cd backend && uv run python -m x_insight.operations.load_probe \
		--patients $${LOAD_PATIENTS:-50} --workers $${LOAD_WORKERS:-4} \
		--provider-samples $${LOAD_PROVIDER_SAMPLES:-5} \
		--output $${LOAD_REPORT:-/tmp/opencode/x-insight-load-report.json}

verify:
	$(MAKE) check
	$(MAKE) test-backend
	$(MAKE) test-e2e
	@echo "verify: offline CI gate passed (check + backend tests + web build + browser smoke); no live provider credentials used."

# One-host production (S57; see compose.prod.yaml + deploy/README.md).
# Requires a prod env file: POSTGRES_PASSWORD at minimum (see .env.example).
# Order on a clean host: prod-build, prod-up (automatic schema + DDI setup).

# Prod env lives in .env.prod (never commit it); the local dev .env is untouched.
PROD_ENV_FILE ?= .env.prod
PROD_COMPOSE = docker compose -f compose.prod.yaml --env-file $(PROD_ENV_FILE)

prod-build:
	$(PROD_COMPOSE) build

prod-up:
	$(PROD_COMPOSE) up -d db app worker edge
	@echo "production stack starting after automatic schema and approved DDI setup."

prod-migrate:
	$(PROD_COMPOSE) run --rm migrate

prod-stop:
	$(PROD_COMPOSE) down
	@echo "production stack stopped; database volumes kept (never 'down -v' in production)."

prod-logs:
	$(PROD_COMPOSE) logs --tail=200 app worker edge

prod-check:
	./deploy/check.sh
