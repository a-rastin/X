# Issue 12 — Registration C-SSRS should use Yes/No, default No

Date: 2026-10-28
Status: implemented; Firefox verification pending

## Requirement and scope

Adding a patient, step 4: each of the five recent-ideation questions must have exactly **Yes** and **No**, with **No** selected by default. The repository root is already `x/`, so this report is `x/issues/issue-12.md`.

Scope assumption: apply this to registration. Follow-up uses the same component at step 3; preserve its current behavior. Backend support for historical Unknown values can remain. This keeps the implementation small without changing unrelated encounters.

## Root cause

This behavior implements an earlier requirement that differs from the current request. The [S11 2026-10-23 handoff](../project-documents/dev/progress/s11.md#follow-up-2026-10-23--c-ssrs-recent-ideation-form-cut) explicitly specifies five Yes/No/Unknown questions, default Unknown, for registration and follow-up.

Before this implementation, the code implemented that policy in several places:

1. [CssrsSection.tsx](../web/src/features/assessments/cssrs/CssrsSection.tsx#L105) hardcodes three entries in `ANSWER_OPTIONS`. The fetched definition supplies prompts, not the rendered option list.
2. `explicitAnswer` accepts Unknown. `normalizedAnswers` fills missing/invalid answers with Unknown; its effect saves the five normalized answers through `setDraftValue`. `answeredItems`, `handleResume`, and `renderItem` also fall back to Unknown. Removing the radio alone would leave saved Unknown values and potentially no selected visible option.
3. [engine.py](../backend/src/x_insight/assessments/engine.py#L465), `normalize_cssrs_answers`, turns missing/null recent answers into Unknown unless skipped. [cssrs.py](../backend/src/x_insight/assessments/cssrs.py) uses it for previews and draft canonicalization. Callers in `cases/encounters.py` and `cases/signing.py` canonicalize draft data too. A server-loaded fresh slice can therefore already contain five explicit Unknowns; changing only a missing-value fallback is insufficient.
4. [cssrs.v2.json](../content/assessments/cssrs.v2.json) declares `yes_no_unknown`, and `require_live_cssrs_definition` requires that type. The server already accepts explicit Yes/No, so changing registration answer entry does not require changing the content release or evaluator.
5. [EncounterPage.tsx](../web/src/features/encounters/EncounterPage.tsx#L870) mounts one shared `CssrsSection` for both encounter kinds. [e2e/cssrs.spec.ts](../e2e/cssrs.spec.ts#L195) currently expects fifteen radios and Unknown defaults, including follow-up.

Evidence: desired behavior is confirmed by this request; the mechanism is corroborated by static source inspection and the dated handoff. The older clinical rationale is not established. No running-browser reproduction or clinical validation was performed. This analysis follows the affected data flow rather than claiming a full application audit.

## Minimal fix plan

1. Pass one registration boolean from `encounter.kind === "registration"` into the existing `CssrsSection`. Do not infer kind from the step number or create another form.
2. In registration, render only the existing Yes/No options. Preserve IDs, prompts, order, native radio semantics, labels and accessibility. Follow-up keeps three options.
3. Apply one consistent registration policy in the component: preserve explicit Yes/No; map missing, null and saved Unknown to No. Apply it to normalization, rendered selection, untouched answers during edits, and Resume. Retain the existing handling pattern for other malformed values rather than expanding validation scope. Reuse the existing normalization effect, latest-answer ref and autosave path; include the mode in relevant dependencies. Ensure a queued normalization cannot overwrite a newer Yes. Follow-up keeps its current policy.
4. Leave `__skipped: true` untouched until explicit Resume. Registration Resume saves five No answers. Keep the existing recovery control; add no Skip button.
5. Keep evaluation on the server. Five saved No answers already produce complete status, severity 0 and `no_positive_items`. The existing negative-screen verdict and caution consequently appear by default after save. Preserve pending/stale-preview handling until saved answers match the preview; do not compute a replacement score in the browser.
6. Update the existing browser suite and affected comments, then record actual implementation/verification results in this issue and the S11 handoff.

### Existing draft policy and boundary

Opening an editable registration C-SSRS section will normalize saved Unknown answers to No through ordinary autosave. This is an intentional consequence of applying the requested default to resumed registrations whose server data already contains Unknown; it does not establish that the clinician explicitly answered No. Preserve saved Yes/No values. Test a mixed Unknown/Yes/No draft.

Do not backfill the database or rewrite signed snapshots. Follow-up Unknowns stay unchanged. Registration drafts that never open this section retain existing backend defaults. Removing Unknown across the entire API or changing unvisited assessments would require broader scope than this form request.

## Implementation allowlist — everything that must change

| File | Allowed changes |
| --- | --- |
| `web/src/features/encounters/EncounterPage.tsx` | Pass the registration boolean at the existing C-SSRS mount. |
| `web/src/features/assessments/cssrs/CssrsSection.tsx` | Add the prop, select binary registration options, align all answer normalization/fallback/Resume paths with No, and update nearby contract comments. |
| `web/src/features/assessments/cssrs/api.ts` | Correct the comment describing all worksheets as tri-state. Retain `CssrsAnswerValue` including Unknown for follow-up/backend compatibility. |
| `e2e/cssrs.spec.ts` | Parameterize existing option/default helpers by encounter kind, update registration expectations, retain follow-up assertions, and cover persisted normalization and alerts. |
| `issues/issue-12.md` | Implementation status and actual verification evidence. |
| `project-documents/dev/progress/s11.md` | Append a concise implementation and verification handoff. |
| `project-documents/dev/progress/README.md` | Keep one short current issue/handoff link. |

Do not change backend normalization/evaluation, API schemas, database/migrations, content definitions or release hashes, medical source documents, signed history, proposal logic, wizard routes/guards, autosave infrastructure, other assessments, shared CSS, question wording, dependencies, or unrelated formatting. No new form/component, generic settings system, test framework or backend rewrite is needed. Preserve historical reports and context as historical evidence; do not mark this proposal implemented.

## Acceptance and verification

- Fresh registration step 4: exactly ten radios, two per question, all five No selected, no Unknown controls; keyboard interaction works.
- After autosave, inspect encounter answers and C-SSRS preview: five explicit `"no"` values, complete status, severity 0, `no_positive_items`, no urgent/review flag. Keep the existing negative-screen caution.
- Selecting Yes preserves the other four answers. Levels 1–3 raise clinical review; levels 4–5 raise clinical review and high-risk alert. A higher Yes never changes lower responses.
- Navigation, reload and resume preserve saved Yes/No. Existing mixed Unknown/Yes/No registration data follows the documented normalization policy. No merely hidden Unknown remains in the saved registration payload.
- Legacy skip remains not assessed until Resume; registration Resume saves five No answers. Signed history stays unchanged.
- Follow-up retains fifteen radios, Unknown defaults and existing persistence/skip behavior.
- Run the frontend production build and root TypeScript check, then the existing focused C-SSRS Playwright suite in Chromium and Firefox with app/database available. These API-backed tests should verify persistence and preview as well as controls. Record unavailable checks as pending; no backend test rewrite is needed.

## Implementation and verification

Registration now presents Yes and No, defaulting to No, and normalizes missing or
Unknown answers through the existing autosave path. Existing Yes/No answers are
preserved; a skipped registration remains skipped until Resume, which resets all
five to No. Follow-up retains its tri-state policy. The server remains responsible
for evaluation. The browser suite was updated to cover the controls, persistence,
preview and legacy skip cases.

The frontend production build and root TypeScript check passed. After starting an
isolated app and database, all ten Chromium cases passed across an initial run
(nine passing) and a rerun of the corrected skip fixture. Firefox remains
unverified: launch as root failed because of home-directory ownership, and the
workspace user could not access the installed browser cache. The isolated services
were stopped. An earlier run against an unavailable app failed at authentication;
it did not verify behavior.
