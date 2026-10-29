# Overview

X-INSIGHT is a **research prototype** (not clinically validated, release gate open) for
exploring schizophrenia treatment options: structured assessments → versioned Bayesian
networks → LLM-estimated CPTs → deterministic inference → physician review and signed plans.
Self-hosted, English desktop UI, one admin + a small physician team sharing a patient pool.

## Stack and entry points

- **API**: Python 3.12 / FastAPI. App factory `create_app()` in
  `backend/src/x_insight/app.py`; every router mounted at `/api/v1`. Run with
  `uvicorn x_insight.app:app`.
- **Worker** (separate process): `python -m x_insight.reasoning` → `poll_forever()`
  (`reasoning/worker.py`). Needs no HTTP.
- **Private MCP server** (stdio subprocess, spawned per read by the worker):
  `python -m x_insight.mcp_server`.
- **Web**: React 19 + Vite, `web/src/main.tsx` → `App.tsx`. **No router library** —
  hand-rolled hash router in `web/src/app/router.ts`.
- **DB**: PostgreSQL 16 (roles `x_insight_app` / `x_insight_migrate` / `x_insight_readonly`),
  Alembic migrations, SQLAlchemy Core DML. Schema head pinned as
  `db.EXPECTED_SCHEMA_VERSION = "0024"`.
- **Commands**: all in `Makefile` — `make dev`, `make check`, `make test-backend`,
  `make test-e2e`, `make test-recovery`, `make verify`, `make prod-up`.

## Major modules (`backend/src/x_insight/`)

- `cases/` — patients, author-owned drafts, signing, chart, reporting (largest HTTP surface).
- `assessments/` — declarative assessment engine + diagnosis / PANSS / C-SSRS.
- `reasoning/` — batch/run/job queue, coordinator pipeline, provider (LLM), snapshots, readiness.
- `models/` — XMLBIF validation, CPT validation, bounded inference, bundles, workflow release.
- `probability_review/` — CPT adjustments, local recalculation, acceptance.
- `ddi/` — coverage-aware drug-interaction checks over immutable content-addressed releases.
- `identity/`, `operations/` — auth/sessions/throttle; audit, backup, restore, status.

## Non-obvious conventions (verify before changing)

- **Everything persists with Core DML**; `CommittedRoute` commits off-loop before the response
  because dirty-checking is impossible. Services never commit — the caller's `session_scope`
  owns the transaction, so audit + mutation stay atomic.
- **Each module owns its `tables.py` read model;** cross-module work goes through the owning
  module's public functions. Migrations own DDL.
- **Inference always runs with empty evidence**; patient projections are provenance only.
- **Immutability by convention:** baselines, snapshots, revisions, releases are SELECT+INSERT;
  staleness is derived by recomputing hashes, never an UPDATE.
- **Three independent revision namespaces**: encounter `revision`, `secondary_plans.revision`,
  `question_review_states.review_revision`. Signing checks all three plus `patients.revision`.
- Frontend has ~15 near-duplicated `request<T>` fetch wrappers, one per `features/*/api.ts`;
  the canonical one is `features/identity/api.ts` (API error, CSRF in `localStorage`,
  `Idempotency-Key`, `If-Match`). No toast framework — inline `role="status"`/`role="alert"`.
- No services commit; local imports inside functions break import cycles (intentional).

## Where to look next

- Behavior/architecture contracts: `project-documents/dev/system-design.md`,
  `system-architecture.md`; implementation policy in `plan.md` + `tasks.md`.
- **Why** decisions were made: `context/index.md` (Keep the Why topic files).
- Recorded progress/handoffs (start here per `AGENTS.md`):
  `project-documents/dev/progress/README.md`.
- Tests: `backend/tests/` (http, worker, ddi, models, mcp, provider, recovery, …),
  `e2e/` Playwright specs. Note `signed-encounter-v3` in `cases/signing.py` vs docs still
  saying `v2` — README/design docs lag the code in places; treat source as authoritative.
