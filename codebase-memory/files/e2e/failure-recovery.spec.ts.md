# e2e/failure-recovery.spec.ts

Source SHA-256: f7760d5d1299e39d70d97044124d1fbc418e70c0e6ffab3741b34ba484627bd1

## Purpose
Mocked Playwright coverage of failed-run recovery, acknowledgement and
transparency presentation for the proposal review UI.

## Important Symbols
- `Issue 19 failed run states no transparency data within 2s of opening the panel`: the
  timing regression for the two-second bound.

## Behavior and Constraints
- The two-second bound is measured entirely in-page: a capture-phase click listener
  records `openedAt`, an in-page watcher (mutation batch and painted frame, whichever
  sees it first) records `appearedAt`, both as `performance.now()`; the difference is
  asserted and reported in the failure message. Measuring across Playwright
  round-trips charged harness latency to the UI (2931 ms observed once).
- The behavioural assertion itself (`toContainText("No transparency data because this
  run failed", { timeout: 2000 })`), the "panel stayed open" check and the
  `reviewCount === 1` check are unchanged and still gate the test.
- Other tests mock batches/reviews and unroute in `finally`; several exceed a 30 s test
  timeout on memory-constrained hosts without failing any assertion.

## Related Files
- `web/src/features/reasoning/ProposalReviewPanel.tsx`: panel under test.
- `e2e/proposal-review.spec.ts`: the remaining Issue 19 transparency cases.
