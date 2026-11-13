# web/src/features/encounters/EncounterPage.tsx

Source SHA-256: ce223f1116e34e8245dfcaec3192413cde9517b3f4500c1c523ad7eec55c2ba1

## Purpose
The wizard shell: step machine, step navigation, autosave wiring, discard, and the
author-only denial view.

## Important Symbols
- `REGISTRATION_WIZARD_STEPS` — register, diagnosis, panss, cssrs, history, medications,
  proposal-review. `FOLLOW_UP_STEPS` drops `register` and inserts `effects` before medications.
- `wizardStepFromHash()` — parses `#/encounters/:id#<step>`; an unknown or missing fragment
  falls back to Diagnosis so old draft links keep working.
- `diagnosisConfirms()` — registration Diagnosis Continue consults the preview: direct
  navigation only when status is `complete` and overall is `criteria_satisfied`; every other
  state asks for confirmation and records no bypass or skip sentinel.
- Forward step moves use native `<button>` actions (not anchors) so a modified click cannot
  bypass the confirmation/flush handler; `saveBeforeNavigation()` flushes before leaving.
- Registration step 1 shows saved demographics only — the working-note panel was removed
  (2026-11-03); saved note data is still preserved, never prompted, never sent to
  prompts/projections/inference/DDI.
- Two separate `hashchange` listeners — one unsaved-changes guard that restores the previous
  draft hash when the user stays, one for back/forward that also flushes.

## Behavior and Constraints
- A 403 renders a denied view with no clinical content (`encounter-denied`); author-only draft
  access is enforced server-side.
- Step links are real anchors so the shared handler and flush work; the deliberate exception is
  registration Diagnosis→PANSS, a `<button>`, because a modified click on an anchor would bypass
  the confirmation handler.
- The heading is literally `"Add New Patient"` on diagnosis/panss/cssrs/history steps, and
  several conditions depend on that same four-step list.

## Related Files
- `web/src/features/encounters/useAutosave.ts`, `DemographicsDraftPanels.tsx`.
- `e2e/panss.spec.ts` — asserts "Continue navigates without completing the assessment".

## Version dispatch and confirmation recovery
`EncounterPage` dispatches `new-case-v1` to `NewCaseWizard` and `follow-up-v1` to
`FollowUpWizard`; only `legacy-v1` enters the original step machine. Unknown
versions fail closed. On a draft-read 404 it tries the author-owned follow-up
proposal endpoint: `signed_patient_id` redirects to the signed chart after a lost
confirmation response. Missing/discarded/other workflows keep the normal error.
The step constants and diagnosis behavior above describe the legacy branch.
