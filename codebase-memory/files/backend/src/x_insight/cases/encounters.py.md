# backend/src/x_insight/cases/encounters.py

Source SHA-256: c8a2535699bcf37a0f76215b6dfb64cf402ffadd45b0cd006f17284e5bab1104

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
- New follow-ups use the typed workflow described below; existing legacy drafts retain their identity.
- New case registration with birth year uses `new-case-v1`; PATCH applies typed state,
  content-pin and attributed-override checks. `follow-up-v1` PATCH routes through
  `cases/follow_up.prepare_autosave` (rejects forged version/pins/baseline/provenance).
  Legacy drafts cannot acquire New case or follow-up state; unsupported workflows fail
  closed on PATCH, and legacy history/effects write commands call `require_legacy_workflow`.
- Registration drafts cannot capture adverse effects: `registration_effects_for_save` accepts
  only a byte-identical round-trip or omission, rejecting add/change/delete with 422.
- `draft_data` bounds: ≤100 top keys, ≤128-char keys, depth ≤6, ≤1000 nodes, ≤200 000 bytes.
- Discard requires explicit `confirm: true` plus the current revision; it moves the row to
  `discarded`, it never deletes the patient.

## Related Files
- `backend/src/x_insight/cases/tables.py` — read model and `one_open_draft_per_patient`.
- `backend/src/x_insight/cases/patients.py` — patient creation, archive policy.

## Current follow-up contract
`FOLLOWUP_V1_ACTIVE = True` selects `follow-up-v1` for newly created follow-ups.
Creation freezes the latest supported signed source across v1–v5 under the patient
lock, refuses client baselines, and uses `follow_up.content_pins`. Existing stored
workflow identities are immutable. `lock_follow_up_patient` makes follow-up draft,
plan and addendum writes take patient before encounter; proposal/signing also
lock the account first to serialize deactivation. Overrides are attributed and
audited, and the legacy command allowlist remains explicit.
