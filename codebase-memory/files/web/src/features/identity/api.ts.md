# web/src/features/identity/api.ts

Source SHA-256: 27f4b1daca72887a5b6e1db25a4ffa3be89f44338068e8adc2b33a2f32c5aba5

## Purpose
Shared HTTP transport. The reasoning explorer imports request<T> and passes an optional AbortSignal.

## Important Symbols
- `ApiError {status, code, message, fieldErrors, researchWarning, researchWarningVersion}` —
  thrown on any non-2xx from the server's `{code, message, field_errors}` body. The warning
  fields are populated only from a 409 `RESEARCH_WARNING_CHANGED` body (`research_warning`,
  `research_warning_version`).
- `LoginResult` / `MeResult` — both carry `research_warning` + `research_warning_version`
  (pending disclaimer text/version, null when acknowledged).
- `me()` — calls `/me?optional=true` and discriminates on `result.user === null`
  (`AnonymousMeResult {user: null}`) so a signed-out first paint makes no failed
  request. Only an authenticated response may write the CSRF token; localStorage
  never decides whether a session exists.
- `acknowledgeResearchWarning(version)` — `POST /me/research-warning-acknowledgement` with
  CSRF and `{version}` of the DISPLAYED text; returns the still-pending text/version
  (null/null once acknowledged).
- `CSRF_HEADER = "X-CSRF-Token"`, stored in **`localStorage`** under `xi_csrf_token` and read
  on every mutation, because the HttpOnly session cookie is shared across browser tabs. A
  legacy `sessionStorage` copy is cleared on upgrade.
- `request<T>` — always `credentials: "include"`; callers provide any revision or
  idempotency headers required by their endpoint.
- `getCsrfToken` / `setCsrfToken`, `newIdempotencyKey`.

## Behavior and Constraints
- No client-side request-id or correlation header is generated anywhere in the app; the request
  id is only *displayed* from server audit rows.
- The token is adopted from the login, `me` and password-change responses.

## Related Files
- `web/src/features/identity/auth.tsx` — session state, `sessionExpired()`, and the 409
  stale-display refresh.
- `web/src/features/encounters/useAutosave.ts` — the heaviest consumer.
