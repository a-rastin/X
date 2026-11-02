# web/src/features/identity/api.ts

Source SHA-256: fc76e03c325225ddca2254d5ecfd46c9d2d5f89219c08c592aa7bad208763bcd

## Purpose
The de-facto shared HTTP client. Every other `features/*/api.ts` (~15 copies) duplicates this
`request<T>` rather than importing it — a real invariant when adding a call.

## Important Symbols
- `ApiError {status, code, message, fieldErrors, researchWarning, researchWarningVersion}` —
  thrown on any non-2xx from the server's `{code, message, field_errors}` body. The warning
  fields are populated only from a 409 `RESEARCH_WARNING_CHANGED` body (`research_warning`,
  `research_warning_version`).
- `LoginResult` / `MeResult` — both carry `research_warning` + `research_warning_version`
  (pending disclaimer text/version, null when acknowledged).
- `acknowledgeResearchWarning(version)` — `POST /me/research-warning-acknowledgement` with
  CSRF and `{version}` of the DISPLAYED text; returns the still-pending text/version
  (null/null once acknowledged).
- `CSRF_HEADER = "X-CSRF-Token"`, stored in **`localStorage`** under `xi_csrf_token` and read
  on every mutation, because the HttpOnly session cookie is shared across browser tabs. A
  legacy `sessionStorage` copy is cleared on upgrade.
- `request<T>` — always `credentials: "include"`; mutations add `If-Match: "<revision>"` and a
  fresh `Idempotency-Key` per attempt.
- `getCsrfToken` / `setCsrfToken`, `newIdempotencyKey`.

## Behavior and Constraints
- No client-side request-id or correlation header is generated anywhere in the app; the request
  id is only *displayed* from server audit rows.
- The token is adopted from the login, `me` and password-change responses.

## Related Files
- `web/src/features/identity/auth.tsx` — session state, `sessionExpired()`, and the 409
  stale-display refresh.
- `web/src/features/encounters/useAutosave.ts` — the heaviest consumer.
