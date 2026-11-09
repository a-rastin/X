# New case Package F partial checkpoint — 2026-11-09

## Implemented

- Added an independent candidate-calculation seam using the pinned oral model,
  formulation-to-Lexicomp mapping and existing candidate-only interaction
  checker. It preserves the 22-outcome distribution and original ranks, filters
  unavailable oral formulations before selecting candidates, and reports LAI's
  distinct model-unavailable state without invoking either service.
- This seam is not connected to a route, persistence or signing. It requires
  trusted pinned content, saved medication IDs and physician settings from its
  eventual caller; it does not itself establish their authenticity or freshness.

## Verification

- `cd backend && .venv/bin/pytest tests/models/test_new_case_candidates.py -q`:
  3 passed. Tests use the bundled model and formulation inventory, with a mocked
  interaction checker; they cover filtering, catalog mapping, LAI and corrupt or
  missing oral models. No database, API, concurrency, restore or browser test ran.
- Changed-file Ruff lint and format checks and `git diff --check` passed.

## Handoff

Package F is **not complete**. Package B physician availability settings and
their revision do not exist yet, preventing the required settings snapshot and
atomic freshness fence. Implement those prerequisites before connecting the seam.
Still required: immutable proposal storage and backup/restore, canonical input
fingerprints, authenticated revision-fenced and idempotent proposal routes,
final-plan editing integration, atomic v4 signing, signed chart/report and
follow-up compatibility, and the full acceptance and race tests in the plan.
Retain the existing legacy/new-workflow guards; do not report the calculation
seam as an end-to-end proposal or a signed clinical record.
