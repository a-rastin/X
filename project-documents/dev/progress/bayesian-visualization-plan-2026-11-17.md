# Bayesian visualization implementation-plan handoff — 2026-11-17

## Delivered

Created the detailed
[Bayesian reasoning visualization and live what-if implementation plan](../bayesian-reasoning-visualization-implementation-plan.md)
from the current working tree at `ff7bbb4`, the new-case integration report,
and the superseding follow-up B/F/G integration handoff.

The plan covers backend attribution and active subgraphs, strict contracts
and a stateless simulation endpoint, probability/attribution SVG charts,
live controls, ghost ticks, diff callouts, reset behavior, and verification.
It adds no approval, confirmation, or research-development gate.

## Findings for the implementing agent

- Current workflows use the dedicated SHA-pinned 16-parent oral row-lookup
  model, not the legacy LLM/empty-evidence inference path.
- Model-reference probability, current patient posterior, historical
  follow-up baseline, and scenario posterior have different meanings.
- The loader currently discards validated root priors; the plan retains
  them for exact additive root-prior-background Shapley attribution.
- PANSS does not currently change oral treatment ranking. C-SSRS can affect
  independent sections. The plan specifies honest unchanged-ranking
  messages rather than inventing symptom-to-probability weights.
- Follow-up branch selection and interaction/availability filtering remain
  separate from raw posterior attribution; some terminals have no model
  probabilities.
- The displayed immutable proposal anchors the sandbox. Every scenario
  starts from its frozen raw inputs and remains outside autosave, proposals,
  final-plan editing, signing, and snapshot/export schemas.

## Verification

- Inspected model/evaluator/candidate/proposal/router and frontend host/API/
  graph code, existing tests, manifests, and relevant rationale/handoffs.
- Read-only bundled-model probe verified unequal root priors and the
  documented all-No/QTc-only raw distribution example.
- Independently checked the planned coalition transform and Shapley
  reconstruction against exhaustive enumeration for all evidence assignments
  in 1–4-parent synthetic examples.
- Queried the installed project Astryx CLI for available control types;
  used `mise exec node@26.13.0 -- astryx search ...` after the default shim
  reported that its active runtime did not provide the CLI. No runtime or
  package settings were changed.
- Documentation links, required sections, source/test paths, and whitespace
  were checked after authoring. Application tests were not run: this session
  delivers a plan, not implemented application behavior.
- Repository-wide `git diff --check` reports pre-existing EOF whitespace in
  six `.codex/agents/` TOML files. Checks scoped to this session's Markdown
  files pass; those unrelated edits were not changed.

## Handoff

Start with the plan's B1–B2/T1 slice: retain model root priors and implement
the exact selected-treatment attribution with small independently enumerated
fixtures. Continue through its execution-order table. Record implementation
and measured test/performance results separately from this planning report.

Unrelated pre-existing changes to `.codex/agents/`, `.devflow/install-state.json`,
and untracked `artifacts/` were left untouched. No commits or branches were
created.

## Follow-up 2026-11-18

The plan and reasoning codebase notes were prepared for the `main` push.
See the [linked explorer checkpoint](bayesian-linked-explorer-2026-11-18.md)
for implementation verification and the remaining acceptance work.
