# backend/src/x_insight/operations/audit.py

Source SHA-256: eaf2acde9fa18b37bf1f7b51ca5d24a543f9cf491c17c76ce2d081c550b997f2

## Purpose
Append-only audit trail with canonical hashing, surfaced in the admin audit view.

## Important Symbols
- `audit_payload_hash(details)` — **`canonical_hash({"schema_version": 1, "details": details})`**:
  always the versioned envelope, never the bare details object.
- `record_audit()` — inserts and flushes, **never commits**: the audit row lands or rolls back
  with the caller's mutation. No UPDATE/DELETE helpers exist anywhere in the module.
- `list_audit_events()` / `count_audit_events()` — filter on actor, operation, time window and
  JSONB targets (`patient_id`, `encounter_id`, `question_run_id`, `batch_id`, `question_key`);
  newest-first ordering by `(occurred_at DESC, id DESC)`.
- `safe_event()` — surfaces `actor_id` / `actor_display` for the UI.

## Behavior and Constraints
- Operation-name constants at the module head are shared with the worker (for example
  `generation.run.success`, `cpt_retry.requested`, `backups.*`, `restores.*`, `exports.*`).
- Because services never commit, calling `record_audit` inside a caller's `session_scope` is
  what makes audit and mutation atomic.

## Related Files
- `backend/src/x_insight/contracts.py` — `canonical_hash`.
- `backend/src/shared/..` n/a; admin UI in `web/src/features/admin/audit/AuditPage.tsx`.
