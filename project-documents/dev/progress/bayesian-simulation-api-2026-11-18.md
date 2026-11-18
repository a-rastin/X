# Reasoning simulation API — 2026-11-18

Step 3 (C1–C3 and T3) adds a shared simulation endpoint for New case and
follow-up. The route resolves the named proposal within the author’s
encounter, replays its pinned context, and returns the typed comparison
without storing the scenario. The response reports partial and non-model
evaluations and distinguishes raw rank from eligible rank. The scenario hash
tracks effective overrides and frozen identities, so changing the selected
treatment or request sequence does not change it.

The TypeScript client uses the shared authenticated transport and forwards
an abort signal. The [API reference](../../../docs/reasoning-simulation-api.md)
describes the contract and bootstrap behavior.

## Verification

- Four simulation API tests passed against disposable PostgreSQL 16. They
  exercise both workflows, authorization and CSRF, strict overrides,
  deterministic results, partial and direct outcomes, and unchanged counts
  and hashes for 17 domain tables.
- Four frozen-proposal replay tests and 69 selected model and evaluator
  regression tests passed. All eight web test files and the production
  build passed. Vite reported its chunk-size warning.
- Backend Ruff lint, formatting on changed Python files, and mypy passed.
  Repository-wide formatting still reports existing differences in
  `cases/encounters.py` and `tests/test_registration_birth_year.py`.

## Handoff

Step 4 (V1–V3) can use `simulateReasoning` and the public wire types to
render the probability comparison and the model-reference tornado. The
API provides patient and scenario values with nullable probabilities for
unevaluated choices, and reports contributions in probability units.
