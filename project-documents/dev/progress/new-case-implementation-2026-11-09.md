# New case implementation — 2026-11-09

## Follow-up 2026-11-14 — remove Unavailable drugs select all

Removed the select-all control and its English and Persian labels. Selection
and saving of individual formulations remain available. The settings API has
no select-all operation; its existing save contract remains necessary. The
frontend build and 24 web tests passed. The browser journey now asserts that
the button is absent; that journey was not run in this follow-up.

## Follow-up 2026-11-14 — remove Unavailable drugs search

Removed the page's search field and client-side name filtering; formulations remain
grouped and selectable. The settings API already returns the complete catalog and
has no search implementation. The shared medication search elsewhere is unchanged.
The frontend build and 24 web tests passed; the existing browser journey now checks
that the page has no textbox, but was not run against the existing local database.

## Status: implementation complete; committed to main

This implements the missing work identified by the
[plan audit](new-case-plan-audit-2026-11-09.md), against the user-supplied
`new-case-registration-plan-2026-11-09.md`. The baseline was `main` at
`4d1fb8feb782ade19af1f0c1d5d6f8428fd9c80a`; changes are uncommitted.
The request explicitly authorized proceeding despite earlier package-order
blockers. Authorization, input validation, clinical gates, source integrity and
historical signed-record invariants were preserved.

The earlier A/B/F/G/H checkpoints describe their dated scopes; their missing
implementation lists are superseded by the work below, subject to final checks.
No existing application database was migrated and no deployment or clinical
validation was performed.

## Implemented packages and contracts

- **A/C/D:** retained the source assets and validated loaders/importers. Their
  hashes remain unchanged. Added the actual integration consumers; no global
  XML limit or generic inference policy was relaxed.
- **B:** additive migration `0031` after existing `0030`; nullable physician
  names, personal formulation settings, immutable proposals and mutually exclusive
  legacy/new signed references. New physicians and explicit name edits require
  at least one name; historical unnamed accounts remain valid. Profile/settings
  writes serialize on the physician row without changing credential semantics.
  New-case names now use the strict helper on registration/edit, and birth-year
  correction recomputes age from the frozen registration year. Independent age
  overrides remain rejected. Browser New case registration uses server-year
  birth-year selection; Known case remains age-based.
- **E:** retained strict raw state/defaults, persistent attributed overrides,
  diagnosis assertion, conditional validation and deterministic rules. Added
  complete evidence-source provenance and bilingual presentation. Diagnosis's
  default Unknown controls no longer insert answers or invalidate an assertion
  when remounted. A lost assertion response can retry its original idempotency key.
- **F:** `cases/new_case_plans.py` owns canonical input fingerprints, immutable
  proposal generation/reuse/read/freshness, real candidate/current interactions,
  availability snapshots, content definitions/mapping/rule IDs and atomic v4
  signing. New routes remain author-, role-, CSRF- and revision-fenced; legacy
  generation/sign/acknowledgment paths retain their workflow refusal. Two
  concurrent identical confirmations return the same signature without fake
  batches/jobs. Existing secondary-plan text storage is reused.
- **G:** dedicated new-case wizard and harm/history/lab/BMI/medication/consideration
  forms, explicit diagnosis assertion, source explanations, manual/automatic
  controls, list keyboard interaction and input-save navigation barriers. The
  separate plan page retains original proposal and edited final text, supports
  regeneration/copy/replacement, preserves text across 412/session/reload recovery,
  flushes plan and inputs before confirmation, and shows settings/input staleness.
  Personal unavailable-drug settings, physician name fields and display fallback
  are connected. English/Persian and RTL/LTR presentation use the existing catalog.
- **H:** chart/print v4 rendering, source/variant/FK/fingerprint restore checks,
  older-archive defaults and root-first backup/restore ordering across 33 tables.
  Follow-up keeps the legacy form, inherits an explicit medication pin from a v4
  baseline, carries historical assessment data and prior final text, and does
  not invent lifetime History or movement findings. User/API/architecture docs
  are updated. Packaging/startup continues to require no optional provider.

The state API retains its previous `/preview`, `/validate` and
`/diagnosis-assertion` aliases alongside the plan's `/new-case`, `/validate-step`
and `/diagnosis-confirmation` paths. Existing age-only API callers retain their
legacy request contract; the browser selects the new variant for New case.
See [the API contract](../../../docs/new-case-state.md).

## Verified evidence

Only disposable PostgreSQL 16 databases in `x-new-case-implementation-db`
(bound to 127.0.0.1:55439) were used. API/browser runs used their own `x_ui`
database; the mixed-data drill used a separate `x_mixed` clone. Backend tests
used separate `x_test`, `x_repro` and `x_verify` databases. No provider credential
or real provider request was needed.

- New proposal journeys initially passed **4 tests**, then expanded integration
  passed **30 tests**. Subsequent tests add name/correction, concurrent
  confirmation/follow-up and signed backup/reference validation coverage.
- All **8** New case oral/LAI × English/Persian × Chromium/Firefox journeys passed:
  registration, blocked ordinary diagnosis, explicit assertion, empty PANSS,
  keyboard C-SSRS disclosure, BMI, unmatched medication refusal, generation,
  final editing/save/reload/sign and chart rendering. Axe ran on representative
  forms without disabling color-contrast checks. A subsequent settings/freshness
  browser test passed in both browsers, including all-candidate exclusion and
  preservation of edited text. A lost-response assertion replay test also passed
  in both browsers: no answers were refilled and the revision advanced once.
- `make check` passed (ruff format/check, mypy, frontend build and TypeScript).
  `make test-web` passed **21** tests, including strict new-case client names and
  birth-year boundaries. Vite retains its large-chunk warning.
- Full `tests/recovery/`: **57 passed in 263.41 s**, including restore commit and
  historical archives. Fresh schema assertions and independent backup inventory
  were updated to `0031`/33 tables; supported historical versions remain present.
- Normal setup installed reviewed DDInter plus Lexicomp and the fixed oral model
  on a fresh database without a provider. All **9** bootstrap tests passed after their source bundle and release-count
  expectations were updated for both profiles.
- A real full backup with both DDI releases, the oral model, an immutable proposal
  and a v4 signature was staged and committed through public recovery HTTP.
  Archive **43,808,625 bytes**; `database.json` **403,836,637 bytes**; total expanded
  **419,968,396 bytes**. The existing bounds were sufficient. Restored signed JSON
  and hash matched exactly; both releases remained. The temporary drill script
  and output are `/tmp/x-new-case-mixed-recovery.py` and
  `/tmp/x-new-case-mixed-recovery.log`.

## Verification findings repaired during integration

- A bootstrap fixture's non-cascading DDI TRUNCATE failed with the new proposal
  FK. Isolated teardown now includes dependent tables through CASCADE.
- Account-creation fixtures/load probes now supply a name rather than weakening
  the production requirement. Name fields were added to safe response assertions.
- Recovery fixtures now clear independent provider settings; otherwise earlier
  load/settings tests contaminated fabricated historical archives with later
  endpoint approvals. The production restore approval boundary was preserved.
- Browser testing found accessible-name mismatches on theme/language controls
  and a startup navigation race that could erase a diagnosis error. These were
  corrected; assertion/default initialization remains separate from clinician input.
- One combined run aborted in native Python during repeated dataset installation.
  Isolated reruns passed; the full gate is checking for recurrence.
- One combined backup test reported unreadable JSON while concurrent large recovery
  work ran. The saved archive reparsed independently and the isolated test passed.
  Diagnostics now report the exception class instead of misclassifying every
  failure as invalid JSON; the full gate must establish the final outcome.

## Final gate and handoff (completion pass)

An earlier full backend run reached 29% with exactly four failures, all in
`tests/http/test_exports.py`. Those were fixed and verified green in isolation.
A fresh full `make test-backend` (1404 tests) was then started on a new
disposable `x_gate` database; it was still running, at 20% with no failures,
when this work was committed. No missing, interrupted or deselected test is
counted as passed. Raw logs are under `/tmp/x-final-*.log` and
`/tmp/x-new-case-*.log`.

Verified evidence at commit time:

- `make check`: passed (ruff format/check, mypy, web build, `tsc --noEmit`).
  Vite keeps its large-chunk warning.
- `make test-web`: 22 passed.
- `e2e/new-case.spec.ts` in Chromium and Firefox: **12 passed** — the 8
  oral/LAI × English/Persian journeys, personal formulation settings/freshness,
  and the lost-response diagnosis-assertion replay. Axe runs on representative
  forms without disabling color-contrast checks.
- Preceding integration checks: 57 recovery tests, 9 bootstrap tests,
  39 contracts tests and the focused 321-test backend selector passed; the
  mixed DDInter/Lexicomp/model/proposal/v4 backup restored with its signed
  JSON and hash unchanged.

### Fixes made in the completion pass

- The accessible C-SSRS magnifier tooltip repeated the disclosure's exact
  guidance text, so an acceptance `getByText` locator matched two elements
  (strict-mode violation). Removed the redundant native `title` and scoped the
  assertion to the expanded disclosure paragraph. All 12 browser journeys pass.
- `tests/http/test_exports.py` still asserted the pre-`0031` CSV layout. Updated
  the patient and physician header constants and column indices for
  `patients.birth_year` and physician `name_fa`/`name_en`, and supplied a name
  to physician-creation fixtures. All four previously failing export tests pass
  in isolation.
- The new physician **Unavailable drugs** navigation entry adds a header Tab
  stop; migrated the `themes.spec.ts` keyboard-focus expectations (passes).
- `codebase-memory/overview.md`: English/Persian interface, schema `0031`,
  fixed-BN scope and current proposal/signing behavior.

### Pre-existing browser baselines (recorded; not New case regressions)

The plan (§2, §17 H) already records legacy browser probes that still use the
removed registration working-note field. A 13-spec Chromium/Firefox regression
run (250 passed, 8 skipped) against the long-lived shared `x_ui` database
(315 users / 306 patients from repeated runs) also shows stale
`#draft-save-status` assertions (wizard one-page-per-step, 2026-10), the raw
`s50_q1` question-ID assertion (readable labels, 2026-11-02), report tests that
require `DATABASE_URL`, accumulated-state "Showing 100 of N" expectations, and
documented focus/axe baselines. The relevant production files are unchanged
from `main`; these are not New case regressions and were deliberately not
migrated. The dedicated New case acceptance suite is green.

Source hashes remain:

- Oral XML: `f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`
- Lexicomp CSV: `31ce2fb78b0bbf364786e25b2c5f26ccca29ea50657f7faba02bb9fd18828255`
