# S48c-backend — Exact current-result acceptance endpoint (2026-10-10)

Backend-only slice of tasks.md S48c §4 (FR-57, FR-42; seam T1):
`POST /api/v1/question-runs/{id}/acceptance` plus the acceptance state in
`GET /question-runs/{id}/review`. No frontend (`web/`), `e2e/`, or
`content/` changes. Tests use real PostgreSQL, real HTTP routes, no
internal mocks, no DB-row asserts (GET review + POST only).

## Scope and synthetic basis

- Author-only (physician + CSRF, same helpers as the adjustment/reset/
  retry routes): wrong author/admin reads and writes are 403 without
  content leak; missing runs stay 404.
- Body carries the exact current `baseline_id` /
  `current_cpt_revision_id` (null for unchanged originals) / `cpt_hash` /
  `result_id` / `input_hash` plus `expected_review_revision` (`If-Match`
  wins when present). The server recomputes the live point and rejects:
  no successful current result — pending original, recalculating, failed
  current, failed original (`409 NO_SUCCESSFUL_RESULT`); stale inputs
  (`409 STALE_INPUTS`); any mismatched reference (`409
  REFERENCE_MISMATCH`); stale pointer (`412 STALE_REVISION`); malformed
  bodies (`422`); idempotency-key reuse with a changed body (`409
  IDEMPOTENCY_CONFLICT`).
- Unchanged originals are accepted too (baseline result, null revision).
- Invalidation is an exact-match check on read (never deletion): later
  edits/reset move the revision pointer, relevant patient edits move the
  input fingerprint, so older rows stop matching; history rows are
  retained. Note-only edits leave the fingerprint unchanged and preserve
  acceptance.
- One immutable `probability_acceptances` row plus atomic
  `prob_acceptance.success` audit per accepted exact state (actor/time/
  patient/encounter/question/run/revision/result/input-hash); re-accepting
  the same exact state returns the existing row (no duplicate, no extra
  audit).
- `GET .../review` adds `acceptance` (current-matching row or null),
  `is_accepted`, and `acceptances` (retained per-run history), alongside
  the existing `current_cpt_revision_id` /
  `displayed_result_revision_id` / `current_result_matches` /
  `calculation_state` / `input_freshness`. Earlier results are never
  labeled current.
- Sign UI consumption (S49): every acceptance exposes id, encounter/
  question-run/batch, baseline, revision (nullable), CPT hash, result
  kind+id, input hash, projection hash, actor/time. No signing here.

Depends DONE: S48a (adjustments), S48b (local calc/reset/retry), S44/S47
(queue/recovery), S45 (baseline + review). NOT modified: S48d
freshness/regeneration, S49 signing, `test_only_s02_tables_exist` drift.

Synthetic scope explicit: two-node A→B network (`80/20`, `90/10`,
`30/70`) with `DeterministicProviderEndpoint` +
`BoundedProviderAdapter` for originals (real MCP stdio path from S45);
locals use `ControlledStubAdapter` (`calls == []`); local failure forcing
via the absent-only template variant. Reuses `s48a_package()` +
`_new_physician_batch`/`_succeed_batch` helpers. Independent fixtures per
test; expected values are worked literals (`[0.66,0.34]` after A→40,
`[20,80]` for B|no yes→80), never implementation output.

## Implementation

New:

- `backend/migrations/versions/0015_probability_acceptance.py`
  (`probability_acceptances`: run/batch/encounter/baseline FKs, nullable
  revision FK, CPT hash, result kind+id, input hash, projection hash,
  actor/time; app SELECT+INSERT, readonly SELECT, migrate ALL).
- `backend/tests/http/test_probability_acceptance.py` (T1, 11 tests).

Extended (backend only):

- `probability_review/tables.py` — `probability_acceptances` metadata.
- `probability_review/service.py` — `safe_acceptance`,
  `list_acceptances`, `acceptance_request_hash`,
  `_live_acceptance_point`, `find_current_acceptance`,
  `apply_acceptance` (caller's transaction; 422/412/409 per above).
- `probability_review/router.py` — `POST .../acceptance`
  (author-only, If-Match-wins, idempotency replay/conflict).
- `reasoning/router.py` — GET review gains
  `acceptance`/`is_accepted`/`acceptances`.
- `db.py` — `EXPECTED_SCHEMA_VERSION 0014` → `0015`.

## What was checked

New suite (`TEST_DATABASE_URL` from `.env`):

```sh
cd backend && set -a; . ../.env; set +a
uv run pytest tests/http/test_probability_acceptance.py -q
# 11 passed in ~37s
```

Regressions (same env, sequential — suites share one test database):

```sh
uv run pytest tests/http/test_probability_acceptance.py tests/probability_review tests/http/test_probability_review.py -q
# 32 passed (11 new + 11 T5 + 10 T1)
uv run pytest tests/worker/test_snapshots.py tests/worker/test_queue.py tests/worker/test_single_question.py tests/worker/test_workflows.py tests/worker/test_recovery.py -q
# 51 passed
uv run pytest tests/worker/test_local_calculation.py -q
# 13 passed
uv run pytest tests/models -q
# 208 passed
uv run pytest tests/mcp/test_scoped_transport.py tests/provider/test_estimation.py -q
# 24 passed
uv run pytest tests/http --deselect tests/http/test_contracts.py::test_only_s02_tables_exist -q
# 268 passed, 1 deselected
uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy src
# 117 files formatted, all checks passed, 60 source files no issues
```

Full `tests/http -q` shows 268 passed + 1 failed:
`test_only_s02_tables_exist` expects only S02–S24 tables but the migrated
head (0015) now also holds reasoning/cpt/calculation/acceptance tables.
Pre-existing failure class from the S48a/S48b implementation (not from
the new test file); left untouched per preserve-unrelated-suites.

## What works

- T1 §4: empty acceptance state on fresh success; author accepts unchanged
  originals (baseline kind, null revision, 80/20 preserved).
- T1 §4: 403-without-leak for stranger/admin + 404 stays 404 + 401
  unauthenticated; no stranger write creates a row.
- T1 §4: recalculating/failed/no-baseline currents are 409 with no write;
  stale inputs are 409; each forged reference is 409 naming the field;
  stale pointers are 412; idempotent replay returns the identical body,
  changed-body reuse is 409, keyless re-accept dedupes to one history row.
- T1 §4: accept → adjust/reset lapses (`is_accepted` false, `acceptance`
  null, history grows); re-accept after recalculation/reset-reuse works
  (reset binds the verified baseline reuse, `reused_from_baseline_id`
  equals the baseline); revisions never deleted.
- T1 §4 + plan §9.2: a proposal-page note after acceptance leaves
  `is_accepted` true with the same acceptance id.

## Remaining blockers

- `test_only_s02_tables_exist` needs its expected table list updated for
  0009–0015 (reasoning, baselines, proposals, cpt_revisions,
  question_review_states, calculation_results, probability_acceptances,
  etc.) in a separate maintenance session; not fixed here to preserve
  unrelated suites.
- S48c §§1–3 (CPT review/comparison frontend), S48d (affected-only
  regeneration), S49 (signing consumes these acceptance references)
  untouched; `prob_acceptance.success` audit is proven atomically via
  acceptance/pointer stability (no separate audit endpoint exists; no
  DB-row asserts per seam contract).

## Handoff / next session

Reuse `s48a_package()` + `_new_physician_batch`/`_succeed_batch`/
`_accept_body(review)` (builds the exact current POST body, with a
`result_id=` override for pending/failed negatives) and the
`["80","20"]`/`["90","10"]`/`["30","70"]` literals. Defaults unchanged
(T=100_000_000, redistribution-v1, review_revision starts 1, +1 per
adjustment/reset; acceptance never bumps the pointer). Frontend (S48c
§§1–3) reads per-question `GET .../review`: `acceptance`/`is_accepted`/
`acceptances` plus the existing
`current_cpt_revision_id`/`calculation_result`/`calculation_state`/
`input_freshness`/`review_revision` to build the accept action and its
POST body; dev-test e2e covers forged-acceptance and edit-invalidates
journeys. Next is **S48c frontend + e2e**, then **S48d**, then **S49**
(signing enforcement).

## Follow-up — 2026-10-10 (S48c code review, docs only)

No code changes here. The S48c completion roll-up and the non-blocking
code-review follow-ups (Standards smells, Spec partials, scope notes, nits)
are recorded in [S48c-devtest](s48-c-devtest.md). Next is S48d, then S49;
content approvals remain as recorded in S39.b.
