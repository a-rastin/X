# backend/src/x_insight/reasoning/snapshots.py

Source SHA-256: 8884a34be97bf111736a7f809bf40482e4d16fd87ffd247e57ffd1ef48054b92
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Freezes everything a generation depends on — analysis fingerprint, per-question patient
projection, applicability gate, pinned DDI — then starts immutable batches and derives
freshness on read.

## Important Symbols
- `compute_analysis_fingerprint()` — canonical JSON of history, medications, reconciliation,
  effects, gate and assessment answers plus pinned content. **Never reads notes**, so note-only
  or plan-only edits leave it unchanged (they can still bump the encounter revision).
- `build_question_projection()` — per-question typed allowlist (≤64 variables, only mapped
  `cpt_context` variables in declared order) plus its hash; `_check_source_path()` rejects note
  paths, posterior/`questions/` chaining, and identifying demographics.
- `evaluate_gate()` — three-valued: `ready` / `not_applicable` / `needs_clarification`;
  unknown estimates **with disclosure**, never maps to false.
- `_diagnose_required_input()` — the single eligibility decision: unknown/null/empty are
  nonblocking; `Unresolved`, `not_assessed`, conflicting lists and malformed non-empty values
  block.
- `start_generation_batch()` — archived-patient 409, `If-Match` 412, readiness 422
  `PROPOSAL_INPUTS_INCOMPLETE`, `_pin_ddi_for_start()` (never raises; pins
  `ddi_status = valid|unavailable`), then immutable batch + ordered runs with frozen packages.
- Freshness: `question_input_hash`, `patient_aware_input_hash`, `compute_question_freshness` —
  recomputed, never stored as a flag.

## Behavior and Constraints
- Batches, runs and baselines are SELECT+INSERT; `reasoning_jobs` is the only mutated table.
- `MAX_WORKFLOW_QUESTIONS = 11`.

## Related Files
- `backend/src/x_insight/reasoning/readiness.py`, `history_gates.py` — nonblocking warnings vs
  blockers without freezing a batch.
- `backend/src/x_insight/reasoning/queue.py` — admission and job insertion.
