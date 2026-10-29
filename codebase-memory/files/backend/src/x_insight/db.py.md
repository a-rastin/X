# backend/src/x_insight/db.py

Source SHA-256: a52fa827083e67d148ef90e89a580026fd22627997b5284a504366359c5936cb
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Engine lifecycle, the schema-version pin, the single transaction scope, and the readiness
check — plus `CommittedRoute`, which makes every mutation visible before its response.

## Important Symbols
- `get_engine()` — lazy process-wide singleton; **import never connects**.
  `reset_engine()` disposes it. Pool 5/5, `pool_pre_ping`, `connect_timeout=3`.
- `EXPECTED_SCHEMA_VERSION = "0024"` and `check_readiness()` — ready only when applied
  Alembic versions equal `["0024"]`; otherwise `SCHEMA_INCOMPATIBLE` (not retryable) or
  `DB_UNAVAILABLE` (retryable).
- `session_scope()` — the only commit-or-rollback context; `get_session` publishes it on
  `request.state.db_session`.
- `CommittedRoute` — commits off-loop (`run_in_threadpool`) after the handler returns and
  before the response is sent.

## Behavior and Constraints
- All persistence is **SQLAlchemy Core DML**, which never populates `session.new/dirty`;
  a dirty-check would therefore be useless. Services never commit — the caller's
  `session_scope` (or `CommittedRoute`) owns the transaction, keeping audit + mutation atomic.
- Roles are separate: `x_insight_app`, `x_insight_migrate`, `x_insight_readonly`.
- URLs are normalized to `postgresql+psycopg://`; the test database defaults to port 5433.

## Related Files
- `backend/src/x_insight/operations/migrate.py` — `alembic upgrade head` + DDI bootstrap.
