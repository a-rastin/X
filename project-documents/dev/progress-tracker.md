# X-INSIGHT progress tracker

## S00 — Establish execution and content review ledger (2026-10-04)

**Scope:** documentation only. No application code, no tests (S00 has no test seam per tasks.md).

### Confirmed product rules (plan.md §1.1, system-design.md §15 — do not reopen)

- Bayesian execution uses an **empty evidence set**; patient variables inform LLM estimation of **every CPT**, including roots.
- At most **one open draft per patient** across authors and encounter types.
- Only the draft **author** can view clinical draft content or derived artifacts and edit/adjust/reset/retry/accept/discard/sign.
- Any active physician can append an attributed **addendum** to any signed encounter.
- **Failed generation cannot be bypassed** by manual-plan signing.
- Signing requires **full CPT review** (plan.md §9.1) and **exact current-result acceptance** for every applicable question (plan.md §9.2).

### Proposals (labeled as such; pin/validate during implementation)

- Stack: modular monolith + worker + private MCP + PostgreSQL (plan.md §3.1).
- Numerical policy: decimal strings ≤ six places, integer units with 100% = 100,000,000 (plan.md §7.4, system-design.md §7.4).
- Test seams T1–T10 (plan.md §12.1); used conceptually, no tests added in S00.
- Queue/concurrency limits (two provider slots, 100 queued runs, 15 s heartbeat, 120 s lease) and capacity/recovery targets (benchmarks, restore-drill target) — assumptions, not proven capacity.

### Verified supply inventory (evidence, not inherited claims)

- `project-documents/bayesian-networks/`: **12 XML files** = BN-04…BN-14 (11 networks) + `schema.xml`. Verified by `ls`/`wc -l` this session.
- `project-documents/medical-documents/DDI-text/`: **128 `.txt` files in 15 categories** (8/5/6/12/9/23/9/14/2/12/5/7/7/7/2 = 128). Verified by `find`/`wc -l` this session.
- Sitagliptin monograph sha256 `e7c9bc45ed5b3f829dfe8e29b015ee727645db2f3ed6cad8de99fea2dcd4022f` — verified by `sha256sum` this session.
- `content/` was empty; `progress-tracker.md` was 0 bytes (this entry is the first write).
- git status was clean before writing; sources under `project-documents/` left unmodified.

### BN structural-vs-executable status (observed this session via `grep` + stdlib parse, no namespaces)

- `<DEFINITION` (executable BIF CPT block): BN-04 has **13**; BN-05…BN-14 have **0**. `<FOR` counts match. Case-insensitive `definition` hits are lowercase `definition=` prose inside `<PROPERTY>` glosses, not CPT blocks.
- BN-04's 13 DEFINITIONs are deterministic 0/1 source-lookup tables (`parameter_status=deterministic_not_patient_risk`), and its own `parameter_inventory` declares "19 run-specific distributions to supply; 13 editable source-lookup examples supplied". BN-04 is therefore also **not executable** as supplied.
- All 11 networks declare `validation_status=EXPERIMENTAL_TEMPLATE_REQUIRES_CPTS` and `inference_mode=after_complete_cpt_validation`. Intended parents survive only as `proposed_parent` properties, which an ordinary XMLBIF importer ignores.
- **Discrepancy note:** blockers.md §5.1 reports partial DEFINITION counts (BN-04 17/15 missing, BN-05 6/14, …). Those counts do not reproduce against current files by either `grep` or stdlib parse. Root cause: blockers.md was committed 13:33, the BNs were re-edited afterwards ("edit BNs", 15:30) — the table describes the pre-edit revision and is **stale**. Likewise, the current XML contains no literal `inference_allowed=false` / `deployment_allowed=false` / "not appropriate for electronic decision support" flags; gating is via `EXPERIMENTAL_TEMPLATE_REQUIRES_CPTS` + `execution_contract`.
- Recorded as **templates requiring complete CPTs, not executable networks**. No invented priors or uniform defaults were added (plan.md §7.3 forbids silent uniform fill).

### Policy carry-forwards (system-design.md §15, plan.md §1.3 — provisional)

Archive handling, discarded-content retention, physician print permission, name/phone validation details, provider/runtime compatibility, dataset/resource/recovery targets. Assessment/history/prompts/networks/gates/templates are owner-supplied, **except** S08 experimental assessment release, which needs no owner review (plan.md §§1.4, 5; tasks.md S08).

### Content review ledger

Created `content/review-ledger.md`: index + per-item entries for assessments, history/severity, DDI, all 11 questions (plan.md §7.1), and workflow bundles. All items: status draft/`awaiting_review`, **none approved** (assessments excepted: S08 releases after validation with no `awaiting_review` flag per tasks.md S08). Reviewer named as owner; no approval claimed. No `content/assessments|history|questions/<key>/{manifest.json,network.xml,prompt.txt,template.json,examples.json,review.json}|bundles|ddi` files exist yet (content/ was empty before this ledger); all are missing and due in S08/S12/S18/S25–S39.

### Handoff

- **First engineering session: S01** (bootstrap reproducible dev loop) — depends on S00, seams T1/T9 smoke only.
- **Content tasks:** S08 assessments (no owner review) → S12 history → S15–S18 DDI → S25 contract → S27 and S29–S38 question packages → S39 bundle review/release.
- **Remaining blockers:** owner decisions enumerated in `content/review-ledger.md` §§2–7 (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure may proceed on synthetic fixtures while responses are pending.

### Hash appendix (verified by `sha256sum` this session; Sitagliptin 0/4/92/70 + Ofloxacin counts are plan.md §6.2 source expectations to be validated in S15, not verified here)

- BN-04 `5e059514127a618ac887a04a04180afa1f8b768fa2a5b27f34a3eb13fc5a8e05`, BN-05 `27b92883167c639859f99db7b5d7610862d90f11d69d0d34ff581e76c22e3fac`, BN-06 `e29107fa432b29ca082a66dcae6aae7b3ecdf8aad00f9610c699c6da61518f96`, BN-07 `ab947f741f0d4a76d01bacf3327fd6c6457cb61eaf08bcfb19a85da4695a222f`, BN-08 `7e83b8710e0e593792be194d0cb3f607616578c50292d8dff1866d88ac1d8be6`, BN-09 `b655bada91b9dbe9b89419e924b416559af676bf3fe2adf8ea2f9892a325b6a1`, BN-10 `b18db707328c793a9b8c586ea9bba277ee12c4283fb604e42f5b3d311994ce80`, BN-11 `c0208a71c631b0a0d468202b821d47c69fdf5277c366ce815bf5c35b23308871`, BN-12 `6998e4d305f5b2671c4fffc304a4fcd7c4bd932cacff4c4285d307f65ecfd058`, BN-13 `63d2ce41f2e5c29dae51e446ee30a116062a8fd9e01c71c3638ea659c708a8cf`, BN-14 `bf78baac82af824d1040a0ee964b6236ad6f471e7a3c4be02ff1bfcc12008b4c`.
- `schema.xml` `a74b5267b70e9f35ed44a3c21f1963664d7e812a837ce09577051d6583d76abc`, Sitagliptin monograph `e7c9bc45ed5b3f829dfe8e29b015ee727645db2f3ed6cad8de99fea2dcd4022f`.

## S01 — Bootstrap a reproducible development loop (2026-10-04)

**Scope:** infra-only. No clinical content, no seeded data, no assessment/DDI/model content. Seams T1/T9 smoke only (tasks.md S01; plan.md §§3, 11–12).

### Pinned runtimes (observed in S01 files; install-smoke verified)

- Python `3.12.14` (`backend/pyproject.toml` `requires-python ==3.12.*`; CI `setup-python 3.12.14`; `Makefile` header).
- Node `22.23.2` / npm `10.9.8` (`Makefile` header; CI `setup-node 22.23.2`; `engines >=22` in `web/package.json` + root `package.json`).
- PostgreSQL 16 image `postgres:16-alpine` (`compose.yaml` `db` + `db-test`); observed server `16.15` recorded in `Makefile` header.
- Backend libs: `fastapi 0.142.2`, `uvicorn 0.54.0`, `pydantic 2.13.5`, `mcp 2.3.0` (MCPServer API), `pgmpy 1.1.2`, `httpx 0.28.1` (`backend/pyproject.toml` + `Makefile` header).
- Dev/verify: `pytest 9.1.1`, `ruff 0.16.10`, `mypy 2.4.0` (`backend/pyproject.toml` dependency-group `dev` + `Makefile` header).
- Web: `react 19.3.0` (+ `react-dom 19.3.0`), `vite 8.3.2`, `typescript 7.0.2`, `@vitejs/plugin-react 6.1.1`, `@types/node 26.6.4` (`web/package.json` + `Makefile` header).
- E2E runner: `@playwright/test 1.63.0` (root `package.json` + `Makefile` header); `playwright.config.ts` projects `chromium` + `firefox`.
- Compat basis (Spec-review gap note): locked install smoke + exact import check (`pgmpy` + MCP `MCPServer` import). No further primary-doc citations are invented in this docs step; `Makefile` header records the same pins as the verified set.

### Red→green, seams, and verification

- Red→green: failing public health test first (`backend/tests/http/test_health.py` asserts `GET /api/v1/health` → `{"status":"ok","service":"x-insight"}`), then minimal `backend/src/x_insight/app.py` implementation; shell smoke (`e2e/smoke.spec.ts` loads `/` and expects `X-INSIGHT` heading over `web/src/app/App.tsx`). Shell only; no fake clinical screens.
- Seams: T1 (authenticated-HTTP shape used as public health check; no auth/DB yet) and T9 (browser shell load) only. No other seams exercised in S01.
- S01 implementation reports: `make check` (ruff format-check + ruff check + mypy + web build + root `tsc --noEmit`) pass; `make test-backend` 1 passed (`tests/http/test_health.py`); `make test-e2e` 2 passed (chromium + firefox, `e2e/smoke.spec.ts` via preview on `:5173`); `make verify` pass.
- `make verify` = `check` + `test-backend` (+ web build inside `check`); browser smoke runs via `make test-e2e` / CI separately, not inside `verify` (`Makefile` `verify` target; `ci.yml` offline gate runs `make verify` + credential check).
- Not-yet suites correctly report unimplemented (nonzero exit, no false success): `make test-web`, `make test-recovery`, `make test-load`, and `make migrate` (S02 adds the Alembic entry point).

### Local loop for next agent (S02+)

- `make setup` — locked installs: `backend` `uv sync --locked --group dev`, `web` `npm ci`, root `npm ci` (e2e runner).
- Copy `.env.example` → `.env` and set `POSTGRES_PASSWORD`; `.env` is git-ignored and must never be committed (`.gitignore` lists `.env`; `.env.example` holds placeholders only).
- `make dev` — `docker compose up -d db db-test`, then backend `:8000` (`uvicorn x_insight.app:app`) + web `:5173` (`vite`). Health: `http://localhost:8000/api/v1/health`; shell: `http://localhost:5173` (proxies `/api` to `:8000`).
- `make stop` == `docker compose down`; volumes are disposable: `docker compose down -v` drops local + test data (`Makefile` header + `compose.yaml` header).
- Ports/env placeholders (`Makefile` header, `.env.example`, `compose.yaml`): `5432` local DB (`x_insight`), `5433` test DB (`x_insight_test`), `8000` backend, `5173` web; `POSTGRES_USER/DB`, `TEST_POSTGRES_DB`, `DB_PORT/TEST_DB_PORT`, `BACKEND_PORT/WEB_PORT`, `DATABASE_URL/TEST_DATABASE_URL` (placeholder password), `PUBLIC_ORIGIN=http://localhost:5173`.
- Host conflict note: a host PostgreSQL already on `5432` conflicts with the default `DB_PORT`; override via `DB_PORT` (and `TEST_DB_PORT` if needed) in `.env` — compose honors `${DB_PORT:-5432}` / `${TEST_DB_PORT:-5433}`.
- No credentials tracked: `.env.example` placeholders only (`CHANGEME_local_only`), `compose.yaml` reads `POSTGRES_PASSWORD` from environment (no password in file), CI greps tracked files for private-key/AWS/GH patterns and fails if `.env` is tracked.

### Handoff

- Next engineering session: **S02** (real persistence and request contracts) — needs DB roles, migration entry point, readiness/health, correlation/error body; `make migrate` currently reports unimplemented by design.
- `backend/README.md` points to this same repo-root `make` loop (minimal pointer; no app-code change in this docs step).
- Unrelated content untouched: `content/review-ledger.md`, BNs, medical docs unchanged; no clinical claims added here.

### Blocker resolutions addendum (2026-10-04)

- (a) Host PG conflict (defaults unchanged): host PostgreSQL 14 observed on `5432` via `ss`/`ps` (implementation-session observation; host PG left untouched). `make dev` (`Makefile` `dev` target, `SHELL := /bin/bash`) now fail-fasts on missing `.env`, preflights effective `DB_PORT`/`TEST_DB_PORT` via `/dev/tcp/127.0.0.1/<port>`, echoes effective ports, and on conflict exits non-zero suggesting stopping host PG or `DB_PORT=5442 TEST_DB_PORT=5443 make dev`. Defaults unchanged `5432`/`5433` (`.env.example` override comment, `Makefile` header).
- (b) `verify` now includes browser smoke: `make verify` = `check` + `test-backend` + `test-e2e` with closing `+ browser smoke` echo (`Makefile` `verify` target). CI installs `chromium firefox` (`npx playwright install chromium firefox`) before the `make verify` gate (`ci.yml`). Supersedes the earlier S01 note that browser smoke ran outside `verify`.
- Test evidence (implementation-session report, S01 scope): `make check` pass; `make test-backend` 1 passed; `make test-e2e` 2 passed (`chromium` + `firefox`); `make verify` exit 0. Unimplemented targets still exit non-zero with clear messages: `test-web`, `test-recovery`, `test-load`, `migrate`. No clinical content, no clinical claims.

## S02 — Establish real persistence and request contracts (2026-10-04)

**Scope:** infra-only. No clinical content, no seeded data, no domain tables. Seams T1 only (tasks.md S02; plan.md §4).

### Pinned / added deps (S01 pins unchanged; S02 adds persistence)

- S01 runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `uvicorn 0.54.0`, `pydantic 2.13.5`, `mcp 2.3.0`, `pgmpy 1.1.2`, `httpx 0.28.1`, `pytest 9.1.1`, `ruff 0.16.10`, `mypy 2.4.0` (`Makefile` header + `backend/pyproject.toml`).
- S02 adds: `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`backend/pyproject.toml` dependencies + `Makefile` header).
- No other new runtime libs; no clinical packages.

### Red→green slices + seams (all T1; no test-only production endpoint)

- Slice A — canonical JSON/hash + UTC at actual uses (`backend/src/x_insight/contracts.py`; `backend/tests/http/test_contracts.py` Slice A): `canonical_json` (sorted keys, compact separators, raw UTF-8, explicit nulls kept, missing keys untouched, finite numbers only, `TypeError` otherwise) + `canonical_hash` (SHA-256 hex); `utcnow` (aware UTC) / `serialize_utc` (`Z` suffix, naive rejected) / `parse_utc` (aware UTC, naive rejected). Conventions `parse_if_match`/`format_etag`, `parse_idempotency_key`, `parse_pagination` (default 25, max 100) defined here for S03+ use.
- Slice B — correlation + standard error body + edge limits (`backend/src/x_insight/app.py`): `GET /api/v1/health` preserved exactly (`{"status":"ok","service":"x-insight"}`, no DB access); `RequestContextMiddleware` echoes valid `X-Request-ID` else fresh UUID on every response/error body; `ErrorBody`/`ContractError` standard body (code/message/field_errors/request_id/retryable, no tracebacks); `SizeLimitMiddleware` enforces `MAX_BODY_BYTES 1_048_576` with safe `413 REQUEST_TOO_LARGE`; `IdempotencyKeyMiddleware` enforces `Idempotency-Key` format (`422 INVALID_IDEMPOTENCY_KEY`) on POST/PUT/PATCH/DELETE only, presence still per-command.
- Slice C — real-PostgreSQL persistence + readiness (`backend/src/x_insight/db.py`, `backend/src/x_insight/operations/audit.py`, `backend/migrations/versions/0001_create_audit_events.py`; tests Slice C): one shared transaction context (`session_scope` commit-or-rollback + `get_session` FastAPI dep); `record_audit` (flush, no commit; `422` on empty operation) + `list_audit_events` (time-ordered, `parse_pagination` bounds) share the caller transaction (commit/rollback together, uncommitted rows invisible); `payload_hash` = `canonical_hash({"schema_version": 1, "details": ...})`; `GET /api/v1/ready` returns `200 ready/checked_at(Zulu)/schema_version 0001` else safe `503` (`DB_UNAVAILABLE` retryable / `SCHEMA_INCOMPATIBLE` non-retryable); `check_readiness` never leaks secrets/tracebacks; S02 adds only `audit_events` + `alembic_version` tables.
- Seams: T1 only. No T2–T10 exercised in S02.

### Verification evidence (implementation-session report, S02 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 40 passed: 1 (`tests/http/test_health.py`) + 39 (`tests/http/test_contracts.py` — 7 canonical/UTC/revision/idempotency/pagination + health-preserved + 8 correlation/error/size-limit edge + 1 unavailable-DB 503 + 15 migrated-DB/readiness/audit/ordering/isolation/roles/table-scope + 8 UTC/null/infinite/safe-method/blank-ID/ready-shape cases).
- `make migrate` (`set -a; . ./.env; cd backend && uv run alembic upgrade head`; `migrations/env.py` resolves `DATABASE_URL` via `x_insight.db.get_database_url()`): fresh migrate records single head `0001`; second `upgrade head` is a no-op (test fixture runs both); `pg_tables` is exactly `alembic_version, audit_events`.
- Readiness safety: unavailable DB → `503 DB_UNAVAILABLE retryable True`; missing/foreign version → `503 SCHEMA_INCOMPATIBLE retryable False` (foreign version string never echoed); health stays `200` while ready is `503`; unexpected failure → `500 INTERNAL_ERROR` with no `Traceback`/secret substrings (`5599`, db name, `POSTGRES`, `password`, `topsecret` asserted absent); unknown route `404 NOT_FOUND`, wrong method `405 METHOD_NOT_ALLOWED`, all with echoed `x-request-id`.
- Roles/grants asserted against real PostgreSQL (never sqlite): `x_insight_app` has SELECT+INSERT but not UPDATE/DELETE, `x_insight_readonly` SELECT only (no INSERT), `x_insight_migrate` UPDATE-capable; `audit` module exposes only `record_audit` + `list_audit_events` (no update/delete/upsert helpers).

### Local loop for next agent (S03+)

- Env (`.env.example` placeholders only; `.env` git-ignored, never commit): `POSTGRES_USER/DB`, `TEST_POSTGRES_DB`, `POSTGRES_PASSWORD=CHANGEME_local_only`, `DB_PORT 5432` / `TEST_DB_PORT 5433` / `BACKEND_PORT 8000` / `WEB_PORT 5173`, `DATABASE_URL` / `TEST_DATABASE_URL` (placeholder password), `PUBLIC_ORIGIN=http://localhost:5173`. `db.py` accepts `postgresql://`/`postgres://` and pins `postgresql+psycopg://`; engine `pool_pre_ping`, `pool_size 5`, `max_overflow 5`, `connect_timeout 3`; import never connects (lazy `get_engine`, `reset_engine` no-op when never created, `lifespan` disposes on shutdown).
- Migrate: `make migrate` applies `DATABASE_URL` (or `.env`) to head; test isolation points `DATABASE_URL` at `TEST_DATABASE_URL` then `alembic upgrade head` twice (`test_contracts.py` `migrated_test_engine` + `clean_audit` TRUNCATE fixture).
- Roles note (deployment hardening; local runs as owner): `x_insight_app` = ordinary runtime (connect + SELECT/INSERT audit, never UPDATE/DELETE audit); `x_insight_migrate` = DDL/migration entry point; `x_insight_readonly` = SELECT only. Migration `0001` declares all three idempotent-safe for non-superuser runs: `DO` blocks `NOTICE`-and-continue on `duplicate_object` / `insufficient_privilege` / `undefined_object` and per-role `REVOKE`/`GRANT` guarded by `pg_roles` existence, so owner-run `make migrate` without CREATEROLE still upgrades the table while skipping grants it may not confer; `downgrade` drops `audit_events` but keeps roles.
- S01 loop unchanged: `make setup`, `make dev` (`:8000` health / `:5173` shell, `5432`/`5433` DBs), `make stop` / `down -v` disposable volumes, host-PG conflict preflight via `DB_PORT`/`TEST_DB_PORT`.

### Handoff

- Next engineering session: **S03** (login, sessions, own credentials) — uses the one shared transaction context (`db.session_scope` / `get_session`), transaction-scoped `operations.audit.record_audit` (never commits itself), `contracts` revision/idempotency conventions (`If-Match`/ETag, `Idempotency-Key`), canonical JSON/hash + UTC serialization, and `0001` audit storage. First meaningful mutation behavior lands in S03/S06, not here.
- `backend/README.md` points to the same repo-root `make` loop plus the `make migrate` entry point (minimal pointer; no app-code change in this docs step).
- Unrelated content untouched: `content/review-ledger.md`, BNs, medical docs, `web/`, BNs unchanged; no clinical claims added here.

### Deferred items (S02 conventions defined, enforcement lands with consumers)

- Idempotent replay `409` (same key + changed body): `parse_idempotency_key` validates format only; per-command stores compare body hashes in S03+.
- `If-Match` enforcement (`412 STALE_REVISION`): `parse_if_match`/`format_etag` are the revision contract; stale-revision checks land in S03+ mutations.
- Audit HTTP route: `list_audit_events` is a module call for S03+ command code, not an HTTP endpoint in S02.
- Cursor pagination: `parse_pagination` fixes limit/offset bounds (25/100); cursor pagination waits for list routes when they land.
- Named role logins: roles/grants declared and asserted via `has_table_privilege`, but disposable local/test stacks connect as the table owner; named `x_insight_app/migrate/readonly` logins are for deployments.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S02 adds no new clinical blocker.
- S02 exit met: all future modules can use one transaction context; audit permissions prepared without a generic repository layer (`test_audit_module_has_no_generic_repository`, `test_only_s02_tables_exist`).

## S03 — Implement login, sessions, and own credentials (2026-10-04)

**Scope:** identity only (tasks.md S03; plan.md §§2.1, 4.3, 11; FR-01–02, FR-04, NFR-02). No clinical content, no seeded patient data, no physician administration (S04), no browser/theme work (S05). Seams T1 only.

### Pinned / added deps (S01/S02 pins unchanged; S03 adds no runtime lib)

- S01/S02 runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`).
- Password hashing is stdlib PBKDF2-HMAC-SHA256 (`hashlib`/`secrets`/`hmac` only, 600 000 iterations, `pbkdf2_sha256$600000$…` format in `backend/src/x_insight/identity/passwords.py`); no new runtime lib pinned for hashing by design.
- No other new runtime libs; no clinical packages.

### Red→green slices + seams (all T1; no test-only production endpoint)

- Slice 1 — singleton seeding + hashing (`backend/src/x_insight/identity/service.py::ensure_default_admin`, `passwords.py`, migration `0002`; `test_identity.py` Slice 1): fresh DB permits `admin/admin` once; second init preserves a changed password (`WHERE NOT EXISTS … ON CONFLICT DO NOTHING` + early-return when an `admin` row exists); partial unique index `one_admin_only` blocks a second admin at the DB; passwords used verbatim (no trimming), `verify_password` constant-time with malformed hashes never authenticating.
- Slice 2 — login + `/me` (`backend/src/x_insight/identity/router.py::login/me`, `service.create_session/get_session_user`; Slice 2 tests): opaque cookie session (`x_insight_session`, HttpOnly/SameSite=Lax, Secure under HTTPS) + per-session `csrf_token`; generic `401 UNAUTHENTICATED` errors with no enumeration/hash/secret leakage; active-account checks; CSRF gate on mutations (`403` on missing/wrong token); login throttling 5 failures / 60 s window per (client IP, normalized username) with `429 RATE_LIMITED retryable True`; privileges come from the stored account row, never the login-selected role (role mismatch → `401`).
- Slice 3 — revocation, no timeout (plan.md §2.1/NFR-02; Slice 3 tests): logout sets `revoked_at`; password change bumps `credential_revision`, revokes all prior sessions via `revoked_at`, then issues a fresh session; `get_session_user` enforces `revoked_at IS NULL` + revision match + active check with no timeout/expiry check (controlled-clock +365 d test still authenticates); no password-complexity rule (single-char secret accepted).
- Slice 4 — mutation denial, empty passwords, audit (Slice 4 tests): no username-mutation route (`PATCH /me/preferences` schema is theme-only, `extra=forbid` → `422` on `username`); no self-registration route (`/auth/register`, `POST/GET /physicians` → `401/403/404/405`); empty login password stays generic `401` while empty new password is explicit `422` with `field_errors`; audit `auth.login.success/failure`, `auth.login.throttled`, `auth.logout`, `auth.password_change.success/failure`, `auth.preferences.success` recorded in the caller transaction with username-only details (no password/hash/token/CSRF substrings asserted absent).
- Seams: T1 only. No T2–T10 exercised in S03.

### Verification evidence (implementation-session report, S03 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 62 passed: 39 (`tests/http/test_contracts.py`) + 1 (`tests/http/test_health.py`) + 22 (`tests/http/test_identity.py` — 4 seeding/hashing + 7 login/`/me`/CSRF/throttling/cookie-flags + 4 revocation/no-timeout + 7 mutation-denial/empty/audit/recreate-guard/health-ready/real-PG).
- `make migrate` to head `0002`: fresh migrate records single head `0002`; second `upgrade head` is a no-op (test fixture runs `upgrade head` on the test DB); tables are exactly `alembic_version, audit_events, users, sessions`.
- Readiness/health preserved: `GET /api/v1/health` still exactly `{"status":"ok","service":"x-insight"}` with no DB access; `GET /api/v1/ready` returns `200` with `schema_version 0002` against the migrated DB.
- Roles note (changed from S02 audit append-only): migration `0002` grants `x_insight_app` SELECT+INSERT+UPDATE on `users`/`sessions` (UPDATE needed for revocation/theme/revision; still no DELETE), `x_insight_readonly` SELECT only, `x_insight_migrate` ALL; same idempotent `DO $$` NOTICE-and-continue pattern as `0001`, so owner-run `make migrate` without CREATEROLE still upgrades tables while skipping grants it may not confer; `downgrade` drops `sessions` then `users`.

### Local loop for next agent (S04+)

- Routes (all under `/api/v1`): `POST /auth/login` (username/password/role → `{user, csrf_token, research_warning}` + session cookie), `POST /auth/logout` (CSRF, revokes, clears cookie), `GET /me` (cookie only), `PATCH /me/preferences` (`{theme: light|dark}` only, CSRF), `POST /me/password` (`{current_password, new_password}`, CSRF, rotates revision + session).
- Cookie/session: `x_insight_session` opaque value (only SHA-256 `token_hash` stored) + `csrf_token` returned in login/password-change body; mutations send `X-CSRF-Token: <csrf_token>` header; HttpOnly + `SameSite=Lax`, `Secure` only under HTTPS (asserted for both schemes in tests).
- Throttling: at most 5 failures per 60 s per (client IP, normalized username); 6th+ attempt is `429` even with correct credentials; in-memory single-host store reset via `service.clear_login_throttle()` in tests.
- Revocation = `revoked_at` + `credential_revision`, no timeout: logout/password-change/deactivation-path set `revoked_at`; stale-revision sessions fail even with a valid cookie; no idle/absolute expiry column exists.
- Test interface: `test_identity.py::admin_client` fixture is the authenticated handoff (TestClient with cookies + `csrf` + `user` + `engine`); `clean_identity` truncates `sessions, users, audit_events` then `ensure_default_admin`; S01/S02 loops unchanged (`make setup/dev/stop`, `make migrate`, host-PG `DB_PORT`/`TEST_DB_PORT` preflight).
- No clinical claims added here; no owner approval claimed.

### Handoff

- Next engineering session: **S04** (physician account administration) — builds on `B/identity/` account commands/routes and the `users.credential_revision` + `revoke_all_user_sessions` revocation path; S03 leaves physician CRUD, reset/deactivation, and retain/discard confirmation to S04.
- `backend/README.md` unchanged in this docs step: it already points to the repo-root `make` loop plus the `make migrate` entry point (verified this session); no new make target or app-code change needed for S03.

### Code-review follow-ups (non-blocking; not approval, no clinical content)

- Router performs direct `users`-table updates (password/theme) rather than going through a service-layer command — consider moving the update + revoke + re-issue sequence into `service.py` when S04 adds account commands.
- Several behavior assertions read rows behind the interface via test SQL (`SELECT count(*)`, `UPDATE users SET active=…`); per plan.md §12.1, future slices should prefer HTTP/module-interface reads where the seam allows, keeping SQL to fixture setup.
- Migration `0002` embeds the default admin hash via an f-string constant duplicated from `passwords.py`; consider importing the constant or documenting the single-source rule so hash-format changes cannot drift.
- `router.py` defines local `error_response`/`get_request_id` helpers duplicating `app.py` middleware helpers; consider sharing the standard error-body path when the next route module lands.
- `If-Match`/ETag and `Idempotency-Key` per-command stores, HTTPS-outside-localhost enforcement, and physician-side research-warning display are deferred to their owning sessions (account commands in S04, generation/signing flows and browser warning in S05/S57 scope) — not S03 gaps.

### Deferred items (S03 conventions reused; enforcement lands with consumers — carry S02 notes forward)

- Idempotent replay `409` (same key + changed body): S02 `parse_idempotency_key` still validates format only; per-command body-hash stores land with the consuming create/generate/adjust/reset/retry/accept/sign/note commands (S04+).
- `If-Match` enforcement (`412 STALE_REVISION`): `parse_if_match`/`format_etag` remain the revision contract; stale-revision checks land in S04+ mutations (physician/draft flows).
- Named role logins: `x_insight_app/migrate/readonly` grants declared and migration-guarded, but disposable local/test stacks connect as the table owner; named logins remain deployment-only.
- Audit HTTP route and cursor pagination: unchanged from S02 — `list_audit_events` stays a module call for command code, `parse_pagination` stays limit/offset (25/100).

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S03 adds no new clinical blocker.
- Env caveat this session: `db-test` on `5433` was down during the S03 implementation session, so the `5432` override was used for the test DB; next agent should restore default `5432`/`5433` ports or record the override in `.env` before running `make test-backend`.
- S03 exit met: fresh DB permits `admin/admin` once, login issues opaque cookie sessions with CSRF/throttling, logout/credential change revokes via `revoked_at` + `credential_revision` with no timeout, and the `admin_client` fixture is available for S04.

## S04 — Implement physician account administration (2026-10-04)

**Scope:** identity administration only (tasks.md S04; plan.md §§2.1, 4.3; FR-03–04, FR-42). No clinical content, no seeded patient data, no browser/theme work (S05), no draft/encounter tables. Seams T1 only.

### Pinned / added deps (S01–S03 pins unchanged; S04 adds no runtime lib)

- S01/S02/S03 runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`).
- Password hashing stays stdlib PBKDF2-HMAC-SHA256 via S03 `identity/passwords.py` (600 000 iterations, `pbkdf2_sha256$600000$…`); no new hashing lib pinned by design.
- S04 storage is migration `0003_physician_idempotency.py`: one new table `idempotency_records` (`operation`, `actor_id` → `users.id` cascade, `idempotency_key`, `request_hash`, `response_status`, `response_body` JSONB, `created_at`; unique `(operation, actor_id, idempotency_key)` + index `ix_idempotency_actor_operation`). Uses already-pinned `sqlalchemy`/`alembic`/`psycopg` (JSONB + PG UUID) only.
- No other new runtime libs; no clinical packages.

### Red→green slices + seams (all T1; no test-only production endpoint)

- Slice 1 — create/edit/retrieve + authorization (`service.create_physician/patch_physician/list_physicians/get_user_by_id/safe_physician`, `router GET/POST /physicians`, `GET/PATCH /physicians/{id}`; 5 tests): admin creates a physician and reads safe fields only (`{id, username, role, active, theme, revision}` — never hash/token/password); rename keeps the stable UUID actor ID and bumps `revision`; admin username is immutable (`422`); `role=admin` creation is denied so the singleton admin stands; physicians get `403` on list/create/patch/single-read and have no elevation path (`PhysicianPatchRequest` is username/password only, `extra=forbid`); unauthenticated reads/mutations are `401`/`403`.
- Slice 2 — reset/deactivation revocation + reactivation + revision (4 tests): admin password reset via `PATCH /physicians/{id}` bumps `credential_revision` and revokes all sessions immediately through the same `revoke_all_user_sessions` path as own-password change (old password → generic `401`, new password works, prior cookie → `401`); `POST .../deactivate` sets `active=false`, bumps `revision` + `credential_revision`, revokes all sessions, and inactive logins fail generic `401 UNAUTHENTICATED`; `POST .../reactivate` restores `active=true` but never revives old sessions (re-login required); `PATCH` honors `If-Match`/`ETag` (`contracts.parse_if_match`/`format_etag`): mismatch → `412 STALE_REVISION`, match returns fresh `ETag`; deactivation/reactivation require the admin role (`403` for physicians).
- Slice 3 — retain/discard choice + empty draft-set revision contract (3 tests): deactivation requires an explicit `draft_action` (`DeactivateRequest`, `extra=forbid`; missing/invented → `422`, no silent default); both `retain` and `discard` are accepted and echoed with the empty set; `draft_set_revision` `0` is current while any other revision is `409 DRAFT_SET_CHANGED` (`service.EMPTY_DRAFT_SET_REVISION = 0`, `validate_draft_action`/`validate_draft_set_revision`); `test_no_draft_tables_fabricated_for_deactivation` pins the table scope — `alembic_version, audit_events, users, sessions` plus `idempotency_records` only, with `encounters/drafts/patients/notes/runs/jobs` asserted absent.
- Slice 4 — idempotency/conflicts/audit/no secrets + gap cover (10 tests: 6 slice-4 + 4 gap-cover): per-command idempotency stores (`service.idempotency_request_hash/lookup_idempotency/store_idempotency`, operations `physicians.create/patch/deactivate/reactivate` scoped per `(operation, actor)`) replay same key + same body without re-executing or duplicating audit rows, while same key + changed body is `409 IDEMPOTENCY_CONFLICT`; duplicate usernames conflict `409 CONFLICT` case-insensitively without a key; audit `physicians.create/patch/deactivate/reactivate.success` is attributed to the admin with username-only details (no password/hash/token/CSRF substrings asserted absent); no physician response leaks `password_hash`/`pbkdf2`/raw secret/`token_hash`/CSRF; list pagination stays bounded (default 25, max 100, `422` over bounds); gap cover pins empty-credential `422`s, CSRF `403` on every physician mutation (missing/wrong token changes nothing), and admin-deactivation denial + unknown-ID `404`s.
- Seams: T1 only. No T2–T10 exercised in S04.

### Verification evidence (implementation-session report, S04 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 84 passed: 22 (`tests/http/test_physicians.py` — 5 create/edit/authz + 4 revocation/revision + 3 retain/discard/empty-set + 6 idempotency/conflicts/audit/secrets/pagination + 4 validation/CSRF/single-read-denial/admin-guard/404s) + 22 (`tests/http/test_identity.py`) + 39 (`tests/http/test_contracts.py`) + 1 (`tests/http/test_health.py`).
- `make migrate` to head `0003`: fresh migrate records single head `0003`; second `upgrade head` is a no-op (test fixture runs `upgrade head` on the test DB); tables are exactly `alembic_version, audit_events, users, sessions, idempotency_records`.
- Readiness/health preserved: `GET /api/v1/health` still exactly `{"status":"ok","service":"x-insight"}` with no DB access; `GET /api/v1/ready` returns `200` with `schema_version 0003` against the migrated DB.
- Roles note (same idempotent `DO $$` NOTICE-and-continue pattern as `0001`/`0002`, so owner-run `make migrate` without CREATEROLE still upgrades while skipping grants it may not confer): migration `0003` grants `x_insight_app` SELECT+INSERT on `idempotency_records` (records immutable — never UPDATE/DELETE), `x_insight_readonly` SELECT only, `x_insight_migrate` ALL; `downgrade` drops `idempotency_records`.

### Local loop for next agent (S05+)

- Routes (all under `/api/v1`): `GET /physicians` (`{items, total, limit, offset}`, bounded `limit`/`offset`, stable username-then-id order), `POST /physicians` → `201` (`{username, password, role?}` — `role` when present must be `physician`), `GET /physicians/{id}` (safe shape + `ETag`), `PATCH /physicians/{id}` (`{username?, password?}`, at least one required, `If-Match` precondition, `ETag` on success), `POST /physicians/{id}/deactivate` (`{draft_action: retain|discard, draft_set_revision?}` → `{user, draft_action, draft_set_revision: 0, reviewed_drafts: []}` + `ETag`), `POST /physicians/{id}/reactivate` (`{user}` + `ETag`). S03 routes unchanged (`POST /auth/login|logout`, `GET /me`, `POST /me/password`, `PATCH /me/preferences`).
- Cookie/session: `x_insight_session` opaque value (only SHA-256 `token_hash` stored) + `X-CSRF-Token` per session; admin reads need the session cookie; every physician mutation needs session + matching CSRF (missing/wrong → `403`); non-admin authenticated callers get `403`, unauthenticated get `401`.
- ETag/`If-Match` usage: responses carry `ETag: "<revision>"` via `contracts.format_etag`; `PATCH /physicians/{id}` parses `If-Match` via `contracts.parse_if_match` — absent/`"*"` means no precondition, a supplied revision that mismatches the stored `users.revision` is `412 STALE_REVISION`; every edit bumps `users.revision`.
- `Idempotency-Key` usage: format gate stays in `IdempotencyKeyMiddleware` (`422 INVALID_IDEMPOTENCY_KEY` on malformed keys for POST/PUT/PATCH/DELETE); per-command stores (`physicians.create/patch/deactivate/reactivate`, keyed `(operation, actor, key)`, request hash = `canonical_hash({body, target_id?})`) replay the stored status + safe body on same key + same body and return `409 IDEMPOTENCY_CONFLICT` on same key + changed body; replays never re-execute the mutation nor duplicate audit rows.
- Fixtures: `test_physicians.py::clean_identity` truncates `sessions, users` + `audit_events` then `idempotency_records` best-effort (so the red phase without `0003` never rolls back the truncates) and reseeds via `ensure_default_admin`; `admin_client` is the authenticated admin handoff (TestClient with session cookies + `csrf` + `user` + `engine`); `_physician_client` logs in as a created physician and asserts the research warning; `_assert_safe_user` pins the safe shape and secret-absence; S01/S02/S03 loops unchanged (`make setup/dev/stop`, `make migrate`, host-PG `DB_PORT`/`TEST_DB_PORT` preflight).
- No clinical claims added here; no owner approval claimed.

### Handoff

- Next engineering session: **S05** (role navigation and both themes) — builds on the S03 session/cookie/CSRF + `/me/preferences` theme path and the S04 admin physician table/CRUD (`GET/POST /physicians`, `PATCH /physicians/{id}`, deactivate/reactivate) with `ETag`/`Idempotency-Key` conventions; S04 leaves role-owned navigation, theme tokens, and browser journeys to S05.
- Cases integration explicitly incomplete until S51 with the `EMPTY_DRAFT_SET` contract: the reviewed draft set is always empty (`reviewed_drafts []`, `draft_set_revision 0` via `service.EMPTY_DRAFT_SET_REVISION`); no draft/encounter tables were fabricated (`test_no_draft_tables_fabricated_for_deactivation`); a confirmed discard releases nothing yet; S51 must replace the empty set with real open-draft IDs + job cancellation + slot occupancy, confirm discard only against that reviewed revision, and keep retained drafts reserved to the author occupying the single-draft slot.
- `backend/README.md` unchanged in this docs step: it already points to the repo-root `make` loop plus the `make migrate` entry point (verified this session); no new make target or app-code change needed for S04.
- Unrelated content untouched: `content/review-ledger.md`, BNs, medical docs, `web/` unchanged; no clinical claims added here.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry forward, S03 items retained)

- S03 retained: router performs direct `users`-table updates (password/theme) rather than going through a service-layer command — S04 moved physician edits into `service.patch_physician` but admin password reset and own-password/theme updates still execute `UPDATE users` in `router.py`; consider one service-layer account-command path when the next account-adjacent session lands.
- S03 retained: several behavior assertions read rows behind the interface via test SQL (`SELECT count(*)`, `SELECT operation/actor/details FROM audit_events`, `UPDATE users SET active=…`); per plan.md §12.1, future slices should prefer HTTP/module-interface reads where the seam allows, keeping SQL to fixture setup — S04 audit assertions still query `audit_events` directly because no `GET /audit-events` route exists yet (carried below).
- S03 retained: migration `0002` embeds the default admin hash via an f-string constant duplicated from `passwords.py`; the single-source rule still needs documenting so hash-format changes cannot drift.
- S03 retained: `router.py` defines local `error_response`/`get_request_id` helpers duplicating `app.py` middleware helpers; S04 adds `_require_admin`, `_idempotency_replay`/`_idempotency_conflict` locals on top — consider sharing the standard error-body/idempotency path when the next route module lands (duplicated idempotency replay/conflict helper is now the concrete instance).
- New S04 items: audit stays SQL-read until the audit HTTP route exists (see above); deactivate/reactivate carry no `If-Match` precondition and confirm via `draft_set_revision` instead — reconcile the two revision contracts when draft flows add `If-Match` there; admin password reset lives on `PATCH /physicians/{id}` while own-password lives on `POST /me/password` — keep the two paths' revocation semantics visibly aligned; `GET /physicians` `total` counts all `users` rows (admin included), not physicians only — decide the intended denominator when CSV/export sessions consume it; physician fixtures (`migrated_test_engine`/`clean_identity`/`admin_client`) are duplicated between `test_identity.py` and `test_physicians.py` — consider one shared fixture module when the next HTTP suite lands.

### Deferred items (S04 conventions reused; enforcement lands with consumers — carry S02/S03 notes forward)

- Idempotent replay `409` now lands for physician create/patch/deactivate/reactivate; per-command body-hash stores for generate/adjust/reset/retry/accept/sign/note/addendum/recovery commands still land with their consuming sessions (S06+).
- `If-Match` enforcement (`412 STALE_REVISION`) now lands for `PATCH /physicians/{id}`; stale-revision checks for draft/plan/network flows still land with their mutations.
- Audit HTTP route and cursor pagination: unchanged from S02/S03 — `list_audit_events` stays a module call for command code (physician audit assertions read `audit_events` via test SQL until the route exists), `parse_pagination` stays limit/offset (25/100).
- Named role logins: `x_insight_app/migrate/readonly` grants declared and migration-guarded (now incl. `0003`), but disposable local/test stacks connect as the table owner; named logins remain deployment-only.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S04 adds no new clinical blocker.
- S04 exit met: admin creates/edits physicians with safe fields and stable IDs, reset/deactivation revokes sessions immediately with reactivation that never resurrects old sessions, deactivation carries an explicit retain/discard choice against the empty draft-set revision, repeated commands respect idempotency/conflicts with safe attributed audit, and no response includes a hash or raw password.

## S05 — Build role navigation and both themes (2026-10-04)

**Scope:** identity/theme UI only (tasks.md S05; plan.md §9; FR-01, FR-03, NFR-03). No clinical content, no seeded patient data, no draft/encounter tables. Backend unchanged. Seams T1/T9.

### Pinned / added deps (S01–S04 pins unchanged; S05 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`).
- Frontend pins carry forward: `react 19.3.0` (+ `react-dom 19.3.0`), `vite 8.3.2`, `typescript 7.0.2`, `@vitejs/plugin-react 6.1.1` (`web/package.json`); E2E runner `@playwright/test 1.63.0` with `chromium` + `firefox` projects (`playwright.config.ts`). No new runtime libs; no clinical packages.
- `web/vite.config.ts` preview proxy for `/api` (`preview.port 5173`, `preview.proxy /api → http://localhost:8000`) carries the dev `/api` proxy into the preview server used by `make test-e2e`; no new make target.

### Red→green slices + seams (T1/T9; no test-only production endpoint)

- Login → role dashboards + Register guidance + exact research warning (`W/features/identity/LoginPage.tsx`, `W/app/pages.tsx`, `W/features/identity/ResearchWarning.tsx`; `e2e/identity.spec.ts`): role-selected login lands on `Administrator dashboard` vs `Physician dashboard`; Register surface shows `Contact administrator` with no register link/button/route; every physician login blocks on the exact research-warning `alertdialog` before the dashboard renders, with acknowledgement scoped to the tab session (refresh keeps dashboard without a second blocking dialog; sign-out clears it so the next login blocks again; admin never sees the warning).
- Identity/sign-out + own password form (`W/features/identity/auth.tsx`, `W/features/identity/PasswordForm.tsx`): header identity + `Sign out` clears the session and guards `#/dashboard` back to sign-in; `Account` hosts the `Change password` form (current/new password, generic error on wrong current password, `Password changed.` on success with fresh CSRF adopted); route guards complement server checks and never replace them.
- Admin physicians table/forms with ETag/If-Match + retain/discard revision 0 + limit=100 total fix (`W/features/identity/PhysiciansPanel.tsx`, `W/features/identity/api.ts`): table lists safe fields only with `ETag`-backed rename (`If-Match: "<revision>"`, `412 STALE_REVISION` on mismatch); deactivate requires explicit retain/discard with `draft_set_revision 0` current (any other revision `409`); list uses one bounded page `GET /physicians?limit=100` at the server maximum with the rendered `total` keeping truncation visible; create/deactivate/reactivate send fresh `Idempotency-Key`; loading/empty/error states render from real endpoints with `Retry` (transport-abort recovery covered).
- Light canonical + deliberate dark tokens (`W/shared/theme.css`): light values are the canonical palette verbatim; dark values are deliberate (deep slate-navy canvas, bright/teal character retained); primary buttons use `--primary-action` `#06786D` with white label (`5.36:1`; hover `#065F57` `7.55:1`) because teal `#0A9E8F` on white is `3.33:1`; dark `--primary-action` `#2DD4BF` with `#04211d` label (`9.09:1`); `--ink-subtle` reserved for disabled/placeholder text only; `prefers-reduced-motion` kill-switch removes transitions/animations; visible `:focus-visible` outline plus skip link and keyboard-operable theme toggle with associated error labels (`role=alert`, `aria-describedby`).
- Seams: T1 (login/session/physician/preferences HTTP) + T9 (browser journeys). No T2–T8/T10 exercised in S05.

### Verification evidence (implementation-session report, S05 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`); web build passes.
- `make test-backend` 84 passed unchanged (22 `test_physicians.py` + 22 `test_identity.py` + 39 `test_contracts.py` + 1 `test_health.py`); no backend change in S05.
- `make test-e2e` 54 passed = 27 chromium + 27 firefox, both browsers: per browser 19 (`e2e/identity.spec.ts`) + 7 (`e2e/themes.spec.ts`) + 1 (`e2e/smoke.spec.ts`). Theme contrast/keyboard evidence via computed `--primary-action`/canvas/focus assertions; build passes.

### Local loop for next agent (S06+)

- Routes (hash router, `W/app/router.ts`): `/#/login`, `/#/dashboard`, `/#/physicians`, `/#/account`; e2e base is preview `:5173` with `/api` proxied to backend `:8000`.
- Cookie/session: `x_insight_session` HttpOnly cookie + per-session CSRF token kept in memory with `sessionStorage` backup across refresh (`W/features/identity/api.ts`); mutations send `X-CSRF-Token` header with `credentials: include`; `Idempotency-Key` (fresh UUID per physician create/patch/deactivate/reactivate) + `If-Match: "<revision>"` on physician patch.
- Theme loop: `PATCH /me/preferences` (`{theme: light|dark}`, CSRF) persists; `GET /me` reflects the stored theme on load/refresh; toggle re-labels `Switch to dark/light theme` and restores `light` at the end of the admin-persistence spec for a stable starting point.
- Fixtures: `e2e/helpers.ts` `uniqueName` (timestamp + random, lowercased), `ensurePhysician` (admin login + `POST /physicians` with `Idempotency-Key`), `loginAs`/`acknowledgeWarning`/`signOut`; each e2e run accumulates ~28 physicians with headroom to the bounded `limit=100` total.
- S01–S04 loops unchanged (`make setup/dev/stop`, `make migrate`, host-PG `DB_PORT`/`TEST_DB_PORT` preflight; `db-test` `5433` down caveat from S03 still applies if observed).

### Handoff

- Next engineering session: **S06** (patient registration) — builds on the S05 hash-router/app shell, session/CSRF/idempotency client (`W/features/identity/api.ts`), and admin physician provisioning path; S05 leaves patient/directory/draft work to S06+.
- Dashboards are navigation placeholders, not directory/drafts: physician dashboard shows `No clinical content` with no recommendation text; no placeholder medical recommendations were added.
- `backend/README.md` unchanged in this docs step: it already points to the repo-root `make` loop plus the `make migrate` entry point; no new make target or app-code change in S05. No `web/` README exists to update.
- Unrelated content untouched: `content/review-ledger.md`, BNs, medical docs, `backend/src`, `Makefile`, `compose.yaml` unchanged; no clinical claims added here.

### Code-review follow-ups (non-blocking; not approval, no clinical content)

- Shared form-error helper: login/password/physician forms repeat the `ApiError` → field/form-error mapping; consider one shared helper when the next form lands.
- Split PhysiciansPanel: table + create/edit/deactivate forms share one module; consider splitting per form when patient/draft forms land.
- Shared role-map: role strings map in several places (login select, guards, dashboard copy); consider one shared role-map.
- Revision type: physician `revision` threads as plain `number` through props/API; consider a branded `Revision` type when draft flows add their revision contract.
- Spec notes (all non-blocking, future-scope or minor): admin password-change uses the same `Change password` form and is untested in the browser; zoom/reflow not checked; same-tab re-login acknowledgement reset is covered while cross-tab/session edge remains to verify; Register `Contact administrator` casing is pinned as written; client falls back to `err.message` vs generic text on non-`ApiError` failures — none blocks S05 exit.

### Deferred items (S05 conventions reused; enforcement lands with consumers)

- Empty-table state: unreachable at current scale (admin + accumulated e2e physicians always present); renderer exists but has no exercised empty case.
- Pagination chrome: no pager under ~10 physicians (plan.md §2.1 pool size); single `limit=100` page plus visible `total` is the current contract.
- 429 hammer: login-throttle hammering deliberately not exercised from the browser; covered by backend T1 tests.
- CSRF fresh-tab coverage: new-tab-without-`sessionStorage` mutation path relies on the stored login CSRF; revocation/rotation semantics stay covered by backend tests.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S05 adds no new clinical blocker.
- S05 exit met: administrator can create a physician through the browser, both roles can log in and change own password/theme, role navigation and both themes hold across reload in both browsers.

## Content Q/A decisions — 2026-10-04

- S12 may draft structured exposure, duration, trial-adequacy, prior-response, and monitoring fields for review; dose, route, and frequency remain excluded. Concrete inventory and severity definitions still require review.
- Reviewed limited-coverage DDI releases are permitted before full-corpus review; explicit coverage declarations and `coverage_unavailable` behavior remain required.
- Current inventory after scope revisions: five registration questions (R2, R4–R7) and six follow-up questions. Active question sessions are S27 and S29–S38; remaining identifiers retain their meanings. Development requirements, architecture/design, plan, tasks, ledger and blocker counts reflect eleven packages. No concrete package was approved.

### Content Q/A follow-up — 2026-10-04

- The short per-effect severity-definition proposal needs clarification: the earlier “No” was interpreted as rejection, and a subsequent question asks why. No rejection rationale or replacement severity/instrument contract is established. Full instruments and severity removal have not been authorized.
- The earlier scope reduction resulted from an unavailable Bayesian network; subsequent removal leaves five registration and six follow-up questions. Scope rationale and its history are recorded in `context/content-scope.md`.

### Current content scope — 2026-10-04

- Registration contains five questions (R2, R4–R7), with six follow-up questions and eleven packages total. Active authoring sessions are S27 and S29–S38. Removed questions have no workflow, review, activation, or generation dependency; remaining identifiers remain stable.
- Severity proposal and its rejection rationale require clarification; no clinical reason for rejecting it has been established.

### Adverse-effect questionnaire decision — 2026-10-04

- Full standardized questionnaires are selected over short custom severity definitions and network-state-only inputs. S12 authors complete versioned instrument contracts and independent examples for owner review; no concrete instrument package is approved. This review is outside the S08 exception.
- Supplied criteria contain BARS/SAS instrument sections; the tardive-dyskinesia criteria reference AIMS without a complete form. Complete AIMS sourcing, acute-dystonia instrument/source and required-completion applicability remain open. No new score bands, diagnostic thresholds or missing-data rules were chosen.

### Adverse-effect form applicability and naming — 2026-10-04

- Full questionnaire completion is required only for the corresponding present effect; absent/not-assessed effects have no completion requirement. This resolves the earlier applicability question.
- The acute-dystonia form name is “Acute Dystonia Dx Criteria”. Supplied acute-dystonia criteria are the drafting lead; concrete items, completeness, severity and network mappings remain for review. Naming does not establish a validated numeric scale or authorize an invented score.
- Complete AIMS sourcing and concrete instrument-package review remain open. No concrete package was approved.

### Pharmacotherapy scope and BN-06 retention — 2026-10-04

- Registration pharmacotherapy preserves BN-04's established-treatment review scope; initial medication selection is outside this question. Retained graph/mappings and applicability when no established treatment exists still require review.
- BN-06-derived continuation content is retained within the experimental/educational scope. Derived packages record the owner decision and the supplied STATEMENT-06 electronic decision-support/quality-measure limitation; the limitation remains explicit. Concrete graph, templates and wording still require review; no package was approved.

### Pharmacotherapy applicability and LAI scope — 2026-10-04

- No established treatment does not mark registration pharmacotherapy not_applicable. The question remains applicable; S27 defines source-compatible mappings for that case. Missing/unknown required network inputs still pause rather than being guessed.
- LAI preserves BN-10's existing discussion/review scope; specific product selection and a new product-choice contract are outside the question. Existing package identity is retained, while displayed wording and template outputs use the preserved scope. Concrete packages still require review.

## S06 — Register and find a patient (2026-10-04)

**Scope:** patient registration + shared directory only (tasks.md S06; plan.md §§2.2–2.3, 4; FR-10, FR-23). No delete/merge, no archive route, no draft autosave/wizard (S07), no clinical assessments. Seams T1/T9.

### Pinned / added deps (S01–S05 pins unchanged; S06 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`).
- Frontend pins carry forward: `react 19.3.0`, `vite 8.3.2`, `typescript 7.0.2`, `@playwright/test 1.63.0` with `chromium` + `firefox` projects. No new runtime libs; no clinical packages.
- S06 storage is migration `0004_patient_registration.py`: new tables `patients` + `encounters` on already-pinned `sqlalchemy`/`alembic`/`psycopg` (PG UUID + TEXT identifier) only.

### Red→green slices + seams (T1/T9; no test-only production endpoint)

- Slice 1 — physician-only atomic create (`B/cases/patients.py::create_patient_with_draft`, `B/cases/router.py::POST /patients`, `BT/http/test_patients.py` create tests): valid physician create atomically returns patient + registration draft (`kind='registration'`, `lifecycle='draft'`) + server timestamp + revision; admin create → `403`, unauthenticated → `401`; identifier `0012345678` round-trips unchanged as TEXT.
- Slice 2 — demographics validation (`validate_registration_payload`/`normalize_person_name`/`validate_identifier`; 26 invalid + 5 missing + 1 Unicode cases): ages 17/100, decimal/float/bool/numeric-string ages, non-ASCII digits, malformed ID, missing sex/status, invalid name characters fail with `422` field errors; one valid Unicode-letter name after NFC normalization passes.
- Slice 3 — uniqueness + idempotency (DB `UNIQUE(identifier)` incl. archived rows + `patients.create` per-`(operation, actor, key)` store): simultaneous same-ID requests produce exactly one patient; duplicate archived ID also `409 CONFLICT`; repeated same key + same body replays original IDs without duplicate audit rows; same key + changed body → `409 IDEMPOTENCY_CONFLICT`.
- Slice 4 — shared directory + browser forms (`search_patients`, `GET /patients`, `W/features/patients/api.ts`, `validation.ts`, `DirectoryPage.tsx`, `RegistrationForm.tsx`; `e2e/registration.spec.ts` 8 journeys × 2 browsers): name/ID substring search (case-insensitive), `clinical_status` filter, bounded `limit`/`offset` pagination, `include_archived` opt-in; results shared across physicians; `Next`/`Register` stays disabled until every field validates; routes `#/patients` + `#/patients/new`.
- Seams: T1 (patient HTTP) + T9 (browser create/search). No T2–T8/T10 exercised in S06.

### Verification evidence (implementation-session report, S06 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 131 passed = 84 (S05 set: 22 physicians + 22 identity + 39 contracts + 1 health) + 47 new (`tests/http/test_patients.py` — 3 create/authz + 32 validation/Unicode + 1 audit + 5 uniqueness/idempotency + 3 search/share + 3 grants/index guards).
- `make test-e2e` 70 passed = 54 S05 (27 chromium + 27 firefox) + 16 new (`e2e/registration.spec.ts` 8 journeys × 2 browsers).
- `make migrate` cycle: fresh upgrade to head `0004`; `0003↔0004` downgrade/upgrade cycle returns to `0004` with `patients` + `encounters` present; tables are exactly `alembic_version, audit_events, users, sessions, idempotency_records, patients, encounters`.
- Health/ready preserved: `GET /api/v1/health` still exactly `{"status":"ok","service":"x-insight"}` with no DB access; `GET /api/v1/ready` returns `200` with `schema_version 0004` against the migrated DB.
- Test report: no new tests needed beyond the 47 + 8×2 above. Race verdict: live-HTTP `login→GET /me` shows 22.5% `401` parallel / 5% sequential (session-cookie race in live sockets, TestClient-masked); patient-identifier uniqueness holds 3/3 passes under concurrency; single retry-once in e2e helpers is acceptable and does not mask the product race.

### Local loop for next agent (S07+)

- Routes (all under `/api/v1`): `POST /patients` (`{identifier, given_name, family_name, sex: M|F, age: StrictInt 18–99, clinical_status: first_time|established, phone?}` → `201` `{patient, draft, encounter, server_timestamp, revision}`), `GET /patients/{id}` (`{patient}`, any active session, `404` unknown), `GET /patients` (`{items, total, limit, offset}`; params `query?`, `clinical_status?`, `limit?`, `offset?`, `include_archived=false` default; archived excluded unless `include_archived=true`).
- Auth: create needs active physician session + `X-CSRF-Token` (missing/wrong → `403`); admin create → `403`, unauthenticated → `401`; reads need any active session cookie. `Idempotency-Key` format gate stays in middleware (`422` malformed); per-command `patients.create` store replays same key + same body, `409` on same key + changed body.
- Revision: S06 returns `revision` as a body field (patient `revision 1`, draft/encounter `revision 1`); no `ETag`/`If-Match` on patient routes — the revision contract is carried for S07 stale-tab `412` use.
- Fixtures: `test_patients.py::physician_client/admin_client` handoffs (TestClient + cookies + `csrf`); `clean_patients` truncates `encounters, patients, sessions, users, audit_events` + best-effort `idempotency_records`; e2e `helpers.ts::ensurePhysician/ensurePatient/physicianSession/uniquePatientId/uniqueLetters`.
- Identifier-as-text rule: identifier is exactly ten ASCII digits stored/transmitted as TEXT (never `Number`); `[0-9]` (not `\d`) so fullwidth/Arabic-Indic digits fail; leading zeros preserved end-to-end. Phone is optional free text with no country validation.
- S01–S05 loops unchanged (`make setup/dev/stop`, `make migrate`, host-PG `DB_PORT`/`TEST_DB_PORT` preflight).

### Handoff

- Next engineering session: **S07** (persist, resume, discard author-owned drafts) — builds on the S06 `patients` + `encounters(kind='registration', lifecycle='draft')` rows, the single-open-draft partial index, the returned `draft`/`encounter` IDs + `revision`, and the `W/features/patients/` shell/API/validation; S06 leaves autosave, stale-revision reconcile, slot-conflict, and discard-confirmation to S07.
- `backend/README.md` unchanged in this docs step: it already points to the repo-root `make` loop plus the `make migrate` entry point (verified this session); no new make target needed for S06.
- Unrelated content untouched: `content/review-ledger.md`, BNs, medical docs unchanged; no clinical claims added here.

### Code-review follow-ups (non-blocking; not approval, no clinical content)

- Standards: `cases/router.py` repeats local `error_response`/`get_request_id` (S03–S04 carry-over, now third copy), `_require_*` auth + `_idempotency_key_or_none`/`_idempotency_conflict` duplicate the identity idempotency path — consider one shared error/auth/idempotency helper when the next route module lands; `patients/api.ts` forwards a caller-supplied `idempotencyKey` instead of always minting fresh — pin the fresh-per-submit rule at the call site.
- S05 carry-overs retained: shared form-error helper (registration form repeats `ApiError` mapping), split patient panels when wizard pages land, branded `Revision` type when S07 adds its revision contract, backend + e2e fixture duplication (`clean_*`/`admin_client`, `ensurePhysician/loginAs`) — consider shared modules with the next suite.
- Spec notes (all non-blocking, by design or minor): no archive route is S51 scope, not an S06 gap; `Next` gating a single `Register` submit (not a multi-step wizard) is the S06 contract until S07; `draft` + `encounter` carry the same object (plan calls it encounter, S06 calls it draft); TestClient concurrency (3/3 uniqueness) does not cover the live-socket `GET /me` race — that race is reported, not hidden; empty-phone `None` vs `""` branch is unreachable through the current form but pinned by tests.

### Deferred items

- Deactivation `draft_action` still resolves against the empty set (`EMPTY_DRAFT_SET_REVISION 0`); real open-draft IDs + job cancellation + slot occupancy wait for S51 with S07 slot mechanics.
- Cursor pagination, audit HTTP route, named role logins: unchanged from S02–S04 — `parse_pagination` stays limit/offset (25/100), `list_audit_events` stays a module call, named logins stay deployment-only.
- Per-command idempotency now covers `patients.create`; generate/adjust/reset/retry/accept/sign/note/addendum stores still land with their consuming sessions.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S06 adds no new clinical blocker.
- New ordering blocker for S07/S51: session commit ordering — S07 draft/autosave and S51 discard/retention must sequence against the S06 registration commit (patient + first open draft) so re-entry never double-creates or orphans the slot; confirm against the reviewed revision.
- Infra note: `dev-*` subagent runs on the `#xhigh` model failed during S06; fallback is the general agent path until the model route is restored.
- S06 exit met: physician registration returns patient + draft + timestamp + revision with text-identifier round-trip, validation/uniqueness/idempotency hold under concurrency, and the shared directory + browser create/search pass in both browsers.

## S07 — Persist, resume, and discard author-owned drafts (2026-10-04)

**Scope:** author-owned draft persist/resume/discard only (tasks.md S07; plan.md §§2.3, 4; FR-16, FR-22, NFR-04). No delete/merge, no archive route, no assessments/DDI/notes/signing. Seams T1/T9. Implemented worktree vs HEAD `98a9315`.

### Pinned / added deps (S01–S06 pins unchanged; S07 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`).
- Frontend pins carry forward: `react 19.3.0`, `vite 8.3.2`, `typescript 7.0.2`, `@playwright/test 1.63.0` with `chromium` + `firefox` projects. No new runtime libs; no clinical packages.
- S07 storage is migration `0005_encounter_draft_content.py`: adds `draft_data JSONB NOT NULL DEFAULT '{}'::jsonb` on `encounters` + `ck_encounters_draft_data_object` (`jsonb_typeof = 'object'`) on already-pinned `sqlalchemy`/`alembic`/`psycopg` only. Existing S06 rows backfill to `'{}'`; no demographics touched.

### Red→green slices + seams (T1/T9; no test-only production endpoint)

- Slice backend — private resume/412/slot+discard + route shapes (`B/cases/encounters.py`, `B/cases/router.py`, `BT/http/test_drafts.py` 12 tests): author saves/retrieves a private draft after restart while other physicians/admin get `403` without content on ordinary routes (transitive privacy — directory/demographics serializers never carry `draft_data`); stale-tab `PATCH` without/with-mismatched `If-Match` fails `412 STALE_REVISION` leaving server truth unchanged; failed validation never shows Saved; `PATCH` idempotency replays without double-bump while same key + changed body is `409`; sequential + concurrent creates (incl. same author) allow one patient draft only via patient-row lock + `one_open_draft_per_patient` backstop with generic `409 OPEN_DRAFT_EXISTS`; discard requires explicit confirmation + current revision, releases the slot atomically (`draft` → `discarded`, discarded rows read `404`), never deletes the patient; `cancel_draft_jobs` hook is a no-op (no job tables fabricated); audit `encounters.create/patch/discard` attributed with safe details.
- Slice frontend — wizard/autosave/conflict/discard/nav-guard (`W/features/encounters/api.ts`, `EncounterPage.tsx`, `useAutosave.ts`, `W/app/navigationGuard.ts`, `W/app/router.ts` `#/encounters/:id`, `e2e/autosave.spec.ts` 8 journeys × 2 browsers): ~1 s debounced autosave + page-transition flush with saving/saved/failed states; full `draft_data` object each save with GET `ETag`/`revision` as `If-Match` and fresh `Idempotency-Key` per attempt; `412` keeps local edits and offers reload/reconcile; failed network save never shows Saved with Retry recovery; navigation warns while unsaved edits remain; unsent registration form stays local-only; discard needs explicit confirmation + frees the slot; occupied slot and stranger reads show generic/author-only denials without content.
- Seams: T1 (encounter HTTP) + T9 (browser autosave/conflict/discard). No T2–T8/T10 exercised in S07.

### Verification evidence (implementation-session report, S07 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 143 passed = 131 (S06 set) + 12 new (`tests/http/test_drafts.py` — author resume/restart, stranger privacy, 412/reconcile, validation/idempotency, sequential + concurrent slot, discard-confirmation, grants/check, audit, 404).
- `make test-e2e` autosave file 16/16 (`e2e/autosave.spec.ts` 8 journeys × 2 browsers); full run 84 passed + 2 failed on identity-rename — pre-existing S04/S05 debt (physicians `limit=100` pagination: accumulated e2e physicians exceed 100, rename target falls off the single page), not an S07 regression.
- `make migrate` to head `0005`: fresh upgrade records single head `0005`; tables are `alembic_version, audit_events, users, sessions, idempotency_records, patients, encounters` with `draft_data` present.
- Restart recovery proven (author save → restart → `GET` resumes private draft); health/ready preserved (`health` exact body, no DB access; `ready` `200` with `schema_version 0005`).

### Local loop for next agent (S08+)

- Routes (all under `/api/v1`): `POST /patients/{id}/encounters` (`{kind: registration|follow_up}` → `201` `{encounter, draft_data, revision, server_timestamp}`, physician-only, `409 OPEN_DRAFT_EXISTS` when slot occupied), `GET /encounters/{id}` (`{encounter, draft_data, revision}`, author-only `403` strangers, discarded → `404`), `PATCH /encounters/{id}` (`{draft_data, expected_revision?}` with required `If-Match: "<revision>"` → `412 STALE_REVISION` on mismatch, `422` on invalid body), `POST /encounters/{id}/discard` (`{confirm: true, expected_revision}` + `If-Match` → `{encounter, revision, server_timestamp}`). S06 patient routes unchanged.
- ETag/`If-Match` + `Idempotency-Key` rules: responses carry `ETag: "<revision>"` via `contracts.format_etag`; `PATCH`/discard parse `If-Match` via `contracts.parse_if_match` (absent/malformed → `412`/`422`, never silent overwrite); mutations send fresh `Idempotency-Key` per attempt (retries of the same attempt reuse the key); per-command stores (`encounters.create/patch/discard` keyed `(operation, actor, key)`) replay same key + same body, `409 IDEMPOTENCY_CONFLICT` on same key + changed body.
- Revision ownership for later assessment pages: every wizard page autosaves through `PATCH` here with the GET revision as `If-Match`; `draft_data` stays one opaque versioned object — later pages extend its keys instead of adding persistence mechanisms; `412` → `GET` truth → reconcile → retry with the new revision; only a `2xx` response is durable ("Saved").
- Fixtures: `test_drafts.py::physician_client/admin_client` handoffs, `clean_registry` truncates `encounters, patients, sessions, users, audit_events` + best-effort `idempotency_records`; e2e `autosave.spec.ts` helpers (`ensurePatient`, physician sessions, unique IDs).
- Identifier-as-text unchanged: patient identifier stays exactly ten ASCII digits as TEXT (`[0-9]`, never `Number`); leading zeros preserved end-to-end (S06 rule, S07 untouched).
- S01–S06 loops unchanged (`make setup/dev/stop`, `make migrate`, host-PG `DB_PORT`/`TEST_DB_PORT` preflight).

### Handoff

- Next engineering session: **S08** (experimental assessments + evaluation interface) — builds on the S07 autosave path (`GET/PATCH /encounters/{id}`, `draft_data` + revision contract) so assessment pages reuse it; S07 leaves assessment/history/DDI/question-package work to S08+.
- `backend/README.md` unchanged in this docs step: it already points to the repo-root `make` loop plus the `make migrate` entry point (verified this session); no new make target needed for S07.
- Unrelated content untouched: `content/review-ledger.md`, BNs, medical docs unchanged; no clinical claims added here.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S07 exit blockers)

- Standards: 0 hard blockers; worst is the duplicated encounter-id parser (route-level id parsing repeats the service parse — consider one shared parse helper when the next route module lands). S06 carry-overs retained (shared error/auth/idempotency helpers, shared form-error helper, shared fixtures).
- Spec partials (all non-blocking): derived-artifact privacy currently covers only `draft_data` (chart/report routes do not exist yet — transitive privacy extends there when they land); deactivation `draft_action` wiring waits for S51 (real open-draft IDs + job cancellation + slot occupancy); discard stale-revision closure edge, unmount-flush `clearTimer`-only path, and `409` no-reconcile UI stay as future polish.

### Deferred items

- Cursor pagination, audit HTTP route, named role logins: unchanged from S02–S06 — `parse_pagination` stays limit/offset (25/100), `list_audit_events` stays a module call, named logins stay deployment-only.
- Per-command idempotency now covers `encounters.create/patch/discard`; generate/adjust/reset/retry/accept/sign/note/addendum stores still land with their consuming sessions.
- Deactivation `draft_action` still resolves against the empty set (`EMPTY_DRAFT_SET_REVISION 0`); real open-draft IDs + job cancellation + slot occupancy wait for S51 with S07 slot mechanics.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S07 adds no new clinical blocker.
- Infra debt (pre-existing, breaks rename test when >100 users): physicians list is a single bounded page `limit=100` with visible `total` — accumulated e2e physicians exceed 100, so identity-rename falls off the page; pagination chrome waits for its owning session.
- S07 exit met: author saves/resumes a private draft after restart, stale-tab `412` reconciles, failed saves never show Saved, one-patient-one-draft holds under concurrency, and confirmed discard releases the slot without deleting the patient.

## S08 — Define and release experimental assessments and implement the evaluation interface (2026-10-04)

**Scope:** experimental assessments + evaluation interface only (tasks.md S08; plan.md §5; FR-11–13, NFR-05). No owner review — tasks.md S08 exit plus `content/review-ledger.md` §1 resolved policy; neither S08 nor its packages is `awaiting_review`, and no assessment approval gates are added. Seams T2/T1. Implemented worktree vs HEAD `4fd4f35`.

### Pinned / added deps (S01–S07 pins unchanged; S08 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S08).
- New modules `backend/src/x_insight/assessments/{__init__,engine,released,router}.py`: `engine.py` is stdlib-only (`typing`); `released.py` adds stdlib `json`/`os`/`pathlib`; `router.py` reuses already-pinned `fastapi`/`sqlalchemy`. No new runtime libs; no clinical packages.
- `B/app.py` +4 additive router lines only (`assessments_router` import + `include_router` under `/api/v1`); no existing route touched.

### Red→green slices + seams (T2/T1; no owner-gated content)

- Slice engine — validated definition loading + `evaluate(definition, answers)` via a tiny synthetic definition first (T2; `BT/assessments/test_definitions.py`): unanswered/partial/complete/not-assessed are distinguishable and no missing answer becomes zero; undeclared item IDs, invalid values, unknown rule operators, and arbitrary executable expressions are rejected (small allowlisted declarative rule set per instrument, validated at load; no `eval`/`exec` in `engine.py`). Engine is pure (no I/O/DB); `evaluate` returns `{status, missing_item_ids, item_errors, scores, findings, definition_version}`.
- Slice released packages — three versioned assessment packages released after schema/rule validation plus independently worked reference examples (T2; `content/assessments/*.v1.json`, hashes read from files this session): `diagnosis.v1.json` (13 items; source `schizophrenia-criteria.md` sha256 `6d47f73b853e39ac89ae8e2cb5c514f3a0fec386e21520a8d54b69be51cecbd0`; 9 reference examples; released for S09), `panss.v1.json` (30 items; source `PANSS.md` sha256 `644a291ac807533b78cec4a20b40acd23e515d6f8c7fd28c5e7773ba83d2ee1e`; 7 reference examples; released for S10), `cssrs.v1.json` (29 items; source `CSSRS.md` sha256 `610ecf937795c695828d15376a2555e3e153f97307f7b758dc9c1e7b6bdc9ced`; 7 reference examples; `no_composite_score: true`; released for S11). Each release block records version, source hashes, assumptions, source gaps, experimental defaults, and validation results with `reviewer: none`, `approval: none`; no package contains `awaiting_review`, and the only clinical-validation mention is the disclaimer denying it (`no_treatment_thresholds` on diagnosis/PANSS).
- Slice routes — authenticated released-definition read (T1; `B/assessments/router.py`): `GET /api/v1/content/assessments/{diagnosis|panss|cssrs}` returns `{type, version, definition}`; any active physician/administrator session may read, missing/revoked sessions get `401`, unknown types `404`; reads need no CSRF (patient-directory contract); failures use the standard `contracts.ErrorBody`.

### Verification evidence (re-verified in this docs session)

- `make check` exit `0` (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`), re-run this session.
- `BT/assessments/test_definitions.py` 56 passed (re-run from `backend/` with `.env` sourced).
- Full backend suite 199 passed = per-file collection counts verified this session: 56 (`assessments/test_definitions.py`) + 39 (`http/test_contracts.py`) + 12 (`http/test_drafts.py`) + 1 (`http/test_health.py`) + 22 (`http/test_identity.py`) + 47 (`http/test_patients.py`) + 22 (`http/test_physicians.py`).
- Env quirk (procedural only, no code impact): tests require sourcing `.env` (disposable DBs on `5442`/`5443`; defaults `5432`/`5433` refused) and the backend suite must run from `backend/` (alembic `migrations` path — running from repo root errors at collection on the 3 HTTP route tests).

### Local loop for next agent (S09+)

- Routes (all under `/api/v1`): `GET /content/assessments/{assessment_type}` (`{type, version, definition}`, any active session, `401` unauthenticated / `404` unknown type, no CSRF). Consumers use the released definitions as-is; no assessment approval is ever required again.
- Evaluator contract: `evaluate(definition, answers)` → `{status, missing_item_ids, item_errors, scores, findings, definition_version}`; unanswered/partial/complete/not-assessed stay distinguishable (missing never zero); diagnosis-only `answers {"__bypass": true}` → `bypassed` with actor/time attribution owned by the consumer (S09).
- Experimental defaults, read from the released JSONs this session (S09–S11 build on these, not on owner input):
  - Diagnosis: explicit `unknown` answers permitted and distinct from unanswered; Kleene/3-state criterion logic (`_tri`/`_kleene_and`/`_kleene_or`/`_kleene_not` in `engine.py`); a complete record with no `not_met` criterion but some `unknown` is `indeterminate` overall and is never labeled `below_threshold`; `c_shortened_by_intervention=yes` satisfies the 1-month active requirement alongside continuous 6-month signs; `f_prominent_psychosis` required iff `f_autism_present=yes`, otherwise F is satisfied without it.
  - PANSS: rating window `previous_7_days`; fresh form has 30 unanswered items and null scores (never pre-filled 1s); Skip is `answers {"__skipped": true}` → `not_assessed`; total reported only when all 30 required items are validly answered (subscale only when its own items are complete), missing suppresses aggregates to null never zero; total bands are `informational_only` + `not_a_treatment_gate`; percent-change formula `(baseline - follow-up) / baseline × 100` with the baseline-zero edge intentionally undefined here for the S37 no-improvement definition.
  - C-SSRS: ideation wording is a concise paraphrase (authorized form governs exact administration); windows are recent ideation `past_month`, recent behavior `past_3_months`, `lifetime`; no branching skips (higher-level yes never fills lower levels); intensity rated for the most severe recent ideation, required iff any recent ideation (levels 1–5) endorsed; lethality codings required iff an actual attempt occurred in that window, potential lethality additionally requiring actual damage 0; NSSI documented separately (contributes to `clinical_review`, never `high_risk_alert` alone); no composite score; emergency concern (current intent/plan, attempt in progress, inability to stay safe) is clinician-determined, never calculated.
- Fixtures: `test_definitions.py` slices (engine/validation/reference-examples) are the independently worked examples; S07 autosave path (`GET/PATCH /encounters/{id}`, `draft_data` + revision contract) unchanged for assessment pages.

### Handoff

- Next engineering sessions: **S09** (diagnosis/threshold/bypass) builds on diagnosis v1 + the S07 autosave path; **S10** on panss v1; **S11** on cssrs v1.
- `backend/README.md` unchanged in this docs step: it already points to the repo-root `make` loop plus the `make migrate` entry point (verified this session); no new make target needed for S08. It needs no change.
- Unrelated content untouched: `content/review-ledger.md` §§2–7 owner gates unchanged (§1 S08 no-review policy stands); BNs, medical docs unchanged; no clinical claims added here.
- S08 exit met: executable definition contract + three released packages with passing validation/reference examples, with no owner review/approval/sign-off/response, never `awaiting_review`.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S08 exit blockers)

- Standards: 0 hard blockers + 5 smells (duplicated error/request-id helpers 4th copy + drift; `JSONResponse` alias double import in `router.py`; Repeated Switches on instrument across engine helpers; public `seen_or_all` staging seam; undocumented `X_INSIGHT_ASSESSMENTS_DIR` override in `released.py`; test-file Divergent Change in `test_definitions.py`).
- Spec: 3 partials (reference examples live externally in `test_definitions.py`, not embedded in the packages; rule payload shape not validated at load; PANSS baseline-zero case deferred to S37) + 1 scope question (`total_band` interpretive labels marked informational) + 2 inconsistencies (partial-score shapes differ missing-vs-invalid; NSSI counted toward `clinical_review` as documented default).

### Deferred items

- PANSS baseline-zero percent-change edge → S37 no-improvement definition (explicitly left undefined in panss v1).
- Rule payload shape validation at load; embedding reference examples in packages; S02–S07 deferred items (cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands) all unchanged.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S08 adds no clinical blocker.
- S08 exit met: executable contract + three released packages, no owner review/approval/sign-off/response, never `awaiting_review`.

## S09 — Implement diagnosis, threshold warning, and bypass (2026-10-04)

**Scope:** diagnosis page state only (tasks.md S09; plan.md §§2.2, 5; FR-11, FR-16). No owner review beyond the S08 release — S09 consumes the S08-released `diagnosis.v1.json` as-is, adds no content approval gate, and marks nothing `awaiting_review`. Seams T2/T1/T9. Implemented worktree vs HEAD `d186d95`.

### Pinned / added deps (S01–S07 pins unchanged; S09 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S09).
- New modules: `backend/src/x_insight/assessments/diagnosis.py` (433 lines; page-state service over `draft_data["diagnosis"]`); router extensions in `B/assessments/router.py` (+276 lines tracked diff: `GET /encounters/{id}/diagnosis`, `POST .../diagnosis/acknowledgment`, `POST .../diagnosis/bypass`); `web/src/features/assessments/diagnosis/{DiagnosisSection.tsx (593 lines), api.ts (181 lines)}`; `EncounterPage.tsx` +16/−? lines (mounts `DiagnosisSection` step 2); `useAutosave.ts` +23 lines (`applyServerSnapshot`); `theme.css` +28 lines (`.xi-warning-panel`, `.xi-bypass-panel`). No new runtime libs; no clinical packages; no new migration.
- S09 files (tracked modifications + new): `backend/src/x_insight/assessments/router.py`, `web/src/features/encounters/EncounterPage.tsx`, `web/src/features/encounters/useAutosave.ts`, `web/src/shared/theme.css`, plus new `backend/src/x_insight/assessments/diagnosis.py`, `backend/tests/assessments/test_diagnosis.py` (637 lines, 14 tests), `e2e/diagnosis.spec.ts` (328 lines, 6 journeys), `web/src/features/assessments/diagnosis/DiagnosisSection.tsx`, `web/src/features/assessments/diagnosis/api.ts`.

### Red→green slices + seams (T2/T1/T9; S08 diagnosis v1 consumed as-is)

- Slice 1 — full criterion logic + live preview, no drift (T2/T1; `BT/assessments/test_diagnosis.py` slice 1): source-derived qualifying worked case satisfies every required criterion (A–F all `met`, `overall criteria_satisfied`, `can_proceed true`, `proceed_via criteria_satisfied`); a case with only symptom count satisfied (A `met`, rest failing) is `overall below_threshold` (`can_proceed false`, `requires_acknowledgment true`). Preview `GET diagnosis` returns the same `evaluate()` object as the direct T2 call (`body["evaluation"] == direct`).
- Slice 2 — partial has no completed result (T2/T1): partial answers save via the shared PATCH autosave with `status partial`, `findings {}`, no `overall` (never `below_threshold`); `unknown` stays `unknown` and a complete record with no `not_met` but some `unknown` is `overall indeterminate` (`can_proceed false`, `requires_acknowledgment false`); missing answers never become zero.
- Slice 3 — attributed acknowledgment tied to revision, invalidated by relevant edits (T1): completed below-threshold continues only after `POST acknowledgment` with empty `{}` body; server stamps actor/time/status + assessed revision + answers hash + definition version (`acknowledgment {status acknowledged, actor_id, revision, acknowledged_at, definition_version, answers_hash}`); validity is hash-derived on every read so unrelated top-level draft keys preserve validity while a flipped answer invalidates (`acknowledgment_valid false`, warning returns). Stale `If-Match` → `412 STALE_REVISION`; replayed `Idempotency-Key` returns the same revision; key reuse on a new revision → `409 IDEMPOTENCY_CONFLICT`; any `reason` field → `422`; partial/satisfied cases → `409 DIAGNOSIS_STATE_CONFLICT`.
- Slice 4 — bypass without reason, attributed, survives resume, distinct UI over shared autosave (T1/T9): `POST bypass` with empty `{}` succeeds with no reason field (`"reason" not in record`), records actor/time/status (`bypass {status bypassed, actor_id, revision, bypassed_at}`), clears answers to `{}`, survives resume via the same `draft_data` body (`GET /encounters` carries the bypass; fresh re-login sees `bypass_valid true`, `can_proceed true`, `proceed_via bypass`). UI keeps bypass distinct from completion (`.xi-bypass-panel` heading "Bypass instead of completing", no textbox/textarea in the panel, bypassed state "Diagnosis bypassed", no criteria table, no warning CTA) and resynchronizes through the shared autosave contract (`applyServerSnapshot`, one saved base, one revision fence, no separate persistence mechanism).

### Verification evidence (implementation-session report, S09 scope)

- `make check` exit `0` (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 213 passed = S08 199 + 14 new (`BT/assessments/test_diagnosis.py` 14 passed, counted via `grep -c "^def test_"` this docs session).
- `e2e/diagnosis.spec.ts` 6/6 chromium + 6/6 firefox (6 `test(` blocks counted this docs session: qualifying, symptom-only, partial, indeterminate, ack-gates-proceed/invalidate, bypass-distinct/resume).
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (no route touched; S02 contracts suite unchanged).
- `make migrate` head unchanged — no new migration (versions still `0001`–`0005`, head `0005`; S09 stores everything in `draft_data["diagnosis"]`, no new table).
- Env quirk carries forward from S08 (procedural only): tests require sourcing `.env` (disposable DBs on `5442`/`5443`) and the backend suite runs from `backend/` (alembic `migrations` path).

### Local loop for next agent (S10+)

- Routes (all under `/api/v1`): `GET /encounters/{id}/diagnosis` → `{answers, evaluation, acknowledgment, acknowledgment_valid, bypass, bypass_valid, can_proceed, requires_acknowledgment, proceed_via, revision, definition_version}` with `ETag "<revision>"`; `POST /encounters/{id}/diagnosis/acknowledgment` with `{}`; `POST /encounters/{id}/diagnosis/bypass` with `{}`; answers travel via `PATCH /encounters/{id}` with `{draft_data: {diagnosis: {answers}}}` (full-object autosave, same S07 path).
- Write shape: every mutating diagnosis call sends `If-Match: "<revision>"` (quoted ETag; missing/wildcard `*` → `422`, stale → `412`), optional `Idempotency-Key` (replay same revision → same result; reuse on new revision → `409`), and `X-CSRF-Token` (missing/invalid → `403`); author-only (`403` strangers/admin, `401` anonymous, `404` unknown encounter).
- State shape: `draft_data.diagnosis = {answers: {<12 item ids: a_delusions … f_autism_present> (+ f_prominent_psychosis when applicable): yes|no|unknown}, acknowledgment: null | {status, actor_id, revision, acknowledged_at, definition_version, answers_hash}, bypass: null | {status, actor_id, revision, bypassed_at}}`; ack/bypass mutually clear each other; bypass clears answers to `{}`.
- Indeterminate handling: complete + no `not_met` + some `unknown` → `overall indeterminate`, `can_proceed false`, `requires_acknowledgment false` (needs no acknowledgment, shows "Indeterminate … needs no acknowledgment"); partial → "Incomplete … not a below-threshold result" with `Still needed:` list; bypassed preview has no criteria table.
- Frontend selectors (e2e contract): heading "Diagnosis (step 2)", radios `#diag-{itemId}-yes|no|unknown`, verdict `#diagnosis-preview-status` (aria-live polite, deliberately not `role=status`), criteria table `#diagnosis-criteria`, warning text "Completed below threshold" + button "Acknowledge below-threshold result", bypass panel `.xi-bypass-panel` + button "Bypass diagnosis" + state "Diagnosis bypassed", gate line "Can proceed to the next step: Yes — … / No".

### Handoff

- Next engineering session: **S10** (PANSS without implicit minimum answers) builds on panss v1 + the same S07 autosave path; diagnosis page needs no further work for S10.
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus `make migrate`; no new make target needed for S09).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged (§1 S08 no-review policy stands; S09 uses the S08 release as-is); BNs, medical docs unchanged; no clinical claims added here.
- S09 exit met: evaluator reference cases, direct HTTP enforcement, browser threshold/bypass/resume.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S09 exit blockers)

- Standards: diagnosis.py direct encounters-table I/O vs plan §3.2 owning-module rule (borderline-hard); duplicated If-Match/idempotency/response bodies in router; duplicated revision-bump/audit tail; thin forwards; repeated switches; DiagnosisSection duplicated 401/412 branches + sectionOf default.
- Spec: gate is advisory can_proceed not enforced transition (enforcement lands with proposal/sign S46/S49); S08 __bypass evaluator path has no reference case, sidecar stores answers {} so preview status unanswered not bypassed (align or add __bypass case in follow-up); ack/bypass idempotency/audit/mutual-clearing beyond minimal spec (harmless); stale aria role=status comment.

### Deferred items

- S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands).
- S09 code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S09 adds no clinical blocker.
- S09 exit met: evaluator reference cases, direct HTTP enforcement, browser threshold/bypass/resume.

## S10 — Implement PANSS without implicit minimum answers (2026-10-04)

**Scope:** PANSS page state only (tasks.md S10; plan.md §§2.2, 5; FR-12, FR-20). No owner review beyond the S08 release — S10 consumes the S08-released `panss.v1.json` as-is, adds no content approval gate, and marks nothing `awaiting_review`. No acknowledgment, no bypass, no treatment gate from score bands, no default `1` values, no hidden zero. Seams T2/T1/T9. Implemented worktree vs HEAD `dc05cd8`.

### Pinned / added deps (S01–S07 pins unchanged; S10 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S10).
- New modules: `backend/src/x_insight/assessments/panss.py` (90 lines; page-state service over `draft_data["panss"]`); router extensions in `B/assessments/router.py` (+43 lines tracked diff: `GET /encounters/{id}/panss` + `_panss_response`); `web/src/features/assessments/panss/{PanssSection.tsx (485 lines), api.ts (120 lines)}`; `EncounterPage.tsx` +11/−4 lines (mounts `PanssSection` step 3 after `DiagnosisSection`); `theme.css` +16 lines (`.xi-panss-panel`). No new runtime libs; no clinical packages; no new migration.
- S10 files (tracked modifications + new): `backend/src/x_insight/assessments/router.py`, `web/src/features/encounters/EncounterPage.tsx`, `web/src/shared/theme.css`, plus new `backend/src/x_insight/assessments/panss.py`, `backend/tests/assessments/test_panss.py` (453 lines, 9 tests), `e2e/panss.spec.ts` (332 lines, 7 journeys), `web/src/features/assessments/panss/PanssSection.tsx`, `web/src/features/assessments/panss/api.ts`.

### Red→green slices + seams (T2/T1/T9; S08 panss v1 consumed as-is)

- Slice 1 — fresh form has 30 unanswered items and null scores, Skip persists `not_assessed` (T2/T1; `BT/assessments/test_panss.py` slice 1): `{}` evaluates to `status unanswered`, `missing_item_ids` == 30 `PANSS_IDS` (P1–P7/N1–N7/G1–G16), `item_errors {}`, all scores null, `definition_version v1`; `{"__skipped": true}` evaluates to `not_assessed` with all scores null. Preview `GET panss` returns the same `evaluate()` object as the direct T2 call (`body["evaluation"] == direct`), with `revision` + `ETag "<revision>"`.
- Slice 2 — exact arithmetic all-1 and all-7 via evaluator and route, bands informational only (T2/T1): all-1 → `complete` with `scores {positive 7, negative 7, general 16, total 30}` (7×1/7×1/16×1/30×1); all-7 → `complete` with `{49, 49, 112, 210}` (7×7/7×7/16×7/210); independently worked literals asserted on both direct `evaluate()` and `GET` preview (no drift). `findings.total_band` is `informational_only true` + `not_a_treatment_gate true` on both extremes (no treatment gate, no owner review, no `awaiting_review`).
- Slice 3 — one missing suppresses total, invalid rejected server-side, resume preserves completeness (T2/T1): all-1 minus `G16` → `partial`, `missing_item_ids ["G16"]`, `total null` while `positive 7`/`negative 7` stand and `general null`; `0`/`8`/`2.5`/`"3"`/`True`/`None` on `P1` plus undeclared `PX` → `item_errors` + `status partial` + `total null` (never zero-filled), visible through the `GET` route after forged `PATCH`; resume via fresh client re-login sees the same complete `7/7/16/30` evaluation, and `GET /encounters` carries the same `draft_data.panss.answers` + `revision`.
- Slice 4 — explicit selection only in the browser, server truth, author-only read (T1/T9): fresh form has zero pre-checked radios (`input[name^="panss-"]:checked` count 0, spot-checked `#panss-P1-1/7` + `#panss-G16-1/7`), verdict "No answers yet / 30 items with nothing selected", null scores render as `—` never zeros; Skip → "not assessed" survives reload, Resume clears to "No answers yet"; seeded partial shows "Incomplete … 1 required item … suppressed to null" + `#panss-still-needed`; UI all-1 click-through yields "Complete — Positive 7 / Negative 7 / General 16 / Total 30" + "Informational only / not a treatment gate"; seeded all-7 shows 30 checked radios; forged `P1: 8` surfaces "rejected (never zero-filled)"; reload + sign-out/re-login preserves completeness. Auth carried from S07: `403` strangers/admin, `401` anonymous, `404` unknown encounter.

### Verification evidence (implementation-session report, S10 scope)

- `make check` exit `0` (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 222 passed = S09 213 + 9 new (`BT/assessments/test_panss.py` 9 passed, counted via `grep -c "^def test_"` this docs session).
- `e2e/panss.spec.ts` 7/7 chromium + 7/7 firefox (7 `test(` blocks counted this docs session: fresh, skip/resume, partial, all-1, all-7, forged-invalid, resume-relogin).
- Full e2e 110 pass / 2 fail — pre-existing identity rename `limit=100` with 584 users, unrelated to S10.
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (existing routes untouched; S10 adds only `GET .../panss`).
- `make migrate` head unchanged — no new migration (versions still `0001`–`0005`, head `0005`; S10 stores everything in `draft_data["panss"]`, no new table).
- Env quirk carries forward from S08/S09 (procedural only): tests require sourcing `.env` (disposable DBs on `5442`/`5443`) and the backend suite runs from `backend/` (alembic `migrations` path); stale `:8000` dev server must be restarted to pick up the new `GET .../panss` route.

### Local loop for next agent (S11+)

- Routes (all under `/api/v1`): `GET /encounters/{id}/panss` → `{answers, evaluation, definition_version, revision}` (+ `encounter` reference) with `ETag "<revision>"`; prompts come from `GET /content/assessments/panss` (released v1, 30 items P1–P7/N1–N7/G1–G16 `scale_1_7`); answers travel via `PATCH /encounters/{id}` with `{draft_data: {panss: {answers}}}` (full-object autosave, same S07 path). No ack/bypass POSTs on PANSS.
- Write shape: PANSS answers reuse the shared PATCH contract — `If-Match: "<revision>"` (quoted ETag; stale → `412`), optional `Idempotency-Key`, and `X-CSRF-Token` (missing/invalid → `403`); author-only (`403` strangers/admin, `401` anonymous, `404` unknown encounter).
- State shape: `draft_data.panss = {answers: {<30 item ids P1…G16>: 1|2|3|4|5|6|7} | {"__skipped": true} | {}}`; evaluation `status unanswered|partial|complete|not_assessed` with `missing_item_ids`, `item_errors`, `scores {positive, negative, general, total}` (aggregate null unless its own items are all valid; total only when all 30 valid), `findings.total_band {range, label, informational_only true, not_a_treatment_gate true}`; assessment window `previous_7_days` (S08 default); no per-draft source-version pin (live released v1 — see follow-ups).
- Frontend selectors (e2e contract): heading "PANSS (step 3)" (`#panss-heading`), radios `#panss-{P1..P7,N1..N7,G1..G16}-{1..7}` (name `panss-{itemId}`), verdict `#panss-preview-status` (aria-live polite), scores `#panss-scores` (`—` for null, never `0`), skip button "Skip PANSS (not assessed)" + resume button "Resume PANSS (clear skip)", still-needed list `#panss-still-needed` ("Still needed:"), band text "Informational only" + "not a treatment gate".

### Handoff

- Next engineering session: **S11** (C-SSRS form and distinct results) builds on cssrs v1 + the same S07 autosave path; PANSS and diagnosis pages need no further work for S11.
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus `make migrate`; no new make target needed for S10).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged (§1 S08 no-review policy stands; S10 uses the S08 release as-is); BNs, medical docs unchanged; no clinical claims added here.
- S10 exit met: independent literal fixtures (7/7/16/30 and 49/49/112/210), browser skip/partial/resume, source-version persistence; no default `1` values or hidden zero scores.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S10 exit blockers)

- Standards: 0 hard violations.
- Spec: prior-score display is a historical placeholder (component never fetches other encounters, never invents numbers); source-version is live (reads current released v1, not pinned per-draft); assessment window is hardcoded (`previous_7_days` from S08). All three are follow-ups, not blockers.

### Deferred items

- S09/S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands).
- S10 code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S10 adds no clinical blocker.
- Infra debt (pre-existing, unrelated to S10): full-e2e 2 failures on identity rename `limit=100` with 584 users.
- S10 code-review follow-ups carried above (prior-score placeholder, live source-version, hardcoded window).
- S10 exit met: independent literal fixtures, browser skip/partial/resume, no default `1`s or hidden zeros.

## S11 — Implement C-SSRS form and distinct results (2026-10-04)

**Scope:** C-SSRS page state only (tasks.md S11; plan.md §5; FR-13, FR-20). No owner review beyond the S08 release — S11 consumes the S08-released `cssrs.v1.json` as-is, adds no content approval gate, and marks nothing `awaiting_review`. No acknowledgment, no bypass, no composite risk score, no treatment gate, no auto-fill of lower levels, no hidden zero. Seams T2/T1/T9. Implemented worktree vs HEAD `156b59f`.

### Pinned / added deps (S01–S07 pins unchanged; S11 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S11).
- New modules: `backend/src/x_insight/assessments/cssrs.py` (93 lines; page-state service over `draft_data["cssrs"]`, mirrors `panss.py`); router extensions in `B/assessments/router.py` (+42 lines tracked diff: `GET /encounters/{id}/cssrs` + `_cssrs_response`); `web/src/features/assessments/cssrs/{CssrsSection.tsx, api.ts (129 lines)}`; `EncounterPage.tsx` +10/−2 lines (mounts `CssrsSection` step 4 after `PanssSection`); `theme.css` +31 lines (`.xi-urgent-panel` + `.xi-cssrs-panel`). No new runtime libs; no clinical packages; no new migration.
- S11 files (tracked modifications + new): `backend/src/x_insight/assessments/router.py`, `web/src/features/encounters/EncounterPage.tsx`, `web/src/shared/theme.css`, plus new `backend/src/x_insight/assessments/cssrs.py`, `backend/tests/assessments/test_cssrs.py` (11 tests), `e2e/cssrs.spec.ts` (10 journeys), `web/src/features/assessments/cssrs/CssrsSection.tsx`, `web/src/features/assessments/cssrs/api.ts`.
- Consumed release (verified this docs session in `content/assessments/cssrs.v1.json`): version `v1`, `released_at 2026-10-04`, `reviewer none`, `approval none`, source `project-documents/medical-documents/CSSRS.md` sha256 `610ecf937795c695828d15376a2555e3e153f97307f7b758dc9c1e7b6bdc9ced`, `no_composite_score true`, ideation wording is concise paraphrase (`experimental_paraphrase true`, authorized form governs), windows `past_month` (recent ideation) / `past_3_months` (recent behavior) / `lifetime`, `validation_results.reference_examples_passed 7` (in `test_definitions.py` slice 3, worked independently from the source).

### Red→green slices + seams (T2/T1/T9; S08 cssrs v1 consumed as-is)

- Slice 1 — all unanswered and skipped produce no assessed result; complete explicit negatives give the S08-defined no-ideation result (T2/T1; `BT/assessments/test_cssrs.py` slice 1): `{}` evaluates to `status unanswered` with null severities (no defaults, no hidden zeros); `{"__skipped": true}` evaluates to `not_assessed` with null severities; complete all-`no` evaluates to `complete` with severity `0/0/0` + `no_positive_items`. Preview `GET cssrs` returns the same `evaluate()` object as the direct T2 call (`body["evaluation"] == direct`, no drift), with `revision` + `ETag "<revision>"`.
- Slice 2 — source-derived level-3 worked example gives severity 3 without auto-filling lower responses; intensity/behavior/lethality remain separate (T2/T1): level 3 endorsed (`css_i3_recent yes` + intensity `2/2/3/1/4`) → `complete` with `severity 3` while lower `no` answers stay `no` (higher yes never fills lower levels); `intensity`/`behavior`/`lethality` render as separate dimensions with no composite score asserted absent. Independently worked literals asserted on both direct `evaluate()` and `GET` preview (no drift).
- Slice 3 — historical versus current answers retain their periods; incomplete required branch has missing-item guidance, not a guessed negative; S08 alert logic holds (T2/T1): recent vs lifetime answers keep `past_month`/`past_3_months`/`lifetime` windows; missing intensity/lethality on an endorsed branch → `partial` with `missing_item_ids` guidance (never a guessed negative); `high_risk_alert` (recent 4/5 or behavior) vs `clinical_review` (any 1–3 or history) with NSSI contributing to `clinical_review` but never triggering `high_risk_alert` alone. Invalid/out-of-range/noninteger/unverdeclared values and period-like IDs → `item_errors` + aggregates suppressed to null (never zero), visible through the `GET` route after forged `PATCH`; resume via fresh client re-login sees the same complete evaluation, and `GET /encounters` carries the same `draft_data.cssrs.answers` + `revision`.
- Slice 4 — persistent review/urgent messages with text and keyboard access; page-switch/autosave cannot erase responses or turn a skip into zero (T1/T9): fresh form shows unanswered verdict with `—` scores, skip → "not assessed" surviving reload, resume clearing to unanswered; level-3 click-through shows severity 3 + separate dimension panels + `high_risk_alert role=alert` vs `clinical_review` panels; forged invalid surfaces item errors (never zero-filled); page-switch/autosave round-trips preserve answers and skip never becomes zero. Auth carried from S07: `403` strangers/admin, `401` anonymous, `404` unknown encounter.

### Verification evidence (implementation-session report, S11 scope)

- `make check` exit `0` (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 233 passed = S10 222 baseline + 11 new (`BT/assessments/test_cssrs.py` 11 passed, counted via `grep -c "^def test_"` this docs session).
- `e2e/cssrs.spec.ts` 20/20 chromium+firefox (10 `test(` blocks counted this docs session × 2 browsers).
- Full e2e 127 pass / 5 fail — all pre-existing and unrelated to S11: 4× identity rename `limit=100` with 758 physicians, 1× diagnosis indeterminate firefox-only flake passing standalone.
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (existing routes untouched; S11 adds only `GET .../cssrs`).
- `make migrate` head unchanged — no new migration (versions still `0001`–`0005`, head `0005`; S11 stores everything in `draft_data["cssrs"]`, no new table).
- Env quirks (procedural only): backend suite runs from `backend/` with `.env` sourced (disposable DBs on `5442`/`5443`); shell-exported empty `E2E_BASE_URL` defeats the Makefile `?=` default so e2e needs `E2E_BASE_URL=http://localhost:5173` explicitly; live `:8000` backend predated the S11 route (restart needed, no `--reload`).

### Local loop for next agent (S12+)

- Routes (all under `/api/v1`): `GET /encounters/{id}/cssrs` → `{encounter, revision, definition_version, answers, evaluation}` with `ETag "<revision>"`; prompts come from `GET /content/assessments/cssrs` (released v1, 29 items: ideation 1–5 recent+lifetime `yes_no`, five intensity dimensions, behavior recent+lifetime `yes_no`, lethality codings); answers travel via `PATCH /encounters/{id}` with `{draft_data: {cssrs: {answers}}}` (full-object autosave, same S07 path). No ack/bypass POSTs on C-SSRS.
- Write shape: C-SSRS answers reuse the shared PATCH contract — `If-Match: "<revision>"` (quoted ETag; stale → `412`), optional `Idempotency-Key`, and `X-CSRF-Token` (missing/invalid → `403`); author-only (`403` strangers/admin, `401` anonymous, `404` unknown encounter).
- State shape: `draft_data.cssrs = {answers: {<29 item ids>: yes|no|1..5|0..5|0..2} | {"__skipped": true} | {}}`; evaluation `status unanswered|partial|complete|not_assessed` with `missing_item_ids`, `item_errors`, `scores {ideation_severity_recent, ideation_severity_lifetime, ideation_severity_max}` (null unless complete), `findings {intensity, behavior, lethality, flags, windows, notes}` (separate, never combined); periods `past_month`/`past_3_months`/`lifetime`; no per-draft source-version pin (live released v1 — see follow-ups).
- Frontend selectors (e2e contract): heading "C-SSRS (step 4)" (`#cssrs-heading`), yes/no radios `#cssrs-{itemId}-yes|no` and numeric radios `#cssrs-{itemId}-{n}` (name `cssrs-{itemId}`), verdict `#cssrs-preview-status` (aria-live polite), scores `#cssrs-scores` (`—` for null, never `0`), flags `#cssrs-flags` with urgent `#cssrs-high-risk-alert` (`role=alert`) and review `#cssrs-clinical-review`, dimension panels `#cssrs-intensity`/`#cssrs-behavior`/`#cssrs-lethality`, still-needed list `#cssrs-still-needed` ("Still needed:"), item errors `#cssrs-{id}-error` (`role=alert`), skip button "Skip C-SSRS (not assessed)" + resume button "Resume C-SSRS (clear skip)".

### Handoff

- Next engineering session: **S12** (structured history and adverse effects) — builds on the same S07 autosave path; C-SSRS, PANSS, and diagnosis pages need no further work for S12.
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus `make migrate`; verified this session — no new make target or loop change needed for S11).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged (§1 S08 no-review policy stands; S11 uses the S08 release as-is); BNs, medical docs unchanged; no clinical claims added here.
- S11 exit met: independent source-derived fixtures, no composite risk score, direct HTTP value/period validation, browser resume.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S11 exit blockers)

- Standards: 0 hard violations; judgement-call smells only — 3rd-copy response/ETag shape (`_cssrs_response` mirrors diagnosis/PANSS), theme panel duplication (`.xi-urgent-panel` vs `.xi-cssrs-panel`), api fetch-shape duplication (`request<T>` vs PANSS/diagnosis clients), thin forwards (`get_answers`/`evaluate_answers`/`definition_version`), `author_id` symmetry param (`del author_id` kept for call-site symmetry, C-SSRS has no gate).
- Spec partials: fixtures are definition-derived wording vs the source-derived exit line (S08 paraphrase carries through); review flag renders as bare `div` while urgent uses `role=alert`; dead "needs no treatment decision" branch for an unreachable state + unused `server_timestamp?` type; skip-replaces-answers is explicit user action per the S08 skip contract — autosave/page-switch non-erasure holds per `cssrs.spec.ts` round-trips.

### Deferred items

- S10/S09/S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands).
- S11 code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S11 adds no clinical blocker.
- Infra debt (pre-existing, unrelated to S11): full-e2e 5 failures — 4× identity rename `limit=100` with 758 physicians, 1× diagnosis indeterminate firefox-only flake passing standalone.
- S11 code-review follow-ups carried above (response/ETag shape, theme/api duplication, thin forwards, paraphrase wording, review-flag role, dead branch, skip contract).
- S11 exit met: independent fixtures, no composite risk score, direct HTTP value/period validation, browser resume.

## S12 — Draft and implement structured history and adverse effects (2026-10-05)

**Scope:** history/effects page state only (tasks.md S12; plan.md §§2.2, 5, 4.2; FR-14, FR-20–21). Content stays `awaiting_review` with no approval claimed — `content/history/review.json` is `status awaiting_review`, `reviewer owner`, `decision pending`, `approval none` (S08 no-review exception does not apply here); `content/review-ledger.md` §2 left unchanged. No medication regimen fields (dose/unit/route/frequency/active-stopped/free-text stay excluded per FR-14). No notes/signing (S13+). Seams T2/T1/T9.

### Pinned / added deps (S01–S07 pins unchanged; S12 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S12).
- Frontend pins carry forward: `react 19.3.0`, `vite 8.3.2`, `typescript 7.0.2`, `@playwright/test 1.63.0` with `chromium` + `firefox` projects. No new runtime libs; no clinical packages.
- No new migration — history lives in `encounters.draft_data["history"]`, effects in `draft_data["effects"]` through the shared S07 autosave contract (opaque `draft_data` object + revision + `If-Match`/`412` + `Idempotency-Key`); `migrations/versions/` head stays `0005`.
- S12 files (code/tests/content definitions untouched by this docs step): `backend/src/x_insight/cases/history.py` + `effects.py`, route inventory in `B/cases/router.py`, `web/src/features/history/{HistorySection.tsx, EffectsSection.tsx, api.ts}`, `backend/tests/http/test_history.py` (23 tests), `e2e/history.spec.ts` (10 journeys), `content/history/{history,bars,sas,aims,acute-dystonia-dx-criteria}.v1.json` + `review.json` (all `awaiting_review`, none approved).

### Red→green slices + seams (T2/T1/T9; tasks.md S12 steps 2–4)

- Slice 1 — history provenance / tri-state / excluded-422 (T2/T1; tasks step 2): history values persist with server-stamped per-field provenance (`{source clinician_entry|copied_baseline, author_id, recorded_at, baseline_encounter_id?}`, `copied_baseline` entries preserved); `unknown`/`not_assessed` stay distinct from `no` (never coerced to false); undeclared ids and FR-14-excluded ids (`dose/dose_unit/unit/route/frequency/active_stopped/active/stopped/free-text`) fail strict validation with `422` (preview surfaces `item_errors` with aggregates suppressed, never zero-filled). 12-field `history.v1.json` inventory (exposure/duration/trial-adequacy/prior-response/monitoring categories, periods/windows, `analysis_visible` label) mirrored in `history.py::ALLOWED_FIELDS` without invented thresholds.
- Slice 2 — four effects present-complete + severity / absent-null / stale-clear (T2/T1; tasks step 3): each effect accepts `present|absent|not_assessed`; `present` requires the corresponding complete reviewed questionnaire plus reviewed severity (akathisia: complete 4-item BARS + severity = BARS global int 0–5 with global ≥ 2 threshold; parkinsonism: complete 10-item SAS + reviewed `mild|moderate|severe` label, no invented bands; tardive dyskinesia: complete AIMS form missing → `awaiting_source` always-`422`, so F1-present is blocked pending the complete AIMS source; acute dystonia: complete 5-criteria `Acute Dystonia Dx Criteria` exact-name checklist `yes|no|unknown` with no total score + reviewed label). `absent`/`not_assessed` carry null severity with no questionnaire-completion requirement; a non-null severity with `absent`/`not_assessed` is the stale case → `422` until the caller explicitly clears it (sends null). Urgent airway flag (`addx_urgent_airway`) routes independently of questionnaire completion.
- Slice 3 — phone/reconciliation + notes separation + nullable results (T2/T1; tasks step 4): optional free-text phone update (no country validation, S06 rule) + minimal reconciliation state (`{status not_required|pending|reconciled, baseline_encounter_id?}`); history is `analysis_visible true` with the `analysis_visible` label while page notes (S13) stay a separate `analysis_visible false` channel enforced by serializer allowlists (proven by exclusion, never relabeled). Item responses, definition versions, completeness, and nullable results preserved per effect; missing required items suppress calculated results to null (never zero) unless a reviewed source defines a missing-data rule (none does here); BARS `component_total` (objective + awareness + distress) is descriptive-only, not a severity rule; completeness enforced only for present effects.
- Slice 4 — author/revision rules + browser saved-state (T1/T9; tasks Verify/exit): author-only reads/writes (`403` strangers/admin, `401` anonymous, `404` unknown/missing), stale `If-Match` → `412 STALE_REVISION` with server truth unchanged, missing/malformed `If-Match` → `422`, CSRF `403` on mutations, per-command idempotency replay (`history.save`, `effects.status.{effect}`) with same key + changed body → `409`; wizard `PATCH` path stays permissive-bounds with preview `item_errors` per the S09–S11 precedent while the strict POSTs enforce `422` and stamp provenance. Browser round-trips preserve answers/severities across reload and re-login with saving/saved/failed states; only a `2xx` is durable.

### Verification evidence (implementation-session report, S12 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 256 passed = S11 233 + 23 new (`backend/tests/http/test_history.py` 23 passed, counted via `grep -c "^def test_"` this docs session).
- `e2e/history.spec.ts` 20/20 chromium+firefox (10 `test(` blocks counted this docs session × 2 browsers).
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (existing routes untouched; S12 adds only history/effects routes).
- `make migrate` head unchanged — no new migration (versions still `0001`–`0005`, head `0005`; S12 stores everything in `draft_data["history"]`/`draft_data["effects"]`, no new table).
- Env quirks carry forward from S08–S11 (procedural only): backend suite runs from `backend/` with `.env` sourced (disposable DBs on `5442`/`5443`); e2e needs `E2E_BASE_URL=http://localhost:5173` explicitly when the shell exports an empty `E2E_BASE_URL` (Makefile `?=` default defeated); stale `:8000` backend must be restarted to pick up new routes (no `--reload`).

### Local loop for next agent (S13+)

- Routes (all under `/api/v1`): `GET /encounters/{id}/history` → `{encounter, revision, definition_version v1, values, provenance, reconciliation, phone_update, evaluation, analysis_visible true, analysis_visible_label}` with `ETag "<revision>"`; `POST /encounters/{id}/history` with `{values, reconciliation?, phone_update?}` (strict, `422` on undeclared/excluded/invalid); `GET /encounters/{id}/effects` → `{encounter, revision, definition_versions {tardive_dyskinesia|akathisia|parkinsonism|acute_dystonia: v1}, effects}` with `ETag`; `POST /encounters/{id}/effects/{effect}/status` with `{status present|absent|not_assessed, severity}` (akathisia int 0–5 = BARS global, others `mild|moderate|severe` label, null for absent/not_assessed). Questionnaire item answers travel via `PATCH /encounters/{id}` with `{draft_data: {history: …, effects: …}}` (full-object autosave, same S07 path).
- Write shape: every mutating history/effects call sends `If-Match: "<revision>"` (quoted ETag; missing/wildcard `*`/malformed → `422`, stale → `412`), optional `Idempotency-Key` (replay same revision → same result; reuse on new revision → `409`), and `X-CSRF-Token` (missing/invalid → `403`); author-only (`403` strangers/admin, `401` anonymous, `404` unknown encounter).
- State shapes: `draft_data.history = {values: {<12 field ids: h_exposure_*|h_onset_timing|h_trial_adequacy_prior|h_prior_response|h_monitoring_baseline|h_alternative_cause_considered|h_functional_impact|h_falls_or_limitation|h_medication_timeline_documented>: yes|no|unknown|not_assessed (onset/trial/prior enums per field)}, provenance: {field: {source, author_id, recorded_at, baseline_encounter_id?}}, reconciliation: null | {status, baseline_encounter_id?}, phone_update: str | null}`; `draft_data.effects.{tardive_dyskinesia|akathisia|parkinsonism|acute_dystonia} = {status: present|absent|not_assessed | null, severity: int|label|null, questionnaire: {answers}}`; evaluations carry `status unanswered|partial|complete (+ awaiting_source for AIMS)`, `missing_item_ids`, `item_errors`, nullable scores, `definition_version`.
- Frontend selectors (e2e contract): headings `#history-heading` ("History") + `#effects-heading`; history radios `#hist-{fieldId}-{value}` with provenance hint `#hist-{fieldId}-provenance`, verdict `#history-preview-status` (aria-live polite), still-needed `#history-still-needed`, item errors `#hist-{fieldId}-error` (`role=alert`), reconciliation `#history-reconciliation-status`/`#history-reconciliation-baseline`, phone `#history-phone-update`; effects status radios `#effect-{key}-{present|absent|not_assessed}`, severities `#effect-{key}-severity` (akathisia int select, others label select), results `#effect-{key}-results` (+ `#effect-{key}-still-needed`), questionnaire radios `#bars-{item}-{n}` / `#sas-{item}-{n}` / `#acute-{item}-{yes|no|unknown}`, per-item errors `#bars-*-error`/`#sas-*-error`/`#acute-{item}-error` (`role=alert`), verdicts `#history-preview-status`/`#effects-preview-status` (aria-live polite), urgent `#effect-acute_dystonia-urgent` (`role=alert`) + `#effect-urgent-heading`.

### Handoff

- Next engineering session: **S13** (attributed page notes and separation proof) — builds on the same S07 autosave path plus the S12 `analysis_visible` history channel; history/effects pages need no further work for S13. S12 leaves notes/signing to S13+.
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus the `make migrate` entry point; verified this session — no new make target or loop change needed for S12).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged (§1 S08 no-review policy stands; §2 history/effects stays owner-review scope, S12 drafts only); BNs, medical docs, DDI content unchanged; no clinical claims added here.
- S12 exit met: author/revision rules inherited from S07, reviewed field fixtures, browser saved-state behavior. Mapping additions later must update the content version and these public checks.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S12 exit blockers)

- Standards: 0 hard violations; judgement-call duplications only (history/effects response/ETag/idempotency shapes mirror diagnosis/PANSS/C-SSRS; theme panel + api fetch-shape duplication; thin forwards) — consider shared helpers when the next route module lands.
- Spec open items (all non-blocking, by design or pending owner input): AIMS `awaiting_source` always-`422` so F1-present stays blocked pending the complete AIMS source; strict POSTs are live while `review.json` stays `awaiting_review` (drafts enforced, never approved); BARS `component_total` is descriptive-only, not a severity rule; wizard `PATCH` path stays permissive-bounds with preview `item_errors` per the S09–S11 precedent while provenance is stamped only on the strict POST.

### Deferred items

- S11/S10/S09/S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands — now also covering `history.save` / `effects.status.*`).
- S12 code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions (content owner; `content/history/review.json` `awaiting_review`, none approved): approve the minimum 12-field typed history inventory as-is so S27 and S29–S38 can draft against a frozen inventory (additions as versioned changes); review the complete BARS (4 items, global principal, threshold global ≥ 2) and SAS (10 items, raw 0–40 / mean 0–4, no bands) definitions plus F2/F3 severity/network mappings; identify the complete AIMS source/form and review concrete AIMS item/completeness/severity/network definitions (incl. Schooler-Kane research-only confirmation); review the Acute Dystonia Dx Criteria checklist (5 criteria, no total), completeness-only-when-present, reviewed severity wording, and urgent-independent handling; resolve R7/F5 trial-adequacy windows and no-improvement baselines (S32/S37) and F1–F4 gate wording, onset/alternative-cause fields, and result mappings (S33–S36). Infrastructure proceeds on synthetic fixtures while responses are pending; S12 adds no new clinical blocker beyond the AIMS-source gap above.
- Infra debt (pre-existing, unrelated to S12): identity-rename `limit=100` pagination (accumulated e2e physicians fall off the single page); diagnosis indeterminate firefox-only flake passing standalone.
 - S12 exit met: structured history with provenance/tri-state/excluded-422, four effects with present-complete + severity / absent-null / stale-clear (AIMS awaiting_source, Acute exact name with no total, urgent independent), phone/reconciliation with notes separation and nullable results, author/revision rules with browser saved-state.

## S13 — Add attributed page notes and prove separation (2026-10-05)

**Scope:** attributed page notes only (tasks.md S13; plan.md §§2.3, 4.1-4.3; FR-16, FR-22). Notes live in their own `notes` table (migration `0006`) — never inside `encounters.draft_data` (the S07 opaque autosave object) and never in the analysis-visible history channel (S12). Notes can change the encounter revision without changing the analysis fingerprint. No snapshot/GenerationBatch/QuestionRun/projection machinery here; S40/S41/S59 carry the mandatory end-to-end note-noninterference checks. No signing/addenda (S49/S50 scope). Seams T1/T9.

### Pinned / added deps (S01–S07 pins unchanged; S13 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S13).
- Frontend pins carry forward: `react 19.3.0`, `vite 8.3.2`, `typescript 7.0.2`, `@playwright/test 1.63.0` with `chromium` + `firefox` projects. No new runtime libs; no clinical packages.
- New migration `0006_page_notes` — head moves `0005` → `0006` (`notes`: UUID PK, `encounter_id` FK → encounters CASCADE, `page` TEXT, `author_id` FK → users CASCADE, `author_display` snapshot, `created_at` TIMESTAMPTZ, `text` verbatim with `ck_notes_text_bounds` 1–2000 backstop; app role SELECT+INSERT only, never UPDATE/DELETE; indexes `ix_notes_encounter_id` + `ix_notes_encounter_page`).
- S13 files (code/tests untouched by this docs step): `backend/src/x_insight/cases/notes.py` (routes, `ALLOWED_PAGES`, `ANALYSIS_EXCLUDED_CHANNELS` + `exclude_notes_from_analysis`, S40/S41/S59 note), route inventory in `B/cases/router.py` (`POST`/`GET /encounters/{id}/notes`), `web/src/features/notes/{NotesSection.tsx, api.ts}` (reusable wizard note control mounted on 6 pages), `backend/tests/http/test_notes.py` (20 tests), `e2e/notes.spec.ts` (7 journeys).

### Red→green slices + seams (T1/T9; tasks.md S13 steps 1–4)

- Slice 1 — author add + resume + idempotent retry (T1/T9; tasks step 1): author appends `{page, text}` with `If-Match` on the current revision and sees the note after reload/re-login with server author/time; a retried submit with the same `Idempotency-Key` + same body replays the single created note (`notes.create` keyed `(operation, actor, key)`), same key + changed body → `409 IDEMPOTENCY_CONFLICT`. Creating a note bumps `encounter.revision` so S07 autosave stays coherent (stale tab reconciles through `GET` first; autosave flushes before submit and `applyExternalRevision` advances the fence without touching the draft editor).
- Slice 2 — ownership / append-only / server-derived actor-time (T1; tasks step 2): author-only reads/writes (`403` strangers/admin without content, `401` anonymous, `404` unknown/missing/discarded encounter); actor/time are server-derived (`author_id` + `author_display` username snapshot + `created_at` UTC from session/clock, never the body — extra body keys rejected `422` via `extra=forbid`); append-only with no edit/delete endpoint and no UPDATE/DELETE grant, correction is a new note; audit `notes.create.success` records attribution without the note body.
- Slice 3 — separate render + demographics-after-creation + escaping (T1/T9; tasks step 3): notes render in their own `.xi-notes-panel` section with page/author/time, never inside the history `analysis_visible` list; demographics control mounts after initial draft creation (same shape `#notes-demographics-*`); text renders as plain escaped text via React default escaping (no `dangerouslySetInnerHTML`), so literal markup like `<b>hello</b>` shows literally; `412` keeps typed text with Reload/Retry on the fresh revision (never silent overwrite), any failure never shows Saved — only a `2xx` is durable.
- Slice 4 — explicit serializer exclusion, no snapshot machinery (T1; tasks step 4): `ANALYSIS_EXCLUDED_CHANNELS = ("notes",)` + `exclude_notes_from_analysis(record)` is the explicit exclusion future S40 snapshots must route through (notes excluded from analysis fingerprint/projection while signed snapshots preserve the full displayed record including notes, plan §4.2); only the exclusion function/allowlist is defined here — no GenerationBatch/QuestionRun/projection tables or endpoints. S40/S41/S59 recorded (in-module docstring + `test_future_note_noninterference_checks_are_recorded`) as the mandatory end-to-end checks proving page notes never leak into analysis-visible history, prompts, CPTs, DDI, or proposal selection and that a note-only change does not invalidate probability acceptance.

### Verification evidence (implementation-session report, S13 scope)

- `make check` exit `0` (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 276 passed = S12 256 + 20 new (`backend/tests/http/test_notes.py` 20 passed, counted via `grep -c "^def test_"` this docs session).
- `e2e/notes.spec.ts` 14/14 chromium+firefox (7 `test(` blocks counted this docs session × 2 browsers: mount-selectors, diagnosis add+reload, demographics survive reload+re-login, markup-literal + history separation, stale-412 keep-text-and-retry, failed-never-Saved + retry, stranger/admin denial).
- `make migrate` head `0006` (versions `0001`–`0006`; S13 adds only the `notes` table, `draft_data` shape untouched).
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (existing routes untouched; S13 adds only `POST`/`GET .../notes`).
- Env quirks carry forward from S08–S12 (procedural only): backend suite runs from `backend/` with `.env` sourced (disposable DBs on `5442`/`5443`); e2e needs `E2E_BASE_URL=http://localhost:5173` explicitly when the shell exports an empty `E2E_BASE_URL` (Makefile `?=` default defeated); stale `:8000` backend must be restarted to pick up new routes (no `--reload`).

### Local loop for next agent (S14+)

- Routes (all under `/api/v1`): `POST /encounters/{id}/notes` with `{page, text}` only → `201 {note {id, encounter_id, page, author_id, author_display, created_at, text}, revision, server_timestamp}` + `ETag "<revision>"` (bumps revision); `GET /encounters/{id}/notes` with optional `?page=` filter + bounded `limit`/`offset` (`contracts.parse_pagination`, default 25, max 100), stable `(created_at, id)` order → `{items, total, revision}` + `ETag`. Notes travel outside `PATCH /encounters/{id}` — `draft_data` carries no notes channel.
- Write shape: every note POST sends `If-Match: "<revision>"` (quoted ETag; missing/wildcard `*`/malformed → `422`, stale → `412 STALE_REVISION` with server truth unchanged), fresh `Idempotency-Key` per submit (replay same revision → same single note; reuse on new revision or changed body → `409`), and `X-CSRF-Token` (missing/invalid → `403`); author-only (`403` strangers/admin without content, `401` anonymous, `404` unknown/discarded encounter); bounds `page` in `ALLOWED_PAGES` + `text` non-empty verbatim ≤ 2000 chars (`422` with `field_errors`).
- State shapes: `draft_data` has no notes key by design (notes live in the `notes` table per-encounter per-page); `exclude_notes_from_analysis(record)` strips the `notes` channel for future S40 snapshots while signed snapshots preserve the full displayed record including notes; `ALLOWED_PAGES = (demographics, diagnosis, panss, cssrs, history, medications, effects, proposal, secondary_plan)`; only 6/9 mounted in the wizard so far (see follow-ups).
- Frontend selectors (e2e contract): panel `.xi-notes-panel` (per page); heading `#notes-{page}-heading` (e.g. `#notes-diagnosis-heading`); list `#notes-{page}-list` (aria-live polite, always rendered); empty `#notes-{page}-empty`; input `#notes-{page}-input` (labelled "Page note for {page}"); add `#notes-{page}-add`; status `#notes-{page}-status` (`role=status`); conflict `#notes-{page}-conflict` (`role=alert`); reload `#notes-{page}-reload`, retry `#notes-{page}-retry`. Demographics uses `#notes-demographics-list/input/add/status`.

### Handoff

- Next engineering session: **S14** (shared chart and follow-up draft entry) — builds on the same S07 autosave path plus the S13 notes channel; notes pages need no further work for S14.
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus the `make migrate` entry point; verified this session — no new make target or loop change needed for S13).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged; BNs, medical docs, DDI content unchanged; no clinical claims added here.
- S13 exit met: public HTTP persistence/ownership and browser placement pass. A page note is never relabeled algorithm-visible history.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S13 exit blockers)

- Standards: 5th-copy error/idempotency/fetch helpers (notes `request<T>` mirrors identity/encounters/history patterns — consider shared helpers when the next route module lands); 6th-copy fixtures (note-posting setup duplicated across HTTP/e2e suites); test SQL audit reads (audit assertions use direct SQL rather than the audit HTTP route).
- Spec open items (all non-blocking, by design or later-session scope): only 6/9 `ALLOWED_PAGES` mounted — `medications`/`proposal`/`secondary_plan` have backend allowlist keys but no wizard step mounts them yet (they mount when those steps land); signed-note frozen read/addenda is S49/S50 scope (no frozen-note endpoint here by design); fingerprint invariance proved in S40, recorded only here (this module defines the exclusion S40/S41/S59 assert against); pagination/filter + CSRF/audit/`412` UI are plan-consistent extras beyond the S13 exit line; whitespace-only text passes (only `""` is rejected — a deliberate verbatim-preservation edge); author FK is CASCADE vs RESTRICT (deactivation history discussed, not a notes exit blocker); exclusion is a strip-helper, not an enforced allowlist at current serializers (enforcement lands with S40 snapshot code).

### Deferred items

- S12/S11/S10/S09/S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands — now also covering `history.save` / `effects.status.*` / `notes.create`).
- S13 code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S13 adds no clinical blocker.
- Infra debt (pre-existing, unrelated to S13): identity-rename `limit=100` pagination (accumulated e2e physicians fall off the single page); diagnosis indeterminate firefox-only flake passing standalone.
- S13 code-review follow-ups carried above (fetch-helper duplication, fixture copies, audit-read style; 6/9 pages mounted, S49/S50 frozen-read scope, S40 invariance proof pending, plan-consistent extras, whitespace-only edge, FK and strip-helper notes).
- S13 exit met: attributed page notes with server-derived author/time, author-only append-only persistence, separate escaped browser placement including demographics-after-creation, explicit serializer exclusion with S40/S41/S59 noninterference checks recorded and no speculative snapshot machinery.

## S14 — Build shared chart and follow-up draft entry (2026-10-05)

**Scope:** shared chart + follow-up draft entry only (tasks.md S14; plan.md §§2.2–2.3/4; FR-20–23). No migration, no bypass-sign route, no clinical content approval. Uses a test-only signed baseline fixture until S49 implements signing; S49 is not a dependency of this session. Seams T1/T9.

### Pinned / added deps (S01–S07 pins unchanged; S14 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S14).
- Frontend pins carry forward: `react 19.3.0`, `vite 8.3.2`, `typescript 7.0.2`, `@playwright/test 1.63.0` with `chromium` + `firefox` projects. No new runtime libs; no clinical packages.
- No new migration — head stays `0006` (S14 stores everything through existing `patients`/`encounters` rows + `draft_data`, no new table).
- S14 files (code/tests untouched by this docs step): chart/follow-up commands in `B/cases/`, route inventory (`GET /patients/{id}/chart`, `POST /patients/{id}/encounters` with `kind: follow_up`), `web` `ChartPage` + `FollowupBaselinePanel` + `api.ts` with directory Chart links, `backend/tests/http/test_followup.py` (15 tests), `e2e/followup.spec.ts` (5 journeys × 2 browsers).

### Red→green slices + seams (T1/T9; tasks.md S14 steps 1–4)

- Slice 1 — shared read with author-only draft privacy (T1; tasks step 1): `GET /patients/{id}/chart` is shared (physician + admin; anonymous `401`, unknown patient `404`); chart carries badge-only draft presence with no draft-content leak and proposal `unavailable` / `generation_not_implemented` (no fake successful proposal). Only the draft author can read/edit the draft and its derived artifacts through ordinary routes; other authors' draft content is never exposed.
- Slice 2 — baseline copy with provenance + fresh assessments (T1; tasks step 2): `POST /patients/{id}/encounters {kind: follow_up, baseline?, baseline_encounter_id?}` copies history/medications with `copied_baseline` provenance and `pending` reconciliation; PANSS/C-SSRS answers start as `{}` (unanswered) with prior scores shown as historical-only, never as newly completed answers.
- Slice 3 — single-draft slot under concurrency (T1; tasks step 3): patient-row lock + partial unique constraint permits one open draft only; two physicians concurrently creating a draft for one patient produce one success and one generic `409 OPEN_DRAFT_EXISTS` without leaking draft content. Different patients have independent drafts. Relevant shared demographic changes marking the author's affected results stale waits for S48d/S51 (see follow-ups).
- Slice 4 — chronology / badges / navigation to review (T1/T9; tasks step 4): chronology, draft badges, phone/history/effect pages, and chart→encounter navigation; encounter mounts the baseline panel for `follow_up` only. Until reasoning exists the proposal stays honestly unavailable; no fake generation.
- Seams: T1 (chart/follow-up HTTP) + T9 (chart + follow-up browser journeys). No T2–T8/T10 exercised in S14. Temporary signed fixtures stay test-only with inline baseline fixtures; no bypass-sign production route was added.

### Verification evidence (implementation-session report, S14 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` 291 passed = S13 276 + 15 new (`backend/tests/http/test_followup.py` 15 passed).
- `e2e/followup.spec.ts` 10 passed (5 `test(` blocks × 2 browsers: chromium + firefox) via `make test-e2e TEST=e2e/followup.spec.ts`, passed twice.
- `make migrate` head unchanged — no new migration (versions still `0001`–`0006`, head `0006`; S14 stores everything in existing `patients`/`encounters` + `draft_data`, no new table).
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (existing routes untouched; S14 adds only chart/follow-up routes).
- Env quirks carry forward from S08–S13 (procedural only): backend suite runs from `backend/` with `.env` sourced (disposable DBs on `5442`/`5443`); e2e needs `E2E_BASE_URL=http://localhost:5173` explicitly when the shell exports an empty `E2E_BASE_URL` (Makefile `?=` default defeated); stale `:8000` backend must be restarted to pick up new routes (no `--reload`).

### Local loop for next agent (S15+)

- Routes (all under `/api/v1`): `GET /patients/{id}/chart` (shared physician + admin read: demographics/signed chart + badge-only draft presence + `unavailable` proposal; `401` anonymous, `404` unknown) ; `POST /patients/{id}/encounters` with `{kind: follow_up, baseline?, baseline_encounter_id?}` (physician-only create; `409 OPEN_DRAFT_EXISTS` when the single-draft slot is occupied). S06 patient routes and S07 `GET/PATCH /encounters/{id}` + discard unchanged. `GET followup-baseline ETag` is an author-only read by design (see scope notes).
- Write shape: follow-up create sends `X-CSRF-Token` (missing/invalid → `403`) with fresh `Idempotency-Key` per submit (same key + same body replays, same key + changed body → `409`); slot enforcement is patient-row lock + partial unique backstop with a generic `409 OPEN_DRAFT_EXISTS` that leaks no draft content; UI create currently sends no baseline (empty shell until the S49 baseline picker — see follow-ups).
- State shapes: follow-up `draft_data` carries copied history/medications with `copied_baseline` provenance + `pending` reconciliation, `phone_update`, PANSS/C-SSRS `answers {}` with prior scores historical-only; baseline preview currently reads live `history.values`, not a pinned snapshot (staleness hook waits for S48d — see follow-ups); `baseline_encounter_id`-only create currently discards the id and returns `not_required`/`None` (carried to S49 — see follow-ups).
- Frontend selectors (e2e contract): `#/patients/:id/chart` `ChartPage` heading `chart-heading`, draft presence `chart-draft-badge`, proposal state `chart-proposal-unavailable`, create entry points `chart-create-followup` (chart) + `directory-create-followup` (directory Chart links), baseline panel `followup-baseline` (mounted for `follow_up` encounters only), prior scores `followup-prior-scores` (value + historical flag, no dates — see follow-ups).

### Handoff

- Next engineering session: **S15** (parse one source through the ingestion interface) — builds on S02 persistence/contract foundations, not on chart/follow-up state; chart/follow-up pages need no further work for S15.
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus the `make migrate` entry point; verified this session — no new make target or loop change needed for S14).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged (§1 S08 no-review policy stands; history/effects stay `awaiting_review`, none approved); BNs, medical docs, DDI content unchanged; `backend/`, `web/`, `e2e/`, `migrations/` untouched by this docs step; no clinical claims added here.
- S14 exit met: follow-up creation/resume and shared-read journey. Temporary signed fixtures stay test-only and do not add a bypass-sign production route.

### Code-review follow-ups (non-blocking; not approval, no clinical claims — carry as future polish, not S14 exit blockers)

- Standards: 0 hard violations; judgement-only smells — Duplicated Code (`422` shapes, baseline branches, safe reference alias, router/App mirrors), Feature Envy (chart reaching into `draft_data` keys), Primitive Obsession overridden by the JSONB convention.
- Spec partials (all non-blocking): prior dates not surfaced (only value + historical flag shown); review navigation deferred (chart→encounter only, proposal review opens once generation exists); UI create sends no baseline yet (empty shell until the S49 baseline picker); reconciliation `pending` is label-only until S49 sign enforcement.
- Spec wrongs to carry to S49/S48d: baseline preview returns live `history.values`, not a pinned snapshot for the future staleness hook; `baseline_encounter_id`-only create discards the id (returns `not_required`/`None`).
- Scope notes (not creep): `MAX_BASELINE_MEDICATIONS=100` / `MAX_BASELINE_NOTE_CHARS=500` are plan §4 size limits; `GET followup-baseline ETag` is an author-only read design choice.

### Deferred items

- S13/S12/S11/S10/S09/S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands — now also covering follow-up create).
- S14 code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged (R5 concrete discussion/review mappings, F6 concrete graph/template and limitation-wording review, concrete history/severity definitions, DDI aliases/release evidence, reference-table provenance). Infrastructure proceeds on synthetic fixtures while responses are pending; S14 adds no new clinical blocker.
- Signing/staleness hooks (not S14 gaps): S49 signing enforcement (reconciliation `pending` label-only until then; baseline picker then sends the baseline; `baseline_encounter_id`-only `not_required`/`None` resolved there) + S48d/S51 staleness hooks (live `history.values` preview → pinned snapshot; shared demographic changes marking affected results stale).
- Infra debt (pre-existing, unrelated to S14): physicians-list `limit=100` single page (accumulated e2e physicians fall off the page, breaks identity-rename); diagnosis indeterminate firefox-only flake passing standalone.
- S14 exit met: shared chart and follow-up draft entry with badge-only privacy, `copied_baseline` + `pending` reconciliation, historical-only priors, generic `409 OPEN_DRAFT_EXISTS`, and honestly unavailable proposal.

## S15 — Parse one source through the ingestion interface (2026-10-05)

**Scope:** one-source offline ingestion only (tasks.md S15; plan.md §6, esp. §§6.1–6.2; FR-14, NFR-05). T3 only — public `build(...)` plus CLI agree, no SQL/runtime checker (S19), no terminology resolution (S17), no publish (S18), no DB migration, no frontend. Expected counts are monograph-declared source parsing expectations, not treatment guidance; no clinical claims, no approval claimed. Seams T3 only.

### Pinned / added deps (S01–S07 pins unchanged; S15 adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S15).
- Ingestion is stdlib only (`hashlib`/`re`/`dataclasses`/`pathlib`, `utf-8-sig` BOM-tolerant decode) — no new runtime lib, no fuzzy matcher, no external terminology dependency, no LLM, no network, no DB access.
- No new migration — head stays `0006` (S15 writes only staged `candidate_dataset.json` + `report.json` under a caller-chosen output dir, never the database).
- S15 files (code/tests untouched by this docs step): `backend/src/x_insight/ddi/ingestion.py` (public `build(source_dir, terminology=None, review_manifest=None) -> (CandidateDataset, Report)`, `PARSER_VERSION ddi-ingestion/0.1.0`), `backend/src/x_insight/ddi/__main__.py` (`uv run python -m x_insight.ddi build --sources <dir> --terminology <aliases> --output <staging-dir>` from `backend/`), `backend/tests/ddi/test_ingestion.py` (10 tests, all through the public `build` seam — the private preprocessor is never tested directly).

### Red→green slices + seams (T3; tasks.md S15 steps 1–4)

- Slice 1 — fixture provenance + monograph discovery (T3; tasks step 1): `build` discovers the staged monograph with stable relative-path order and records byte-sha256 provenance; the original under `project-documents/medical-documents/DDI-text/Antidiabetic Agents/Sitagliptin.txt` is never modified (copied into an isolated tmp dir with hash re-verified). Real category headings recorded: `Contraindicated (0)`, `Serious (4)`, `Monitor Closely (92)`, `Minor (70)` — expected counts below are these monograph-declared literals, never the parser's own totals. Path discrepancy noted: tasks.md S15 names the source `docs/medical-docs/DDI-text/...` — that path does not exist; the `project-documents/` prefix above is the verified actual location (also pinned in the test-module docstring).
- Slice 2 — actual interaction section, nav headings ignored (T3; tasks step 2): `build` locates the actual `Interactions` section and its `Name (N)` severity headings; earlier bare navigation/summary headings carrying no counts are ignored, so they never become entries. Minimal section/state machine only.
- Slice 3 — chrome-free spans, BOM/wrapping, stop marker (T3; tasks step 3): preprocessing preserves original 1-based line spans while dropping repeated page chrome (timestamp + URL lines) — a block separated from the previous one by chrome-only lines continues the same entry, any other block boundary starts a new entry; BOM-tolerant decode and wrapped entity headings handled; parsing stops before `Adverse Effects`/`Warnings` so later sections are never swallowed as interactions. Raw interacting names kept verbatim (resolution lands in S17); every entry keeps raw text plus `span_start`/`span_end` with chrome-free text asserted.
- Slice 4 — exact 0/4/92/70 + removed-entry anomaly (T3; tasks step 4): candidate report contains exactly 0 contraindicated / 4 serious / 92 monitor-closely / 70 minor with declared-vs-parsed agreement per category — a mismatch fails that document's eligibility and is never repaired by truncation or padding. Ofloxacin keeps both category assertions (repeated-pair evidence preserved, not deduplicated across categories). A deliberately removed entry fails count validation yet still produces an anomaly report (exit nonzero, report still written).
- Seams: T3 only. No T1/T2/T4–T10 exercised in S15; no SQL, no runtime checker, no LLM call to obtain passing counts.

### Verification evidence (implementation-session report, S15 scope)

- `make check` pass (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- `make test-backend` slice: `backend/tests/ddi/test_ingestion.py` 10 passed (`grep -c "^def test_"` = 10 this docs session: discovery/provenance, declared-counts-with-spans, raw-text-and-valid-spans, actual-section, ofloxacin-both-categories, removed-entry-anomaly, BOM-prefixed, stop-before-warnings, CLI-agrees-with-library, CLI-failure-still-writes-anomaly).
- CLI vs library agree: `build` exit 0 with all documents passing, nonzero on structural/count failure with the anomaly report still written (`candidate_dataset.json` + `report.json` under the staging dir); no patient data enters the command.
- Original untouched: Sitagliptin sha256 `e7c9bc45ed5b3f829dfe8e29b015ee727645db2f3ed6cad8de99fea2dcd4022f` re-verified by hash before staging; 1264 lines per `wc -l` (UTF-8 BOM present), 1265 decoded lines (per test-module provenance note).
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (existing routes untouched; S15 adds no routes, no tables).
- `make migrate` head unchanged — no new migration (versions still `0001`–`0006`, head `0006`).
- Env quirks carry forward from S08–S14 (procedural only): backend suite runs from `backend/` with `.env` sourced (disposable DBs on `5442`/`5443`); e2e needs `E2E_BASE_URL=http://localhost:5173` explicitly when the shell exports an empty `E2E_BASE_URL` (Makefile `?=` default defeated); stale `:8000` backend must be restarted to pick up new routes (no `--reload`).

### Local loop for next agent (S16+)

- Library: `build(source_dir, terminology=None, review_manifest=None) -> (CandidateDataset, Report)` from `x_insight.ddi.ingestion`; `terminology`/`review_manifest` accepted for the plan.md §6 interface but unused in S15 (tolerated, never fatal — resolution lands in S17, review in S18). `CandidateDataset {parser_version, documents[]: {source_path, checksum, entries[]}}`; entry `{source_path, source_category, interacting_name, raw_text, span_start, span_end}`; `Report {parser_version, documents[]: {source_path, checksum, expected_counts, parsed_counts, passed, anomalies[]}, passed}`; `PARSER_VERSION ddi-ingestion/0.1.0`.
- CLI: from `backend/`, `uv run python -m x_insight.ddi build --sources <dir> --terminology <aliases> --output <staging-dir>`; exit 0 when every document passes, nonzero on structural/count failure with `report.json` still written; a separate `publish --manifest <reviewed-manifest>` (S18) validates approvals and imports a release (not built here).
- Provenance helpers (test-only, not production): `_stage_sitagliptin(tmp_path)` copies the untouched original into an isolated source dir after hash check; `SOURCE_RELATIVE project-documents/medical-documents/DDI-text/Antidiabetic Agents/Sitagliptin.txt`; `EXPECTED_COUNTS {contraindicated 0, serious 4, monitor_closely 92, minor 70}`.
- S01–S14 loops unchanged (`make setup/dev/stop`, `make migrate`, host-PG `DB_PORT`/`TEST_DB_PORT` preflight).

### Handoff

- Next engineering session: **S16** (extend the parser across real source formats) — builds on the S15 `build` + CLI seam with representative monographs from at least three different source groups, page-break/wrapped-heading handling, repeated-pair/contradictory-severity preservation, and a whole-corpus report-only run where every discovered file carries passed/failed status, counts, checksum, and diagnostic location. S15 leaves all of that to S16; single-Sitagliptin coverage is the explicit S15 boundary.
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus the `make migrate` entry point; verified this session — the DDI CLI is a direct `uv run python -m x_insight.ddi` invocation from `backend/`, no new make target, so no pointer change needed for S15).
- Top-level `README.md` unchanged (still the `project-documents/dev/` + `context/index.md` pointer; no new doc entry point needed for S15).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged (DDI aliases/release evidence still pending — see blockers); `content/ddi/` aliases/manifest untouched (S17/S18 scope); BNs, medical docs, `backend/`, `web/`, `e2e/`, `migrations/` untouched by this docs step; no clinical claims added here.
- S15 exit met: build interface and CLI agree; no SQL/runtime checker yet; expected counts come from the monograph, not the parser's own totals; no LLM used.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S15 exit blockers)

- Standards verdict: pass — `ruff format/check` + `mypy` pass, stdlib only, public `build`/CLI seam only with no private-preprocessor tests, no new deps.
- Spec verdict: pass — all 4 S15 steps met (provenance/discovery, actual-section identification, chrome-free spans with BOM/wrapping handled and stop before Adverse Effects/Warnings, exact 0/4/92/70 with removed-entry anomaly); CLI/library agree; no SQL/checker/LLM.
- Non-blocking follow-ups to carry: `_same_entity` / `_confirms_header` overlap (consider one helper when the next DDI session lands); `terminology` tolerated-not-resolved by design (resolution lands in S17); single-source only by design (≥3 groups + corpus report-only run lands in S16).

### Deferred items

- S14/S13/S12/S11/S10/S09/S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands — now also covering follow-up create; AIMS `awaiting_source`; S40/S41/S59 note-noninterference proofs; S48d/S51 staleness + S49 signing hooks).
- S16 owns multi-format parsing + whole-corpus report-only accounting; S17 owns concept/alias resolution (canonical/case/whitespace/alias → stable ID, ambiguity → unresolved, no fuzzy/salt/strip/split without reviewed rules, unordered-pair identity); S18 owns corpus report + reviewed publish (atomic immutable release, limited-coverage only with explicit owner acceptance, `coverage_unavailable` for excluded material); S19 owns the deterministic coverage-aware checker (T4/T1) — none of that was built in S15.
- S15 code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged, with the DDI items still pending: DDI aliases/release evidence (S17 terminology review for uncertain equivalences, S18 corpus-report review of high-risk/conflict/anomaly records plus contraindicated/serious evidence, unresolved names, and conflicting severities/management). Infrastructure proceeds on the S15 single-source fixture while responses are pending; S15 adds no new clinical blocker.
- Path discrepancy (recorded, not resolved here): tasks.md S15 cites `docs/medical-docs/DDI-text/Antidiabetic Agents/Sitagliptin.txt` — that path does not exist; verified actual path is `project-documents/medical-documents/DDI-text/Antidiabetic Agents/Sitagliptin.txt` (sha256 above). Future DDI sessions should keep citing the verified prefix and may correct the tasks.md shorthand.
- Infra debt (pre-existing, unrelated to S15): physicians-list `limit=100` single page (accumulated e2e physicians fall off the page, breaks identity-rename); diagnosis indeterminate firefox-only flake passing standalone.
- S15 exit met: one source parsed through the ingestion interface with provenance, actual-section identification, chrome-free traceable spans, exact 0/4/92/70 counts plus removed-entry anomaly, and CLI/library agreement — with terminology tolerated (S17), single-source boundary (S16), and review/publish (S18) explicitly deferred.

## S16.a — First bounded cut across real source formats (2026-10-06)

**Scope:** first bounded cut of S16 (tasks.md S16; plan.md §6, esp. §§6.1–6.2; FR-14, NFR-05). T3 only — extend the S15 `build` + CLI seam across 5 distinct real formats, whole-corpus report-only run, anomaly enumeration. Split into S16.a/b explicitly allowed by tasks.md ("Split into S16.a/b if real variants exceed a bounded session"). No terminology resolution (S17), no publish (S18), no SQL/runtime checker (S19), no migration, no frontend. Expected counts are monograph-declared source parsing expectations, not treatment guidance; direction preserved only when explicitly supported, otherwise `unknown`; no clinical claims, no approval claimed. Seams T3 only.

### Pinned / added deps (S01–S07 pins unchanged; S16.a adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S16.a).
- Ingestion stays stdlib only (`hashlib`/`re`/`dataclasses`/`pathlib`, `utf-8-sig` BOM-tolerant decode) — no new runtime lib, no fuzzy matcher, no external terminology dependency, no LLM, no network, no DB access.
- No new migration — head stays `0006` (S16.a writes only staged `candidate_dataset.json` + `report.json` under a caller-chosen output dir, never the database).
- `PARSER_VERSION` unchanged `ddi-ingestion/0.1.0` (no version bump for this bounded parser extension).
- S16.a files (code/tests untouched by this docs step): `backend/src/x_insight/ddi/ingestion.py` (+161/-40), `backend/tests/ddi/test_ingestion.py` (+195/-0 append-only). Only these 2 files changed by the implementation session; S15 tests untouched.

### Red→green slices + seams (T3; tasks.md S16 steps 1–3 for 5 formats + step 4 report-only)

- Slice 1 — Furosemide glued headings (loop-diuretic group): declared `0/11/177/127` (contraindicated/serious/monitor-closely/minor). Glued `Name (N)` severity headings split without span loss; repeated-pair evidence preserved across categories, not deduplicated.
- Slice 2 — Acetaminophen subject continuations (analgesic group): declared `0/3/24/48`. Multi-line subject continuations re-joined to complete interacting names with original 1-based `span_start`/`span_end` preserved; next sections never swallowed as interactions.
- Slice 3 — Atropine chrome/dense layout (anticholinergic group): declared `0/10/103/24`. Denser page chrome + wrapped entity headings handled; chrome-free spans asserted, timestamp/URL chrome dropped while entry continuity across chrome-only gaps preserved.
- Slice 4 — Gabapentin repeated pairs (anticonvulsant group): declared `1/30/211/15`. Repeated-pair entries and contradictory severity assertions survive ingestion (same pair in two categories kept as two entries); Sitagliptin/Ofloxacin S15 both-categories behaviour retained.
- Slice 5 — Empagliflozin prose commas (antidiabetic group): declared `0/0/40/1`. Prose-comma-separated interacting lists split into individual entries with shared span provenance; direction kept `unknown` unless explicitly supported (direction-unknown preserved by design, S17/S19 own resolution/checking).
- Slice 6 (report-only, tasks step 4) — whole-corpus report-only run: every discovered file carries passed/failed status + category counts + checksum + diagnostic location; failures stay in denominators. `report.json` + `candidate_dataset.json` still written on FAIL (nonzero exit), never silently dropped.
- Seams: T3 only. No T1/T2/T4–T10 exercised in S16.a; no SQL, no runtime checker, no LLM call to obtain passing counts. Tests extend `backend/tests/ddi/test_ingestion.py` only for distinct observed formats (tasks.md S16 test rule met: 5 new tests, one per format, append-only).

### Verification evidence (implementation-session report, S16.a scope)

- `make check` PASS (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- DDI slice: `backend/tests/ddi/test_ingestion.py` 15/15 PASS (`grep -c "^def test_"` = 15: 10 S15 carried + 5 new, one per format above).
- Full backend: 306 passed (no regressions beyond the DDI slice).
- Corpus accounting: 128 `.txt` files in 15 groups via sorted `rglob` (deterministic discovery order); CLI report-only run 69 passed / 59 failed, 26,956 entries total. Every file has passed/failed + counts + checksum + location in `report.json`; `candidate_dataset.json` + `report.json` still written on FAIL.
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (existing routes untouched; S16.a adds no routes, no tables).
- `make migrate` head unchanged — no new migration (versions still `0001`–`0006`, head `0006`).
- Env quirks carry forward from S08–S15 (procedural only): backend suite runs from `backend/` with `.env` sourced (disposable DBs on `5442`/`5443`); e2e needs `E2E_BASE_URL=http://localhost:5173` explicitly when the shell exports an empty `E2E_BASE_URL` (Makefile `?=` default defeated); stale `:8000` backend must be restarted to pick up new routes (no `--reload`).

### Local loop for next agent (S16.b+)

- Library: `build(source_dir, terminology=None, review_manifest=None) -> (CandidateDataset, Report)` from `x_insight.ddi.ingestion`; `terminology`/`review_manifest` accepted for the plan.md §6 interface but unused in S16.a (tolerated, never fatal — resolution lands in S17, review in S18). `CandidateDataset {parser_version, documents[]: {source_path, checksum, entries[]}}`; entry `{source_path, source_category, interacting_name, raw_text, span_start, span_end}`; `Report {parser_version, documents[]: {source_path, checksum, expected_counts, parsed_counts, passed, anomalies[]}, passed}`; `PARSER_VERSION ddi-ingestion/0.1.0`.
- CLI: from `backend/`, `uv run python -m x_insight.ddi build --sources <dir> --terminology <aliases> --output <staging-dir>`; exit 0 when every document passes, nonzero on structural/count failure with `report.json` + `candidate_dataset.json` still written under the staging dir; report-only corpus run uses the same command against the full `project-documents/medical-documents/DDI-text/` tree.
- Anomaly taxonomy (S16.a observed): `count-mismatch` 77, `no-section` 12, `missing-heading` 12 (remainder are span/format diagnostics enumerated per-file in `report.json` with source evidence for review/repair).
- S01–S15 loops unchanged (`make setup/dev/stop`, `make migrate`, host-PG `DB_PORT`/`TEST_DB_PORT` preflight).

### Handoff

- Next engineering session: **S16.b** (bounded remainder of tasks.md S16) — owns the queued variants below with source evidence; then **S17** (concept/alias resolution) and **S18** (corpus report + reviewed publish). S16.a leaves all of the queue to S16.b; 5-format + report-only coverage is the explicit S16.a boundary.
- S16.b queue (62 files, all under `project-documents/medical-documents/DDI-text/`, counts are declared-vs-parsed evidence where known):
  - 11 citation-style (Antipsychotics group, e.g. Aripiprazole) — whole group currently 0/23 passed.
  - 9 condensed-bullet (e.g. Clozapine `6/131/467/8 -> 1/1/1/1` parsed — bullet block yields one entry per category).
  - 2 uncounted (Loxapine, Lurasidone — no countable `Name (N)` headings).
  - 1 hybrid (Valproic acid — mixed heading styles in one file).
  - 1 no-interaction monograph (Simethicone — no interaction section by source).
  - 38 full-page residuals (±1–7 per file — off-by-small-count chrome/wrap variants for systematic pass in S16.b).
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus the `make migrate` entry point; DDI CLI remains a direct `uv run python -m x_insight.ddi` invocation from `backend/`, no new make target).
- Top-level `README.md` unchanged (still the `project-documents/dev/` + `context/index.md` pointer; verified this session — no new doc entry point needed for S16.a).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged (§1 S08 no-review policy stands; history/effects stay `awaiting_review`, DDI aliases/release evidence still pending — see blockers); `content/ddi/` aliases/manifest untouched (S17/S18 scope); BNs, medical docs, `backend/`, `web/`, `e2e/`, `migrations/` untouched by this docs step; no clinical claims added here.
- S16.a exit met: 5 distinct real formats through the ingestion interface with provenance, chrome-free traceable spans, exact declared counts per slice, direction-unknown preserved, whole-corpus report-only accounting with anomalies enumerated, S16.b split documented per tasks.md allowance.

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S16.a exit blockers)

- Standards verdict: PASS — `ruff format/check` + `mypy` pass, stdlib only, public `build`/CLI seam only with no private-preprocessor tests, S15 tests untouched, only 2 files changed (`ingestion.py`, `test_ingestion.py`).
- Spec verdict: PASS for the S16.a bounded cut — 5 formats + report-only + direction-unknown preserved + anomalies enumerated; S16.b split explicitly allowed by tasks.md S16 verify/exit. Remaining 59 corpus failures are queued S16.b scope, not S16.a incompleteness.
- Non-blocking follow-ups to carry: helper overlap noted in S15 (`_same_entity` / `_confirms_header`) still open — consider one helper when S16.b lands; `terminology` tolerated-not-resolved by design (resolution lands in S17); prose-comma span granularity (shared-span vs per-name spans) to revisit if S17 resolution needs tighter provenance.

### Deferred items

- S15/S14/S13/S12/S11/S10/S09/S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands — now also covering follow-up create; AIMS `awaiting_source`; S40/S41/S59 note-noninterference proofs; S48d/S51 staleness + S49 signing hooks).
- S16.b owns the 62-file queue above (citation-style, condensed-bullet, uncounted, hybrid, no-interaction, full-page residuals); S17 owns concept/alias resolution (canonical/case/whitespace/alias → stable ID, ambiguity → unresolved, no fuzzy/salt/strip/split without reviewed rules, unordered-pair identity); S18 owns corpus report + reviewed publish (atomic immutable release, limited-coverage only with explicit owner acceptance, `coverage_unavailable` for excluded material); S19 owns the deterministic coverage-aware checker (T4/T1) — none of that was built in S16.a.
- S16.a code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged, with the DDI items still pending: DDI aliases/release evidence (S17 terminology review for uncertain equivalences, S18 corpus-report review of high-risk/conflict/anomaly records plus contraindicated/serious evidence, unresolved names, and conflicting severities/management). Infrastructure proceeds on staged fixtures while responses are pending; S16.a adds no new clinical blocker.
- Path discrepancy carry-forward (recorded, not resolved here): tasks.md S15/S16 cite `docs/medical-docs/DDI-text/...` / `B/ddi/ingestion.py` / `BT/ddi/test_ingestion.py` shorthand — verified actual paths are `project-documents/medical-documents/DDI-text/...`, `backend/src/x_insight/ddi/ingestion.py`, `backend/tests/ddi/test_ingestion.py`. Future DDI sessions should keep citing the verified prefix.
- Infra debt (pre-existing, unrelated to S16.a): physicians-list `limit=100` single page (accumulated e2e physicians fall off the page, breaks identity-rename); diagnosis indeterminate firefox-only flake passing standalone.
- S16.a exit met: five distinct real formats parsed through the ingestion interface with provenance, chrome-free traceable spans, exact declared counts per slice (Furosemide 0/11/177/127, Acetaminophen 0/3/24/48, Atropine 0/10/103/24, Gabapentin 1/30/211/15, Empagliflozin 0/0/40/1), whole-corpus 128-file report-only accounting (69 passed / 59 failed, 26,956 entries, reports still written on FAIL), and anomaly taxonomy enumerated — with terminology (S17), remaining 62-file queue (S16.b), and review/publish (S18) explicitly deferred.

## S16.b — Bullet splitting → uncounted/cite/hybrid policies → per-file residual triage (2026-10-06)

**Scope:** bounded remainder of tasks.md S16, T3 only (tasks.md S16; plan.md §6, esp. §§6.1–6.2; FR-14, NFR-05). No S17 (concept/alias resolution), no S18 (corpus report + reviewed publish), no S19 (deterministic checker), no migration (head stays `0006`), no frontend. Ingestion stays stdlib only with `PARSER_VERSION ddi-ingestion/0.1.0` unchanged — no LLM, no fuzzy matcher, no SQL. Expected counts are monograph-declared source parsing expectations, not treatment guidance; direction preserved only when explicitly supported, otherwise `unknown`; no clinical claims, no approval claimed. Seams T3 only.

### Pinned / added deps (S01–S07 pins unchanged; S16.b adds no runtime lib)

- Runtimes carry forward: Python `3.12.14`, Node `22.23.2` / npm `10.9.8`, PostgreSQL 16 (`postgres:16-alpine`), `fastapi 0.142.2`, `sqlalchemy 2.1.3`, `alembic 1.20.0`, `psycopg[binary] 3.3.6` (`Makefile` header + `backend/pyproject.toml`, both untouched by S16.b).
- Ingestion stays stdlib only (`hashlib`/`re`/`dataclasses`/`pathlib`, `utf-8-sig` BOM-tolerant decode) — no new runtime lib, no fuzzy matcher, no external terminology dependency, no LLM, no network, no DB access.
- No new migration — head stays `0006` (S16.b writes only staged `candidate_dataset.json` + `report.json` under a caller-chosen output dir, never the database).
- `PARSER_VERSION` unchanged `ddi-ingestion/0.1.0` (no version bump for this bounded parser extension).
- S16.b files (code/tests untouched by this docs step): `backend/src/x_insight/ddi/ingestion.py` (+~305/-27), `backend/tests/ddi/test_ingestion.py` (+475/-0 append-only after line 504). Only these 2 files changed by the implementation session; S15 10 tests + S16.a 5 tests untouched and passing.

### Red→green slices + seams (T3; tasks.md S16 bounded remainder)

- Slice (a) — condensed-bullet splitting: bare `•`-header blocks split into one entry per bullet with per-bullet spans preserved. Pimozide `98/152/385/56` bare `•` headers now parse to declared counts; Trifluoperazine inline `• n:` splits pinned with honest declared-vs-parsed mismatches retained (never repaired by truncation or padding).
- Slice (b) — uncounted / citation / hybrid / no-interaction policies + markdown/citation family: Aripiprazole `DRUG INTERACTIONS` section recorded as `uncounted` (no countable `Name (N)` headings — passed with zero entries, not failed); Loxapine bare headings without counts likewise `uncounted`; Valproic mixed heading styles handled via `Significant-Monitor` alias to `Monitor Closely`; Simethicone with no interaction section by source recorded as terminal `no-section` pass with zero entries. Markdown/citation family: Asenapine `###`-headings with cite-per-item bullets, Thiothixene `*` bullets with `[cite]` headings — parsed with honest mismatches pinned where declared-vs-parsed differ.
- Slice (c) — per-file residual triage: Tramadol, Aspirin, Bisacodyl, Famotidine, Amantadine representatives used to derive shared guards (chrome/wrap/bullet guards) that move 33 residual files to green without per-file special-casing; 2 over-merge rules reverted (rejected: merging across blank-line boundaries and merging citation-only lines into the prior entry — both dropped after they collapsed distinct entries on the triage files).
- Seams: T3 only. No T1/T2/T4–T10 exercised in S16.b; no SQL, no runtime checker, no LLM call to obtain passing counts. Tests extend `backend/tests/ddi/test_ingestion.py` only for observed variants (append-only after line 504; 13 new tests this session).

### Verification evidence (implementation-session report, S16.b scope)

- `make check` PASS (ruff format-check + ruff check + mypy `src` + web build + root `tsc --noEmit`).
- DDI slice: `backend/tests/ddi/test_ingestion.py` 28/28 PASS (10 S15 carried + 5 S16.a carried + 13 new, all untouched prior tests still passing).
- Full backend: 319/319 PASS (306 S16.a baseline + 13 new; no regressions beyond the DDI slice).
- Corpus accounting (CLI report-only from `backend/`, terminology tolerated — `content/ddi/aliases.json` does not exist): 128 discovered, 102 passed / 26 failed, 30,667 entries total. `candidate_dataset.json` + `report.json` still written on FAIL (exit 1, never silently dropped). Every file carries passed/failed + counts + sha256 + diagnostic location; 0 missing keys/checksums, 0 passed-with-anomalies.
- `GET /api/v1/health` + `GET /api/v1/ready` preserved (existing routes untouched; S16.b adds no routes, no tables).
- `make migrate` head unchanged — no new migration (versions still `0001`–`0006`, head `0006`).
- Env quirks carry forward from S08–S16.a (procedural only): backend suite runs from `backend/` with `.env` sourced (disposable DBs on `5442`/`5443`); e2e needs `E2E_BASE_URL=http://localhost:5173` explicitly when the shell exports an empty `E2E_BASE_URL` (Makefile `?=` default defeated); stale `:8000` backend must be restarted to pick up new routes (no `--reload`).

### Local loop for next agent (S17+)

- Library: `build(source_dir, terminology=None, review_manifest=None) -> (CandidateDataset, Report)` from `x_insight.ddi.ingestion`; `terminology`/`review_manifest` accepted for the plan.md §6 interface but unused in S16.b (tolerated, never fatal — resolution lands in S17, review in S18). `CandidateDataset {parser_version, documents[]: {source_path, checksum, entries[]}}`; entry `{source_path, source_category, interacting_name, raw_text, span_start, span_end}`; `Report {parser_version, documents[]: {source_path, checksum, expected_counts, parsed_counts, passed, anomalies[]}, passed}`; `PARSER_VERSION ddi-ingestion/0.1.0`.
- CLI: from `backend/`, `uv run python -m x_insight.ddi build --sources <dir> --terminology <aliases> --output <staging-dir>`; exit 0 when every document passes, nonzero on structural/count failure with `report.json` + `candidate_dataset.json` still written under the staging dir; report-only corpus run uses the same command against the full `project-documents/medical-documents/DDI-text/` tree (terminology arg tolerated while `content/ddi/aliases.json` is absent).
- S16.b policies for reuse: condensed `•` bullets split per-bullet; `DRUG INTERACTIONS` without countable headings → `uncounted` pass with zero entries; bare headings without counts → `uncounted`; `Significant-Monitor` → `Monitor Closely` alias; no interaction section by source → terminal `no-section` pass; direction stays `unknown` unless explicitly supported; S15 Ofloxacin both-categories retention unchanged.
- S01–S16.a loops unchanged (`make setup/dev/stop`, `make migrate`, host-PG `DB_PORT`/`TEST_DB_PORT` preflight).

### Handoff

- Next engineering sessions: **S17** (concept/alias resolution) + **S18** (corpus report + reviewed publish). S16.b leaves terminology resolution and reviewed publish to S17/S18; bounded-remainder parsing with whole-corpus accounting is the explicit S16.b boundary.
- S16.b queue resolution (S16.a queued 62 files at 69 passed / 59 failed; S16.b closes to 102 passed / 26 failed — +33 to green via shared guards, no per-file special-casing beyond the triage representatives above).
- Remaining 26 failures with S17 vs S18 routing (all under `project-documents/medical-documents/DDI-text/`, counts are declared-vs-parsed evidence):
  - S17 scope (10: terminology/citation parsing, not honest content gaps) — `uncounted` (4): Aripiprazole, Chlorpromazine, Loxapine, Lurasidone; citation under-parses (6): Cariprazine, Paliperidone, Risperidone, Iloperidone, Thioridazine, Brexpiprazole.
  - S18 scope (15 honest declared-vs-parsed mismatches for owner review): Asenapine, Thiothixene, Trifluoperazine, Valproic acid, Clozapine, Olanzapine, Fluphenazine, Perphenazine, Haloperidol, Ziprasidone, Quetiapine, Molindone, Nortriptyline, Trazodone, Pantoprazole; plus terminal `no-section` Simethicone (1, no interaction section by source — review-confirmed terminal, not a parser gap).
- `backend/README.md` unchanged in this docs step (still points to the repo-root `make` loop plus the `make migrate` entry point; DDI CLI remains a direct `uv run python -m x_insight.ddi` invocation from `backend/`, no new make target).
- Top-level `README.md` unchanged (still the `project-documents/dev/` + `context/index.md` pointer; verified this session — no new doc entry point needed for S16.b).
- Unrelated content untouched: `content/review-ledger.md` §§1–7 owner gates unchanged (§1 S08 no-review policy stands; history/effects stay `awaiting_review`, DDI aliases/release evidence still pending — see blockers); `content/ddi/` aliases/manifest untouched (S17/S18 scope — `content/ddi/aliases.json` still absent); BNs, medical docs, `backend/`, `web/`, `e2e/`, `migrations/` untouched by this docs step; no clinical claims added here.
- S16.b exit met: every supplied source accounted for with anomalies enumerated (128 discovered, 102 passed / 26 failed with per-file passed/failed + counts + sha256 + location, reports written on FAIL).

### Code-review follow-ups (non-blocking; not approval, no clinical content — carry as future polish, not S16.b exit blockers)

- Standards verdict: 0 hard violations; 4 judgement-call smells only — Data Clumps (parallel heading/count params), Duplicated bullet cleaning (two adjacent cleaners), Shotgun category triple (`Significant-Monitor` alias + two call sites), test Data Clumps (repeated stage/build/assert preamble). All non-blocking; consider shared helpers when S17 lands.
- Spec verdict: 3 missing/partial + 3 wrong-look notes, all dispositioned non-blocking — corpus-run-not-in-diff (accounting verified operationally outside the diff: 128/102/26 with 0 missing keys/checksums), partial queue (remaining 26 are S17 terminology / S18 review scope by design, not S16.b incompleteness), direction unasserted (direction stays `unknown` by design), shared spans (prose-comma/bullet shared-span provenance carried from S15/S16.a), prose-filter breadth (shared guards deliberately broad to close 33 residuals), uncounted zeros (Aripiprazole/Loxapine zero-entry passes are policy, not silent drops). S15 Ofloxacin both-categories retained.
- Non-blocking follow-ups to carry: bullet-cleaner dedup; category-alias centralisation if S17 adds more aliases; tighter per-name spans only if S17 resolution needs it.

### Deferred items

- S16.a/S15/S14/S13/S12/S11/S10/S09/S08/S02–S07 deferred items unchanged (PANSS baseline-zero edge → S37; rule payload shape validation; cursor pagination, audit HTTP route, named role logins, per-command idempotency for remaining commands — now also covering follow-up create; AIMS `awaiting_source`; S40/S41/S59 note-noninterference proofs; S48d/S51 staleness + S49 signing hooks).
- S17 owns concept/alias resolution (canonical/case/whitespace/alias → stable ID, ambiguity → unresolved, no fuzzy/salt/strip/split without reviewed rules, unordered-pair identity); S18 owns corpus report + reviewed publish (atomic immutable release, limited-coverage only with explicit owner acceptance, `coverage_unavailable` for excluded material); S19 owns the deterministic coverage-aware checker (T4/T1) — none of that was built in S16.b.
- S16.b code-review follow-ups above are future polish, not exit blockers.

### Remaining blockers

- Owner decisions enumerated in `content/review-ledger.md` §§2–7 unchanged, with the DDI items still pending: DDI aliases/release evidence (S17 terminology review for uncertain equivalences incl. the 10 S17-routed files above, S18 corpus-report review of high-risk/conflict/anomaly records plus contraindicated/serious evidence, unresolved names, and conflicting severities/management for the 15 S18-routed honest mismatches). `content/ddi/aliases.json` still absent. Infrastructure proceeds on staged fixtures while responses are pending; S16.b adds no new clinical blocker.
- Path discrepancy carry-forward (recorded, not resolved here): tasks.md S15/S16 cite `docs/medical-docs/DDI-text/...` / `B/ddi/ingestion.py` / `BT/ddi/test_ingestion.py` shorthand — verified actual paths are `project-documents/medical-documents/DDI-text/...`, `backend/src/x_insight/ddi/ingestion.py`, `backend/tests/ddi/test_ingestion.py`. Future DDI sessions should keep citing the verified prefix.
- Infra debt (pre-existing, unrelated to S16.b): physicians-list `limit=100` single page (accumulated e2e physicians fall off the page, breaks identity-rename); diagnosis indeterminate firefox-only flake passing standalone.
- S16.b exit met: bounded remainder parsed through the ingestion interface with provenance, chrome-free traceable spans, condensed-bullet splitting + uncounted/citation/hybrid/no-interaction policies + residual-triage shared guards (33 files to green, 2 over-merge rules reverted), whole-corpus 128-file report-only accounting (102 passed / 26 failed, 30,667 entries, reports still written on FAIL with 0 missing keys/checksums and 0 passed-with-anomalies) — with terminology (S17), review/publish (S18), and checker (S19) explicitly deferred.
