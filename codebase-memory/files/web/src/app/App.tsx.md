# web/src/app/App.tsx

Source SHA-256: 0fc698a58c811e4eb744d6f206e15094029ad046bb9a2b57b3a10ae41c668e9b
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Root component: resolves the hash route, guards roles, renders role-scoped navigation and the
page switch, and wraps the parametric routes.

## Important Symbols
- Route resolution (`:158-165`) — anonymous always lands on `login`; an authenticated user is
  never left sitting on `login`; otherwise the hash route wins. The comments stress these
  guards are a *client-side complement* to the server's authority.
- `NavLink` — blocks navigation through the global unsaved-changes guard.
- Admin-only nav links: physicians, networks, audit, exports, backups, restores,
  provider-settings, status.
- Parametric wrappers `EncounterRoute`, `ChartRoute`, `ReportRoute` — each owns its own
  `hashchange` listener and uses `key={id}` so switching ids remounts cleanly.

## Behavior and Constraints
- Roles come only from `GET /me`; the login form's role select must match the stored role.
- The physician research-warning gate is layered on top of routing before any page renders.

## Related Files
- `web/src/app/router.ts` — hash parsing and navigation.
- `web/src/app/pages.tsx` — role-guard page wrappers.
- `web/src/features/identity/auth.tsx` — the session status this component consumes.
