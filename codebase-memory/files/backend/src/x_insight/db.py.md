# backend/src/x_insight/db.py

Source SHA-256: f8aafcd62c9293a6a6311bceb779b651a321c40e2f1cd78892d9e7c5b7561d88
Source: working tree (includes uncommitted changes)

## Purpose
Engine lifecycle, the schema-version pin, the single transaction scope, and the readiness
check — plus `CommittedRoute`, which makes every mutation visible before its response.

## Important Symbols
- `get_engine()` — lazy process-wide singleton; **import never connects**.
  `reset_engine()` disposes it. Pool 5/5, `pool_pre_ping`, `connect_timeout=3`.
- `EXPECTED_SCHEMA_VERSION = "0029"` and `check_readiness()` — ready only when applied
  Alembic versions equal `["0029"]`; otherwise `SCHEMA_INCOMPATIBLE` (not retryable) or
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
