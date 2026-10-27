# Issue 05 — Diagnosis questions have no default value

Date: 2026-10-27  
Status: implemented; runtime verification pending
Affected flow: Register new patient → step 2 (Diagnosis). The same component also renders follow-up Diagnosis at step 1.

## Requirement and finding

Every Diagnosis question must default to the stored value `"unknown"`, displayed as **Unknown**. Existing Yes, No, and Unknown answers must survive initialization, autosave, navigation, and reload.

The requirement is confirmed by the supplied problem statement. The proposed persistence behavior below interprets “default value” as an actual editable draft value, rather than only a selected radio whose underlying answer is still absent. This interpretation and the implementation are proposals; no fix was made during this investigation.

The current form includes Unknown as an option but never initializes absent answers to it. This explains the blank controls on a fresh form. The defect is in Diagnosis answer initialization, rather than CSS or the list of available options.

## Root cause and evidence

Paths and line references describe the inspected tree.

| Location | Relevant behavior |
| --- | --- |
| [DiagnosisSection.tsx:86](../web/src/features/assessments/diagnosis/DiagnosisSection.tsx#L86), `sectionOf()` | Missing Diagnosis data or missing answers become `answers: {}`. Existing answers are copied without inserting defaults. |
| [DiagnosisSection.tsx:115](../web/src/features/assessments/diagnosis/DiagnosisSection.tsx#L115), `ANSWER_OPTIONS` | `unknown` is already a valid selectable value. Defining an option does not select it. |
| [DiagnosisSection.tsx:223](../web/src/features/assessments/diagnosis/DiagnosisSection.tsx#L223), answer state and `setAnswer()` | Controls read the raw autosave slice; answers are inserted through a radio change. Loading the definition does not initialize them. |
| [DiagnosisSection.tsx:389](../web/src/features/assessments/diagnosis/DiagnosisSection.tsx#L389), worksheet rendering | Counts and response labels inspect stored answers. Radios use `checked={answers[item.id] === option.value}`. For an absent key, all three comparisons are false. |
| [diagnosis.py:54](../backend/src/x_insight/assessments/diagnosis.py#L54), `get_diagnosis_section()` | Server-side extraction likewise returns empty answers for a missing section. It does not supply defaults. |
| [engine.py:535](../backend/src/x_insight/assessments/engine.py#L535), `evaluate()` | Diagnosis retains the distinction between missing input and explicit Unknown. Only C-SSRS receives instrument-specific normalization here. Missing required Diagnosis answers produce `unanswered`/`partial`; explicit Unknown is a valid answer. |
| [diagnosis.v1.json](../content/assessments/diagnosis.v1.json) | The released form contains 13 questions: 12 always required and `f_prominent_psychosis`, conditionally required when `f_autism_present === "yes"`. The release explicitly distinguishes Unknown from unanswered. There is no item default consumed by the Diagnosis UI. |
| [diagnosis.spec.ts](../e2e/diagnosis.spec.ts) | Current tests expect fresh forms to show “No answers yet” and browser-edited partial forms to show “Incomplete”. They do not assert Unknown is selected by default. |

**Causal chain:** empty draft slice → absent answer keys → no initialization after definition loading → strict checked comparisons against `undefined` → no selected radio → unanswered server preview.

### Why the implementation behaves this way

The [original S09 report](../project-documents/dev/progress/s09.md) and released definition describe explicit Unknown and unanswered as distinct states. The later worksheet redesigns retained that answer contract. The recent Continue change, commit `741b6fc`, added navigation confirmation without adding defaults. Thus, the evidence supports an existing initialization policy that does not meet the newly stated default requirement; it does not establish a regression caused by the worksheet styling or Continue handler.

[Issue 03](issue-03.md#default-unknown-discrepancy) already identified the empty-answer discrepancy. Its analysis suggested a display-only default as a possible separate change while preserving missing storage. This issue proposes actual draft initialization to satisfy the explicit default-value request and keep controls, response counts, persisted answers, and preview consistent. That earlier suggestion is historical, not an implemented default.

The mechanism above is established by static inspection. The historical explanation is inferred from code, reports, and release metadata; the original author's unstated intent remains unknown. No live browser reproduction or test execution was performed for this report.

## Proposed solution

Initialize missing Diagnosis question values in the editable form, after the released definition and encounter draft have loaded, using the existing shared autosave path. Keep the backend evaluator's raw-input contract intact: API callers and forms that have never been opened can still submit missing inputs and receive an incomplete result.

### 1. Add narrowly scoped Diagnosis initialization

In `DiagnosisSection.tsx`, derive defaults from `definition.items`; do not maintain another hardcoded list of question IDs. Fill each absent question key with `"unknown"`, including the displayed conditional F question. Merge existing answers over those defaults so stored selections always win. Preserve unrecognized keys and invalid supplied values for existing validation; this change is not a data-cleaning pass. A default is for an absent key, not an instruction to turn every falsey or invalid value into Unknown.

Use the current local Diagnosis slice at initialization time, not the draft captured before an asynchronous definition request. Preserve `acknowledgment`, `bypass`, and any unrelated section metadata when writing the merged section. Change only `draft_data.diagnosis` through `autosave.setDraftValue()`; never replace the full draft or write directly through a separate PATCH request.

Make initialization idempotent: write only when question keys are missing. Re-renders, preview refreshes, remounts, and React effect replay must not cause repeated saves. An immediate answer edit must use the same merged answer set so it cannot discard defaults or a newer selection. Handle the short definition-loading/initialization interval so a user cannot start a Continue check against an uninitialized draft; defaults must be queued before the existing save barrier checks that draft.

Wait while the Diagnosis component is locked for navigation or an acknowledgment action, and do not automatically resolve a failed save or concurrency conflict. Retain the hook's existing retry/reconciliation controls. A definition-load failure must leave the draft intact and show the existing error state.

The existing C-SSRS initialization in [CssrsSection.tsx](../web/src/features/assessments/cssrs/CssrsSection.tsx) demonstrates using the shared setter for missing Unknown values. Borrow that local pattern selectively; do not copy its retired-item removal, skip semantics, or backend normalization into Diagnosis.

### 2. Preserve existing records and explain the state change

- Initialize editable new drafts and missing values in resumed editable drafts. The shared Diagnosis component should behave consistently on registration and follow-up; keep their different Continue policies unchanged.
- Do not initialize legacy bypass records or `__bypass`/`__skipped` sentinel answer sets. A valid attributed bypass requires empty answers. Adding defaults would invalidate the bypass and mix mutually exclusive states. Preserve its status-only presentation and existing recovery behavior; do not add a new bypass/skip action.
- Do not rewrite signed encounters, historical snapshots, stored assessment releases, or drafts in bulk. Defaults become persisted only through normal editing-session autosave when Diagnosis is opened.
- Preserve acknowledgment records rather than deleting or recreating them. Adding previously absent answer keys changes the answer hash and can invalidate an older acknowledgment. Keep the server's existing validity result visible; never forge a replacement acknowledgment to hide that consequence. Fully populated forms must receive no initialization write.

With all 13 defaults saved, the existing evaluator returns `status: "complete"`, all A–F criteria Unknown, and `overall: "indeterminate"`. Here complete means the required answer keys are present; it does not confirm the diagnosis. `can_proceed` stays false and no below-threshold acknowledgment is requested for all Unknown. Registration Continue must still show the issue-03 confirmation.

After a few Yes/No edits, the result may become indeterminate or below threshold rather than remaining structurally partial, because other answers now contain Unknown. A below-threshold result still uses the existing acknowledgment rules. Do not force all partially reviewed forms to remain indeterminate or modify criterion thresholds to retain old UI expectations.

### 3. Keep the worksheet and summary truthful

Render selected values, response counts, per-question status, and pending-save comparison from the initialized local answers. Show Unknown while initialization is pending only with a corresponding pending-save state; do not present it as server-saved before a successful response. The assessment summary must continue to use the server evaluation and refresh after the initialization save.

Adjust nearby copy to explain that questions start as Unknown and can be changed to Yes or No. Counts describe available answer values, not questions personally reviewed or criteria satisfied. Do not imply default Unknown constitutes clinician confirmation. Retain useful unanswered/partial and validation-error states for API-supplied invalid data and initialization failures.

### 4. Update requirements and affected tests

Add a dated issue-05 clarification to `project-documents/dev/plan.md` §2.2: Diagnosis initializes absent editable answers to Unknown through autosave, which can make the answer record structurally complete without satisfying diagnostic criteria. Clarify this exception to the older “visiting never writes … completion” language without changing other instruments. Keep the released definition and historical reports unchanged; the clarification describes a form default, not a new clinical threshold.

Update Diagnosis browser expectations affected by real initialization: untouched and browser-edited partial forms, saved response maps, and revision assumptions. Expect saved all-Unknown forms to be indeterminate, and assert retained Yes/No selections plus Unknown defaults for remaining questions. Use returned revisions when a fixture needs to acknowledge after an initialization save. Keep direct-backend partial-input tests: backend normalization is deliberately outside this fix.

## Implementation change allowlist

This is the complete intended change surface. Expand it only for a concrete failure caused by this fix, documenting that reason rather than refactoring adjacent code.

| File | Required changes |
| --- | --- |
| `web/src/features/assessments/diagnosis/DiagnosisSection.tsx` | Definition-driven missing-answer initialization; latest-state merge; idempotence and legacy/lock/conflict guards; integration with the existing answer setter/autosave; consistent selection/count/status display and concise default-value copy. Keep any small helper in this module. |
| `e2e/diagnosis.spec.ts` | Default-selection and actual persistence coverage; update affected incomplete/fresh expectations and revision assumptions; preserve Continue, acknowledgment, bypass, and follow-up regression coverage. |
| `project-documents/dev/plan.md` | One dated Diagnosis default-value clarification with a link to this issue. |
| `issues/issue-05.md` | Record implementation status, actual checks, and remaining verification gaps when implementation occurs. |
| `project-documents/dev/progress/s09.md` and its `README.md` index | Append a short implementation handoff and update the issue link/status. |

Other browser specs may need a **test-only** adjustment if a proven assertion assumes no Diagnosis initialization save. Inspect `e2e/autosave.spec.ts` and `e2e/followup.spec.ts`; do not broadly rewrite revision assertions or unrelated journeys preemptively.

### Explicitly outside scope

- No changes to backend evaluator/service/router behavior, acknowledgment hashes or attribution, proposal readiness, generation workflows, clinical rules, thresholds, source prompts, or conditional requiredness.
- No database migrations, new tables/endpoints, bulk draft backfill, signed-history changes, or release/version/hash regeneration.
- No changes to PANSS, C-SSRS, History, medications, DDI, or their defaults.
- No redesign, CSS/theme changes, dependency additions, generic assessment framework, or cleanup of nearby code.
- No rewrite of `useAutosave.ts` or `EncounterPage.tsx`. Reuse the existing setter, save barrier, locked state, preview freshness checks, and Continue dialog. A discovered integration defect requires a separately justified scope addition.
- No automatic acknowledgment, bypass, skip, or criteria-satisfied result. No overwriting stored Yes/No/Unknown, removing unknown answer keys, or masking invalid data.

## Acceptance and verification plan

1. Open fresh registration Diagnosis. After the definition loads, all 13 Unknown radios are selected; no Yes/No is selected. Autosave persists the 13 `"unknown"` values, and the server preview becomes complete/indeterminate with no acknowledgment requirement. Reload reproduces the values without another initialization save.
2. Change Yes → No → Unknown on representative questions. Values survive autosave, Back/Continue, remount, and reload. Editing immediately after definition loading retains the newer selection and all other defaults.
3. Open a resumed draft with some saved Yes/No/Unknown answers. Preserve those exact values and unrelated draft/section data; default only absent question keys. A fully populated draft causes no initialization PATCH or revision bump. Do not mask invalid values or remove unknown keys.
4. Change `f_autism_present` to Yes. `f_prominent_psychosis` already has Unknown unless previously answered, and existing F evaluation/requiredness remains authoritative. Changing applicability never resets either answer.
5. On all Unknown, registration Continue still waits for a successful initialization save and fresh preview, then asks for confirmation. Cancel stays and confirm navigates; a genuinely satisfied fixture still navigates directly. Follow-up uses the same defaults while retaining its existing unguarded Continue behavior.
6. Legacy attributed bypass and sentinel fixtures receive no default-writing side effect. Existing bypass validity and status survive. An existing acknowledgment is preserved; if newly added keys invalidate its hash, the UI shows the real server result and requires the ordinary acknowledgment action when applicable.
7. Exercise delayed definition loading, fast Continue, remount/effect replay, save failure, stale-revision conflict, and retry/reload. No repeated initialization loop, overwritten newer edit, silent conflict resolution, or false Saved state. Confirm other draft fields survive the default save.
8. Check native radio keyboard operation, accessible selection, response counts, and default-value copy in Chromium and Firefox. Run the targeted Diagnosis suite and relevant autosave/follow-up regressions against the API. Run `make check` and the existing Diagnosis/definition backend regressions to verify unchanged evaluator semantics. Add no new test framework for this fix.

## Investigation scope and handoff

This report is based on source, existing tests, released content metadata, relevant progress handoffs, and local Git history. It supplies a static root-cause trace and a proposed implementation plan, not runtime proof or clinical guidance. Implementation and the verification plan remain pending.

Only this report and the required progress follow-up/index were written for this investigation. Application code, tests, configuration, and released clinical content were not changed. The reasoning remains in this requested issue document rather than a duplicate `context/` entry.

## Implementation follow-up — 2026-10-27

The editable Diagnosis component now fills missing definition items with Unknown through the shared autosave setter, preserving existing answer keys and section metadata and leaving legacy bypass and sentinel states alone. The local merge is also used for immediate edits. Registration Continue is fenced until initialization is queued; the existing save barrier and server preview remain authoritative. The backend evaluator and released definition are unchanged.

The change to `EncounterPage.tsx` expands the original allowlist narrowly: the child cannot prevent a fast parent Continue while its asynchronous definition is loading. The test-only change to `e2e/followup.spec.ts` removes a fixed revision assumption invalidated by the new initialization save. Diagnosis browser tests were updated and extended for defaults, persistence, preservation, legacy states, failure and conflict handling, and immediate editing.

`git diff --check` passed. The frontend build, `make check`, backend regressions and browser/API tests were not run: this environment lacks Node/npm, `uv`, Docker and the running API. The new tests are therefore coverage to execute, not evidence of passing behavior. Run the acceptance plan above, including Chromium and Firefox, when the project runtime and services are available.
