# Issue 06 — Registration repeats Demographics instead of advancing

Date: 2026-10-28  
Status: implemented; browser verification pending

## Requirement

After valid Demographics submission at `#/patients/new`, open **Diagnosis, step 2 of 7**, directly at `#/encounters/<returned-draft-id>#diagnosis`. The user must not need another Continue action.

Currently creation opens `#/encounters/<id>#register`: saved demographics and the now-enabled working note appear, followed by Continue to Diagnosis. This is the same wizard step backed by a persisted draft, rather than another patient creation.

## Root cause and evidence (analysis before implementation)

| Location | Finding |
| --- | --- |
| [RegistrationForm.tsx:110](../web/src/features/patients/RegistrationForm.tsx#L110), `handleSubmit()` | After awaiting `createPatient()`, stages the success notice and explicitly assigns `window.location.hash` to `#/encounters/${result.draft.id}#register` at line 127. This is the immediate cause. |
| [api.ts:157](../web/src/features/patients/api.ts#L157), `createPatient()` | Posts to `/patients`, returning the already-created draft. |
| [patients.py:235](../backend/src/x_insight/cases/patients.py#L235) | Patient, registration encounter and audit event are created atomically. Another create request is unnecessary. |
| [router.ts](../web/src/app/router.ts), `parseHash()` | Selects the encounter route; it does not choose the wizard step. |
| [EncounterPage.tsx:132](../web/src/features/encounters/EncounterPage.tsx#L132), `wizardStepFromHash()` | Honors explicit `register`. Registration's step list puts Register patient first and Diagnosis second. |
| [EncounterPage.tsx:822](../web/src/features/encounters/EncounterPage.tsx#L822) | The register branch renders `RegisteredDemographics` and `DemographicsDraftPanels`. Its normal next-step pager leads to Diagnosis; there is no automatic advance. |
| [registration.spec.ts:101](../e2e/registration.spec.ts#L101) | Explicitly asserts `#register` after successful registration and checks step-1 content. Tests encode the old behavior. |

**Causal chain:** successful create → explicit `#register` destination → parser selects step 1 → persisted demographics render → manual Continue needed.

The mechanism is established by static inspection and matches the reported symptom. It is a successful-submit destination problem; changing the router or creating another draft would not address its cause.

## Historical explanation and evidence limits

Commit `fceecd6` (2026-10-18, `fix(encounters): open new patient registration on the wizard`) deliberately kept successful creation on step 1. The [S07 handoff](../project-documents/dev/progress/s07.md#follow-up-2026-10-18--registration-opens-on-the-wizard) describes this explicitly. The new requirement changes that earlier workflow choice.

The earlier design allowed the working note to become editable after creation on step 1. Today `PendingRegistrationWorkingNote` is a disabled placeholder before creation: there is no editable note payload to transfer on submit. Persisted step 1 remains useful for viewing demographics and editing that note. See [registration-flow.md](../context/registration-flow.md) for historical rationale.

Evidence: expected behavior is confirmed by the supplied requirement; the navigation mechanism is corroborated by code; historical behavior is corroborated by the commit, report and tests. Any additional author intent is unknown. No browser reproduction or runtime tests were performed for this analysis.

## Proposed implementation (historical plan)

1. Change only the successful-submit destination in `RegistrationForm.tsx` to explicit `#diagnosis`, using the returned draft ID. Correct the header comment describing the old destination. Preserve the single POST, notice staging, busy/double-submit protection, validation and error paths.
2. Preserve registration feedback on the new landing page. The existing staged success notice is rendered only inside `RegisteredDemographics`, so a URL-only fix would hide it on arrival. Move its rendering in `EncounterPage.tsx` outside the step-1 branch into the encounter page, remove the nested rendering and now-unused notice prop, and show it once for the matching draft using the existing notice lookup. Preserve identifier, server timestamp and creation revision; do not confuse creation revision with later autosave revisions. Keep the existing in-memory lifecycle rather than introducing storage. Update the notice-location comment in `DemographicsDraftPanels.tsx`.
3. Retain step 1 and its existing Back/wizard navigation. Diagnosis must be step 2; Back to Register patient must show saved demographics and the editable working note. Keep the pre-create note disabled. Preserve the seven-step order and follow-up behavior.
4. Update only affected tests/comments. Explicit `#register` links used to test saved demographics and working-note autosave remain valid; do not globally replace that fragment.
5. Record actual implementation/validation results in this issue and the S07 handoff/index. Reconcile the relevant registration-flow rationale while preserving historical entries.

Do not add an encounter-mount redirect: it would also redirect deliberate visits to persisted step 1. The submit handler already identifies the exact successful-create transition.

## Complete change allowlist

| File | Allowed changes |
| --- | --- |
| `web/src/features/patients/RegistrationForm.tsx` | Successful-create destination and directly related comments. |
| `web/src/features/encounters/EncounterPage.tsx` | Relocate existing success notice so it displays on arrival at Diagnosis; remove obsolete nested rendering/prop. |
| `web/src/features/encounters/DemographicsDraftPanels.tsx` | Correct notice-location comment; retain notice functions and autosave behavior. |
| `e2e/registration.spec.ts` | Assert direct Diagnosis URL, heading, step number/current indicator and success metadata. Check saved demographics/working note after explicit Back. Adapt the effects-fragment test, which currently replaces a trailing `#register`. Add reload and single-create coverage. Preserve validation/error assertions. |
| `e2e/ddi-fresh-install.spec.ts` | Change post-create URL expectation to `#diagnosis`; preserve DDI checks. |
| `e2e/autosave.spec.ts` | Correct header comment claiming successful creation opens `#register`; retain intentional step-1 autosave journeys. |
| `issues/issue-06.md`, `project-documents/dev/progress/s07.md`, `project-documents/dev/progress/README.md`, `context/registration-flow.md` | Narrow status, handoff and rationale updates. |

Additional files require a demonstrated dependency. No backend, API contract, schema, migration, routing framework, styling, labels, dependency or clinical-content changes are needed. Do not remove Demographics, move the working note to Diagnosis, enable pre-create note capture, add browser storage, change transaction/idempotency logic, or refactor shared navigation/autosave.

Keep the existing Diagnosis initialization and Diagnosis-to-PANSS confirmation from issues 05/03 intact. Direct entry must use those existing mechanisms; this task does not change assessment defaults, evaluation, readiness or follow-up semantics. Leave unrelated work untouched.

## Acceptance and verification plan

- One valid Register click goes directly to `#diagnosis`, with **Diagnosis, step 2 of 7**, Diagnosis current in the wizard and no intermediate persisted `#register` stop or extra Continue action.
- Exactly one patient-create POST occurs; no extra encounter-create request occurs. Use the response draft ID. Ignore independent Diagnosis autosave requests when counting creates.
- The registration success notice appears once with server-provided creation metadata. Reload retains the Diagnosis step; existing notice reload behavior need not change.
- Back opens persisted step 1 with unchanged demographics and leading-zero identifier, enabled working note, and no Register submit button. Continue still returns to Diagnosis. Working-note edits flush and survive reload; existing notes/autosave tests cover this.
- Invalid fields keep Register disabled. Duplicate ID, server validation, authorization/session and transport failures retain handling and do not advance. Rapid repeated clicks cannot create a second patient.
- Diagnosis initialization/default Unknown values, save status and guarded Continue to PANSS still work on immediate arrival. Preserve direct persisted `#register` visits, follow-up behavior, seven registration steps and exclusion of adverse effects.
- Run frontend type/build checks and focused Playwright registration, notes, autosave, Diagnosis and fresh-install DDI coverage in configured Chromium/Firefox projects. Use the fresh-install environment for the DDI test. Record blockers honestly; broaden testing only if failures expose dependencies.

## Implementation and verification — 2026-10-28

Successful creation now opens Diagnosis using the returned draft ID. The existing
matching-draft success notice is rendered at encounter level, so its creation
metadata remains visible on arrival. Persisted Demographics and the working note
remain accessible through Back. The affected browser tests now cover the direct
destination, notice, reload, Back and Continue, rapid repeated clicks, and
Diagnosis defaults and confirmation guard.

The frontend production build and root TypeScript check passed; scoped
`git diff --check` passed. Independent standards and specification reviews found
no implementation defects; the specification review identified test gaps that
were subsequently covered. The focused Chromium run could not reach
`localhost:5173` (connection refused), so the browser assertions remain
unverified. Docker was unavailable, preventing the fresh-install DDI environment;
the requested Chromium and Firefox browser suites remain pending. This report is
repository-relative `issues/issue-06.md`; the repository root is already named `x`.
