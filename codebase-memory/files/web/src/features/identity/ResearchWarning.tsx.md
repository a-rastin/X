# web/src/features/identity/ResearchWarning.tsx

Source SHA-256: be946e9892b8aefaed841282d92df750422246028d21f1de6b1313bc17f16442

## Purpose
`ResearchWarningGate` presents the server-supplied clinical-use disclaimer as a blocking modal
for both roles until the acknowledgement is persisted server-side.

## Behavior and Constraints
- Backed by persisted server state via `useAuth()`: `researchWarning`/`researchWarningVersion`
  content plus `warningPending`, `warningError`, and `acknowledgeWarning`. No duplicate
  disclaimer wording; no tab-session-only acknowledgement.
- Splits the server text into sentence paragraphs and highlights the second sentence (the
  validation limitation in the current warning). If the warning changes, review this
  position-based emphasis.
- Opens a native modal `dialog` with `role="alertdialog"`; initial focus goes to the title.
  Re-opens on text-or-version change, guarded by `dialog.open`. The scrollable reading region
  and enabled controls participate in explicit Tab/Shift+Tab cycling (disabled buttons are
  skipped). Escape cancellation is prevented.
- The acknowledge button disables while pending with an "Acknowledging…" label; server errors
  render inline with `role="alert"`. `App.tsx` owns whether the gate mounts; `auth.tsx` owns
  the versioned POST and the 409 stale-display refresh.

## Related Files
- `web/src/features/identity/auth.tsx` — acknowledgement state machine and login/logout race guards.
- `web/src/features/identity/api.ts` — `acknowledgeResearchWarning(version)`.
- `backend/src/x_insight/identity/service.py`: authoritative `RESEARCH_WARNING`.
- `web/src/shared/theme.css`: `.xi-research-*` reading surface and persistent footer.
