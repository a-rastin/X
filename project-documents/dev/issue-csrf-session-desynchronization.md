# Issue: CSRF token desynchronizes from the browser session

Status: fixed on `dev`
Severity: medium
Priority: high
Affected area: authenticated frontend mutations
Observed on: Provider settings save (`PUT /api/v1/api-settings`)

## Summary

An authenticated page can hold a CSRF token that is absent or belongs to a
different session than the shared HttpOnly session cookie. The page still
appears signed in because `GET /api/v1/me` accepts the cookie, but the next
mutation fails with `403 FORBIDDEN` and `CSRF validation failed.`

This is not a provider URL, model, API-key, deployment-key, or allowlist error.
The provider mutation checks CSRF before it validates any provider setting.

## Impact and rating

The security boundary fails closed: no unauthorized mutation is accepted.
However, the defect can block all authenticated write actions that use the
shared identity client, including provider administration, physician account
changes, patient and encounter writes, notes, medications, plans, backups,
restores, and network administration.

Severity is medium because there is no demonstrated confidentiality or
integrity bypass and signing in again recovers the tab. Priority is high because
the failure affects a broad mutation surface, presents as an unexplained
security error, and is reachable through ordinary multi-tab use.

## Root cause

- The session identifier was an HttpOnly cookie shared by same-origin browser
  tabs.
- The matching CSRF token was held in memory with a tab-local `sessionStorage`
  backup under `xi_csrf_token`.
- Startup restored the user through `GET /api/v1/me`, but that response did
  not provide the CSRF token for the current cookie.
- A tab could therefore be authenticated without a CSRF token. If another tab
  signed in, it could also replace the shared cookie while the first tab retained
  the old session's CSRF token.
- Mutation clients omitted `X-CSRF-Token` when no token existed; the backend
  correctly rejected an absent or mismatched token.

Relevant code:

- `web/src/features/identity/api.ts`: token storage, login, `/me`, and mutation
  header construction.
- `web/src/features/identity/auth.tsx`: authenticated startup through `/me`.
- `web/src/features/admin/provider-settings/api.ts`: provider mutation header.
- `backend/src/x_insight/reasoning/provider_config.py`:
  `_require_admin_mutation` and `put_api_settings`.

## Reproduction

### Missing token in a new tab

1. Sign in as an administrator in one browser tab.
2. Open the application directly in a separate same-origin tab that does not
   inherit the first tab's `sessionStorage`.
3. Observe that the second tab becomes authenticated through `/api/v1/me`.
4. Open Provider settings, enter valid values, and select Save.
5. Observe `403 FORBIDDEN` with `CSRF validation failed.`

### Stale token after another login

1. Sign in in tab A.
2. Sign in again in tab B. The browser now sends tab B's session cookie for
   both tabs.
3. Return to tab A and perform an authenticated mutation.
4. Observe that tab A sends its old CSRF token with tab B's session cookie and
   receives the same 403 response.

For a deterministic automated setup, seed a valid session cookie while leaving
`xi_csrf_token` empty, or pair the cookie for one test session with the CSRF
token from another.

## Expected behavior

When the UI reports an authenticated session as ready, it has the CSRF token
for that exact session. Opening another tab or replacing the cookie in another
tab must not leave the page apparently usable while all writes fail with the
raw backend CSRF message.

## Acceptance criteria

- A tab starting with a valid session cookie and no tab-local CSRF token can
  complete an authenticated mutation without signing in again.
- A tab whose token no longer matches the shared cookie safely resynchronizes
  or moves to an explicit sign-in-required state before a mutation is lost.
- `X-CSRF-Token` remains mandatory and is still compared with the token bound
  to the presented session; the fix must not weaken or disable CSRF checks.
- Logout, password-change session rotation, expired/revoked sessions, and
  unavailable browser storage continue to behave safely.
- Browser regression coverage proves the missing-token and stale-token cases;
  at least one case saves Provider settings and one exercises the shared
  identity mutation client.
- Provider validation errors remain distinct and occur only after successful
  authentication and CSRF validation.

## Resolution

- Authenticated `GET /api/v1/me` now returns the CSRF token bound to the
  presented session cookie and marks the response `private, no-store`.
- Authenticated startup adopts that token; a 401 clears local identity and CSRF
  state.
- The frontend stores the token in origin-shared `localStorage`, matching the
  scope of the shared session cookie, and reads it for every mutation so a
  login or session rotation in another tab is observed immediately.
- The obsolete `sessionStorage` copy is removed during synchronization.
- Backend mutation checks are unchanged: an absent or mismatched token still
  receives `403 FORBIDDEN`. The session cookie remains HttpOnly.

## Workaround before the fix

Sign out, close or stop using other application tabs, then sign in and perform
the mutation in the same tab. Reloading alone may retain the stale tab-local
token and was not a reliable recovery. This workaround is no longer required
with the fixed frontend and backend deployed together.

## Verification

- `backend/tests/http/test_identity.py`: 22 passed against the configured
  PostgreSQL test database. The `/me` test proves the returned token matches
  login, the response is `private, no-store`, and the token authorizes a real
  mutation.
- `npm --prefix web run build`: passed (TypeScript plus Vite production build).
- Ruff check and format check passed for the changed backend source and test.
- Provider missing-token startup plus save/test/clear passed in Chromium and
  Firefox against isolated ports 8010/5174.
- Cross-tab cookie replacement plus a physician-creation mutation from the
  first tab passed in Chromium and Firefox.
- The first combined Firefox run timed out during the second password hash
  while two workers shared the test database; the same cross-tab test passed
  when rerun alone. No application error was present in the timed-out page.
- Root `npx tsc --noEmit` remains blocked only by the pre-existing
  `e2e/signing.spec.ts:848` TS2352 error; it reported no new error in the
  changed tests.
- The full backend and browser suites were not run.
