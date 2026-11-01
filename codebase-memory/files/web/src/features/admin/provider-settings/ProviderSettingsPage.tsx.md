# web/src/features/admin/provider-settings/ProviderSettingsPage.tsx

Source SHA-256: 2e4a36a26183cadf412fc31b2e0a2a62a7377ca8f86c51e01570470a56bbcb10

## Purpose
Admin form for saved provider settings, endpoint approvals, key management, and capability testing.

## Behavior and Constraints
- `ProviderSettingsPage` keeps the editable mode separate from the saved and effective modes. It shows deployment override and last detected mode when available; no effective mode is claimed before a saved revision.
- Test uses the saved revision, leaves the editable selection intact, and does not select the detected mode. Testing is disabled before the first save; saving resets the diagnostic and clears the typed key.
- Stale revision errors require reload. The client route guard hides this page from non-admins, but authorization remains server-side.

## Related Files
- `web/src/features/admin/provider-settings/api.ts`: settings API calls and types.
- `e2e/provider-capability-mode.spec.ts`: mocked browser coverage.
