# backend/src/x_insight/cases/chart.py

Source SHA-256: 7ac0ff6cee108ccfcc638c4ba1591d0fa8f020f07336ac64054f76cc2c1097bf
Source commit: b153b7b32f624a0c907362a7fe0bac7a6a67b181

## Purpose

Read the shared signed chart and the draft author's follow-up baseline preview.

## Important Symbols

- `read_chart`: current demographics, signed references/snapshots and addenda,
  with only an occupancy badge for the open draft. No draft clinical body is shared.
- `read_followup_baseline_for_author`: author-only preview; prior scores are
  historical and never become current assessment answers.

## Behavior and Constraints

The current baseline preview returns editable `history.values` under its
`baseline.history_values` field. Medications, prior plan and prior assessments
come from the stored baseline slice. A future immutable historical summary must
read the signed-source projection separately; this endpoint does not currently
provide that separation or prior-visit dates. This is a source finding, not an
implemented correction.

## Related Files

- `backend/src/x_insight/cases/encounters.py`: draft creation and baseline copy.
- `backend/src/x_insight/cases/signing.py`: immutable signed snapshot reads.
- `web/src/features/patients/chart/FollowupBaselinePanel.tsx`: current display.
