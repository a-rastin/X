# backend/src/x_insight/cases/new_case.py

Source SHA-256: 64fdc6236dd720e079c444366cc9f57696b191668326962c38413619b577ed00
Source commit: 4d1fb8feb782ade19af1f0c1d5d6f8428fd9c80a

## Purpose
Owns backend new-case raw state, immutable content pins, override attribution,
diagnosis assertion, navigation validation and deterministic rule preview.

## Important symbols
- `fresh_draft`: persists explicit defaults and content pins.
- `prepare_autosave`: validates full state, prevents metadata forgery, stamps
  actual override changes and invalidates assertions after diagnosis edits.
- `resolve_pins` / `validate_pins`: verify exact installed model/release and
  assessment/content identities.
- `evaluate_new_case`: returns effective inputs, step errors, lab findings,
  16-factor evidence and deterministic sections.
- `preview` / `assert_diagnosis`: enforce author/workflow/revision boundaries.

## Behavior and constraints
The preview does not generate candidates or interactions, persist proposals or
sign records. Oral sections say `not_generated`; LAI says `model_unavailable`.
The independent candidate helper has no production caller. The frontend rejects
this workflow with an unavailable screen. Do not confuse these backend seams
with a completed clinical journey.

Registration is owned by `cases/patients.py`: its birth-year branch selects this
workflow, but still uses the legacy name validator. Physician settings and v4
signing are absent at this source revision.

## Evidence
See [plan audit](../../../../../../project-documents/dev/progress/new-case-plan-audit-2026-11-09.md)
for package gaps, reproductions and verification limits. Relevant tests are
`tests/assessments/test_new_case.py` and `tests/http/test_new_case.py`.
