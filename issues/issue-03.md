# Issue 03 — Diagnosis Continue does not request confirmation

Date: 2026-10-27  
Status: implemented on `dev` working tree (uncommitted); pending live verification  
Affected flow: Register new patient → Diagnosis (step 2) → Continue to PANSS

## Finding

The wizard's Continue control changes pages without checking the Diagnosis Assessment summary. There is no confirmation handler between pressing Continue and opening PANSS. The existing diagnosis warning is an inline panel for completed below-threshold assessments; it is not a navigation confirmation and does not cover unanswered, partial, or indeterminate assessments.

The requested behavior is a warning with an explicit choice, rather than a requirement to satisfy the diagnosis before proceeding:

| Current assessment | Press Continue to PANSS |
| --- | --- |
| Complete and `findings.overall === "criteria_satisfied"` | Open PANSS directly. |
| Unanswered, partial, all Unknown, indeterminate, or below threshold | Stay on Diagnosis and ask for confirmation. Cancel stays; explicit confirmation opens PANSS. |
| Previously acknowledged below threshold or a legacy diagnosis bypass | Still ask: neither record means that the criteria confirm the diagnosis. |
| Result unavailable, stale, or not yet saved | Resolve the current assessment first; show a retryable error if that fails. Do not treat a failed request as a clinical result. |

The first two rows express the supplied requirement. The remaining rows are proposed implementation details that prevent stale results and legacy records from silently avoiding the warning.

## Root cause and evidence

Paths and line numbers refer to the inspected repository tree.

| Location | Evidence |
| --- | --- |
| `web/src/features/encounters/EncounterPage.tsx:408–437` | `nextStep` depends only on the wizard index. `followStepLink()` prevents the normal click, changes the hash and calls `setStep()`. It never reads a diagnosis evaluation or requests confirmation. |
| `EncounterPage.tsx:619–625, 709–717` | `DiagnosisSection` receives the encounter, autosave, session callback and step number. Its preview remains internal. The parent renders `#wizard-continue` as an anchor using the same navigation handler as other step links. There is no diagnosis-to-pager validation contract. |
| `EncounterPage.tsx:484–495` | Hash navigation starts `autosave.flush()` without awaiting it and selects the requested page. Saving on navigation does not validate assessment results. |
| `web/src/features/assessments/diagnosis/DiagnosisSection.tsx:119–122, 184–195, 278–314, 514–538` | The server preview is local component state, refreshed after saved revisions. `requires_acknowledgment` controls an inline warning/action for a completed below-threshold result. Nothing connects that warning to Continue. |
| `backend/src/x_insight/assessments/diagnosis.py:168–215` | `compute_diagnosis_state()` returns evaluation, `can_proceed`, and `requires_acknowledgment`. A valid bypass or acknowledged below-threshold result can set `can_proceed` to true, although criteria are not satisfied. These fields are not consumed by the pager. |
| `diagnosis.py:281–300` | The acknowledgment command rejects incomplete assessments and results other than below threshold. It cannot implement a warning covering all nonconfirming states. |
| `web/src/features/encounters/useAutosave.ts:104–180, 224–237` | Saving catches failures into save state. `flush()` is explicitly best effort; it starts a save for dirty/failed state but immediately resolves for a save already in progress or a conflict. Merely awaiting it is insufficient evidence that the latest answers were saved. |
| `e2e/diagnosis.spec.ts:290–330` | The current Continue journey explicitly expects partial answers to navigate immediately to PANSS without acknowledgment or bypass. No confirmation is asserted. |

**Causal chain:** parent pager owns navigation → child owns assessment preview → no shared pre-navigation check → clicking Continue always advances → assessment warnings cannot interrupt that transition.

This is a navigation integration gap, not evidence that the calculator is incorrectly confirming the diagnosis. The evaluator already computes A–F and distinguishes `criteria_satisfied`, `below_threshold`, and `indeterminate` (`backend/src/x_insight/assessments/engine.py:381–430`). No change to the clinical criteria is needed for the requested warning.

### How the behavior arose

The original [S09 report](../project-documents/dev/progress/s09.md) describes the diagnosis gate as advisory rather than an enforced page transition. Commit `05b13f8` introduced the page-by-page wizard with a generic pager. Later, [issue-02](issue-02.md) and commit `4b7e02f` removed redundant skip/bypass controls while explicitly retaining unrestricted Continue. The current requirement is a refinement of that navigation behavior, not a reason to restore the removed controls.

`project-documents/dev/plan.md:58` still says Back/Continue do not consult completion. The implementation plan must add a dated exception for registration Diagnosis Continue. Historical issue reports should remain historical; the new requirement supersedes their unconditional-navigation expectation for this transition.

The code and recorded history establish the mechanism and previous behavior. The causal explanation above is inferred from those sources; the requested confirmation behavior is confirmed by the supplied problem statement. No browser reproduction was performed in this investigation.

### Default Unknown discrepancy

The problem statement describes every diagnosis item as Unknown by default. In the inspected code, a missing diagnosis section starts with `answers: {}` (`DiagnosisSection.tsx:73–85`), and a radio is checked only when its stored value exactly matches that option (`:395–399`). A fresh form therefore has no selected radios; missing and explicitly Unknown answers are distinct states.

Both must trigger the proposed warning. If visible Unknown defaults are part of the intended correction, include that as an explicit form change: select Unknown for absent values while preserving their missing/unanswered storage meaning until an explicit edit. Do not populate or save every item merely on mount. That would change assessment completeness and existing drafts without a user action. Add a test for the chosen display behavior, separately from the navigation fix.

## Proposed implementation plan

### 1. Add a diagnosis-specific Continue check

In `EncounterPage.tsx`, give the registration Diagnosis → PANSS Continue action an asynchronous handler that runs before any hash or step change. Use an action button for this guarded transition, retaining `#wizard-continue`; an anchor's modified-click/new-tab behavior otherwise bypasses the handler. Keep other pager transitions unchanged.

Have that handler obtain the current server evaluation through `getDiagnosis(encounterId)` after the save barrier described below. Update the visible Assessment summary with that same response, using a small explicit parent/child contract or shared preview state. Do not independently reproduce the A–F logic in React.

Allow direct navigation only when `evaluation.status === "complete"` and `evaluation.findings.overall === "criteria_satisfied"`. Do not substitute `can_proceed` or `!requires_acknowledgment`: the former also allows acknowledged/bypassed states, while the latter includes unanswered and indeterminate states.

### 2. Check the latest answers, not a cached successful result

Add a narrowly scoped save barrier in the autosave API that waits for any in-flight write and pending edits, and returns an explicit success/revision or failure/conflict outcome. Preserve existing best-effort `flush()` behavior for its other callers. The barrier must account for edits made during a save rather than equating a resolved promise with saved current data.

While checking or confirming, disable duplicate Continue attempts and diagnosis edits that could invalidate the checked result. After successful saving, fetch diagnosis and verify its revision and answers correspond to the saved snapshot. Ignore outdated preview responses so a slower background request cannot replace the checked result in the summary. If the draft changes externally, re-evaluate before completing the transition.

On save failure, conflict, expired session, or preview failure, retain the page and answers and use the existing recovery/session handling. Show a clear retry action. Never allow a previously satisfied preview to approve a newer nonconfirming answer set. A fresh result should replace the old summary before the dialog appears.

### 3. Show an explicit confirmation for every nonconfirming result

Suggested text:

> The current assessment does not confirm the diagnosis. Do you want to continue to PANSS anyway?

Offer **Return to diagnosis** and **Continue to PANSS anyway**. Optionally add the precise summary state, such as unanswered or indeterminate, without describing every nonconfirming result as below threshold. Use an accessible dialog with a labelled heading, focus management, Escape/cancel handling, and focus restoration. Cancel and dismissal must leave the route unchanged; only confirmation may navigate.

Confirmation authorizes this transition only. Keep it transient, rechecking on a later Continue attempt. Preserve answers and their real assessment state. Do not call the answer-clearing bypass API or automatically write a clinical acknowledgment, skip sentinel, or completed assessment.

Keep the existing attributed below-threshold acknowledgment and proposal-generation checks intact. That acknowledgment has a narrower, revision-bound meaning. The requested navigation confirmation does not imply authorization to change those backend requirements. No database migration or new acknowledgment endpoint is needed for this plan.

### 4. Update requirements and regression coverage

Add the dated Diagnosis Continue exception to the current plan and link this issue. Revise the unconditional partial-answer Continue browser test to exercise cancel followed by confirm. Keep the answer-preservation assertions. Preserve issue-02's removal of active skip/bypass actions.

Limit the new behavior to the requested registration transition. Diagnosis is also follow-up step 1, and the component and pager are shared (`EncounterPage.tsx:88–113, 619–625`); add a regression proving follow-up and other assessment transitions retain their current behavior. Direct step URLs, browser history and other step links are separate navigation paths: this request concerns pressing Continue and does not establish a global route-access restriction. If that wider restriction is wanted later, implement it centrally rather than relying on a button handler.

## Acceptance and verification plan

1. Fresh untouched diagnosis: Continue shows the warning and stays on step 2. Cancel, Escape, and dismissal stay there; explicit confirmation opens PANSS step 3.
2. Repeat for explicit all Unknown, partial, indeterminate, completed below threshold, acknowledged below threshold, and a legacy bypass record. None silently skips the warning because of an old record or `can_proceed` value.
3. A genuinely criteria-satisfied result opens PANSS directly without a warning. Use the released evaluator's existing satisfied fixture, including applicable conditional items.
4. From a satisfied result, change an answer so it no longer confirms and immediately press Continue before debounce completes: the warning must reflect the new answers. Reverse the change and verify the now-satisfied result navigates directly after saving. Cover an in-flight save and out-of-order preview responses.
5. Save/network failure, preview failure, expired session, and concurrent-edit conflict never use stale confirmation. Local answers survive; retry/reconciliation works. Double-clicking Continue produces one check/dialog/transition.
6. After cancel and confirm, return, reload, and inspect saved draft data: answers survive and no new bypass, skip, or clinical acknowledgment was created. Confirmation alone does not change proposal readiness. Returning to Diagnosis and pressing Continue again reevaluates and prompts if still nonconfirming.
7. Verify keyboard operation, dialog focus and accessible labels in Chromium and Firefox. Check registration Back, other Continue controls, and shared follow-up navigation for regressions. Verify the agreed fresh-form Unknown presentation without automatic answer persistence.

During implementation, run `make check`, the targeted `e2e/diagnosis.spec.ts` suite, and the relevant registration/follow-up journeys in both browsers with the API running. Add focused tests for the new autosave barrier's in-flight, failure, conflict and pending-edit behavior because other callers share that hook. Retain existing evaluator/acknowledgment backend tests; expand them only if backend contracts change.

## Investigation scope and handoff

Analysis used source, existing tests, session reports, requirements and Git history. The app and test suites were not run, so this report establishes a static root-cause trace rather than browser proof. Implementation and the verification plan remain pending. Only this issue document and required progress documentation were changed; application code, tests and clinical content were left unchanged. Rationale is kept here rather than duplicated in a new `context/` entry.

## Implementation summary (2026-10-27, uncommitted on `dev`)

Backend untouched; no migration, no new endpoint, no clinical-rule change.

- `web/src/features/encounters/EncounterPage.tsx`: registration Diagnosis → PANSS Continue is a guarded action button (`#wizard-continue`, retained id) instead of a pager link, used only when `encounter.kind === "registration"`, current step is `diagnosis`, and next step is `panss`. `handleGuardedContinue()` runs `autosave.ensureSaved()` first, then `getDiagnosis(encounterId)`, verifies revision/answers against the saved snapshot with one reread on mismatch, ignores stale out-of-order responses via a sequence guard, navigates directly only when `evaluation.status === "complete"` and `evaluation.findings.overall === "criteria_satisfied"` (predicate `diagnosisConfirms()`; `can_proceed` / `requires_acknowledgment` are not substituted), and otherwise opens a transient confirmation dialog. Cancel/Escape/dismissal stays on Diagnosis with route unchanged and focus restored to Continue; only explicit confirm navigates. Dialog state is transient per press and rechecked on the next press. Never calls bypass/acknowledgment, never writes a skip sentinel or completed assessment. Duplicate presses and diagnosis edits are locked while checking/confirming. Other pager transitions, step links, hash/back-forward handling, and follow-up Diagnosis Continue (plain link) are unchanged.
- `web/src/features/encounters/useAutosave.ts`: added narrowly scoped `ensureSaved()` save barrier alongside unchanged best-effort `flush()`. It waits for in-flight writes plus pending edits, reports an explicit `{ok, revision, data}` / `{ok: false, reason}` outcome (`conflict` / `failed` / `session`), carries the saved snapshot for caller verification, persists edits made during a save in a bounded follow-up pass, and reports failure rather than looping on continuous concurrent editing. `saveNow()` coalesces concurrent saves via `inflightRef`; saved/edit sequence counters keep the barrier exact without trusting render-synced refs.
- `web/src/features/assessments/diagnosis/DiagnosisSection.tsx`: accepts parent `checkedPreview` / `locked` props. The visible Assessment summary adopts the exact server response the check decided on and invalidates older in-flight preview refreshes via a sequence guard, so the verdict shown matches the decision. Inputs lock while the parent checks or confirms. Existing below-threshold acknowledgment, bypass status-only display, and proposal-generation gates are unchanged.
- `e2e/diagnosis.spec.ts`: revised unconditional partial-Continue expectation into guarded cancel/confirm coverage; added journeys for untouched diagnosis, check-failure retry, satisfied direct navigation, explicit all-Unknown, indeterminate, completed below-threshold, acknowledged below-threshold, legacy bypass status, and follow-up plain-link regression. Confirm/cancel assertions verify answers survive and no bypass, skip, or clinical acknowledgment is created.

## Verification status

- Static checks (reported): web build PASS, root `tsc` PASS, `ruff check` PASS, `mypy` PASS, `ruff format` pre-existing FAIL on `tls_fixture.py` only; backend non-DB 17+51 PASS.
- BLOCKED (no PG/browsers in this environment): DB-backed backend suite and live Chromium/Firefox `e2e/diagnosis.spec.ts` plus registration/follow-up journeys.
- Code-review must-fix repairs applied on the working tree; details in the S09 implementation follow-up.
- Remaining gaps: live Chromium/Firefox e2e, in-flight/out-of-order preview behavior, conflict/session/double-click paths, reload and proposal-readiness after confirm, keyboard operation in both browsers, and a dedicated `ensureSaved` unit harness.
