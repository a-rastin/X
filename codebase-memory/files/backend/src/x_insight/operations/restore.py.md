# backend/src/x_insight/operations/restore.py

Source SHA-256: 62d100f2c35fe60857619de700f77d11bf44d6d85ccdaf6c8b4b0ea067a587ec
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Staged restore: inspect an uploaded backup without touching live state, gate the destructive
commit behind an exact confirmation digest, then apply it under a maintenance lock with
rollback.

## Important Symbols
- `stage_restore()` — writes only `restore_jobs` + audit; a new successful upload expires prior
  succeeded digests. Staging directory entries are mode 0600 with a 30-minute TTL.
- `validate_archive_bytes()` — traversal/symlink/oversized-expansion/corrupt-hash checks plus
  singleton-identity, patient, reference and model consistency; nothing writes live state.
- `build_confirmation_digest()` — `canonical_hash({"restore_schema": "restore-v1", backup_id,
  archive_sha256, app_schema_version, inventory, artifacts})`; the operator must type this exact
  64-hex value to commit.
- `build_impact_report()` — staged vs live counts and `key_consequence`; S55 always reports
  `commit_available: False`.
- `commit_restore()` — phased: maintenance + **deployment-generation bump** (so in-flight
  provider commits fail `DEPLOYMENT_FENCED`) + pre-restore backup, then leaf-first DELETE /
  root-first INSERT preserving `alembic_version`, `sessions` and `restore_jobs`, then
  revoke all sessions and grants, reconcile pending jobs, verify, patch the report.
- `reopen_restore()` — required after any failure: maintenance is intentionally kept.

## Behavior and Constraints
- The maintenance lock is a **filesystem file independent of either database**; the API
  middleware 503s non-restore writes and the worker returns `maintenance` while it is held.
- Supported application schema versions span `0017`…`0024`; inspection runs under the
  SELECT-only role.
- `RESTORE_COMMIT_FAIL_AT` injects `before_switch` / `during_restart` / `health_check` faults
  for rehearsals.

## Related Files
- `backend/src/x_insight/app.py` — `MaintenanceMiddleware` reads `is_maintenance_active()`.
- `backend/src/x_insight/operations/backup.py` — table order and manifest.
