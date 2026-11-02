# backend/src/x_insight/identity/tables.py

Source SHA-256: c86377d054dca5c1631ca77ffd4eeb5dc8cf2b6c34f5cb88e2e7405f7ac19311
Source: working tree (includes uncommitted changes)

## Purpose
SQLAlchemy Core definitions for the identity tables: `users`, `sessions`, and
`idempotency_records`, plus their indexes.

## Important Symbols
- `users` — includes `research_warning_acknowledged_version` (text, nullable; migration 0029).
  NULL or any value different from `service.RESEARCH_WARNING_VERSION` means the research
  warning is pending for that account.
- `sessions` — hashed opaque tokens, CSRF token, credential-revision snapshot; no timeout.

## Behavior and Constraints
- Migrations own DDL; this module must match the migrated columns (`EXPECTED_SCHEMA_VERSION`).
- Public account shapes (`_public_user`, `safe_physician`) pick fields explicitly, so the
  acknowledged version is never exposed through them.

## Related Files
- `backend/migrations/versions/0029_research_warning_acknowledgement.py` — adds the column.
- `backend/src/x_insight/identity/service.py` — reads and writes the column.
