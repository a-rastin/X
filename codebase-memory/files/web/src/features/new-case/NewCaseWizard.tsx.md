# web/src/features/new-case/NewCaseWizard.tsx

Source SHA-256: 7a386c4a71cc48947ff6c9080ccb367a4bedcc0a1d75bf784f091f792a5e149b

## Purpose
Coordinates the New case encounter wizard, autosave, server validation, proposal generation, and final signing navigation.

## Behavior and Constraints
- The ordered steps are diagnosis, PANSS, harm, history, medications, considerations, and proposal; navigation passes through existing save and validation logic.
- The existing patient fetch supplies the visible patient context and sex; no additional patient request is needed for the context header.
- The proposal view keeps the original and physician plan separate; its responsive layout is in `new-case.css`.

## Related Files
- `web/src/features/new-case/NewCaseSections.tsx`: clinical inputs and medication selection.
- `web/src/features/new-case/NewCasePlanPage.tsx`: immutable proposal and editable final plan.
- `web/src/features/encounters/useAutosave.ts`: revision-aware draft persistence.
