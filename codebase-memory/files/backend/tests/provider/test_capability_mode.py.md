# backend/tests/provider/test_capability_mode.py

Source SHA-256: cbc9b58cca2d9342dc34d7f206c773dd84e89b58d2750efacf8052f72e770324

## Purpose
Regression coverage for saved, detected, and effective provider capability modes.

## Behavior and Constraints
- Database-backed tests cover saved and historical pinned revisions, detection persistence and clearing on save, and stale test revision rejection.
- Worker resolution tests cover an explicit deployment override; invalid overrides fall back to the saved mode. Restore checks validate mode fields against archive schema versions.
- Isolated detection tests verify schema-first probing, fallback only on explicit format rejection, and rejection of numeric `ready` values in both schema and JSON paths. Settings validation accepts only supported modes.

## Related Files
- `backend/src/x_insight/reasoning/provider.py`: format detection.
- `backend/src/x_insight/reasoning/provider_config.py`: saved and effective modes.
