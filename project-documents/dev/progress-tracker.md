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

Created `content/review-ledger.md`: index + per-item entries for assessments, history/severity, DDI, all 13 questions (plan.md §7.1), and workflow bundles. All items: status draft/`awaiting_review`, **none approved** (assessments excepted: S08 releases after validation with no `awaiting_review` flag per tasks.md S08). Reviewer named as owner; no approval claimed. No `content/assessments|history|questions/<key>/{manifest.json,network.xml,prompt.txt,template.json,examples.json,review.json}|bundles|ddi` files exist yet (content/ was empty before this ledger); all are missing and due in S08/S12/S18/S25–S39.

### Handoff

- **First engineering session: S01** (bootstrap reproducible dev loop) — depends on S00, seams T1/T9 smoke only.
- **Content tasks:** S08 assessments (no owner review) → S12 history → S15–S18 DDI → S25 contract → S26–S38 question packages → S39 bundle review/release.
- **Remaining blockers:** owner decisions enumerated in `content/review-ledger.md` §§2–7 (R1 setting/disposition, R3 jurisdiction/criteria, R5 product-choice contract, F6/BN-06 scope resolution, FR-14 regimen mismatch, history fields, DDI aliases/coverage, reference-table provenance). Infrastructure may proceed on synthetic fixtures while responses are pending.

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
