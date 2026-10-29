# web/src/features/encounters/EncounterPage.tsx

Source SHA-256: 560ed4b11748b5541365270f1da5186298decdcfb478e50fa4fa64ea5ad2268a
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

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
