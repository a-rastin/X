# backend/src/x_insight/cases/encounters.py

Source SHA-256: a2e761d1d791a8ce09b6b20cd1df46570cab0ae53b73fad17b0e60755f70a892
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Encounter drafts: creation, author-only access, patching with revision fencing, discard, and
follow-up baseline reads. Owns the "at most one open draft per patient" rule.

## Important Symbols
- `create_open_draft()` — takes the **patient row `FOR UPDATE`** first so concurrent creates
  serialize; the partial unique index `one_open_draft_per_patient` is the backstop and an
  `IntegrityError` is re-raised as a generic `409 OPEN_DRAFT_EXISTS` naming neither author
  nor content.
- `require_draft_lifecycle()` / `require_author()` — non-drafts read 404 (no lifecycle
  oracle), non-authors get 403 with no content.
- `safe_draft_for_author()` / `read_draft_for_author()` — the only shapes carrying
  `draft_data`; `safe_encounter_reference()` never does.
- `patch_draft()` — order matters: privacy (404/403) → archive/active fence → revision fence
  (412) → body validation (422). Only a fully valid write bumps `revision = expected + 1`.
- `validate_followup_baseline()` — prior scores are displayed as `historical`, **never seeded
  as answers**.

## Behavior and Constraints
- Registration drafts cannot capture adverse effects: `registration_effects_for_save` accepts
  only a byte-identical round-trip or omission, rejecting add/change/delete with 422.
- `draft_data` bounds: ≤100 top keys, ≤128-char keys, depth ≤6, ≤1000 nodes, ≤200 000 bytes.
- Discard requires explicit `confirm: true` plus the current revision; it moves the row to
  `discarded`, it never deletes the patient.

## Related Files
- `backend/src/x_insight/cases/tables.py` — read model and `one_open_draft_per_patient`.
- `backend/src/x_insight/cases/patients.py` — patient creation, archive policy.
