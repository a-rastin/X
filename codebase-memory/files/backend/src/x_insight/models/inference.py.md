# backend/src/x_insight/models/inference.py

Source SHA-256: 86b2f7c517160211927d30d42e60fb468a423d2f87c0c9fa65e4088013dfee5e
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
The deterministic reasoning core: exact CPT validation, effective-artifact construction, and
bounded exact inference over that artifact.

## Important Symbols
- `validate_cpts(document, payload)` — fixed check order (document → envelope → identity →
  tables → rows). Units are `UNITS_FOR_100_PCT = 100_000_000` with ≤6 decimals; every row must
  total **exactly** 100 000 000 units. Invalid values are never normalized, clipped or repaired.
- `build_effective_artifact()` — populates every `TABLE` in a run-local XML preserving source
  order, freezes `effective_sha256`, and records a runtime record with pinned python/pgmpy/lxml
  versions and `PINNED_ENGINE_CONFIG`.
- `infer_effective()` — **`patient_projection` is provenance only; evidence is always `{}`.**
  Posteriors are checked finite, in range and summing to 1 within 1e-6.
- `replay(artifact, expected)` — requires empty evidence, pinned config and matching runtime.

## Behavior and Constraints
- Inference runs in a `spawn` child process (`_infer_worker` / `_run_bounded_inference`) that
  builds pgmpy `TabularCPD`s and runs `VariableElimination.query(..., evidence=None)`;
  exceeding the 30 s budget terminates it as `inference_timeout` / `inference_crash`.
- Admission bounds: 64 nodes, 131 072 cells, 1 MiB effective artifact.

## Evidence
- Comments at the module head state the empty-evidence rule explicitly; `PINNED_ENGINE_CONFIG`
  carries `"evidence": "empty"`.

## Related Files
- `backend/src/x_insight/models/validation.py` — safe XMLBIF import feeding this module.
- `backend/src/x_insight/models/capacity.py` — admission pre-flight using the same constants.
