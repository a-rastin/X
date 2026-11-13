# backend/src/x_insight/cases/follow_up.py

Source SHA-256: b24212b71d8936046e753795e00b7e2198299f4f1e55ccc5ef9a215e5cd2015e

## Purpose
Typed follow-up defaults, strict autosave, exact content pins, conditional validation and saved-state previews.

## Behavior and constraints
Owns the strict `draft_data.follow_up` contract, fresh assessments/defaults and
server-owned baseline/provenance. `content_pins` is shared by creation, proposal
verification and restore, including hashes of assessment file bytes.
`prepare_autosave` stamps manual overrides and clears removed main treatment.
`evaluate_state` combines inherited answers, current labs and manual choices,
requires applicable carried values/model factors, and delegates decisions and
independent recommendations to `follow_up_rules`. Reads never create proposals.

## Evidence
Current source and `backend/tests/http/test_follow_up_plans.py`; see the B/F/G progress checkpoint.
