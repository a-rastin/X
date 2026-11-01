# Bug-fix log

Baseline: `dev` at `c012ad4`; integration branch `bugfix/journey-2026-10`.
B01, B02 and B26 are tracked separately.

| Bug | Status | Commit | Tests added | Remaining |
| --- | --- | --- | --- | --- |
| B06 | Blocked by B05 prerequisite; not reproduced | — | None | Complete B05 and replace the local capability override before reproducing and fixing B06 |
| B20 | Implemented; focused checks pass; uncommitted | — | Three failures, newest-first legacy pagination, success, stale claim, heartbeat/reclaim race, rollback, Chromium UI | Full backend suite, live-data browser ordering, Firefox, coordinator stale and re-lease race tests remain open |
| B21 | Implemented; 3 timing regressions passed; commit authorized with open gates | this commit | Five-second success/failure UTC spans and coordinator failure timing | Existing format and missing web-unit gates; full backend and bounded HTTP success not verified this session |
| B03 | Blocked by B05 prerequisite; provider not tested | — | None | Complete and merge B05, supply provider key through environment, then run the real walkthrough and record each attempt |
| B05 | Implemented; focused checks pass; uncommitted | — | Pinned and new worker jobs, override, detection persistence, legacy/current restore, browser checks, 0026→0027 and 0027→0028 upgrades | Full backend and browser suites, final review, and commit remain open; permission for the migration test covered only one exact command |
| B02 | Acceptance passed; commit held because repository gates remain red | uncommitted | 23 regressions: real HTTP failure taxonomy; worker ledger/log/privacy; persisted retry budget; 0025→0026 preservation | 25 assertion failures reproduce on unchanged dev; full-run client-slot exhaustion required a workflow rerun; existing formatting and missing web-unit gate remain red; real streaming unverified |
| B26 | Implemented; focused acceptance passed; committed at user request with gates incomplete | this commit | Response selection, fenced/prose JSON, strict malformed/empty rejection, and private diagnostics | Full backend suite not rerun: host disk is 99% full; existing formatting and frontend test gates remain red |
| B01 | Implemented; acceptance passed; committed at user request with gates red | this commit | Timeout default/env/range/settings/history/restore; real 141 s HTTP and worker generation; lease reclamation; Status/settings browser checks | Repository gates remain red; baseline/host failures and unresolved broad-run failures are recorded below |

## B01 reproduction and baseline (2026-10-30)

- Starting worktree was clean. The reported uncommitted `PROVIDER_TIMEOUT_SECONDS = 300` override was absent: committed and working versions both used 60. Local `.env` capability was `schema`, not the reported `json`; no environment file was changed or staged. Capability persistence belongs to B05.
- Actual root cause: `provider.py` used the same 60-second constant as both the default HTTP deadline and the validation ceiling. The worker also did not call the existing heartbeat function while generation was blocked on HTTP, leaving its lease and MCP grant at 120 seconds.
- Before implementation, `tests/provider/test_timeout.py`: **15 failed, 2 passed** (300 default, environment loading, and expanded range unsupported).
- Reproduction and baseline use PostgreSQL 16 in an isolated `/tmp` cluster, Python 3.12 with existing repository packages, and Node 22.23.4. The checked-in virtual environment has no interpreter and `uv` is unavailable; tests use the installed Python 3.12 interpreter plus the existing package directory. Existing application data is untouched.
- Pre-existing formatting failure: `backend/tests/provider/tls_fixture.py` needs a blank line. Recorded here; unrelated file left unchanged.
- Backend suite, frontend suite, lint/types/build and wall-clock acceptance results will be recorded below after completion.

## B01 implementation and focused verification

- Timeout precedence is saved revision → `X_INSIGHT_PROVIDER_TIMEOUT_SECONDS` → 300 seconds. Finite values from 10 through 900 are accepted. The generation adapter and capability probe use the same resolution; the old hard-coded provider timeout is removed.
- Provider settings expose an optional timeout. Null uses deployment/default resolution. The append-only migration is `0025`; existing rows retain null. Capability checks copy the timeout into their new revision. The job's pinned revision supplies generation's timeout. Status shows the latest effective setting.
- Generation renews its existing lease and MCP grant on the existing 15-second heartbeat interval. Three attempts retain the existing queue backoff policy; no attempt-wide, Uvicorn, edge-proxy, or proposal-polling deadline cuts short the asynchronous worker. The inference subprocess has its independent 30-second budget after estimation. Proposal polling has no overall time limit and continues through queued/leased attempts.
- Real wall-clock HTTP acceptance: **2 passed in 217.36 s**. The fake endpoint waits 141 seconds; default succeeds with admitted CPTs, explicit 60 seconds returns retryable `PROVIDER_TIMEOUT`. This uses real MCP and the actual outbound HTTP transport, without provider credentials.
- Full delayed-worker acceptance: **3 passed in 296.91 s** on Python 3.12.14: 141-second/default generation persists a baseline, 141-second/60-second configuration times out, and the compressed-lease/reclaimer case succeeds. No inference/runtime gate was bypassed.
- Timeout/env/range/settings/history/restore regressions: **28 passed**. The compressed-lease worker regression passed with Python 3.12.14, real MCP, HTTP, inference and a persisted baseline while another connection repeatedly reclaimed expired leases.
- Migration test: upgraded a dedicated database to 0024, inserted an existing keyless provider row, upgraded to 0025, checked the row and null timeout were preserved, accepted 900 and rejected 901 at the database constraint.
- Real UI: Chromium Status check passed; saved-timeout save/reload/clear/Status journey passed in Chromium and Firefox. Firefox ran as the existing local browser user with its existing browser installation.
- Backend mypy (78 modules), frontend build, root TypeScript, Ruff lint and deployment packaging check pass. Repository-wide formatting still has the pre-existing `tls_fixture.py` failure; B01 files are formatted.
- The first broad baseline was invalidated by host constraints: a disk-full WAL write restarted the disposable PostgreSQL cluster; the host Python 3.12.3 also fails the application's existing exact-runtime inference gate. That interrupted run is not a valid product baseline. Python 3.12.14 was installed under `/tmp`, and an unchanged `c012ad4` source snapshot was used for comparisons with bounded WAL storage. No existing application database was altered.
- Scope: B05 capability persistence, B02 failure metadata and B21 attempt timestamps are deferred to their own bugs. Keep the Why: this is a correction with its rationale recorded here; no separate context entry is needed.

## Broad-run diagnostics and unrelated findings

- The initial parallel broad runs were stopped to reduce host load. Provider subset at interruption: **42 passed**. The browser run stopped with **20 passed, 3 failed, 1 interrupted, 210 not run** in Chromium. The failures include missing `#draft-save-status`, missing `#meds-catalog-info`, and a clinical-workflow timeout. B01 does not change those screens/selectors. The complete browser suite has not passed; focused B01 Chromium/Firefox checks have passed.
- The first unchanged-source snapshot also omitted the shared `e2e/provider_tls_fixture.py`, causing four setup failures. The missing fixture link was corrected. Its interrupted broad result (**234 passed, 5 failed**) is not a clean baseline and must not be presented as one.
- A separate comparison against unchanged `c012ad4` with the correct runtime confirms **3 pre-existing failures**: `test_only_s02_tables_exist`, `test_concurrent_creates_allow_one_draft_only`, and `test_discard_requires_confirmation_and_current_revision`. The first expects an obsolete table inventory; the latter two expect empty history values while the current registration initialization supplies `unknown` values. These are recorded, not fixed as part of B01.
- The complete serial backend run finished: **1,148 passed, 39 failed, 4 skipped**, 2,917.61 seconds. This is the observed full-run result, not a green gate. Subsequent targeted results do not replace that full-run count.
- The new disconnect reproduction initially expected `PROVIDER_TRANSIENT`. Isolated execution showed that this transport returns a truncated body and the existing JSON parser classifies it as retryable `PROVIDER_INVALID_RESPONSE`. The observational test was corrected to that actual behavior; no response-parsing or failure-class code was changed under B01.

## Reproduce B01 acceptance

With the documented toolchain and a disposable `TEST_DATABASE_URL` configured:

```sh
cd backend
B01_SLOW_TESTS=1 uv run pytest tests/provider/test_timeout_http.py tests/worker/test_provider_timeout.py
uv run pytest tests/provider/test_timeout.py tests/provider/test_timeout_settings.py
```

The slow tests deliberately wait real wall-clock time. The fake service also
supports mid-response disconnects, selected HTTP status codes, and JSON in
`reasoning_content`; it records no authorization headers. No provider API key is
needed. For UI acceptance, run `e2e/provider-timeout.spec.ts` and `e2e/status.spec.ts`
against the isolated running API/Vite app.

## Final verification and handoff

- **Final focused rerun: 37 passed, 4 skipped** in 137.56 seconds. Covers all new non-slow B01 tests, saved-revision timeout selection, retry-budget recovery, two-physician isolation, stage resume, overlapping generation/restart, and an isolated workflow. The four opt-in slow cases were already executed successfully in the HTTP and worker wall-clock runs above.
- Existing tests with 0.5- or 1-second provider deadlines were updated to the new 10-second minimum, using 12-second delayed responses so they still exercise real timeouts. The three retry/concurrency cases affected by the 1-second deadline now pass. No production retry policy was changed.
- The standalone Python installation initially needed `PYTHONPATH` to locate the existing locked packages. A temporary `.pth` under the `/tmp` Python installation now supplies those packages even to test subprocesses that replace `PYTHONPATH`. The restart integration now passes. No runtime-version check was bypassed and no dependency manifest or lockfile changed.
- Additional unchanged-code comparison: **2 passed, 1 failed**. The isolated workflow passes on original code; the saved-provider DNS case reproduces the same `KeyError: error_code` on original code. That missing failure metadata belongs to B02. An isolated workflow also passes on the fixed branch; the other workflow failures from the full run were not exhaustively rerun and remain unresolved, not waived.
- Other full-run failures include history null/empty-value and revision assertions, archive/deactivation expectations, and the status test's assumption of no disk alert on a host above 80% disk usage. These are outside the B01 change. Only the explicitly compared cases above are claimed as confirmed pre-existing; no blanket no-regression claim is made.
- Final web build, root TypeScript, backend mypy (78 source modules), Ruff lint, deployment packaging and whitespace checks pass. Repository-wide Ruff formatting still fails solely on unchanged `backend/tests/provider/tls_fixture.py`. Focused Chromium/Firefox acceptance passed; the complete browser suite did not pass and was not completed.
- **Committed at the user’s subsequent request.** The original all-green pre-commit gate is not met. No other bug was started, no real-provider generation success is claimed, and `.env` was neither changed nor staged. Phase 0's complete green baseline/walkthrough gate is not claimed.

Commit subject:
`fix(B01): configure provider timeouts and renew generation leases`

Root cause for its body: one 60-second constant was both the HTTP default and
validation ceiling, rejecting a provider response that takes 141 seconds; the
worker also left its 120-second lease/MCP grant unrenewed during estimation.
The fix resolves a bounded timeout from the pinned settings revision or
operator environment, defaults to 300 seconds, and renews the existing lease.

## B26 provider response parsing (2026-10-31)

- The user authorized B26 to proceed despite the incomplete Phase 0 gate. The B26 branch was created from `dev` at `81ca561`, which contains B01.
- Root cause: response selection allowed a non-string `content` to override `reasoning_content`, and the shared validator required the entire string to be JSON; Python's default decoder also accepts non-standard numeric constants.
- Before the fix, the new focused cases produced **5 failed, 7 passed**. Afterward, the response cases and timeout regressions produced **32 passed**. The parser selects nonempty string content first, extracts the last balanced object with strict JSON decoding, and keeps CPT validation. Failures use the existing retryable `PROVIDER_INVALID_RESPONSE`; logs contain length and shape only.
- Backend Ruff lint, mypy, web build and web TypeScript passed. Repository-wide format check still fails on the unchanged `tests/provider/tls_fixture.py`; `make test-web` reports that no web unit suite exists and exits nonzero. Full backend and browser suites were not rerun: only 281 MB remained on the filesystem, and the previous full-run handoff records a disk-full database failure. The disposable database was not started.
- Committed on `dev` at the user’s subsequent request despite incomplete pre-commit gates. This work does not establish real-provider success. The branch-consolidation progress edits already present in the working tree were preserved.


## B02 failure classification and HTTP status (2026-10-31)

- Scope: only B02, on `fix/B02-provider-failure-details` from `dev` at `00a47b2`
  (B01 and B26 already integrated). Existing uncommitted progress edits were preserved.
  The direct B02 request resumes work with the prerequisite fixes present; it does not
  waive the all-green commit gate. No B05, B21 or B20 work was started.
- Reproduced before production edits: five new regressions failed. A forced 503 lost
  its status, a pre-header disconnect lost its exception identity, and a mid-body
  disconnect was incorrectly treated as malformed JSON.
- Confirmed root cause: `_post_once` reduced transport exceptions and HTTP responses
  to `ProviderResult(error_code, retryable)`, whose contract had no diagnostic fields.
  The coordinator discarded unexpected exception types, and fenced attempt completion
  had nowhere to store these details or emit structured diagnostics. The bounded
  `HTTPResponse.read(amt)` also silently returned short Content-Length bodies.
- The result contract now carries failure class (`timeout`, `disconnect`,
  `http_status`, `parse`, `other`), received HTTP status, exception type name, sub-code
  and elapsed seconds. Existing top-level codes remain; a truncated HTTP body now
  follows the existing `PROVIDER_TRANSIENT` transport path rather than parse failure.
  Complete invalid responses retain `PROVIDER_INVALID_RESPONSE`. Exception messages,
  bodies, headers and provider credentials are never copied into diagnostics.
- Append-only migration **0026**, because B01 already owns 0025, adds nullable fields
  and finite elapsed/status/class constraints. Schema readiness and backup/restore
  compatibility advance together. The isolated database test downgrades only the new
  migration around a seeded attempt, then tests upgrade from 0025 to 0026: original
  columns are preserved and new diagnostics are null for the historical row.
- Failed provider results reach the fenced ledger update and one structured
  `provider_attempt_failed` log with matching fields and opaque job/attempt IDs.
  Real-worker pre-header disconnect, mid-body disconnect and 503 checks compare the
  actual ledger and log, reject prompt text/fictional identifier/header/key markers,
  and verify that no baseline was created. Monotonic elapsed time covers estimation;
  other completion paths use elapsed wall time from the claim. B21's existing
  start/finish timestamps are intentionally not changed.
- Retry settings are documented in `docs/installation.md`, exposed in `.env.example`
  and forwarded to both production services. Defaults stay at three total attempts,
  2-second exponential base plus 0–1-second jitter, and a 60-second cap. Bounds are
  1–10 attempts and finite 0 ≤ base ≤ cap ≤ 900 seconds. One attempt disables
  automatic retries. New jobs persist their budget; a test changes the environment
  after enqueue and confirms the original two-attempt budget, two 503 ledger rows,
  exactly two HTTP calls, and one log per attempt. No nested retry was added.
- Focused provider/worker run: **111 passed, 4 skipped in 177.98 s**. This includes
  the new worker, retry-budget and migration checks; skips are B01's separately
  recorded slow wall-clock cases. Three additional 500/502/429 cases were added
  afterward and are included in the subsequent full-suite run.
- Backend lint and mypy (78 modules), frontend build and root TypeScript pass.
  Formatting still fails only on unchanged `backend/tests/provider/tls_fixture.py`.
  `make test-web` still exits nonzero because no unit suite exists. Chromium smoke
  passed; Firefox also passed as the local browser user using its existing cached
  installation after the initial root/HOME ownership launch failure.

### B02 streaming spike

Command: `cd backend && python -m tests.support.streaming_spike`.
The small synthetic endpoint delays a buffered response for 400 ms or sends SSE
heartbeats every 40 ms before completion. Under a 100 ms socket idle deadline,
buffered mode timed out at **0.100 s**, while streaming completed at **0.406 s**.
This demonstrates only that regular response bytes avoid a simulated idle timeout;
it does not reproduce the remote gateway or demonstrate that the model sends early
chunks. The production transport has an absolute request deadline, which streaming
must not extend. No provider credential was found in the local environment file. A read-only check
for a saved admin credential failed with `OperationalError`; availability could
not be confirmed. No credential value was read and no real-provider call was made.
Production streaming was deferred because this experiment does not establish a
benefit for the reported remote disconnect. The spike took minutes, within the
half-day limit; no dependency was added.

Proposed commit subject (only after the commit gate passes):
`fix(B02): retain provider failure details on attempts`

Commit body root cause: the adapter collapsed transport exceptions and HTTP status
into an error-code-only result; the coordinator and attempt schema consequently
lost failure identity. Bounded reads also hid truncated HTTP bodies as parse errors.

### B02 final regression and handoff

- Complete backend command: `python -m pytest tests -q --tb=short` on Python
  3.12.14 and the isolated PostgreSQL 16 database: **1,190 passed, 24 failed,
  4 skipped, 11 setup errors in 2767.47 s (46:07)**. The four skips are the
  opt-in slow B01 wall-clock cases; their earlier B01 evidence remains above.
- The 11 setup errors all came from PostgreSQL's 100-client limit at the last
  workflow module. Session-scoped fixture pools accumulated during the full run.
  A fresh-process `python -m pytest tests/worker/test_workflows.py -q --tb=short`
  ran all 11 cases: **10 passed, 1 failed in 163.45 s**. The remaining assertion
  is `test_required_unknown_stops_progression` (proposal is null). This is a
  targeted follow-up, not a claim that the single-process full gate passed.
- The new B02 test module now disposes its imported fixture's idle pool at module
  teardown, instead of adding retained connections until session exit. The broader
  existing test-pool accumulation remains outside B02. No production connection
  settings were changed, and no second 46-minute full run is claimed after this
  test-only cleanup.
- Final focused command: `python -m pytest tests/provider/test_failure_details.py
  tests/provider/test_retry_settings.py tests/worker/test_failure_details.py -q
  --tb=short`: **23 passed in 41.73 s**, after that cleanup. Every new B02 test also
  passed in the complete run. The separate broader provider/worker run above was
  111 passed with 4 skips before the three additional HTTP status cases.
- All **25 distinct assertion failures** (24 from the full run plus the one from
  the workflow rerun) were individually selected and reproduced on an untouched
  archive of `dev` at `00a47b2`, with its own PostgreSQL database and schema 0025.
  Comparisons: table inventory/draft cases **3 failed**; history invalid-values
  **1 failed**; empty-history parameters **15 failed**; saved-provider DNS
  **1 failed**; required-unknown workflow **1 failed**; remaining history choice,
  Status disk-alert, archive and deactivation cases **4 failed**. This establishes
  their pre-existing assertion failures; it does not waive their gates or fix them.
- Backend Ruff lint and mypy (78 source modules), frontend build and root
  TypeScript, deployment packaging and whitespace checks pass. Repository Ruff
  formatting still fails on unchanged `tests/provider/tls_fixture.py`.
  `make test-web` exits nonzero because no web unit suite exists. Existing shell
  smoke passed in Chromium and Firefox; the complete browser suite was not rerun.
  B02 has no UI change.
- No source credential or environment file was changed or staged. Only disposable
  fictional-data databases were migrated. The test cluster is stopped after the
  run; no live application database was migrated. Original progress edits remain.
- **Not committed.** The user's mandatory all-green pre-commit gate is unmet.
  The B02 acceptance criterion is verified, while the full definition of done
  remains open on the repository gates above. No next bug was started.


## B05 capability mode reproduction (2026-10-31)

- Branch `fix/B05-provider-capability` started from `dev` at `8229cf4`; the
  worktree was clean. `python3 -m pytest backend/tests/provider/test_capability_mode.py -q`
  fails because `ProviderSettingsWrite` rejects `capability_mode` as an extra field.
- Root cause confirmed: the settings schema and revisions have no capability mode;
  the worker selects it from `X_INSIGHT_PROVIDER_CAPABILITY` and defaults to schema.
  The existing capability probe checks a generic tool/JSON exchange and does not
  detect schema support. Production Compose also defaults the variable to schema,
  which must be considered when making the environment override optional.
- No production fix, migration, UI change, environment edit, or acceptance run has
  been made. The next migration is 0027 (0025 and 0026 already exist). The test
  intentionally remains failing. Existing full-suite and formatting failures are
  recorded under B02; they are not waived.
- Rechecked the reproduction: `python3 -m pytest backend/tests/provider/test_capability_mode.py -q --tb=short` still fails on the forbidden field. The user chose to fix existing gate blockers first. `make test-web` now runs two Node unit tests instead of an unconditional failure; both pass and the web build and root TypeScript check pass. The formatting blocker was corrected; lint also revealed three pre-existing B20-test violations, now corrected. `make check` passes (Ruff formatting and lint, mypy, web build, root TypeScript). A full backend run launched from the repository root produced 581 passed, 1 failed, 4 skipped and 649 setup errors because Alembic could not locate its relative migrations path; this invocation is invalid as a baseline. A rerun from `backend` reached approximately 75% and showed failures, but was stopped by the system due to critically low memory before a final report. It is not a completed suite. At the user's request, the suite was restarted from `backend` against a fresh disposable database; only 243 MiB of memory was available at launch, with swap available. It completed in 43:49: **1,197 passed, 26 failed, 4 skipped, 11 errors**. One failure was an outdated B20 ascending-order assertion, now corrected; one is the existing B05 reproduction. The remaining assertion failures correspond to the earlier B02 baseline. The 11 workflow setup errors report PostgreSQL client exhaustion. A fresh-process rerun of the corrected audit test plus all workflow tests produced **11 passed, 1 failed**: the remaining workflow failure is the previously recorded `test_required_unknown_stops_progression`, not a setup error. Investigation identified 40 session-scoped database-engine fixtures whose idle pools can accumulate; the exact connection attribution was not measured, and no pool fix has been applied. No B05 implementation or commit has been made.

## B05 implementation in progress (2026-10-31)

- Changes for persisted and pinned capability mode, provider detection, the settings API and UI, migration 0027, and restore compatibility are in the working tree and require review. No commit or acceptance is claimed.
- A new browser test reproduced the missing mode selector before the UI change and passes in Chromium and Firefox afterward, including reload, using stubbed settings responses. It does not verify a real worker or provider. Firefox required a disposable home directory and the existing browser cache. The web build, root TypeScript check and two web unit tests pass.
- The delegated focused database test was blocked by the permission classifier pending user permission. After the user authorized the disposable test, a further attempt was also denied by the classifier; no database test ran. Migration upgrade and restore coverage, worker acceptance and the full backend gate remain open. After backend formatting settled, `make check` passed (Ruff, mypy, web build and root TypeScript), as did `make test-web` and `git diff --check`. Existing changes remain uncommitted.

## B05 focused database checks (2026-10-31)

- Started a new isolated PostgreSQL cluster on port 55447; no existing application database was used. The focused B05 settings, pinned worker configuration and override tests passed: **9 passed**. These tests do not run a complete worker job.
- Upgraded a separate populated database from 0026 to 0027: the existing provider setting was preserved and backfilled to `schema`; the check constraint rejected an invalid mode. Added `tests/provider/test_capability_migration.py` to repeat this upgrade in a unique disposable database; **1 passed**.
- `make check` passed, including Ruff, mypy, the web build and TypeScript; `make test-web` passed (2 tests). A subsequent command including the new migration test was denied by the permission classifier because its teardown drops its uniquely created database. Do not retry that denied action without resolving the permission; no full-suite pass is claimed. Full restore and worker acceptance remain open.
- Review identified issues requiring reproduction before a fix: the sample environment explicitly sets `schema`, so deployments using it can override saved modes; the capability test records generic verification independently of the detected mode, and detection is not persisted; testing can advance a stale settings form's revision without refreshing its values. Confirm each with a regression before changing behavior.
- The isolated provider/settings/worker/backup/restore regression run completed: **48 passed, 5 failed**. Four settings tests expected two provider calls but observed three. The legacy 0023 restore fixture retained `capability_mode` and failed validation; this demonstrates a fixture incompatibility, not a failure of an authentic legacy archive. These failures remain unresolved.

## B05 follow-up after authorized migration test (2026-10-31)

- The user authorized one exact migration/capability test command; it passed **10 tests**. That authorization does not extend to other commands that run the migration test.
- The five previously failing provider and synthetic legacy restore cases passed in a focused rerun (6 parameterized cases total). The restore fixture now omits the field absent from 0023 and checks the restored schema default; the tool-probe fixture accounts for the separate format-detection request.
- A new deployment-example regression first failed because the example set `schema`; the example now leaves the override blank, and installation guidance explains existing deployments must clear their own explicit override. No local `.env` was changed.
- A new two-revision API test reproduced a stale capability test returning 200. The UI now sends the loaded revision and the API rejects a stale `If-Match`; the B05 capability suite passed **11 tests**. The browser test checks the header and clearly distinguishes generic tool-probe status from transient detection; it passed in Chromium and Firefox with a stubbed API. Detection is still not persisted across reloads, so the last-detected-mode requirement remains open. A complete worker job and full-suite gate remain unverified.
- `make check` passed (Ruff, mypy, web build and TypeScript); `make test-web` passed (2 tests). The broader capability, provider settings, saved-worker, backup and restore regression rerun finished with **63 passed**; it ran while some later changes were in progress and does not replace a final full-suite run.

## B05 persisted detection and worker acceptance (2026-10-31)

- The provider test's detected mode disappeared from GET responses and historical revisions; a new API test reproduced the missing field before the fix. Append-only migration **0028** adds a nullable constrained detection field to provider settings. The result is stored on the tested revision; a later save is untested and clears detection. The UI labels generic tool verification separately, shows mismatches and retains the last detected mode after reload.
- On an isolated populated 0027 database, upgrading to 0028 preserved the saved mode, backfilled detection as null and rejected an invalid value. Separate restore-commit tests passed for a synthetic 0027 archive defaulting detection to unknown and for a current archive preserving a detected mode (2 passed). B05 capability tests passed (12 tests; further historical checks were subsequently added).
- A complete fake-provider worker regression passed: a queued job retained its pinned schema mode after a JSON save; the next job sent JSON mode; a third honored the explicit schema override. Chromium and Firefox stubbed browser checks passed. `make check`, two web unit tests, Ruff and `git diff --check` passed.
- Two accidentally overlapping broad test runs against the same isolated database were stopped; their results are invalid. A single clean rerun on a new isolated database passed **66 tests in 264.92 seconds** across capability, API settings, saved-provider worker, backup and restore validation. This is not the full backend suite. No full-suite result or commit is claimed. The user granted database-drop permission for one exact earlier command only; a full suite includes other disposable database drops and is not yet authorized.

## Test-gate follow-up (2026-10-31)

- A disposable-database cross-module probe reproduced one idle connection retained by a session-scoped migrated-engine fixture. The test fixtures now dispose their pools at module teardown; the new cross-module regression failed with the original session scopes and passed again with module scope, and imported-fixture checks passed. A full backend run completed with **1,209 passed, 26 failed, 4 skipped and no setup errors**. The connection-exhaustion setup errors did not recur; their exact prior attribution remains unmeasured.
- After that run, the migrated-schema inventory test was updated to cover all 34 current public tables (39 contract tests passed). Fifteen history empty-input cases were corrected to use the revision returned by their first successful write (15 passed). Neither correction changes production or clinical behavior. The fresh-draft/race tests were also aligned with the independently specified nine unknown defaults (including no inherited provenance), and invalid-value tests no longer reject `null`, which separate missing-input tests cover; all six selected tests passed. The workflow missing-context test now publishes the DDI release its proposal assertion requires (1 passed); the healthy Status test now controls disk usage while retaining the separate alert test (8 status tests passed). The saved-provider denial test checks the persisted attempt error, which both the coordinator and stub paths write, rather than assuming both worker return shapes have `error_code`; DNS mocking ends before the separate unreadable-key case (2 passed). The full backend baseline excluding the intentionally failing B05 reproduction passed: **1,234 passed, 4 skipped in 45:19**. This is not a full-suite green gate: B05's test remains excluded until its implementation. These changes are uncommitted.

## B21 attempt timing (2026-10-31)

- Root cause: `worker.run_once` captured the claim timestamp and passed it as an
  explicit completion clock to both the simple provider path and coordinator.
  Queue completion and coordinator success consequently wrote the claim time
  into `finished_at`, even after slow estimation.
- Before production edits, two actual five-second fake adapter calls (success
  and failure) both failed the regression with a **0.0-second** ledger span.
  Reproduction: from `backend`, set `TEST_DATABASE_URL` to an isolated UTF-8
  PostgreSQL database and run `python3 -m pytest tests/worker/test_attempt_timing.py -q`.
- Production now leaves the completion clock unset until work finishes. Explicit
  deterministic test clocks remain fixed. Queue completion and coordinator success
  compute elapsed seconds from the persisted UTC start and the same finish value.
  No migration, clinical content, credentials or environment file changed.
- B01 is already integrated; the reported hard-coded timeout edit is absent.
  B05 remains reproduced but unimplemented in the current handoff; its persisted
  capability setting and local override replacement remain open, outside B21.
- Final timing regression: **3 passed in 24.19 s**: simple provider success and
  failure plus coordinator failure, each sleeping five real seconds and requiring
  a 4–6-second UTC span with elapsed seconds matching that span.
- Ruff lint, mypy (78 modules), frontend build and root TypeScript passed.
  Repository formatting still fails on unchanged `tests/provider/tls_fixture.py`;
  `make test-web` exits nonzero because no web unit suite exists. The commit was
  subsequently authorized with these gates open.
  Full backend suite was not rerun; prior B02 baseline failures remain open.
  The broader queue run was stopped after stalling; it is not counted as passing.
  Local system Python lacks MCP, so bounded HTTP/inference success was not tested;
  the timing tests use an explicit fake estimation adapter, including coordinator
  failure routing, without claiming real-provider success.

Commit subject: `fix(B21): record actual attempt completion time`.
Commit body root cause: the worker passed its captured claim time as the completion
clock, causing both attempt timestamps to be equal despite provider latency.

## B20 audit fix (2026-10-31)

- Generation attempt completion now records a success or failure audit event in the same transaction as the guarded ledger transition, including ordinary completion, stale completion and lease reclaim. Existing run events and legacy data are retained. Audit listing sorts timestamp and UUID descending before pagination.
- Independent review found a heartbeat/reclaim race that could create a false expiration event. A deterministic PostgreSQL regression reproduced it; the fix locks expired jobs and rechecks the expiration predicate. The reviewer found no remaining actionable defect in the revised change.
- In a separate disposable PostgreSQL database, the worker queue, HTTP audit and worker failure-detail suites passed (34 tests). A bounded coordinator success test and focused pagination/legacy tests also passed. Chromium acceptance with a real admin login and controlled audit response verified rendering, not live-data ordering; Firefox launched but timed out clicking Audit. Coordinator stale completion and concurrent re-lease remain untested. These focused results do not replace a full suite.
- The reviewed worktree changes were applied to the main worktree. No historical events were backfilled and no clinical content or environment files changed. The full backend rerun completed with 26 failures and 11 setup errors; an old ascending audit-order test failed and was corrected afterward. No commit was made.

## B20 audit reproduction (2026-10-31)

- A real worker and synthetic 503 provider produced three failed ledger attempts but zero `generation.attempt.failed` audit events; the new regression failed as expected. The coordinator intentionally records only the final run failure, while queue completion logs each failure without inserting an audit event.
- A separate HTTP regression with a legacy actor and tied timestamps failed because `list_audit_events` orders `(occurred_at, id)` ascending. It requires newest-first ordering with a deterministic descending ID tie-break across pages and legacy actor fallback.
- Both reproductions used a new disposable PostgreSQL 16 cluster on port 55441 and Python 3.12.14, with no real provider or patient data. No production code, existing audit data, migration, or environment file was changed. Fix, UI acceptance, full gates, and commit remain outstanding. Success, stale-input, and expired-lease paths also need consideration before claiming one event per attempt.
