# backend/src/x_insight/reasoning/coordinator.py

Source SHA-256: 2e90c88794068b945626a8c280aa99634a1a19af91645de4179e46fcaa4fef6b
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Executes one claimed job end to end: package validation → provider estimation → CPT
validation → effective artifact → bounded inference → deterministic rendering → atomic
success transaction.

## Important Symbols
- `execute_claimed_job()` — the pipeline for a single job.
- `load_document()` — requires network XML whose SHA-256 equals `manifest.network_hash`,
  else `HASH_MISMATCH` (no repair).
- `render_section()` / `_argmax_per_node()` — local renderer only: `==`/`!=` branches on each
  node's argmax with ties resolved by declared order; `{Node}` slots become
  `"<state> <pct>%"`. `in/and/or/not` fail closed with `RENDER_UNSUPPORTED_OPERATORS`.
  **No LLM prose is ever produced.**
- `try_assemble_proposal()` — inserts an immutable `proposal_snapshots` row only when every
  applicable run has a baseline and the pinned DDI report recomputes validly (partial → None).
- `recompute_pinned_ddi()` — recomputes from the frozen medication fingerprint and pinned
  dataset version, never from the live chart.
- `build_transparency()` — the five transparency fields, all read from persisted rows.
- `_commit_failure()` — records stage + resume artifact; audits only terminal failures.
- Three-level recovery: job retry, stage resume (`s47_validated`, `s47_inference`), and
  carried baselines under a matching fingerprint or per-question projection.

## Behavior and Constraints
- Inference runs with an **empty evidence set**; the patient projection is provenance only.
- Success is one transaction: fencing → eligibility → insert immutable baseline (skip if it
  exists) → job succeeded → enqueue successor → maybe assemble proposal → audit. Failed
  originals create **no** baseline row.
- Stage bundles only match an identical network hash + projection hash.

## Related Files
- `backend/src/x_insight/models/inference.py`, `reasoning/provider.py`, `reasoning/queue.py`,
  `reasoning/snapshots.py`.
