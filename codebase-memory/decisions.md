# Durable decisions

Rationale that is hard to recover from code. Full entries with Ids, evidence and status live in
[`context/`](../context/index.md) — read that index for the authoritative records. This file
captures what a future task needs before it re-derives the same conclusions.

## Product-level constraints (owner-confirmed, documented in `context/architecture.md`)

- **Empty evidence set.** Patient variables are given to the LLM only to estimate CPTs; they are
  never submitted as observed/soft/likelihood evidence. Inference runs with `evidence={}`
  (`models/inference.py`). Reason: results stay reproducible from stored CPTs and pinned config.
  Anything conditioning inference on patient values contradicts this rule.
- **One open draft per patient, author-only.** Enforced by the partial unique index
  `one_open_draft_per_patient` plus a patient-row `FOR UPDATE` during creation; another physician
  gets a generic 409 with no content. Draft restrictions apply transitively to every derived
  artifact (runs, CPTs, results, downloads).
- **No manual-plan signing bypass.** Signing requires successful *current* executions and
  acceptance of their current results; a failed initial generation cannot be bypassed.
- **Addenda, not edits.** Any active physician may append an attributed addendum to any signed
  encounter; the snapshot itself never changes and the new author does not replace the signer.
- **Exact integer percentages.** 100 % = 100 000 000 units, ≤6 decimals, every row totals
  exactly. Redistribution uses floor + largest-remainder with the pinned network's state order for
  tie-breaking. Floating-point percentages were rejected (drift, inexact totals).

## Reasoning pipeline (`context/mcp.md`, `docs/proposal-generation.md`)

- **Attempt timing is claim-to-completion, not provider HTTP latency.** The previous worker reused the claim clock at completion, causing zero spans; B21 changed normal completion to resolve UTC after work while preserving explicit deterministic test clocks. See [B21 evidence](../docs/BUGFIX_LOG.md#b21-attempt-timing-2026-10-31) and [issue 17](../issues/issue-17.md). Deployment and current test results require separate verification.
- **Audit and Status answer different questions.** B20 added transactional per-attempt audit events and newest-first listing without backfilling historical rows; Status alerts only on specific operational conditions, not every failed run. See [issue 18](../issues/issue-18.md); verify source and deployment before relying on either.

- **One private stdio MCP tool, one scoped grant.** `get_question_patient_inputs` returns only the
  current question's saved projection; a worker-owned subprocess per read, no public MCP port and
  no in-process production shortcut. Reason: isolation between concurrent provider slots.
- **Questions are sequential** in the bundle's declared order; a failed/clarifying question pauses
  later ones. Parallelizing inside a run was rejected.
- **Bounded retries: three attempts total**, shared across transport and validation failures.
- **Invalid CPT output is rejected, never repaired** (no clamping, renormalizing, default-filling).
  Every CPT, including roots, must be estimated — registered tables are never a fallback.

## DDI (`context/ddi.md`, `docs/ddinter-operations.md`)

- No LLM in DDI checking — deterministic lookup against an immutable, content-addressed release.
- **Unavailable coverage is reported as unavailable, never as "no interaction."** Only a
  `released_complete` release can claim `covered_no_listed_interaction`.
- Publication requires an owner-reviewed manifest (`approved_complete` / `approved_limited`);
  the bundled template stays `awaiting_review` and cannot authorize anything. Releases are
  content-addressed (`ddi-<12 hex>`) and immutable, so identical content reuses the existing release.
- Normal startup bootstraps the bundled approved release from committed sources by re-deriving the
  content hash locally, so no generated artifact and no Git LFS are needed.

## Frontend

- **No router library** — web deps are only `react`/`react-dom`; `web/src/app/router.ts` is a
  hand-rolled hash router and wizard steps live in a second `#` fragment.
  *(Evidence: inferred from source; no decision record found in `context/`.)*
- **CSRF token in `localStorage`** (`xi_csrf_token`) rather than session-scoped storage, because the
  HttpOnly session cookie is shared across tabs. This is deliberate, not sloppiness. The token is
  re-read on every mutation so another tab's rotation is observed.
- **Teal `#0A9E8F` on white is only ~3.33:1**, insufficient for small text; the darker
  `--primary-action` tokens (`#06786D` light, `#2DD4BF` dark) are the accessible action fill
  (`context/ui-contrast.md`). Do not "restore" the canonical teal on buttons.
- **Continue does not require a completed step.** Only registration Diagnosis→PANSS asks for
  confirmation, and it records no bypass sentinel. This is asserted by `e2e/panss.spec.ts`.
- **Transparency loading is bounded, and "failed" is read from the joined run, never `review.job`.**
  Issue 19 wrapped the persisted-review GET in a native `AbortController` with a 1500 ms
  deadline covering the body read (so an unsettled request can never leave "Loading persisted
  transparency…" up), and renders `No transparency data because this run failed` only when the
  joined per-question display is `failed`. The review response's `job` field is the batch's
  *first* job and may belong to another question, so inferring per-question failure from it is
  wrong. The deadline deliberately trades a slow-but-healthy request for a bounded error plus a
  one-click retry; it was chosen under the 2 s acceptance, not from measured endpoint latency —
  revisit only with that evidence (during Firefox runs the deadline itself fired once because the
  mocked delivery exceeded it; the UI behaved correctly). Full record:
  [`issues/issue-19.md`](../issues/issue-19.md).
  Status: current. Evidence: source plus that issue; the tester's incident itself is unverified.

## Identity and research warning

- **The research warning is one nullable acknowledged version per account, for both roles.**
  `users.research_warning_acknowledged_version` (migration 0029) holds only the last version
  the account acknowledged; NULL or a different value means pending. Login and `GET /me` return
  `research_warning` plus `research_warning_version` from the stored row, never from the login
  role. Reason: issue 24 asked for one server-supplied text/version contract; no history,
  timestamp, or table was requested. Acknowledgement (`POST /me/research-warning-acknowledgement`)
  writes only that column: no credential or profile revision bump and no audit row. A stale
  submitted version is 409 `RESEARCH_WARNING_CHANGED` with the current pending text, so text the
  user did not see is never recorded. Bump `RESEARCH_WARNING_VERSION` in `identity/service.py`
  whenever `RESEARCH_WARNING` wording changes. Restore: 0028 archives restore every account
  unacknowledged; 0029 archives keep the column. Status: current. Evidence: `issues/issue-24.md`
  plan and source; the backend tests in `tests/http/test_identity.py` (slice 5) and
  `tests/recovery/test_restore_validation.py`.

## Testing and fixtures

- **Child-first TRUNCATE order in test fixtures** (`TRUNCATE notes, encounters, patients`).
  Migration 0006 added `notes→encounters` FK and the fixture helpers swallow failures, so a wrong
  order silently poisons unrelated suites with 409s. Any new child table with an FK must be added
  to those lists (`context/test-fixtures.md`).
- **Timing bounds are measured in-page, never across Playwright round-trips.** For Issue 19's
  two-second transparency bound, `e2e/failure-recovery.spec.ts` records the click and the first
  appearance of the failure text with the page's own `performance.now()` (capture-phase listener
  plus mutation/frame watcher) and asserts their difference. The earlier form used the runner's
  `Date.now()`, which charged harness latency to the UI and reported 2931 ms while the same text
  already satisfied its own 2000 ms assertion. Status: current. Evidence: measured values and the
  documented runs in `project-documents/dev/progress/s48.md` (2026-11-02).
- **Firefox e2e needs per-test runs or a larger timeout on this host.** With ~955 MB RAM and an
  active OOM killer, Firefox tests sit near Playwright's 30 s default test timeout and fail only
  by exceeding it; run `-g "<title>"` individually or raise `--timeout`. Status: current,
  environment-specific.
- `make test-web` intentionally exits non-zero: there is no web unit suite; browser coverage is
  `e2e/` via `make test-e2e` (Chromium + Firefox).

## Project status facts that get mistaken

- **Research prototype, release gate open.** Not clinically validated, not highly available; no
  live paid-provider request, full-repository suite, or clinical validation is claimed anywhere.
- **The working note is not page notes.** Page notes were removed (2026-10-20); only
  `draft_data.working_note` survives, and it never enters prompts, projections, applicability,
  inference, or DDI. Page-note routes no longer exist.
- **Docs lag the code in places.** Example: README and design docs still say new signed snapshots
  use `signed-encounter-v2`, while `cases/signing.py` sets `signed-encounter-v3`; the progress
  index says schema head `0022` while `db.EXPECTED_SCHEMA_VERSION` is `0024`. Treat source as
  authoritative and update the doc rather than the code.
- **Progress reporting is mandatory per `AGENTS.md`**: record work in the relevant
  `project-documents/dev/progress/sNN.md` and keep `README.md` there short.
