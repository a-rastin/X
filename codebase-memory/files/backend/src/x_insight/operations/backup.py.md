# backend/src/x_insight/operations/backup.py

Source SHA-256: 72459ba2121314970193ece598d09f1e0927a50848044dbdac0539eb8d2578a0
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Consistent full database backups as a zip archive with a per-table inventory, plus the
authoritative table set that restore replays.

## Important Symbols
- `BACKUP_TABLES` — **32 tables in FK-dependency (root-first) order**; restore INSERTs in this
  order and DELETEs in reverse. No lifecycle/status filters: drafts, failed jobs/attempts,
  resets and adjustments are all exported.
- `export_snapshot_tables()` — reads every table inside **one `REPEATABLE READ` transaction on
  its own connection**; missing tables export empty with a manifest warning rather than failing,
  and each table contributes `{rows, sha256}` (row hashes are canonical for order-independence).
- Manifest version `backup-v1`; bounds 100 MiB archive / 256 MiB file / 512 MiB expanded.
- `.zip.part` → atomic rename; every attempt mints a fresh UUID so retries never overwrite a
  good backup.

## Behavior and Constraints
- **`sessions` is deliberately absent.** The deployment encryption key never lives in the
  database, so it cannot enter the archive; escrowing it is an operator responsibility, and
  restoring without it requires re-entering provider credentials.
- `pg_dump --snapshot` was explicitly rejected in favour of the single repeatable-read read.
- Network XML is read inside the same snapshot; bytes are carried as `__bytes_b64__`.

## Related Files
- `backend/src/x_insight/operations/restore.py` — consumes the manifest and inventory.
- `backend/src/x_insight/reasoning/provider_key.py` — the key excluded from backups.
