# backend/src/x_insight/operations/restore.py

Source SHA-256: cd0f4a7e07c9b627c3dd52aa0996060be6a9f9b2ecaa0d6b387f13051a487a95
Source: working tree (includes uncommitted changes)

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
- Supported application schema versions span `0017`…`0032`. Provider-mode and detection
  fields are validated against the archive version; old rows restore with safe defaults.
- `users.research_warning_acknowledged_version` (0029): `_check_research_warning_acknowledgements`
  requires the key on 0029 archives (text or null) and rejects any non-null value in older
  archives, so 0028 accounts restore unacknowledged. Checked on validation and again at commit.
- `RESTORE_COMMIT_FAIL_AT` injects `before_switch` / `during_restart` / `health_check` faults
  for rehearsals.

## Related Files
- `backend/src/x_insight/app.py` — `MaintenanceMiddleware` reads `is_maintenance_active()`.
- `backend/src/x_insight/operations/backup.py` — table order and manifest.

## Versioned workflow validation
Supported schema versions now include `0032`. `_check_new_case_state` dispatches
typed follow-up records to `_check_follow_up_state`; absent new tables/references
remain safe for older archives. `_check_follow_up_proposals` checks raw shapes,
content/assessment/model/DDI identities, fingerprint consistency, recomputed completeness/decisions, supported
outcomes and the exactly-one v5 signed-reference variant. Baseline and historical
lab sources must belong to the same patient and retain their immutable hashes.
Both staging and commit run the structural validators. Public mixed-v5 commit
acceptance remains a separate Package H drill.
