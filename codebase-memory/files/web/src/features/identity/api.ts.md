# web/src/features/identity/api.ts

Source SHA-256: 1e27e2ce6d00c7e67c509a4bb4be2e64b16baca10a089b230eba3a844b477ebe
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
The de-facto shared HTTP client. Every other `features/*/api.ts` (~15 copies) duplicates this
`request<T>` rather than importing it — a real invariant when adding a call.

## Important Symbols
- `ApiError {status, code, message, fieldErrors}` — thrown on any non-2xx from the server's
  `{code, message, field_errors}` body.
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
- `web/src/features/identity/auth.tsx` — session state and `sessionExpired()`.
- `web/src/features/encounters/useAutosave.ts` — the heaviest consumer.
