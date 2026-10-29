# web/src/features/identity/ResearchWarning.tsx

Source SHA-256: 3fd12353a9c754ec762de2e5e6b73c7d7d49cce6467d2a93ffad1ba5a5a24921

## Purpose
`ResearchWarningGate` presents the server-supplied clinical-use disclaimer before the physician workspace is available.

## Behavior and Constraints
- Uses `useAuth()` for warning content and acknowledgment; this component contains no duplicate disclaimer wording.
- Splits the server text into sentence paragraphs and highlights the second sentence (the validation limitation in the current warning). If the warning changes, review this position-based emphasis.
- Opens a native modal `dialog` with `role="alertdialog"`; initial focus goes to the title. The scrollable reading region and acknowledgment button participate in explicit Tab/Shift+Tab cycling. Escape cancellation is prevented.
- Acknowledgment invokes the existing auth callback. `App.tsx` owns whether the gate mounts and hides physician pages until acknowledgment; `auth.tsx` owns tab-session persistence.

## Related Files
- `backend/src/x_insight/identity/service.py`: authoritative `RESEARCH_WARNING`.
- `web/src/shared/theme.css`: `.xi-research-*` reading surface and persistent footer.
- `project-documents/dev/progress/s05.md`: 2026-10-29 handoff; Chromium/Firefox UI checks used synthetic auth, not a running backend.
