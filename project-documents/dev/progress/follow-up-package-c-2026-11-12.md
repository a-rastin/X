# Follow-up Package C partial checkpoint — 2026-11-12

Package C is **incomplete**. At base revision `dd9dd766d36590ca8e77e7861e68282d1804a0dc`, uncommitted changes move existing baseline resolution after the patient-row lock and slot check. Legacy-only operations now use an explicit workflow allowlist; autosave rejects unsupported workflow versions and attempts to add follow-up state to legacy drafts. New follow-up creation still uses `legacy-v1` and its existing New case-only baseline selection; this is not the required latest-across-variants adapter.

Verification on the uncommitted working tree: Ruff check and format check passed for the changed Python files. `TEST_DATABASE_URL=<disposable PostgreSQL 16 database> .venv/bin/pytest -q tests/http/test_drafts.py tests/http/test_followup.py` from `backend/` passed 28 tests, including the new guard/forged-state check. The isolated database was migrated to head `0032` by the test fixture and its container removed. No application database was migrated. A sign/create race was not specifically tested.

Remaining: latest signed source across all variants and stable ordering; explicit v1–v5 adapters, frozen summary and per-field provenance; typed defaults, normalization and conditional validation; config/preview/validate-step APIs and shared types; comprehensive legacy-route audit, concurrency and compatibility checks. Package B recovery validation remains incomplete, and packages D–H remain open. Do not activate `follow-up-v1` until its state and guards are complete. No commit or deployment was made.

## Follow-up 2026-11-12 — backend state contract implemented (still partial)

Continuing from the checkpoint above, the typed `follow-up-v1` backend state layer
is now implemented and tested, but the workflow is **still not activated** and
Package C is **still incomplete**. All changes are uncommitted on the same base
revision `dd9dd766d36590ca8e77e7861e68282d1804a0dc`.

Added:

- `cases/follow_up.py`: strict Pydantic `follow_up` state (identity/pins/baseline
  server-owned; harm, history, effects, treatment, considerations, branches),
  exact fresh defaults (empty PANSS, five explicit C-SSRS `no` values, seven
  simple effects, null recovery/branch answers), `fresh_draft`, `structural_draft`
  (unknown-key/partial-state/finite-decimal/bounded-string refusal),
  `prepare_autosave` (rejects forged version, pins, frozen baseline, inherited
  answers and override attribution; stamps current overrides), pure
  `evaluate_state` (effective condition flags with manual > lab suggestion >
  inherited priority, BMI, lab warnings, conditional per-step errors, reached
  branch-question mask), `preview`, `validate-step` semantics and a versioned
  `config`. No decision engine, proposal, model call or provider dependency.
- `cases/follow_up_baseline.py`: `latest_signed_source` resolves the newest
  signed snapshot across all variants under the existing patient `FOR UPDATE`
  lock, ordered `signed_at DESC, id DESC` (stable tie-break) with the recorded
  visit ordinal. Explicit adapters for legacy v1–v3, New case v4 and follow-up
  v5 map only semantically equivalent structured fields; retired BN tristates,
  prior free-text plans and historical assessment/effect answers are never
  copied. Produces a bounded frozen summary, per-field provenance, dated prior
  labs/contraception, and pending items for unmatched historical catalog IDs.
  An unsupported variant fails closed instead of falling back to an older record.
- `cases/encounters.py`: `FOLLOWUP_V1_ACTIVE = False` gates creation. When on,
  `create_open_draft` resolves the signed source under the lock, refuses client
  inline baselines, and builds the typed draft; `patch_draft` routes typed
  autosave through `prepare_autosave` and keeps an explicit workflow allowlist;
  draft reads serve typed state verbatim. Legacy history/effects/diagnosis
  write commands now call `require_legacy_workflow` (medications, legacy sign
  and reasoning snapshots already did) so no sibling route can rewrite a
  typed body.
- `cases/router.py`: `GET /follow-up/config`, `GET /encounters/{id}/follow-up`
  (author-only preview, never creates a proposal) and
  `POST /encounters/{id}/follow-up/validate-step`; the legacy
  `/followup-baseline` read refuses the new version with `409 WRONG_WORKFLOW`.

Gate rationale (a real decision fork, recorded here rather than in `context/`):
activating `follow-up-v1` now would hand physicians a draft the running frontend
cannot dispatch (`web/src/features/encounters/workflow.ts` only renders
`legacy-v1` / `new-case-v1`) and that no v5 signing route can finalize (F), so
the single server-side constant stays off until F/G land. Flipping it is a
one-line change; the creation/read/edit/preview path is fully exercised in tests
by enabling the constant with `monkeypatch`.

Verification (disposable PostgreSQL 16 container, `postgres:16-alpine`, three
separate databases migrated to head `0032` by the fixtures; no application
database touched). All runs below are clean and serial — one pytest process per
database. An earlier attempt ran two pytest processes against one database and
produced interference failures (login/truncate races); those results are
discarded, not counted.

Static checks (repo root / `backend/`):

- `.venv/bin/ruff format --check src tests` → 212 files already formatted.
- `.venv/bin/ruff check src tests` → all checks passed.
- `.venv/bin/mypy src` → no issues in 88 source files.
- `git diff --check` clean; `codebase-memory/index.json` parses as valid JSON.

Test runs (`backend/`, `TEST_DATABASE_URL` pointing at a disposable database):

- `tests/assessments/test_follow_up_state.py
  tests/assessments/test_follow_up_baseline.py` — **23 passed** (defaults,
  forged metadata/provenance, override priority, conditional validation, age,
  all five adapters, stable tie-break, bounds).
- `tests/http/test_follow_up_state.py` — **6 passed** (config physician read,
  gated creation with exact defaults, verbatim read, strict autosave with
  422/412, read-only validate-step, inline-baseline refusal, legacy
  sibling-route guards, activation-off legacy behavior, and a real-SQL
  latest-signed-source ordering check with synthetic signed snapshots).
- `tests/assessments/test_follow_up_state.py
  tests/assessments/test_follow_up_baseline.py tests/http/test_follow_up_state.py
  tests/models/test_follow_up_sources.py tests/http/test_drafts.py
  tests/http/test_followup.py` — **58 passed**.
- `tests/http/test_follow_up_state.py tests/http/test_drafts.py
  tests/http/test_followup.py` — **34 passed**.
- Regression batch `tests/http/test_history.py tests/http/test_medications.py
  tests/http/test_new_case.py tests/http/test_new_case_plans.py
  tests/http/test_established_followup.py tests/http/test_signing.py` —
  **149 passed** (18m38s). The two diagnosis write commands were guarded after
  this batch started, so `tests/assessments/test_diagnosis.py
  tests/http/test_new_case.py` were rerun against the final code —
  **22 passed**.
- Final consolidated rerun on the finished code:
  `tests/assessments/test_follow_up_state.py
  tests/assessments/test_follow_up_baseline.py tests/http/test_follow_up_state.py
  tests/models/test_follow_up_sources.py tests/http/test_drafts.py
  tests/http/test_followup.py tests/assessments/test_diagnosis.py` —
  **72 passed**.
- Total across passing runs: 364 test executions (with deliberate overlap
  between the focused and combined selectors). No failures in any clean run.

The disposable container is removed after verification.

Remaining for Package C: a full two-physician sign/create race drill and
comprehensive compatibility/concurrency acceptance (H), TypeScript literal shared
types and the seven-step UI (G), the authoritative decision trace (D), proposal
fingerprint/freshness and v5 atomic signing (F), and complete mixed-record
recovery (B/H). Package C's own state, adapters, defaults, autosave, config and
preview/validate-step contracts are complete; creation activation is deliberately
deferred. No commit or deployment was made.
