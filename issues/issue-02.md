# Issue 02 — Redundant skip/bypass controls in Encounter draft

Date: 2026-10-26  
Status: analyzed; fix proposed; no application code changed  
Affected flow: Register new patient → Encounter draft → Diagnosis (step 2), PANSS (step 3), C-SSRS (step 4)

## Finding

The wizard already lets users move through these steps with **Continue**, regardless of assessment completeness. The extra skip/bypass buttons are unnecessary for navigation. They also do something materially different from Continue: they clear assessment answers and persist an explicit bypass/not-assessed state.

The requested behavior is one forward action: Continue to PANSS, Continue to C-SSRS, and Continue to History. Leaving an assessment incomplete must preserve its answers and must not silently skip, bypass, complete, acknowledge, or supply negative answers for it.

## Root cause

Two independently implemented concepts coexist in the UI:

1. **Assessment state commands.** The original S09–S11 work introduced an attributed diagnosis bypass and explicit PANSS/C-SSRS skips. These remain in each assessment component and in tests asserting the old behavior.
2. **Wizard navigation.** Commit `05b13f8` (`fix(encounters): show each draft wizard step on its own page`) added the shared Back/Continue pager. It switches the displayed page without consulting assessment completion or diagnosis `can_proceed`.

The historical implementation is supported by the session reports and that commit. The resulting design mismatch is inferred from the current code: the older state-changing controls remain alongside the later navigation mechanism. This is not a missing Continue implementation or a backend rejection of navigation.

Diagnosis makes the mismatch especially confusing: it renders **“Can proceed to the next step: No”** for several states while the parent Continue link is available. Its bypass panel says “Continue without completing this assessment,” but its handler records a bypass and stays on the same page. PANSS/C-SSRS skips also stay on the current page.

## Evidence and execution paths

Paths below are relative to the repository root; line numbers describe the analyzed tree.

| Location | Observed behavior |
| --- | --- |
| `web/src/features/encounters/EncounterPage.tsx:408–437, 697–717` | The next step depends only on the current step index. `#wizard-continue` is an anchor styled as a button, rendered whenever a next step exists. `followStepLink()` changes the hash and displayed step; there is no completeness, acknowledgment, or skip condition. |
| `web/src/features/encounters/EncounterPage.tsx:484–495` | Hash changes flush the parent autosave and select the page. Assessment answers live in that parent, so unmounting a step does not discard them. The flush is asynchronous; changing pages is not proof that a save succeeded. |
| `web/src/features/assessments/diagnosis/DiagnosisSection.tsx:280–310, 586–631` | `handleBypass()` flushes/gets the current revision, calls `bypassDiagnosis()`, synchronizes the server snapshot, and updates the preview. The bypass panel and misleading next-step eligibility text are independent of the pager. |
| `backend/src/x_insight/assessments/diagnosis.py:168–215, 383–415` | The service derives `can_proceed` from assessment state. Bypass replaces diagnosis with empty answers, null acknowledgment, and an attributed bypass record; it increments the draft revision and records an audit event. |
| `web/src/features/assessments/panss/PanssSection.tsx:187–193, 456–478` | Skip replaces the PANSS answer map with `{ "__skipped": true }` through shared autosave. Resume replaces it with `{}`. Neither action navigates. |
| `web/src/features/assessments/cssrs/CssrsSection.tsx:294–303, 647–672` | Skip replaces C-SSRS answers with the same skip sentinel; Resume restores the five recent items to explicit values or Unknown. Neither action navigates. |
| `web/src/features/reasoning/ProposalReviewPanel.tsx:518–534, 621–630` | Proposal generation separately checks readiness and a completed below-threshold diagnosis acknowledgment. Error text currently directs users to acknowledge **or bypass**. |

Historical sources: [S07 wizard follow-up](../project-documents/dev/progress/s07.md#follow-up-2026-10-18--one-page-per-wizard-step), [S09](../project-documents/dev/progress/s09.md), [S10](../project-documents/dev/progress/s10.md), and [S11](../project-documents/dev/progress/s11.md). The latest S11 follow-up supersedes the original C-SSRS form description: the live form is `cssrs.v2`, five recent-ideation questions with Yes/No/Unknown and Unknown defaults. It still supports explicit skip.

## Impact and boundaries

- Users see multiple apparent ways to proceed, although only Continue navigates.
- Choosing skip/bypass can erase partially or fully entered answers. A diagnosis bypass also removes its acknowledgment. This is explicit existing command behavior, not evidence of an autosave defect.
- The diagnosis eligibility and warning copy imply that navigation is blocked or that Continue records acknowledgment. Neither matches the pager handler: acknowledgment is a separate command.
- Navigation and proposal generation are separate. Letting users reach History or Proposal review does not guarantee generation readiness. `backend/src/x_insight/reasoning/readiness.py` checks required inputs, and `snapshots.py:264–279` treats an incomplete/not-assessed C-SSRS result as missing context.
- The three assessment components are shared by registration and follow-up (`EncounterPage.tsx:618–642`). Removing their controls will affect both flows. This matches “remove these buttons from the app”; follow-up must receive regression coverage too, using its own step numbering.

## Proposed fix plan

### 1. Remove active skip/bypass actions from the assessment UI

- Diagnosis: remove the “Bypass instead of completing” action panel, its button, `handleBypass()`, unused import, and bypass-only busy handling. Retain acknowledgment behavior and read support for already stored bypass records. Show historical bypass attribution as status information without an action encouraging a new bypass.
- PANSS and C-SSRS: remove the Skip buttons, `handleSkip()` functions, and copy encouraging skip or describing its answer-clearing action. Retain recovery for existing skipped drafts, as described below.
- Remove unused bypass/skip styles only after checking references across the app. Update component/API comments that describe the removed user actions. Keeping legacy API response types or backend support is compatible with removing the UI controls.

### 2. Keep Continue as navigation and correct conflicting text

- Preserve `#wizard-continue`, its destination selection, step links, Back behavior, and shared autosave. No new completion gate or alternate navigation handler is needed.
- Remove the diagnosis “Can proceed to the next step” line. Keep clinical evaluation results and the below-threshold warning, but describe acknowledgment as a separate recorded action required before proposal generation when applicable. Remove the claim that merely continuing records acknowledgment.
- Update `ProposalReviewPanel.tsx` messages that recommend using the removed bypass action. Direct users to the retained acknowledgment action for a completed below-threshold result. Preserve generation readiness, medication reconciliation, and signing requirements.
- Continue must not invoke acknowledgment or bypass, write `__skipped`, clear answers, or manufacture a completed assessment. Preserve C-SSRS's existing Unknown normalization; all Unknown is not an explicit negative screen.

### 3. Preserve compatibility with saved drafts

- Keep backend decoding/evaluation of legacy `__skipped` answers and attributed diagnosis bypass records. No schema migration or rewrite of signed snapshots is needed for this UI change.
- Keep a recovery-only “Resume assessment” action visible on an already skipped PANSS/C-SSRS draft, or provide an equivalent explicit recovery control. Existing skipped forms disable their inputs (`PanssSection.tsx:266`, `CssrsSection.tsx:360`); deleting the entire Skip/Resume block would strand those records. Resume clears the sentinel through the existing autosave contract and enables normal answering. It is not another forward-navigation action.
- Preserve historical diagnosis bypass status and attribution. Adding answers already invalidates that bypass on the server; do not silently convert it into completion or acknowledgment.
- Do not automatically clear legacy flags on mount or on Continue. Merely visiting a draft must not replace “not assessed” with a default assessment result. The old controls already discarded answers; removing them cannot recover those answers.
- Backend bypass-route removal is unnecessary for this request. Retaining it for compatibility does not expose a new button; API retirement can be scoped separately if required.

### 4. Update requirements and regression coverage

- Add a dated requirement clarification to the current plan (`project-documents/dev/plan.md`, especially the explicit bypass/skip descriptions around lines 56, 246–252, 495, and FR-11–13 mapping). Preserve historical S09–S11 reports rather than rewriting their original decisions. State that completion requirements govern assessment results/generation, while Continue permits page navigation.
- Update `e2e/diagnosis.spec.ts`, `e2e/panss.spec.ts`, and `e2e/cssrs.spec.ts`: replace journeys that click removed controls with Continue journeys; seed legacy skip/bypass records through fixtures/API for compatibility tests. Retain evaluator and backend legacy-contract tests.
- Cover registration and shared follow-up behavior. Update any proposal-review assertions that reference bypass instructions.

## Acceptance criteria and verification

1. On registration steps 2–4, no active Bypass diagnosis, Skip PANSS, or Skip C-SSRS button appears. Each Continue opens the correct next page even when Diagnosis/PANSS is unanswered or partial, or C-SSRS is left at its Unknown defaults. A completed below-threshold or indeterminate diagnosis also permits navigation.
2. Enter answers, immediately press Continue before the debounce expires, return with Back, wait for acknowledged save, and reload. Answers survive; no new skip sentinel, diagnosis bypass, or acknowledgment was created. Check saved draft data, not just visible controls. C-SSRS's existing default normalization remains allowed.
3. Existing skipped PANSS/C-SSRS drafts retain “not assessed” on visit/reload and can explicitly Resume and save new answers. Existing diagnosis bypass attribution remains readable and later explicit answers invalidate it as before.
4. Diagnosis no longer claims that the next step is blocked or that Continue records acknowledgment. Proposal review offers reachable actions and retains its actual readiness/acknowledgment requirements; incomplete PANSS does not gain scores and C-SSRS Unknown does not become No.
5. Repeat the relevant shared-component journeys on follow-up. Verify keyboard navigation and absence of an empty skip/bypass panel.
6. During implementation, run `make check`, then the targeted Playwright assessment/registration/follow-up suites in Chromium and Firefox. Run `backend/tests/assessments/` and `backend/tests/models/test_proposal_readiness.py` if shared data handling changes; expand backend checks if the implementation touches backend contracts.

This investigation used static source, test, documentation, and Git-history inspection. It did not run the app or test suites, and it does not claim browser reproduction. Only this report and the required progress/handoff documentation were changed; implementation remains pending.
