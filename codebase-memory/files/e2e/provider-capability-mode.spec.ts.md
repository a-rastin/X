# e2e/provider-capability-mode.spec.ts

Source SHA-256: fbddf662ec2e15e76833b5486151b940ebb272e355b2a1805379ed121ec1fa4b

## Purpose
Mocked Playwright coverage of provider capability mode settings.

## Behavior and Constraints
- Exercises an unsaved selection differing from the saved mode, saved versus effective mode, deployment override, empty test request and revision precondition, detection, save, and reload persistence.
- A no-saved-settings scenario verifies that no effective mode is claimed and Test is disabled. API routes are mocked; these tests do not establish live-provider or worker behavior.
- `/me` mocks use `/me?optional=true` (issue 29 anonymous probe) to avoid a 401 failed request on signed-out first paint.

## Related Files
- `web/src/features/admin/provider-settings/ProviderSettingsPage.tsx`: admin form under test.
- `backend/tests/provider/test_capability_mode.py`: backend capability regressions.
