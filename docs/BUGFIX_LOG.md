# Bug-fix log

Baseline: `dev` at `c012ad4`; integration branch `bugfix/journey-2026-10`.
B01 and B26 are tracked separately.

| Bug | Status | Commit | Tests added | Remaining |
| --- | --- | --- | --- | --- |
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
