# web/src/app/App.tsx

Source SHA-256: 84c72914620c7b13201f376ea351fcf1d26ef1f5b4c071cd6aca7a25c8b18d5b

## Purpose
Root component: resolves the hash route, guards roles, renders role-scoped navigation and the
page switch, and wraps the parametric routes.

## Important Symbols
- `showWarningGate` (`:176-177`) — `status === "ready" && user !== null && !warningAcked`.
  Applies to **both** roles (Issue 24 removed the physician-only check). While set,
  `ResearchWarningGate` mounts and every page stays hidden behind `!showWarningGate`.
- `warningAcked` from `useAuth()` — true when the server reports no pending warning text for
  the session user (`researchWarning === null`).
- `NavLink` — blocks navigation through the global unsaved-changes guard.
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
