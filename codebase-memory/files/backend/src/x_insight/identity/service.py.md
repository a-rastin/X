# backend/src/x_insight/identity/service.py

Source SHA-256: 0a4104529d65d16ac544ecb1c7fbb197538ca79d95c92abff61b5073539016bb
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Authentication and account administration primitives: admin seeding, opaque sessions, CSRF
tokens, login throttling, draft-set revisions, and physician deactivation.

## Important Symbols
- `ensure_default_admin()` — seeds the singleton admin **once**; exits early if any admin row
  exists, so changing the password never resurrects the default at next startup.
- `create_session()` / `get_session_user()` — 32-byte opaque cookie token stored only as a
  SHA-256; privileges always come from the stored row, never the login-selected role.
  **Sessions have no timeout** (stated NFR-02); revoked, inactive, or
  `credential_revision`-mismatched sessions are rejected.
- Login throttling — max 5 failures / 60 s per (client IP, normalized username), in-memory by
  design for a single host; `clear_login_throttle()` exists for tests.
- `deactivate_physician()` — locks the user row, then either `retain` (drafts keep occupying
  their slot) or `discard` (requires the exact reviewed draft-set revision); bumps
  `credential_revision` and revokes sessions. Admin cannot be deactivated.

## Behavior and Constraints
- Three distinct revision notions collide nowhere but must not be confused:
  `users.revision` (profile concurrency), `users.credential_revision` (kills all sessions),
  and the **draft-set revision** — a canonical hash over an author's open drafts, 0 when empty.
- Idempotency is stored per `(operation, actor)`: same key + same body replays, changed body → 409.

## Related Files
- `backend/src/x_insight/identity/router.py` — HTTP layer, CSRF comparison, cookie flags.
- `backend/src/x_insight/cases/encounters.py` — the drafts that the draft-set revision hashes.
