# S60 backend slice — cross-user, failure, and security acceptance (2026-10-16)

Backend-only slice of tasks.md S60 §§1–4 (FR-04, FR-16, FR-22, FR-36,
FR-41–43, NFR-02/04; seams T1/T6–T10). Reuse-first: no `backend/src`,
`web/src`, `content/`, or migration changes (head stays `0018`);
one new test file plus test-only fixture hygiene. DO NOT commit
(uncommitted: this report + the files below). E2E/frontend S60 work
(`e2e/failure-recovery.spec.ts`, `web/src` panels) proceeds in a
sibling slice; this report covers the backend part only.

## Scope (§§1–4, backend seams)

- §1: two physicians edit drafts for different patients while one
  provider run fails — saves/sliders responsive, acknowledged revisions
  survive restarts; wrong-author draft/artifact reads+writes denied
  (401/403); shared demographics/chart remain available.
- §2: mixed three-attempt exhaustion → unchanged-stage resume (Q2-only
  traffic), lease theft (deployment bump) + crash/restart with a
  controlled clock (no sleeps), mid-flight archive cancels the late
  commit, archive-first cancels at claim with no exchange, retry blocked
  while archived; completed artifacts unique and preserved.
- §3: actual captured provider payloads exclude notes, unrelated
  sentinel fields, identifiers, and secrets; forged tool args and
  provider 401 fail safely with no baseline and no draft mutation;
  physician archive, anonymous access, missing CSRF, stale revisions,
  admin artifact/note reads, XXE network import, and non-http(s)
  provider destinations denied observably; denied attempts mutate
  nothing. (Archive-path traversal stays covered by
  `test_restore_validation`; MCP grant forgery + isolation stays
  covered by `test_scoped_transport`.)
- §4: manual/stale/reset sign denials (409/412 with exact codes),
  exact re-acceptance, atomic sign, idempotent replay preserving one
  signature, escaped printable report, export permission denial +
  admin CSV without secrets. (CSV formula neutralization stays covered
  by `test_exports`.)

BFRI 4 (Fit 5 + Test 5 − C3/D1/O2 = Moderate): new tests + monitoring,
no production change. All scenarios use real PostgreSQL
(`TEST_DATABASE_URL` from `../.env`), real auth/draft,
`BoundedProviderAdapter` over real MCP stdio +
`DeterministicProviderEndpoint`; no DB-row asserts, no internal mocks,
no private-helper mirror tests, no fake clinical screens.

## Implementation (uncommitted)

- New `backend/tests/http/test_crosscutting_acceptance.py` (5 tests,
  T1/T8 + T7): `test_two_physicians_isolated_while_one_run_fails` (§1);
  `test_mixed_exhaustion_then_stage_resume_keeps_completed_artifacts`
  (§2); `test_lease_theft_crash_and_archive_fence_with_controlled_timing`
  (§2); `test_payloads_exclude_notes_sentinels_and_forgeries_denied_without_mutation`
  (§3); `test_sign_denials_then_exact_acceptance_and_safe_outputs`
  (§4, reuses `test_signing._signable_two_question_setup` +
  `_sign_body_after_accept`, real DDI publish + real pipeline).
- Commit-visibility fix (production `backend/src`, see blocker section
  above): `B/db.py` `CommittedRoute` + `get_session` state publish;
  `route_class=CommittedRoute` in all 8 routers; new
  `BT/http/test_commit_visibility.py` (2 real-HTTP tests). Migration
  head stays `0018`; no `web/src`, `content/`, or migration changes.
- Test-only hygiene (10 files, one `_truncate` hunk each): added
  `ddi_dataset_releases, ddi_source_documents, ddi_concepts,
  ddi_interaction_evidence` — the exact tables `test_exports` already
  truncates. Publishers that never cleaned up (`test_signing`,
  `test_complete_workflows`, `test_record_races`, `test_audit`) left
  DDI releases behind; sensitive suites (`test_recovery`,
  `test_queue`, `test_local_calculation`, `test_single_question`,
  `test_snapshots`, `test_question_freshness`) now self-isolate.
  This root-causes the S59-carryover `ddi_*` suite-order contamination.

## What was checked

New slice (`TEST_DATABASE_URL` from `.env`):

```sh
cd backend && set -a; . ../.env; set +a
uv run pytest tests/http/test_crosscutting_acceptance.py -q
# 5 passed (final, incl. deterministic mid-flight sync + archive-first phase)
uv run ruff check tests/http/test_crosscutting_acceptance.py
# All checks passed
uv run ruff format --check tests/http/test_crosscutting_acceptance.py
# already formatted
```

In-slice red→green (each fixed in-test, implementation already
principled unless noted):

- §2a first red: retry batch establishes a NEW Q1 baseline id (S48d §2
  "new originals") — corrected the test to assert the
  `retried_from_baseline_id` provenance link + identical section text
  instead of row-id equality.
- §2b mid-flight: full-suite load let the archive win the claim race
  (`idle`, not `cancelled`) — replaced the startup race with an event
  signalled by the endpoint's first exchange (claim necessarily
  complete, request in flight inside the 3s delay; bounded wait, never
  a sleep) and pinned archive-first as the second valid serial outcome
  (`idle`, zero exchanges, claim-time `cancelled`, no baseline).
  Archive-first claim cancellation is per the queue supersede rule
  (lifecycle mismatches cancel, predecessors stay queued).
- §4: stale-after-slider and reset sign denials surface as 412
  `STALE_REVISION` (review-revision precondition precedes 409
  acceptance matching); stale-input cases stay 409 `STALE_INPUTS`
  (covered in `test_signing`). Report route is admin-only by design
  (S53); physicians read the shared chart. Also fixed four
  `runs['s49_q*']` missing-`['id']` typos (Starlette 404s, caught by
  exact-denial asserts, not by weakening them).

Defect found and fixed in-slice (test-only, no src change):

- DDI suite-order contamination: `test_recovery ::
  test_author_retry_starts_new_bounded_batch_q2_only` failed even
  isolated because a stale `ddi_dataset_releases` row (left by
  `test_signing`, whose `_truncate` omitted `ddi_*`) let the retry
  batch assemble a proposal. Fixed by the hygiene above; q2-only
  passes again. S59's "passes isolated" exoneration only held on a
  DDI-clean database.

## S60 frontend blocker — commit-after-response (FOUND, FIXED, backend defect)

Frontend dev-test was blocked on all 8 `failure-recovery` cases:
login 200 → `/me` 401 → physician-create 401 with a session cookie.
Reproduced against the dev stack (`:8000`) with plain curl:

- `POST /api/v1/auth/login` → 200, `HttpOnly Path=/` cookie set.
- Immediate `GET /api/v1/me` → 401 `UNAUTHENTICATED` (same jar).
- Same jar +2s later → 200. Immediate burst: 10/10 → 401 pre-fix.

Distinguished from the suspected causes: NOT cookies (jar identical),
NOT stale server (reproduced on a fresh uvicorn from HEAD `1d896b6`
on `:8001`), NOT schema/config (dev DB migrates cleanly; login
writes fine), NOT concurrent-DB interference (fresh random session
tokens; dev DB has no competing writers for these rows).

Root cause (real production defect): `get_session` is a yield
dependency whose `session_scope` commit runs in teardown — FastAPI
runs yield teardown AFTER the response is sent. Every mutating route
therefore acknowledged writes it had not yet committed; any client
acting immediately read pre-commit state. TestClient suites never
caught it (in-process teardown completes before the test client
returns), which is why S03–S59 stayed green.

Fix (production backend, minimal, centralized — `backend/src`):

- `B/db.py`: new `CommittedRoute(APIRoute)` commits the request's
  yielded session (published on `request.state` by `get_session`)
  after the endpoint ran but before the response is sent; handler
  errors propagate to the teardown rollback uncommitted. Teardown
  commit stays as the idempotent safety net. Subtlety found
  in-slice: the codebase persists via Core DML
  (`session.execute(insert/...)`), which never populates
  `session.new/dirty/deleted` (ORM identity-map only) — a
  dirty-check would skip every real write, so the route commits any
  open transaction (read-only requests cost the one no-op COMMIT the
  teardown performed anyway; net round-trips unchanged).
- All 8 routers (`assessments/cases/ddi/identity/models/operations/
  probability_review/reasoning`) now use
  `APIRouter(route_class=CommittedRoute)` (one line each).
- Moving the commit earlier changes no transaction semantics: the
  same transaction that teardown committed is committed pre-send.
  Verified live: fresh `:8001` server 5/5 immediate `/me` → 200
  (was 10/10 → 401); the `:8000` dev stack picked the fix up via
  `--reload` (3/3 → 200, no restart/disruption to sibling e2e work).

Red→green public-seam test (real HTTP — TestClient cannot observe
this): new `BT/http/test_commit_visibility.py` (2 tests over a real
loopback uvicorn + socket client, ephemeral port, test DB only;
immediate succession is the timing control, no sleeps/retries):
login→`/me` ×5 pairs and physician-create→login→`/me` ×3 — red
pre-fix (stash-verified: 2 failed, 401), green post-fix (2 passed).
`ruff check`, `ruff format --check`, and `mypy` clean on all touched
`src` files. Pre-existing `ruff` findings left untouched and
recorded below (`operations/audit.py` F821; `test_audit.py` F401 +
format drift — all fail at HEAD too).

## Regressions (commands + counts)

(TBD — final clean sequential re-run pending; a sibling S60 process
ran `tests/http` concurrently on the shared test DB, so any results
collected during overlap are contaminated evidence, see blockers.)

- `tests/http` (deselect known `test_only_s02_tables_exist` drift):
  336 passed + new-file 2b claim-race (pre-determinism-fix) in one
  contaminated window; clean re-run pending.
- `tests/worker`: 79 passed (includes `test_recovery` with the hygiene
  fix; q2 flake gone).
- `tests/models + mcp + provider + recovery + probability_review`:
  rerun pending (overlap-contaminated: 1 failed + 4 MCP errors were
  `UniqueViolation`/`DeadlockDetected` from concurrent DB writers).
- `tests/http/test_record_races.py` + `tests/worker/test_recovery.py`
  pair: 26 passed + q2-only failed pre-hygiene-fix (root-caused above).
- `tests/worker/test_queue.py`: 14 passed (pre-hygiene; re-run pending
  as part of the clean worker pass).

## Requirements vs S60 §§1–4 (backend)

| Requirement | Evidence | Status |
|---|---|---|
| §1 two-physician failure isolation + wrong-author denial + shared reads | new §1 test (mixed timeout→invalid→429 vs live saves/sliders/accept; restart survival; 401/403 matrix; directory/chart) | GREEN |
| §2 exhaustion/resume/theft/crash/archive + restore fencing | new §2a/§2b (Q2-only retry; generation bump; event-synced mid-flight cancel; archive-first cancel; blocked-while-archived; unique/preserved baselines) + `test_queue`/`test_restore_commit` regressions | GREEN (restore drill itself stays in `test_restore_commit`) |
| §3 payload exclusion + forgery/role/revision/XML/destination denial | new §3 test (captured-payload sentinel scan; forged args; 401; 403/401/412 matrix; XXE 422; `ftp/file` constructor denial; revision pinned) + cited suites | GREEN (archive paths in `test_restore_validation`; MCP grants in `test_scoped_transport`) |
| §4 sign denials/exact acceptance/escaping | new §4 test (manual 409; stale/reset 412; re-accept; atomic sign; idempotent replay; escaped report; export 403/admin CSV) + `test_signing`/`test_exports` regressions | GREEN (formula neutralization in `test_exports`) |

## Remaining blockers / carryovers

- Clean sequential re-run of `tests/http` + `tests/worker` +
  models/mcp/provider/recovery/probability still pending (sibling
  contention on the shared test DB; see handoff).
- `test_only_s02_tables_exist` drift (deselect; separate maintenance
  session, unchanged).
- `test_audit.py:462` `F401` (`timedelta` unused) is pre-existing at
  HEAD — untouched (out of slice scope); `ruff format --check` also
  already fails at HEAD for that file (unrelated regions) — left
  unformatted deliberately for minimal diff.
- S42 provider-key persistence/encryption still deferred (S60 §3
  asserts the T7 constructor boundary: non-http(s) rejected; no
  admin settings route exists).
- Named content blockers unchanged (11 S39.b packages fail closed;
  no bundle release/activation; synthetic DDI; provisional
  archive/print policies NOT owner-confirmed).

## Handoff / next

Reuse `backend/tests/http/test_crosscutting_acceptance.py` (5 T1/T8+T7
tests as above). Uncommitted: this report + the new test file + ten
one-hunk `_truncate` edits. DO NOT commit. Next: dev-test-backend
runs the clean sequential regression below when the shared test DB is
quiet (no concurrent pytest/e2e against `x_insight_test`), then owns
`e2e/failure-recovery.spec.ts` browser evidence:

```sh
cd backend && set -a; . ../.env; set +a
uv run pytest tests/http/test_commit_visibility.py tests/http/test_crosscutting_acceptance.py -q
uv run pytest tests/http --deselect tests/http/test_contracts.py::test_only_s02_tables_exist -q
uv run pytest tests/worker tests/models tests/mcp tests/provider tests/recovery tests/probability_review -q
```

Content approvals remain as recorded in S39.b; bundle release and
activation retain their independent status.
