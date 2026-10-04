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
