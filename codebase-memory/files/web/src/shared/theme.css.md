# web/src/shared/theme.css

Source SHA-256: 0167351d6f09ce23ff6c1645e95db49f93d367160d2e7f33e995bab35123c977

## Purpose
Defines semantic light/dark theme tokens and the shared `.xi-*` UI classes. This summary covers the research disclaimer styling investigated during the 2026-10-29 redesign.

## Behavior and Constraints
- `.xi-research-dialog[open]` uses a vertical flex layout, capped at 720px wide and the dynamic viewport height minus outer margins; its native backdrop dims the surrounding workspace.
- Header and footer do not shrink. `.xi-research-body` has `min-height: 0` and independent vertical scrolling, keeping acknowledgment visible on short screens.
- `.xi-research-limitation` uses warning background/border tokens while preserving ink text; the document icon uses the accessible primary-action token.
- At 560px and below, typography/padding reduce and the footer stacks with a full-width action. Both themes reuse existing semantic colors.
- Other dialogs retain the separate `.xi-modal` styles.

## Related Files
- `web/src/features/identity/ResearchWarning.tsx`: native modal and content markup.
- `context/ui-contrast.md`: rationale for accessible action colors.
- `project-documents/dev/progress/s05.md`: responsive and theme UI verification scope.
