# Follow-up Package E checkpoint — 2026-11-12

Base revision `5baf45e85c735f8af1278a3a4984f09c72b2dc06`; changes are
uncommitted. Added a standalone follow-up candidate calculation: it calls the
trusted 16-factor oral ranking only on oral replacement, keeps the full ranked
probabilities, checks current and candidate pairs separately against the pinned
DDI dataset, filters and backfills after availability/current-main/D/X exclusions,
and records reasons, offending pairs and missing coverage. Direct clozapine and
LAI suggestions use formulation availability and D/X policy; missing catalog
coverage is reported. Original and final decisions are retained and sections
(including timing) recomputed from the final decision. Model and lookup errors
propagate rather than masquerading as physician-review outcomes. New case code is
unchanged.

Verification on the uncommitted tree: Ruff and mypy passed on the new module;
`cd backend && .venv/bin/pytest -q tests/models/test_follow_up_candidates.py
 tests/models/test_new_case_candidates.py tests/assessments/test_follow_up_rules.py`
passed 46 tests. Tests use the actual pinned model and synthetic DDI responses;
they cover backfill, original probabilities, current-main exclusion, D/X, missing
coverage, direct availability, unsupported mappings and corrupt/missing model.
No database was migrated.

This is a partial package checkpoint: the calculation has no public proposal
caller until F, and public DDI lookup regressions, complete multiple-drug and
formulation matrix, real database integration, mixed recovery and end-to-end
acceptance remain unverified. Package D's exhaustive matrix and A–C limitations
remain open; creation stays gated pending F/G. No commit or deployment was made.
