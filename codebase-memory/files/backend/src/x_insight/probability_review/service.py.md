# backend/src/x_insight/probability_review/service.py

Source SHA-256: ad38c8f0fad2135fef02057d494c832202306300e04f138fc22969084c246606
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
The physician's probability-review half: immutable CPT adjustments/resets, deterministic
local recalculation, and explicit acceptance of a current result.

## Important Symbols
- `apply_adjustment()` — persists one immutable `cpt_revisions` row (`adjustment`) + audit.
- `apply_reset()` — baseline-reuse reset; `execute_local_revision()` runs recalculation
  without any LLM or provider call.
- `find_current_acceptance()` — scans acceptance history newest-first and matches only on
  **full exact equality** of baseline, revision, cpt hash, result kind/id and input hash;
  invalidation is never a deletion.
- `apply_acceptance()` — locks `question_review_states` `FOR UPDATE`; 412 on a stale pointer,
  409 for pending/failed/stale-input or any non-exact reference; re-accepting an identical
  state is idempotent (no duplicate row or audit).
- `derive_calculation_view()` — calculation state is derived on read, not stored.

## Behavior and Constraints
- Only `question_review_states.review_revision` is mutable (pointer, starts at
  `INITIAL_REVIEW_REVISION = 1`); revisions, results and acceptances are append-only.
- Failed local recalculations create **no** result row, so nothing can be accepted off a
  failed run.
- This is the third revision namespace alongside encounter and secondary-plan revisions.

## Related Files
- `backend/src/x_insight/probability_review/redistribution.py` — integer redistribution rule.
- `backend/src/x_insight/cases/signing.py` — signing requires acceptances for exactly the
  ready runs.
