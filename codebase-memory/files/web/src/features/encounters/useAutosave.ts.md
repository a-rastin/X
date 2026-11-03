# web/src/features/encounters/useAutosave.ts

Source SHA-256: 5117e276b84ab9c10004b19e0435045c62088725c3bae7662a04a6a671df635f
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
The single draft-persistence contract for the wizard: debounced PATCH with revision fencing,
conflict handling that keeps local edits, and a flush barrier before navigation or signing.

## Important Symbols
- `AUTOSAVE_DEBOUNCE_MS = 250` (was 1000); `SaveState` — `saved | saving | dirty | failed | conflict`.
- `setDraftValue(key, value)` — new wizard pages add a `draft_data` key rather than a new
  endpoint; always send the whole `draft_data` object.
- `flush()` / `ensureSaved()` — bounded 5-attempt barrier used before step transitions and
  signing.
- `attemptRef {key, data, revision, seq}` — replaces the key-only ref: a retry of the same
  attempt reuses body + base revision + key so a lost response replays instead of
  double-bumping. 401 surfaces as retriable `failed` ("Sign in in another tab…"), not a
  session-expired callback.
- `applyServerSnapshot()`, `applySavedSlice()`, `adoptServerVersion()`, `retryOnFreshRevision()`,
  `reloadTruth()`.

## Behavior and Constraints
- Only a 2xx sets `saved`. A **412 keeps the local edits**, fetches server truth for display and
  adopts the fresh revision as the next base — never a silent overwrite. 409 is terminal.
- Edit/saved sequence counters distinguish edits made during an in-flight save; one in-flight
  request is coalesced; the idempotency key is reused only when retrying the same attempt.
- `applySavedSlice()` exists because a command POST can bump the revision outside a PATCH.

## Related Files
- `web/src/features/encounters/api.ts` — the PATCH/GET calls.
- `web/src/features/encounters/EncounterPage.tsx` — drives flush on step transitions.
