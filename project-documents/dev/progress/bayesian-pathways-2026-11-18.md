# Frozen proposal evaluation and active pathways — 2026-11-18

Step 2 (B3–B4 and T2) adds the read-only `explore_proposal` calculation for New
case and follow-up. It takes an access-checked immutable proposal and applies
allowlisted raw-source changes to a copy. Each side shares the proposal's
content, model, interaction release and availability. The patient result is
checked against the stored candidate ordering, raw distribution, evidence,
sections and follow-up decisions.

Both sides evaluate PANSS and C-SSRS with the same released definitions.
Follow-up verifies the historical assessment byte hashes. New case verifies
the stored versions and reports the hashes of the bytes used for the run.
Unresolved oral factors and newly reached branch questions produce partial
results. Direct choices and other non-model decisions retain their existing
meaning without probabilities.

The active graph uses selected-outcome CPT row pairs, executed rule traces
and section reasons, and candidate exclusions. It retains patient-only paths
as superseded and identifies simulated source provenance separately. The diff
uses normalized source changes and actual evaluation results.

## Verification

- All four exploration tests passed against stored proposals on disposable
  PostgreSQL 16. The suite checks replay parity, normalized overrides,
  assessment-only and partial results, modeled and direct decisions, and
  deterministic graphs with structurally relevant zero-attribution factors.
- Existing checks passed for 138 selected model, candidate and evaluator
  tests, the follow-up fingerprint test, and 19 pinned-model registry and
  proposal/signing tests. The database suites used separate fresh PostgreSQL
  16 databases so each suite controlled its installed content.
- Backend Ruff lint and mypy passed. The repository-wide Ruff format check
  reports existing differences in `cases/encounters.py` and
  `tests/test_registration_birth_year.py`. The step's changed files were
  checked separately.
- Review identified normalized BMI no-ops that still appeared in the graph
  and diff. The fix compares evaluated raw values before adding simulated
  provenance. The independent re-review passed.

## Handoff

Step 3 can call
[`explore_proposal`](../../../backend/src/x_insight/cases/reasoning_exploration.py)
after resolving the proposed route's session, author scope, encounter and
proposal. Keep that access check at the HTTP boundary. Translate the internal
calculation into the versioned request and response contract, including the
normalized scenario identity, and verify that repeated HTTP calls leave
domain state intact. Exclude the DDI checker's per-call `generated_at` from
the stable response projection and semantic hash. The endpoint, typed
frontend transport and rendering are still pending.
