# Oral Bayesian attribution checkpoint — 2026-11-18

Step 1 (B1–B2 and T1) of the Bayesian reasoning visualization plan is
complete. The SHA-pinned loader now keeps the validated root priors in CPT
parent order and stores the table in a read-only view. Ranking and
`explain_oral` share the evidence encoder. The explanation returns the
selected treatment's model reference, raw posterior, all 16 signed
contributions in probability units, and the reconstruction residual.
The cache holds up to 128 immutable results per loaded model for the
`oral-root-prior-shapley-v1` algorithm.

## Verification

- Exhaustive synthetic tests compare every coalition and Shapley value
  against independent enumeration for all evidence assignments in one-,
  two-, and three-parent models. The absolute reconstruction tolerance is
  `1e-10` probability units.
- Bundled-model tests check parent-bit and outcome ordering, full-distribution
  normalization, the all-No tie, and the QTc-only lookup. They also verify
  model identity isolation, ranking parity before and after attribution, and
  preservation of raw probabilities through availability and interaction
  exclusions.
- All 158 selected backend tests passed, covering the model, both candidate
  calculators, both workflow evaluators, and the proposal/signing routes.
  Database-backed tests ran on disposable PostgreSQL 16.
- Scoped Ruff formatting, repository backend Ruff lint and mypy, Python
  compilation, Node 22 web build, root TypeScript, and all 23 web tests passed.
  Repository-wide `make check` stopped on pre-existing Ruff formatting
  failures in `cases/encounters.py` and `tests/test_registration_birth_year.py`.
- On the local i7-3537U using Python 3.12, median direct model-call latency
  was 1,087 ms on a cold load (5 runs), 199 ms with a loaded model and
  uncached evidence (16 runs), and 0.004 ms for identical cached requests
  (100 runs). The probe reused in-memory model bytes and measured the
  model function.

## Handoff

Step 2 (B3–B4 and T2) can consume the pure
[`explain_oral` function](../../../backend/src/x_insight/models/oral_antipsychotic.py).
Run it for each modeled comparison side with the same pinned bytes and
treatment ID. Use the regular ranking distribution for all treatments and
apply each workflow's existing candidate policy. Use the immutable
proposal's evidence for the patient posterior and the model root priors
for the chart reference.
