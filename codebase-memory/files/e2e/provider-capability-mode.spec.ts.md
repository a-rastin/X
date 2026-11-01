# e2e/provider-capability-mode.spec.ts

Source SHA-256: 6aee9d0bfcf6e1b526466d01608ff341793dd9256b151a725226cd97e7a59d07

## Purpose
Mocked Playwright coverage of provider capability mode settings.

## Behavior and Constraints
- Exercises an unsaved selection differing from the saved mode, saved versus effective mode, deployment override, empty test request and revision precondition, detection, save, and reload persistence.
- A no-saved-settings scenario verifies that no effective mode is claimed and Test is disabled. API routes are mocked; these tests do not establish live-provider or worker behavior.

## Related Files
- `web/src/features/admin/provider-settings/ProviderSettingsPage.tsx`: admin form under test.
- `backend/tests/provider/test_capability_mode.py`: backend capability regressions.
