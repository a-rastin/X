# backend/src/x_insight/cases/encounters.py

Source SHA-256: b00be57fc9e60a2d8fea133999f5083447b46dea5470c25af93ee355698e77c0
Source commit: b153b7b32f624a0c907362a7fe0bac7a6a67b181

## Purpose
Encounter drafts: creation, author-only access, patching with revision fencing, discard, and
follow-up baseline reads. Owns the "at most one open draft per patient" rule.

## Important Symbols
- `create_open_draft()` — takes the **patient row `FOR UPDATE`** before checking the slot so concurrent creates
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
- Current follow-ups remain `legacy-v1`. With no supplied baseline, creation selects the
  latest signed **New case** snapshot only, before acquiring the patient lock; it can miss a
  newer signed legacy follow-up. It carries the prior medication pin/plan/assessments but
  does not translate New case conditions into legacy History answers.
- New case registration with birth year uses `new-case-v1`; PATCH applies typed state,
  content-pin and attributed-override checks. Legacy drafts cannot acquire that state.
- Registration drafts cannot capture adverse effects: `registration_effects_for_save` accepts
  only a byte-identical round-trip or omission, rejecting add/change/delete with 422.
- `draft_data` bounds: ≤100 top keys, ≤128-char keys, depth ≤6, ≤1000 nodes, ≤200 000 bytes.
- Discard requires explicit `confirm: true` plus the current revision; it moves the row to
  `discarded`, it never deletes the patient.

## Related Files
- `backend/src/x_insight/cases/tables.py` — read model and `one_open_draft_per_patient`.
- `backend/src/x_insight/cases/patients.py` — patient creation, archive policy.
