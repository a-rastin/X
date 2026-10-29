# web/src/app/router.ts

Source SHA-256: d325a96afdbfcbd911c148fa549729e3f44d4194fe8d7a17bdfc530022737e90
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
The hand-rolled hash router — **there is no react-router** (web dependencies are only
`react` and `react-dom`).

## Important Symbols
- `Route` — 17 static route names plus a static `HASHES` map.
- `parseHash()` — also recognises the parametric `#/encounters/:id`,
  `#/patients/:id/chart` and `#/patients/:id/report`.
- `useRoute()` — listens to `hashchange`; navigation is an assignment to
  `window.location.hash`.
- `encounterHash()`, `chartHash()`, `reportHash()`, `encounterIdFromHash()`,
  `patientIdFromHash()`.

## Behavior and Constraints
- Wizard steps use a **second** `#` fragment: `#/encounters/<id>#<step>`.
- Quirk worth knowing: `routeToHash("encounter")` returns the bare `#/encounters`, so callers
  build encounter links with `encounterHash(id)` from `features/encounters/api.ts`.

## Related Files
- `web/src/app/App.tsx` — consumes the route.
- `web/src/features/encounters/EncounterPage.tsx` — parses the step fragment.
