# backend/src/x_insight/identity/router.py

Source SHA-256: b1d38f2f8d6e52b89649f89416e96038c2bf230986326150acb4e9836f0a8892
Source: working tree (includes uncommitted changes)

## Purpose
HTTP routes for login, logout, `GET /me`, own password and preferences, the research-warning
acknowledgement, and the admin physician-account routes, all under `/api/v1`.

## Important Symbols
- `login()` / `me()` — return `research_warning` and `research_warning_version` from
  `service.research_warning_fields(user)` for the stored account (both roles).
- `me(optional)` — `GET /me?optional=true` is the opt-in anonymous probe (issue 29):
  missing/invalid/revoked/inactive session answers 200 `{"user": null}` instead of 401,
  so signed-out first paint makes no failed request. Default path keeps the 401 contract;
  the flag never authenticates anything.
- `acknowledge_research_warning()` — `POST /me/research-warning-acknowledgement`, body
  `{"version": str}` (`extra` forbidden, max 100 chars). Session + CSRF gate via `_check_csrf`
  (401 `UNAUTHENTICATED` / 403 `FORBIDDEN`). Empty version → 422 `VALIDATION_FAILED`. A version
  different from `RESEARCH_WARNING_VERSION` → 409 `RESEARCH_WARNING_CHANGED`. Match → 200.
- `_check_csrf()` — shared mutation gate used by every state-changing identity route.

## Behavior and Constraints
- 200 body of the acknowledgement: `{"research_warning": null, "research_warning_version": null}`.
- 409 body: standard `ErrorBody` fields (`code`, `message`, `field_errors`, `request_id`,
  `retryable: false`) plus the account's current pending `research_warning` and
  `research_warning_version`. Those two are null when the account already acknowledged the
  current version.
- Only the session user's row changes; no credential/profile revision bump and no audit row.
- The router uses `CommittedRoute`, so a successful write commits before the response is sent.

## Related Files
- `backend/src/x_insight/identity/service.py` — warning state, acknowledgement write, sessions.
- `backend/src/x_insight/identity/tables.py` — `users` columns including the acknowledged version.
- `backend/tests/http/test_identity.py` — slice 5 covers the contract and its denials.
