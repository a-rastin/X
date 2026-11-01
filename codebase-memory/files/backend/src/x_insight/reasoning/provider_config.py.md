# backend/src/x_insight/reasoning/provider_config.py

Source SHA-256: 8bad78141d828bce72347608e8277c923e56b858d821fa681a1c9d3119385a09
Source: working tree (includes uncommitted B05 changes)

## Purpose
Admin provider settings: encrypted API-key revisions, a masked HTTP API, and capability
diagnostics. Mounted at `/api/v1/api-settings*` under the `/api/v1` prefix.

## Important Symbols
- `save_settings()` — destination allowlist check → `If-Match` revision fence (412) → secret
  action in {unchanged, replace, clear} → inserts the **next revision** (`latest + 1`) with an
  AES-GCM sealed key on replace. The masked placeholder can never be stored.
- `open_runtime(session, revision)` → `(approved_endpoint, model, api_key)` or a non-retryable
  error code; the key is never logged.
- `latest_settings_revision()`, `record_capability_test()` (`untested|verified|failed`).
- Routes: `PUT/GET /api-settings`, `POST /api-settings/test`, `GET /api-settings/revisions/{n}`;
  admin-only.

## Behavior and Constraints
- Replacing or clearing the key **cancels active generation jobs**; the environment endpoint is
  only a fallback for jobs queued before any settings existed and sends no bearer token.
- Later questions in a batch reuse the revision the batch pinned, so an "unchanged" save that
  changes the URL cannot hijack running work. A valid `X_INSIGHT_PROVIDER_CAPABILITY` overrides
  the revision's saved mode; otherwise the pinned mode is used.
- A capability test appends a revision and accepts an optional `If-Match` fence. Its stored
  status covers the generic tool probe; the separately detected mode is nullable and persists
  on the tested revision. A later save starts untested with no detection.
- The AES-GCM deployment key lives outside the database (`provider_key.py`), optionally at
  `X_INSIGHT_PROVIDER_DATA_KEY_FILE`; it is excluded from backups and must be escrowed to keep
  encrypted settings recoverable.

## Related Files
- `backend/src/x_insight/reasoning/provider_key.py`, `provider_destination.py`.
- `web/src/features/admin/provider-settings/ProviderSettingsPage.tsx`.
