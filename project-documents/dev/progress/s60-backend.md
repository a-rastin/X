# S60 backend slice — cross-user, failure, and security acceptance (2026-10-16)

Backend-only slice of tasks.md S60 §§1–4 (FR-04, FR-16, FR-22, FR-36,
FR-41–43, NFR-02/04; seams T1/T6–T10). Reuse-first: two new test files,
one production commit-timing fix with test-only hygiene. Head stays
`0018` (no migrations; no `web/src` or `content` changes). The slice's
files were committed as `3294e8c` by the coordinating session; only
the `B/db.py` import-order line and this report's finalization below
remain uncommitted — DO NOT commit (coordinator owns commits). E2E/frontend S60 work
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

## Implementation (committed as `3294e8c` save noted deltas)

- New `backend/tests/http/test_crosscutting_acceptance.py` (7 tests,
  T1/T8 + T7): `test_two_physicians_isolated_while_one_run_fails` (§1);
  `test_mixed_exhaustion_then_stage_resume_keeps_completed_artifacts`
  (§2); `test_lease_theft_crash_and_archive_fence_with_controlled_timing`
  (§2); `test_payloads_exclude_notes_sentinels_and_forgeries_denied_without_mutation`
  (§3, incl. valid tool-bridge + echoed-MCP-result + stored-response
  scans); `test_sign_denials_then_exact_acceptance_and_safe_outputs`
  (§4, reuses `test_signing._signable_two_question_setup` +
  `_sign_body_after_accept`, real DDI publish + real pipeline);
  `test_overlapping_provider_hold_with_restart_durable_reread` (§1
  review: 5s held attempt + subprocess worker + 2× API restart);
  `test_inflight_generation_bump_fences_stale_commit` (§2 review:
  mid-flight fencing + reclaim-once).
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

## Final review gaps (both closed red→green, 2026-10-16)

- **Overlap ordering proof.** The first version held A's attempt with a
  fixed 5s delay but never proved B's acknowledgments landed BEFORE
  A completed (under load B's ops could slip past the release and the
  test would still pass). Now the endpoint supports a public `hold`
  script step (gated on a caller-supplied `threading.Event`, bounded
  wait, fail-safe serve on timeout) plus the `on_exchange` hook; the
  test releases ONLY after B's acceptance is recorded AND A reads
  back still-leased with no baseline — all inside try/finally with
  bounded join (no sleeps). Caught in-slice: unifying the exchange
  signal and the release gate into one `Event` opened the gate the
  moment the exchange fired (attempt completed pre-release);
  they are now two distinct events (`exchange_fired` vs
  `release_gate`).
- **Seeded tool encounters + exact grant value.** The tool-bridge
  encounter previously carried no sentinels of its own. Now both
  tool-bound encounters (T7 `0012345609`, T8 `0012345608`) are seeded
  with their own notes, unrelated history fields, and sentinel
  phones/names; every outbound exchange, the echoed MCP tool result,
  and the stored `raw_response` are scanned for those values — and
  the T7 probe additionally scans both exchanges for the EXACT
  per-job grant token value (minted via a deterministic queue claim
  while it is the only eligible job), not just the `grant_token` key.

**Restart / process-boundary limits (accurate).** The numbered items
below are the earlier code-review blockers 1–5.

- In-test uvicorn servers run in-process threads on ephemeral ports,
  NOT separate OS processes. What the two restarts prove: fresh
  server instances + rebuilt engines + fresh logins reread
  byte-identical durable state (no in-memory/pool coupling).
- The TRUE OS-process boundary proven here is the worker
  SUBPROCESS (`sys.executable -c run_once`, frozen clock → `idle`),
  which observes the same durable rows from another process.
- NOT proven here: full multi-process deploy restart (supervised
  API + worker + MCP processes, socket handover, in-flight drain).
  That remains S57 packaging / S62 rehearsal scope; replacement and
  rollback fencing itself is proven by the S56 restore drill, whose
  deployment-generation mechanism the in-flight fencing test
  exercises directly.

## Code-review blockers 1–5 (all addressed, red→green, no full S60 claim)

1. **ASGI blocking commit** — `CommittedRoute` called sync
   `session.commit()/rollback()` on the event loop. Fixed via
   `starlette.concurrency.run_in_threadpool` (same threading posture
   as the pre-existing teardown commit, strictly safer). Added
   `test_event_loop_responsive_during_slow_commit` (real HTTP):
   every `Session.commit` delayed 1.5s by controlled fault injection
   while `GET /health` (session-free probe) must answer < 1.0s —
   red on sync code (probe stalled 1.97s), green on threadpool.
   Minimal safe lifecycle preserved (error → rollback, teardown
   safety net intact).
2. **§1 acceptance gaps** — new
   `test_overlapping_provider_hold_with_restart_durable_reread`: A's
   attempt HELD open 5s (`on_exchange`-synced) while B saves,
   adjusts, recalculates locally (concurrent `run_once`, independent
   slot), and accepts — all during the hold; then a worker
   SUBPROCESS (separate OS process, frozen clock → `idle`) plus TWO
   API-server restarts (new instances, rebuilt engines) with
   byte-identical real-HTTP rereads of B's acceptance and A's ledger.
   (Caught in-slice: `run_once` has no question filter, so B must
   succeed before A starts, else the first claim takes oldest-eligible
   A; and live-server rereads need real logins — both fixed, green.)
3. **In-flight fencing** — new
   `test_inflight_generation_bump_fences_stale_commit`: response held
   open while generation bumps (the exact mechanism the S56 restore
   drill uses — restore bumps `max(live,staged)+1`, old workers fence
   identically), released stale response denied
   (`fencing_failed`/`DEPLOYMENT_FENCED`), no baseline, ledger
   intact; post-lease-expiry reclaim succeeds exactly once with a
   unique artifact (2 exchanges total, attempts 2, one baseline).
   No destructive restore needed; drill stays in
   `test_restore_commit`.
4. **Tool-exchange scan** — §3 test extended: a VALID tool-bridge run
   (2 exchanges) scans BOTH outbound payloads plus the echoed MCP
   tool result (`tool_call_id` correlation, projection binding) for
   notes, unrelated sentinels, identifiers, secrets, and any
   `grant_token` key; stored `raw_response` scanned for logged
   secrets. All excluded (if the MCP result ever echoed grant
   material it would be a real leak — asserted absent, not weakened).
5. **Public endpoint hook** — the private `_next` monkeypatch is
   gone: `DeterministicProviderEndpoint` gained a public
   `on_exchange` constructor callback (test-only double, never
   production) fired per POST before the scripted step; both
   mid-flight tests use it. No other `_next` users.

## Regressions (commands + counts, final tree)

`TEST_DATABASE_URL` from `.env` (`x_insight_test`, quiet — sibling
chains isolated on `x_insight_test_s60*`); sequential, no sleeps.

- New slice: `test_commit_visibility` 3 passed (2 red→green +
  responsiveness red→green on sync code); `test_crosscutting_acceptance`
  7 passed (5 + overlap/restart + in-flight fencing; §2b/§3/§4
  red→green in-slice as recorded above). Final confirmation on the
  exact final tree: both files together **10 passed** (mypy `hold`
  guard rework included, behavior-identical).
- Related: `test_identity` 22, `test_notes` + `test_drafts` 32,
  `test_queue` 14, `test_estimation` 11, `test_mcp` 13, `test_models`
  212, `test_provider + recovery + probability_review` 59 — all passed.
- `tests/worker` full: 79 passed (pre-threadpool src; behaviorally
  equivalent for TestClient-speed commits — responsiveness is proven
  by the dedicated test; dev-test re-runs isolated).
- `tests/http` full: 336 passed + 1 deselected (known S02 drift) on
  the pre-threadpool tree; the only failure seen anywhere in-window
  was the §2b claim race, fixed deterministically since (event sync +
  archive-first phase) and green in the 7-test file pass.
- Contamination accounting: an overlapping-window
  `models/mcp/provider/recovery/probability` result (1 failed + 4 MCP
  `UniqueViolation`/deadlock errors) was concurrent-writer noise —
  clean re-runs are all green above. One REAL failure surfaced there:
  `test_backup … app_schema_version == "0017"` vs actual `"0018"` —
  pre-existing S59-carryover literal in an untouched file (not fixed
  here; separate maintenance session).
- My own killed chains (waiter + partial http leg, SIGTERMed to stop
  shared-DB interference) reported partials are discarded, not evidence.

## Requirements vs S60 §§1–4 (backend)

| Requirement | Evidence | Status |
|---|---|---|
| §1 two-physician failure isolation + wrong-author denial + shared reads | §1 test (mixed timeout→invalid→429 vs live saves/sliders/accept; restart survival; 401/403 matrix; directory/chart) + overlap test (5s held attempt; B acknowledges inside hold; subprocess worker; 2× API restart, byte-identical rereads) | GREEN |
| §2 exhaustion/resume/theft/crash/archive + restore fencing | §2a/§2b (Q2-only retry; generation bump; event-synced mid-flight cancel; archive-first cancel; blocked-while-archived; unique/preserved baselines) + in-flight fencing test (mid-flight bump → `DEPLOYMENT_FENCED`, reclaim-once) + `test_queue`/`test_restore_commit` regressions | GREEN (restore drill itself stays in `test_restore_commit`) |
| §3 payload exclusion + forgery/role/revision/XML/destination denial | §3 test (captured-payload sentinel scan incl. valid tool exchange + echoed MCP result + stored raw response; forged args; 401; 403/401/412 matrix; XXE 422; `ftp/file` constructor denial; revision pinned) + cited suites | GREEN (archive paths in `test_restore_validation`; MCP grants in `test_scoped_transport`) |
| §4 sign denials/exact acceptance/escaping | new §4 test (manual 409; stale/reset 412; re-accept; atomic sign; idempotent replay; escaped report; export 403/admin CSV) + `test_signing`/`test_exports` regressions | GREEN (formula neutralization in `test_exports`) |

## Remaining blockers / carryovers

- Full `tests/http` + `tests/worker` on the FINAL tree (threadpool +
  endpoint hook) were not re-run here — dev-test owns them isolated
  per instruction; pre-threadpool full passes stand (worker 79,
  http 336 + 1 deselected) and every behaviorally-affected seam was
  re-proven bounded above (visibility 3, crosscutting 7, identity 22,
  notes+drafts 32, queue 14, estimation 11, mcp 13, models 212,
  provider+recovery+probability 59).
- `test_backup … app_schema_version == "0017"` (actual `"0018"`) is a
  pre-existing S59-carryover literal in an untouched file — separate
  maintenance session, not fixed here.
- `test_only_s02_tables_exist` drift (deselect; separate maintenance
  session, unchanged).
- `operations/audit.py` F821 + mypy `Mapping` and `test_audit.py`
  F401 + format drift are pre-existing at HEAD — untouched (out of
  slice scope).
- S42 provider-key persistence/encryption still deferred (S60 §3
  asserts the T7 constructor boundary: non-http(s) rejected; no
  admin settings route exists).
- Named content blockers unchanged (11 S39.b packages fail closed;
  no bundle release/activation; synthetic DDI; provisional
  archive/print policies NOT owner-confirmed).

## Handoff / next

Reuse `backend/tests/http/test_crosscutting_acceptance.py` (7 T1/T8+T7
tests as above) and `backend/tests/http/test_commit_visibility.py`
(3 real-HTTP tests: login→`/me`, create→login, slow-commit
responsiveness). Uncommitted now: `B/db.py` import-order line,
this report. Everything else S60-backend is committed in `3294e8c`
(sibling commit of this slice's files). Next: dev-test-backend runs
the full isolated regression (commands below, quiet DBs) then owns
`e2e/failure-recovery.spec.ts` browser evidence (already 8/8 per
`s60-frontend-devtest.md` after this slice's commit fix):

```sh
cd backend && set -a; . ../.env; set +a
uv run pytest tests/http/test_commit_visibility.py tests/http/test_crosscutting_acceptance.py -q
uv run pytest tests/http --deselect tests/http/test_contracts.py::test_only_s02_tables_exist -q
uv run pytest tests/worker tests/models tests/mcp tests/provider tests/recovery tests/probability_review -q
```

Content approvals remain as recorded in S39.b; bundle release and
activation retain their independent status.

## Completed checkpoint follow-up — 2026-10-16

The canonical [S60 completed report](s60.md) consolidates the later isolated
dev-test regression, final 10-test confirmation, nonblocking final reviews,
remaining limitations, and S61 handoff. Earlier pending-rerun and commit
instructions above are preserved as checkpoint history; consult the canonical
report for the current commit handoff. No additional tests were run by this
documentation follow-up.
