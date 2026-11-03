# web/src/app/App.tsx

Source SHA-256: 5767b78e8810a001a8a1d409ceaad79d0fe13eb1c8d845d0bbf6a61b8982e8b6

## Purpose
Root component: resolves the hash route, guards roles, renders role-scoped navigation and the
page switch, and wraps the parametric routes.

## Important Symbols
- `showWarningGate` (`:176-177`) — `status === "ready" && user !== null && !warningAcked`.
  Applies to **both** roles (Issue 24 removed the physician-only check). While set,
  `ResearchWarningGate` mounts and every page stays hidden behind `!showWarningGate`.
- `warningAcked` from `useAuth()` — true when the server reports no pending warning text for
  the session user (`researchWarning === null`).
- `NavLink` — plain hash navigation; the unsaved-changes barrier lives in
  `saveBeforeNavigation()` (`app/navigationGuard.ts`), not in the link.
- Sign-out flushes via `saveBeforeNavigation()` first so draft edits are not stranded.
- Admin-only nav links: physicians, networks, audit, exports, backups, restores,
  provider-settings, status.
- Parametric wrappers `EncounterRoute`, `ChartRoute`, `ReportRoute` — each owns its own
  `hashchange` listener and uses `key={id}` so switching ids remounts cleanly.

## Behavior and Constraints
- Route guards complement the server checks: anonymous users always land on login,
  authenticated users never sit on it. Authority stays server-side; these only choose what
  to render.
- Roles come only from `GET /me`; the login form's role select must match the stored role.

## Related Files
- `web/src/app/router.ts` — hash parsing and navigation.
- `web/src/app/pages.tsx` — role-guard page wrappers.
- `web/src/features/identity/auth.tsx` — the session and warning-acknowledgement state this
  component consumes.
- `web/src/features/identity/ResearchWarning.tsx` — the blocking gate mounted while unacknowledged.
