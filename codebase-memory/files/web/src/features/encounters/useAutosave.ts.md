# web/src/features/encounters/useAutosave.ts

Source SHA-256: b5d44f9e16110aee4cdcf15a133a873b1ba5146d55890485d990f82ba9ba6cab

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
  requires explicit reconciliation before resubmission — never a silent overwrite. 409 is terminal.
- Edit/saved sequence counters distinguish edits made during an in-flight save; one in-flight
  request is coalesced; the idempotency key is reused only when retrying the same attempt.
- `applySavedSlice()` exists because a command POST can bump the revision outside a PATCH.

## Related Files
- `web/src/features/encounters/api.ts` — the PATCH/GET calls.
- `web/src/features/encounters/EncounterPage.tsx` — drives flush on step transitions.

## Server normalization and in-flight edits
A successful PATCH rebases its canonical `draft_data` through
`reconcileAutosave(sent, local, saved)`. Unchanged paths adopt server metadata and
normalization; paths edited during the request keep their local values. This
preserves server-owned override attribution without erasing typing. `412` keeps
local edits and exposes server truth; adopting or retrying on a fresh revision
is an explicit action. The runnable check is
`web/tests/autosave-reconciliation.test.mjs`.
