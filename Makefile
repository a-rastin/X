# X-INSIGHT development loop (S01 bootstrap).
#
# Local ports:
#   backend API  http://localhost:8000  (health: /api/v1/health)
#   web dev      http://localhost:5173  (proxies /api to :8000)
#   db (local)   localhost:5432 (database x_insight)
#   db-test      localhost:5433 (database x_insight_test)
#
# Environment placeholders live in `.env.example` (copy to `.env`, never commit it).
# Stop the stack with `make stop` (== `docker compose down`).
# Volumes are disposable: `docker compose down -v` drops local and test data.
#
# Pinned runtimes (verified by install smoke in S01):
#   Python 3.12.14, Node 22.23.2 / npm 10.9.8, PostgreSQL 16 (postgres:16-alpine; verified 16.15)
#   fastapi 0.142.2, uvicorn 0.54.0, pydantic 2.13.5, httpx 0.28.1,
#   mcp 2.3.0 (MCPServer API), pgmpy 1.1.2, pytest 9.1.1,
#   ruff 0.16.10, mypy 2.4.0,
#   react 19.3.0, vite 8.3.2, typescript 7.0.2,
#   @vitejs/plugin-react 6.1.1, @playwright/test 1.63.0, @types/node 26.6.4

.PHONY: setup dev stop migrate check test-backend test-web test-e2e test-recovery test-load verify help

TEST ?=
E2E_BASE_URL ?= http://localhost:5173

help:
	@echo "make setup        # locked dependencies and development prerequisites"
	@echo "make dev          # documented local stack (db + backend :8000 + web :5173)"
	@echo "make stop         # stop the local stack"
	@echo "make migrate      # explicit migration command (no migrations yet in S01)"
	@echo "make check        # formatting/lint/types/build, excluding network access"
	@echo "make test-backend # backend tests (TEST=tests/http/test_health.py for a selector)"
	@echo "make test-web     # web unit tests (no suite yet in S01)"
	@echo "make test-e2e     # browser smoke (TEST=e2e/smoke.spec.ts for a selector)"
	@echo "make test-recovery# recovery drill (no suite yet in S01)"
	@echo "make test-load    # load check (no suite yet in S01)"
	@echo "make verify       # defined offline CI gate; no live provider credentials"

setup:
	cd backend && uv sync --locked --group dev
	cd web && npm ci
	npm ci

dev:
	docker compose up -d db db-test
	@echo "databases up (5432 local, 5433 test). Starting backend :8000 and web :5173."
	@echo "Stop with 'make stop' (Ctrl-C then 'make stop' if running foreground)."
	trap 'kill 0' INT TERM; \
	cd backend && uv run uvicorn x_insight.app:app --host 0.0.0.0 --port 8000 --reload & \
	cd web && npm run dev; \
	wait

stop:
	docker compose down

migrate:
	@echo "migrate: no migrations yet (S01 bootstrap; S02 adds the Alembic entry point)." >&2
	@exit 1

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
	@echo "test-recovery: no recovery suite yet (S01 bootstrap; recovery lands with operations work)." >&2
	@exit 1

test-load:
	@echo "test-load: no load suite yet (S01 bootstrap; synthetic load lands with capacity work)." >&2
	@exit 1

verify:
	$(MAKE) check
	$(MAKE) test-backend
	@echo "verify: offline CI gate passed (check + backend tests + web build); no live provider credentials used."
