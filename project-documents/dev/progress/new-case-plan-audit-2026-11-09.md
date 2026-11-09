# New case registration plan audit — 2026-11-09

## Result: implementation incomplete

Audited the supplied `new-case-registration-plan-2026-11-09.md` against clean
`main` at `4d1fb8feb782ade19af1f0c1d5d6f8428fd9c80a`. This is a source and
verification audit, not an implementation-completion claim. No application
source, migration, clinical asset or existing signed record was changed.

The new clinical workflow cannot currently be completed through either the
browser or HTTP. Passing helper tests and packaging checks do not satisfy the
package exit criteria. B, F, G and H remain incomplete; the implemented E
backend also has an uncovered demographic validation defect.

## Package-by-package assessment

| Package | Current implementation | Exit assessment |
| --- | --- | --- |
| A — baseline/content | Runtime CSV/XML, workflow manifest, labs, BP classification and formulation inventory now exist; the older A checkpoint predates these E additions. Corpus/hash and content-loading checks pass. | Content delivered; full baseline/clinical-content acceptance is not certified by this audit. |
| B — schema/identity/settings | `0030_new_case_state.py` adds birth year, age 100 and frozen workflow identity. It does not add physician names, unavailable-drug settings, proposal storage or the signed-reference variant. | **Incomplete.** Identity/settings and birth-year correction contracts are missing. |
| C — Lexicomp | Transactional installer, stable IDs, explicit profile, pinned release, symmetric indexed lookup, A-versus-missing coverage and candidate/current-only comparison exist. | Focused corpus/import/lookup/rollback tests pass; downstream proposal/signature evidence retention awaits F. |
| D — fixed oral BN | Dedicated bounded XML loader, all-row normalization validation, exact ordered row lookup, filtering and immutable registry bootstrap exist. | Focused model/registry tests pass; real workflow integration awaits F. No generic inference limit was raised. |
| E — typed state/rules | Typed autosave, server pins/defaults/attribution, persistent overrides, diagnosis assertion, navigation validation and deterministic rule preview exist. | Substantial backend implementation; **not fully conformant** because registration still calls legacy name validation. Rendered bilingual messages await G. |
| F — proposals/signing | Only `new_case_candidates.calculate_candidates` exists; it has no production caller. Preview reports `not_generated`. | **Incomplete.** No immutable proposal table/routes/fingerprint/freshness fence, editor integration, v4 signing or compatible signed readers. |
| G — UI | Only immutable-workflow gating and an unavailable message exist. Age-based registration still creates legacy drafts. | **Incomplete.** No new wizard, forms, settings page, final-plan page or browser acceptance suite. |
| H — packaging/compatibility/acceptance | Bundled files, content-directory override, startup installation, trusted model restore dispatch and expanded backup bounds exist. | **Incomplete.** Cannot restore/test proposal and v4 signature rows that do not exist; full integration/browser/release acceptance is absent. |

## Findings, ordered by impact

### 1. Blocking: New case users still enter the legacy workflow

`web/src/features/patients/RegistrationForm.tsx` retains an age field and
`validation.ts::toRegistrationPayload` sends `age`, including when status is
`first_time`. `cases/patients.py::create_patient` selects `new-case-v1` only
when birth year is present. Therefore the normal New case entry continues
through legacy assessment/generation behavior rather than the specified fixed
BN path. Conversely, `EncounterPage.tsx:386` displays only
`encounterWorkflowUnavailable` for a real new-case draft. The dedicated
`web/src/features/new-case/` feature and `UnavailableDrugsPage.tsx` are absent.

### 2. Blocking: proposal generation and confirmation do not exist

`cases/new_case_candidates.py` is an isolated calculation helper, called only
by tests. `cases/new_case.py` returns candidate/interactions placeholders.
The generated OpenAPI exposes only four new-case endpoints:

- `GET /api/v1/content/new-case`
- `GET /api/v1/encounters/{encounter_id}/new-case/preview`
- `POST /api/v1/encounters/{encounter_id}/new-case/validate`
- `POST /api/v1/encounters/{encounter_id}/new-case/diagnosis-assertion`

There are no new-case proposal create/read or sign endpoints. There is no
`new_case_proposals` table. `cases/tables.py:175` still requires legacy batch
and proposal IDs, and `cases/signing.py:67` still produces only v3 snapshots.
The planned original/final separation, proposal/settings freshness, signing
races/idempotency, v4 chart/report/follow-up and backup FK checks are absent.
Existing legacy/new-workflow refusal guards correctly remain in place; removing
them would not implement the missing signing path.

### 3. High: physician names and personal formulation settings are absent

`identity/tables.py::users`, migrations, identity service/router and browser
contracts have no `name_fa`, `name_en` or `unavailable_antipsychotics` fields.
There are no `/me/unavailable-drugs` endpoints or corresponding page/navigation.
New physician creation therefore lacks the requested name requirement, and
candidate calculations cannot consume an authenticated, revision-fenced
personal availability snapshot. This blocks both B's acceptance and F's
freshness/signing contract. SQL inspection of the migrated disposable database
confirmed Alembic `0030`, zero matching physician-name/settings columns and no
`new_case_proposals` table.

### 4. High: New case registration uses the wrong name validator

`cases/patients.py:145` and `:149` always call `normalize_person_name`, which
allows hyphens/apostrophes, rejects outer spaces instead of trimming them, and
rejects repeated interior ordinary spaces. The intended new-case helper
`new_case_demographics.normalize_name` exists and passes its unit tests but is
not called by registration or demographic edits.

A direct call to the actual registration service validator with valid
birth-year/`first_time` data reproduced:

| Given name | Actual | Required by plan |
| --- | --- | --- |
| `Anne-Marie` | accepted | reject |
| `O'Neil` | accepted | reject |
| `  Ali  ` | rejected | accept as `Ali` |
| `Ali  Reza` | rejected | accept |

This is an uncovered implementation defect, separate from the missing UI.
The correction must be scoped to the new-case contract so legacy name rules
remain compatible, and must cover both names and applicable edit paths.

### 5. High: a recorded birth year cannot be corrected

`patients.validate_patch_fields` excludes `birth_year` from its allowlist;
`patch_patient` rejects age edits for birth-year-backed records. There is no
supported correction command that recomputes encounter age from its frozen year
and invalidates the proposal. The age-override refusal is correct but does not
complete the correction behavior required by plan section 7.

### 6. Medium: planned API and translation contracts are not delivered

The four implemented paths above differ from the specified `/new-case/config`,
`/new-case`, `/validate-step` and `/diagnosis-confirmation` contracts. The content
endpoint does not return the server registration year needed for the birth-year
list. `new_case.errors.*` and `new_case.sections.*` message keys returned by the
server have no entries in the browser message catalog. These must be completed
or explicitly reconciled before wiring the missing UI; successful preview JSON
is not evidence of rendered bilingual behavior.

### 7. Blocking acceptance gap: no end-to-end new-case suite

`e2e/new-case.spec.ts` does not exist. No oral or LAI browser journey can currently
complete, so reload/save barriers, direct navigation, keyboard controls,
accessibility, Persian/RTL and signed-record display are not accepted. The
focused HTTP suite has eight tests and stops at state/preview/assertion/backup;
it cannot prove generation, editor, signing or concurrent confirmations.

## Verification executed in this audit

Environment: Python 3.12.14, pytest 9.1.1, existing locked dependencies;
PostgreSQL 16 in disposable container `x-new-case-audit-db` bound only to
127.0.0.1:55439. No existing application database or deployment was used. The disposable
container was stopped and automatically removed after verification.

- `make check`: **passed** (ruff format/check, mypy, frontend build, TypeScript).
  Vite retains its large-chunk warning.
- `make test-web`: **20 passed**, none skipped.
- `make prod-check`: **passed**; this is packaging inspection, not a production
  image build or deployment.
- Focused backend: **98 passed in 136.80 s**. Selector:
  `tests/assessments/test_new_case.py tests/assessments/test_new_case_rules.py
  tests/test_new_case_demographics.py tests/models/test_new_case_sources.py
  tests/models/test_oral_antipsychotic.py tests/models/test_new_case_candidates.py
  tests/ddi/test_lexicomp.py tests/http/test_new_case.py
  tests/operations/test_new_case_packaging.py`.
- Shared HTTP and complete recovery selector: **223 passed in 790.97 s**:
  `tests/http/test_patients.py tests/http/test_identity.py
  tests/http/test_physicians.py tests/http/test_signing.py
  tests/http/test_medications.py tests/http/test_ddi.py tests/recovery/`.
  This includes 166 shared HTTP tests and all 57 recovery tests, including
  restore-over-live against the disposable database. The earlier checkpoint's
  signing/recovery failures did not reproduce after the schema-path repair.
- Total executed backend tests: **321 passed** across the two selectors; no
  failures or skips. These do not cover the missing workflow contracts.
- Registration-validator reproduction and generated OpenAPI inspection confirm
  findings 2 and 4 independently of checkpoint statements.
- `git diff --check`: **passed**. Memory JSON, unique paths, summary link and
  inspected-source SHA were validated.
- Full `make verify` and browser acceptance were not run: the plan puts them
  after working integration, which is absent. A missing suite is not a pass.

Source bytes match the plan:

- XML: `f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`
- CSV: `31ce2fb78b0bbf364786e25b2c5f26ccca29ea50657f7faba02bb9fd18828255`

Raw command logs for this session are under `/tmp/x-new-case-audit-*.log`.
They are local evidence, not committed artifacts or a guarantee of retention.

## Handoff

Complete B's remaining schema/identity/settings/correction behavior, correct
new-case name validation, implement F's proposal/signing/read/restore contract,
then replace G's unavailable screen with the specified wizard/editor/settings.
Finish H using a dataset with historical records plus real new-case proposals
and v4 signatures. Run the complete plan section 18 matrix, including browser
and full repository gates, after integration exists. Do not certify every
package complete or the implementation free of problems from the checks above.

No new rationale entry was required: the audit found missing implementation
and a correction to an already specified rule, rather than a new design choice.
Existing `context/new-case.md` already distinguishes intended v4 behavior from
shipped code.
