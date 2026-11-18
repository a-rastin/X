# Bayesian reasoning verification — 2026-11-18

Step 7 adds a [Playwright suite](../../../e2e/reasoning-explorer.spec.ts)
for the New case and follow-up proposal pages. It checks patient marks,
selected-treatment attribution, factor-to-pathway linkage, assessment-only
and combined changes, failure handling, reset during an outstanding
response, and a narrow Persian RTL layout. It also exercises a partial
follow-up branch, a direct outcome and restoration of the original decision.
The suite seeds its own proposals through the application API.

The [user guide](../../../docs/user-guide.md#explore-a-proposal-with-what-if-simulation)
explains the controls and chart references. The
[simulation API reference](../../../docs/reasoning-simulation-api.md) describes
the response lifecycle and local reset.

## Verification

- Python 3.12 and disposable PostgreSQL 16 passed all 166 focused attribution,
  simulation HTTP, candidate, evaluator and proposal regression tests. The
  HTTP suite checked replay identity and domain-table fingerprints before
  and after repeated simulations.
- Node 22 passed all eight web test files, the production build and
  repository TypeScript compilation. Ruff lint and mypy passed. The full
  `make check` passed after formatting
  `backend/src/x_insight/cases/encounters.py` and
  `backend/tests/test_registration_birth_year.py`.
- All four explorer tests passed with the live endpoint in Chromium and
  Firefox. The follow-up fixture seeds a ready draft and generates its
  proposal via the API.
- The existing browser suites exposed stale locators for registration,
  availability feedback and follow-up questions and summaries. The tests now
  target the rendered controls and summary. The focused Chromium rerun
  passed registration and the English oral signing journey.
- The New case in-process HTTP timing probe on an Intel Core i7-3537U took
  2,106 ms after clearing the model cache. Twenty warm requests varied the
  selected treatment and a PANSS answer while applying the QTc override.
  Median latency was 328 ms, p95 was 880 ms, and maximum was 982 ms.
  These measurements include request handling and local PostgreSQL access.
  The warm p95 exceeded the plan's 500 ms target.

## Anti-drift review

The regression run checked the pinned oral model and its 16 mapped inputs,
including exact root-prior Shapley reconstruction and the absence of an oral
ranking change from a PANSS-only edit. Graph checks distinguished model
dependencies, executed rules and candidate filters. The API tests used
synthetic fixtures for both workflow-specific eligibility policies
and checked domain statelessness. Source review confirmed the legacy empty
evidence entry point is unchanged, the display gives the model prior and
patient baseline different roles, and the simulation is scoped to the
displayed proposal rather than the historical follow-up baseline. This
slice changes browser tests, documentation and backend formatting. It adds
no dependency or migration. Existing focused proposal/signing regressions
passed.

## Remaining verification

The wider Playwright run began before the locator corrections. Its final
rerun was prevented when automatic approval review started returning HTTP 404
again. The full backend run reached the audit grant probe with a failure. Its
disposable PostgreSQL cluster uses a development role name, while that probe
requires the production-style migration, runtime and readonly role names.
The backend run continued, with the recovery command queued after it. Git
staging also hit the approval service error.
