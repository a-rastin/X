# Bayesian reasoning visualization and live what-if sandbox

**Implementation plan — 2026-11-17**

**Repository inspected:** working tree at `ff7bbb4`.

**Status:** planning only; the feature is not implemented by this document.

**Audience:** the AI agent implementing the feature in INSIGHT.

## 1. Outcome and scope

Add an explanation workspace to the original-proposal area of both current
workflows, `new-case-v1` and `follow-up-v1`:

1. A probability comparison chart for treatments, with dotted patient-posterior
   ticks and solid simulated-posterior bars.
2. A diverging horizontal factor-attribution tornado chart for a selected
   treatment, anchored at that treatment's **model-reference probability**.
3. An adjacent live control strip for supported symptoms/effects, PANSS,
   C-SSRS, comorbidities, and workflow branch answers.
4. A pruned pathway graph connecting relevant inputs, derived factors,
   executed rules, treatment probabilities, and candidate selection.
5. A deterministic diff banner and a single-click **Reset to Patient Data**.

Build this as an additive, transient exploration surface. Reuse the current
model, clinical evaluators, ranking policies, translations, and design system.
Do not introduce a new LLM call, CPT editing, persisted simulation, database
migration, approval workflow, confirmation dialog, or research-use gate.
Existing proposal generation, final-plan editing, and signing remain separate
and unchanged. Implementation can proceed directly through the steps below.

### 1.1 Important findings from the current code

The implementation must reflect these distinctions rather than an older
architecture description:

| Verified finding | Code to read | Implementation consequence |
| --- | --- | --- |
| Current oral ranking is a SHA-pinned CPT row lookup with exactly 16 observed binary parents and 22 outcomes. | `backend/src/x_insight/models/oral_antipsychotic.py`: `PARENTS`, `OUTCOMES`, `_load_cached`, `rank_oral` | Extend this execution profile; do not route the feature through the legacy inference worker. |
| The loader validates root probability tables but currently discards them. | `models/oral_antipsychotic.py`: `_load_cached` | Retain root priors to calculate a genuine model-reference probability. An all-No row is not that prior. |
| New-case evidence comes from raw answers, derived flags, and BMI; four movement-effect factors are fixed False. | `backend/src/x_insight/cases/new_case.py`: `evaluate_new_case`, `evidence_sources` | Apply simulations to source inputs and rerun the evaluator. Do not edit an evidence dictionary independently of its sources. |
| New-case ranking removes unavailable/zero-probability candidates, then takes five; interaction reports do not apply the follow-up exclusion policy. | `backend/src/x_insight/cases/new_case_candidates.py`: `calculate_candidates` | Preserve this workflow's existing ranking behavior. |
| Follow-up executes a decision tree; the oral model runs only for `oral_replacement`. Other terminals include direct treatment and non-model decisions. | `backend/src/x_insight/cases/follow_up_rules.py`: `decision`; `follow_up_candidates.py`: `calculate_candidates` | Model probability charts are conditional on the evaluated terminal, not universally available. |
| Follow-up retains the original model probabilities and then excludes zero probability, unavailable drugs, the current main drug, and D/X interactions. | `follow_up_candidates.py`: `calculate_candidates` | Keep raw posterior, exclusion reasons, and eligible rank as different fields. Do not renormalize after filtering. |
| PANSS items are `P1`…`P7`, `N1`…`N7`, `G1`…`G16`, scored 1–7. They are not parents of the current oral model or inputs to the current follow-up decision function. | `content/assessments/panss.v1.json`; `new_case.py`; `follow_up_rules.py` | PANSS controls update assessment results, but do not invent treatment-probability changes. |
| Current workflow C-SSRS inputs are five recent-ideation yes/no answers. Some affect independent care-setting sections, not the oral CPT. | `content/assessments/cssrs.v2.json`; both workflow `structural_draft` functions; `new_case_rules.py`: `plan_sections`; `follow_up_rules.py`: `sections` | Use five answer controls, not a fabricated continuous C-SSRS treatment score. |
| Both proposal payloads contain frozen raw inputs, model/content identity, ranking results, and an availability snapshot. | `backend/src/x_insight/cases/new_case_plans.py`; `follow_up_plans.py` | Anchor patient comparison to the displayed immutable proposal, not a moving latest-draft query. |
| Original plans currently display text/list probabilities, not a reusable chart. | `web/src/features/new-case/NewCasePlanPage.tsx`: `ProposalView`; `web/src/features/follow-up/FollowUpPlanPage.tsx`: `FollowUpProposalView` | Add the comparison chart; do not assume an existing probability-bar component. |
| The shared proposal/final-plan host supports both workflows. | `NewCasePlanPage.tsx`: `NewCasePlanPage` | Mount one shared exploration workspace here, leaving the final-plan controls untouched. |
| Existing network rendering uses native SVG and an admin-only graph API. | `web/src/features/admin/networks/GraphView.tsx`; `api.ts` | Reuse useful drawing conventions, not the admin API or its permissions. |
| React, Astryx, and native SVG are already available; no chart or graph-layout library is installed. | `web/package.json` | Use SVG plus existing Astryx controls rather than introducing charting infrastructure. |
| Older LLM workflows deliberately execute inference with empty evidence. | `backend/src/x_insight/reasoning/coordinator.py`: `infer_effective` call; `models/inference.py` | Leave that behavior unchanged; this plan targets the separate fixed-model workflows. |

Read the current handoffs before implementation:

- [New-case integration](progress/new-case-implementation-2026-11-09.md).
- [Follow-up B/F/G integration](progress/follow-up-bfg-2026-11-13.md), which
  supersedes earlier incomplete follow-up implementation reports.
- [New-case rationale](../../context/new-case.md) and
  [follow-up rationale](../../context/follow-up.md).
- Existing API descriptions in [new-case state](../../docs/new-case-state.md)
  and [follow-up API](../../docs/follow-up-api.md).

### 1.2 Define the three baselines before coding

Use distinct names throughout Python, JSON, TypeScript, labels, and tests:

- **Model reference**, `model_reference_probability`: the selected outcome's
  probability with all 16 parents marginalized under their pinned root priors.
  This is the tornado's anchor and does not change when controls move.
- **Patient baseline**, `patient_probability`: the actual patient's posterior
  in the displayed proposal, with that proposal's raw input and availability
  snapshots. This supplies the dotted ticks on probability bars.
- **Scenario**, `scenario_probability`: the posterior after applying the
  current transient overrides to that same baseline snapshot.

Neither the model reference nor the patient's preceding signed encounter is
the ghost-marker baseline. For follow-up, the historical signed baseline
remains an input to the current-visit evaluator; it is not substituted for the
current proposal's patient posterior.

### 1.3 Treat the PANSS example as illustrative, not an existing model behavior

The requested example, “Lowering PANSS-P1 from 6 to 3 shifts top recommendation
from Olanzapine (46%) to Aripiprazole (51%),” is a desired **banner format**,
not a behavior supported by today's oral CPT.

For this implementation:

- Render and recompute PANSS controls and assessment summaries normally.
- Return explicit impact metadata when an input changes an assessment or an
  independent section but not treatment ranking.
- A PANSS-only change should say, for example, “PANSS-P1: 6 → 3; treatment
  ranking unchanged in this model.”
- A C-SSRS change can identify a care-setting-section change without claiming
  it changed oral probabilities.
- Use real probability changes from currently connected factors, such as QTc
  concern or hepatic failure, to demonstrate recommendation changes.
- Do not add arbitrary symptom-to-CPT weights to make the illustrative
  numbers appear. A different symptom-responsive model is a separate content
  change, not part of this visualization feature.

## 2. Backend Attribution & Active Subgraph Engine

### Step B1 — Retain the pinned model's calculation metadata

**Modify:** `backend/src/x_insight/models/oral_antipsychotic.py`.

1. Change the private cached loader to retain the validated target CPT and
   ordered `(P(No), P(Yes))` root priors in one internal loaded-model record.
2. Preserve existing byte/hash, dimensions, node/outcome ordering, finite
   probability, and normalization checks. Keep the large bundled XML on its
   dedicated loader; do not relax generic XMLBIF admission limits.
3. Treat cached arrays as read-only implementation details. Any attribution
   working vector is a copy, never the cached CPT itself.
4. Keep `rank_oral`'s signature, return type, sort order, tie behavior, and
   filtering results unchanged. Reuse a private evidence-to-row helper rather
   than implementing a second interpretation of parent order.
5. Give the new explanation calculation a small public surface, such as
   `explain_oral(model_bytes, model_sha256, evidence, treatment_id)`.
   Keep coalition transforms and Shapley weighting private. A separate
   `models/oral_attribution.py` is reasonable if those internals make the
   existing loader/ranker hard to navigate; do not create a generic engine
   framework.

**Deliverable:** existing ranking tests still describe exactly the same
probabilities, with root priors available to the explanation calculation.

### Step B2 — Calculate exact, additive probability-space attribution

Use **exact Shapley attribution with the model's independent root-prior
background**. This supplies additive probability contributions without a
sampling seed, a SHAP dependency, an LLM explanation, or a second clinical
model. Shapley additive feature attribution is the mathematical reference;
the following background distribution and calculation are this plan's
specific design choice.[^shapley]

For selected treatment `t`, parent set `F`, and observed factor values `e`:

```text
f_t(x) = pinned CPT probability of t at complete parent assignment x
q_i(x_i) = pinned root prior for parent i

v_t(S; e) = sum over assignments z to F\S of:
             f_t(e_S, z_(F\S)) * product(q_j(z_j), j in F\S)

p_model(t) = v_t(empty; e)
p_case(t)  = v_t(F; e) = f_t(e)

phi_i(t; e) = sum over S subset of F\{i} of:
                (|S|! * (n-|S|-1)! / n!)
                * (v_t(S union {i}; e) - v_t(S; e))

p_model(t) + sum(phi_i(t; e), i in F) = p_case(t)
```

1. Use all 16 parents, including observed False values: absence can contribute.
2. Return signed probability-unit contributions; format `100 * phi_i` as
   **percentage points** in the UI, not percent relative change or log odds.
3. Calculate attributions independently for patient evidence and scenario
   evidence, using identical model bytes, root priors, and outcome order.
4. Return model reference, posterior, per-factor contributions, and the
   additive reconstruction residual. Keep full precision in JSON.
5. Do not normalize contributions to force them to sum, conceal residuals,
   or call a one-factor removal delta an additive decomposition.
6. Define the numeric algorithm identity as
   `oral-root-prior-shapley-v1`; include it in the response and cache key.

**Efficient exact calculation for this specific 16-parent star model:**

1. Extract the selected outcome's CPT column into a working vector with
   `2^16 = 65,536` entries, preserving the loader's row order.
2. Transform each parent axis once. For paired entries `a` and `b` differing
   only in that axis, replace the output for “not in coalition” with
   `q_no * a + q_yes * b`, and the output for “in coalition” with `a` or `b`
   according to the observed value. Read both old entries before writing.
3. After all axes, `work[mask]` is `v_t(S; e)`, where each mask bit denotes
   coalition membership, not an observed clinical value.
4. Use `bit = 1 << (15 - parent_index)` to match the current CPT encoding;
   do not silently reverse `PARENTS` when interpreting masks.
5. Aggregate the marginal differences with weights
   `1 / (n * comb(n - 1, subset_size))` and stable summation.
6. Compute attribution only for the selected treatment, not all 22 on every
   keystroke. All-treatment posterior comparison still comes from the normal
   row lookup.

This needs `O(n * 2^n)` arithmetic and `O(2^n)` temporary values per selected
treatment, rather than enumerating every missing-variable assignment for
every coalition. Verify the transform against exhaustive enumeration on
small synthetic models before applying it to the bundled model.

Cache only bounded, immutable derived results keyed by model SHA, algorithm
version, canonical evidence bitmask, and treatment ID. Retain the existing
bounded model-loading cache. Cache final small explanation results rather
than unbounded coalition vectors or patient records. Start with standard
library arrays and arithmetic; optimize the measured hot loop rather than
adding dependencies preemptively.

### Step B3 — Build an active dependency subgraph, not a decorative full network

**Add:** `backend/src/x_insight/cases/reasoning_exploration.py`, containing
the shared orchestration and graph-building functions. Keep model arithmetic
inside `models/`.

Represent four explicit edge types:

| Edge kind | Meaning | Source of truth |
| --- | --- | --- |
| `input_derivation` | Raw answer or lab/override produces an effective factor. | `new_case.evidence_sources`, follow-up `model_factors`, and their evaluator code. |
| `bn_dependency` | A parent factor participates in the selected treatment's CPT. | Validated `PARENTS` and the loaded CPT. |
| `rule_dependency` | An evaluated input participates in an executed decision/section branch. | Existing decision trace, section reasons, and explicit local mappings adjacent to those rules. |
| `candidate_filter` | Raw distribution is filtered into the final eligible choice. | Existing candidate calculators and removal reasons. |

1. Give nodes stable IDs based on semantic paths and treatment IDs, not array
   positions, translated labels, or current ranks.
2. Reuse existing input provenance. When simulation overrides a value, attach
   transient `simulated_override` provenance in the result; do not attribute
   it to a real recorded actor or write attribution timestamps.
3. Extract branch reasons directly from the same evaluation that supplies
   the charts. Do not rebuild the follow-up decision tree in the graph code.
4. For the Bayesian portion, identify CPT dependence by comparing row pairs
   that differ only in a parent for the selected outcome. A zero local
   Shapley contribution alone does **not** establish structural irrelevance.
5. For this star network, all 16 parents may remain relevant. Pruning does
   not mean inventing hidden symptom-to-drug paths or dropping False-valued
   inputs; it means excluding unrelated outputs/sections and unexecuted
   branches while retaining the sources of the selected explanation.
6. Retain source → derived-factor → treatment paths for relevant model
   parents, including workflow-default sources where applicable.
7. Retain executed rule paths to changed care-setting/assessment summaries
   and final treatment selection. Include meaningful post-model exclusions
   as separate rule/filter nodes, not probability contributions.
8. Produce the union of patient-active and scenario-active paths, tagging
   each node/edge `patient`, `scenario`, or `both`; tag departed paths
   `superseded` in the scenario. This makes branch flips visible instead of
   making the old branch disappear without explanation.
9. If a PANSS-only change has no path to treatment ranking, show its assessment
   result in the change summary; do not connect it to `Antipsychotic`.
10. Keep graph ordering deterministic. Return any presentation pruning counts
    and keep full explanation rows available for expansion.

The requested UI can be titled **Pruned Causal Pathway Graph**, with a legend
that distinguishes model dependencies from deterministic rules. These are
the app's encoded pathways, not a newly estimated causal-effect model. No
`do()` intervention engine or generic causal-discovery package is needed.

### Step B4 — Share current evaluation and candidate selection

1. Resolve the immutable displayed proposal and its workflow. Use its
   `raw_inputs`, `demographics_at_encounter`, pinned content/model identity,
   and `availability_snapshot` as the comparison context.
2. Deep-copy the frozen raw inputs and apply only the requested source-field
   overrides; start from the patient baseline on **every** request.
3. Call `new_case.evaluate_new_case` or `follow_up.evaluate_state` to derive
   factors, independent sections, assessments, and branch decisions.
   Neither current state result exposes a complete PANSS summary: explicitly
   call `assessments.engine.evaluate` with the pinned released PANSS and
   C-SSRS definitions for both sides and attach those assessment results.
   Preserve absent/partial PANSS answers rather than filling them with 1s.
4. Reuse the current candidate calculator for that workflow. It owns the
   difference between new-case and follow-up exclusions. Supply the pinned
   DDI release and the proposal's availability snapshot, not changing user
   preferences halfway through an exploration session.
5. Reuse the existing exact content/assessment identity checks without
   invoking proposal generation, signing, autosave attribution, or the
   locking `*_plans.context()` command helpers. If existing input helpers
   assume a live encounter or user row, extract their pure calculation
   pieces instead of fabricating mutated database rows.
   Note the current asymmetry: follow-up pins assessment byte hashes, while
   new-case pins assessment versions only. Return the actual definition
   hashes used in this calculation and label their origin accordingly;
   do not claim an older new-case proposal contains a historical byte pin
   that it never stored. Both comparison sides use the same resolved
   definitions. Do not rewrite old proposals to manufacture that metadata.
6. Follow-up's evaluator currently loads verified installed assets internally.
   If necessary, add a small internal path accepting the already resolved
   pinned assets so evaluation and simulation cannot use different content.
   Preserve existing callers and defaults.
7. Recompute the baseline through the same shared calculation path and check
   it against the frozen proposal distribution/decision. Use stable IDs to
   align rows; ordering alone is insufficient.
8. Derive change summaries from actual input, assessment, effective-factor,
   terminal, probability, eligibility, and section differences. Never use an
   LLM or static example text to describe what happened.

**Branch and partial-result behavior:**

- New-case LAI remains `model_unavailable`; do not assign invented oral or
  injectable probabilities.
- Follow-up `continue_same`, `reduce_dose`, `adjunct_only`, direct clozapine,
  and same-drug LAI results retain their existing non-probabilistic meaning.
  A direct choice is not a posterior of `1.0`.
- When a scenario enters or leaves `oral_replacement`, update the pathway
  graph and callout even when one side has no probability distribution.
- If a branch answer becomes relevant and is null, return its active control
  and a typed partial result. Other valid assessment/section updates remain
  visible. Do not insert an extra submit/confirmation step or force the
  clinician to restart the sandbox.
- Unresolved observed model factors remain null/partial, not False or zero
  probability. No global `require_complete` call should suppress all useful
  simulation output because an unrelated wizard step is incomplete.

## 3. Typed Contracts & Stateless Simulation Endpoint

### Step C1 — Define a narrow, versioned request/response contract

**Add:** `backend/src/x_insight/cases/reasoning_contracts.py` for Pydantic
models and the small control-to-source mapping. Use the repository's strict
model style and discriminated unions. Leave generic HTTP error types in
`backend/src/x_insight/contracts.py`.

Proposed endpoint:

```text
POST /api/v1/encounters/{encounter_id}/reasoning/simulate
```

Initial request and every subsequent change use the same endpoint:

```json
{
  "schema_version": "reasoning-simulation-v1",
  "baseline_proposal_id": "<displayed-immutable-proposal-uuid>",
  "selected_treatment_id": "oral:aripiprazole",
  "client_request_id": 7,
  "changes": [
    {"kind": "panss", "item_id": "P1", "value": 3},
    {"kind": "boolean", "control_id": "qtc_risk", "value": true}
  ]
}
```

Contract details:

1. `changes: []` bootstraps the patient baseline, control descriptors,
   probability rows, selected-treatment attribution, and graph.
2. Permit a null selected treatment only on bootstrap or a non-model view;
   resolve the initial treatment deterministically from the first eligible
   patient candidate, then the first raw outcome if all are excluded.
3. Use a client request sequence only to correlate responses. It is not an
   idempotency key and creates no server-side request state.
4. Represent changes as discriminated variants: PANSS integer item, C-SSRS
   yes/no item, Boolean control, nullable Boolean/automatic override, and
   enumerated choice control. Enumerate supported IDs and value types per
   workflow; Boolean values must not pass as integer PANSS scores.
5. Do not accept arbitrary JSON paths, SQL/catalog identifiers as editable
   metadata, content pins, model versions, inherited baseline values,
   author/timestamp fields, final-plan text, or server-calculated flags.
6. Map each control ID to an explicit raw source field or small typed setter.
   For a comorbidity, edit its current `manual` override; `null` restores
   the existing automatic/inherited behavior. Do not overwrite lab values
   merely to force the effective flag.
7. Treat the list as the full set of overrides relative to the baseline,
   not as increments on a previous simulation. Use deterministic ordering,
   disallow duplicate control/item IDs, and canonicalize effective no-ops.

The response should contain these typed sections:

| Section | Minimum contents |
| --- | --- |
| `identity` | Schema/algorithm versions, echoed request ID, workflow, baseline proposal ID/input hash/payload hash, model SHA, content/assessment/DDI identities, availability snapshot identity, canonical scenario hash. |
| `controls` | Stable control ID, kind, label key, source descriptor, baseline/current value, allowed choices/range, active branch, and affected output kinds. |
| `patient` / `scenario` | Model-evaluation status, original/final terminal, full raw distribution, eligible candidates, exclusion reasons, sections, assessment results, effective factors. |
| `comparison_rows` | Union by stable treatment ID; patient/scenario probability and raw/eligible rank, nullable values for unevaluated sides, exclusion reasons, probability-point delta. |
| `selected_attribution` | Selected treatment ID; model-reference probability; patient/scenario posteriors, 16 signed factor contributions with source descriptors, and each reconstruction residual. Nullable when no model side is evaluated. |
| `diff` | Changed raw controls, derived-factor changes, assessment/section changes, top-choice sets and deterministic selected tops, terminal/eligibility changes, plus a banner kind and message parameters. |
| `active_graph` | Typed stable nodes and edges, pathway state, provenance references, selected target, deterministic order, pruning counts. |
| `evaluation` | `complete`, `partial`, or `non_model`, with relevant field issues or unevaluated reasons, not a catch-all false/empty-success value. |

Use raw floating-point probabilities in `[0, 1]`, signed attribution and
delta values in probability units, and nullable probabilities for genuinely
unevaluated sides. Only the UI formats percentages. Do not expose enormous
CPT tables or coalition vectors in this response.

### Step C2 — Implement the HTTP boundary as a transient calculation

**Modify:** `backend/src/x_insight/cases/router.py`.

1. Add the route with an explicit Pydantic response model. Use existing
   session/author access and POST CSRF transport conventions, without new
   roles, prompts, acknowledgments, or confirmation flows.
2. Read the owned encounter and the proposal identified by both proposal ID
   and encounter/workflow. Retrieve the needed model bytes by the pinned
   identity; do not resolve a mutable “latest model” pointer.
3. Do not call proposal-generation, autosave, signing, probability-review,
   worker-enqueue, idempotency-storage, or audit-write command functions.
   Existing transport/session behavior can remain unchanged; the simulation
   itself writes no domain or simulation records.
4. Use read-only loading rather than holding command-style `FOR UPDATE`
   locks throughout attribution. The immutable proposal is the request's
   anchor, so do not require a new `If-Match` or idempotency token for each
   slider change.
5. Hash the baseline identity plus normalized overrides and semantic version
   identities to produce the scenario hash. Treat selected treatment as an
   explanation-view key; include it in the explanation cache/response
   identity without changing the scenario's clinical-input hash.
6. Return `Cache-Control: no-store`; keep the sandbox out of browser
   persistent storage and application snapshot/export formats.
7. Use existing `ContractError` envelopes for malformed/unsupported controls
   and content mismatches. Keep partial clinical evaluation as typed result
   data, not an opaque request failure.
8. For repeated identical requests, return the same semantic result. Do not
   put a fresh server timestamp into the hashed result.

### Step C3 — Add matching frontend contracts and cancellable transport

**Add:** `web/src/features/reasoning-explorer/api.ts` and `types.ts`.

1. Define typed workflow, treatment/factor IDs, change unions, model statuses,
   probability rows, attribution rows, graph records, and diff variants.
2. Mirror the backend discriminator names and null semantics exactly. The
   new explorer must not rely on the current proposal payload's loose
   `[key: string]: unknown` shape or follow-up's local `unknown as Ranking`
   cast as its data contract.
3. Reuse `features/identity/api.ts`'s canonical `request` wrapper. Add an
   optional `signal?: AbortSignal` to its options and pass it to `fetch`;
   preserve all existing callers and error/session handling.
4. Send simulation POSTs through that wrapper, including existing CSRF
   transport. Do not build another authenticated fetch wrapper.
5. Maintain a backend-produced contract example or JSON-schema fixture with
   version and enum assertions in Python and frontend tests. Use existing
   tooling rather than adding a code-generation dependency just for this
   endpoint.

## 4. Factor Attribution Tornado Chart Component

### Step V1 — Build the treatment probability comparison first

**Add:** `web/src/features/reasoning-explorer/TreatmentProbabilityChart.tsx`.

1. Show full-distribution rows aligned by stable treatment ID, with a compact
   default view and an expandable list of all 22 outcomes. Include both
   patient and scenario top candidates, the selected treatment, and newly
   excluded/formerly eligible candidates in the compact union.
2. Use a shared fixed `0–100%` probability scale for all treatment bars.
3. Each row has a solid bar ending at `scenario_probability` and a **dotted
   vertical tick at `patient_probability`**. On initial load/reset, the tick
   coincides with the solid bar endpoint.
4. If only one side has a model evaluation, render the existing side and a
   textual “not evaluated for this decision” label for the missing side.
   A missing posterior is not a zero-length probability bar.
5. Add exact patient/scenario percentages and percentage-point change to
   accessible text/tooltips. Render zero-probability rows distinctly from
   unevaluated rows.
6. Separate eligible recommendation rank from raw probability and show
   exclusion badges without rescaling the remaining bars.
7. Keep the selected treatment stable when rankings change. Row selection
   changes the tornado/graph target, not the simulated inputs. Do not
   automatically switch the target on every top-recommendation flip.
8. Use a stable row order during active dragging and a deterministic
   documented order after settling. Avoid jumping rows while the clinician
   is interacting with a control.

### Step V2 — Render an anchored diverging tornado, not a waterfall

**Add:** `web/src/features/reasoning-explorer/FactorAttributionTornado.tsx`.

1. Accept only the typed selected-attribution result, selection callbacks,
   and localization/formatting inputs. Do not calculate Bayesian values in
   the component.
2. Use native SVG. Place the reference line at the selected treatment's
   `model_reference_probability`, annotated “Model reference: X%.”
3. For each factor, draw an independent horizontal bar from the reference
   line: left for negative attribution, right for positive attribution.
   The bar's signed extent is `100 * phi_i` percentage points.
4. Sort by absolute scenario attribution, using parent order as the stable
   tie-break. In comparison mode, sorting by
   `max(abs(patient_phi), abs(scenario_phi))` keeps both profiles visible.
5. Give the patient attribution an outline/subdued companion mark and the
   scenario attribution the main fill. Label the two consistently with
   the probability comparison chart.
6. Use a symmetric contribution extent around the reference line based on
   the maximum absolute contribution across both profiles. Do not make
   every row's length relative to its own local range.
7. Label horizontal displacement in signed percentage points. The reference
   is a real model probability, but an individual `reference + phi_i`
   endpoint is **not** a one-factor posterior. Do not clip a contribution
   just because that constructed coordinate falls below 0% or above 100%.
8. Present model-reference probability, patient posterior, simulated
   posterior, and `reference + sum(contributions)` in a compact summary.
   Individual rows are not stacked cumulative steps.
9. Optionally draw selected-treatment patient/scenario posterior reference
   lines with separate legend styles. The per-treatment dotted ghost ticks
   belong to the probability bars, not to a fabricated per-factor posterior.
10. Tooltips/focus details include source answer, effective Boolean, patient
    and scenario attribution, delta, and the exact mapped pathway.
11. Keep all 16 factors in the result. Collapse numerical-zero rows only
    for presentation, with an expansion control and displayed hidden total.
    Do not hide negative evidence or renormalize the visible bars.

### Step V3 — Match INSIGHT's current UI conventions

1. Use Astryx controls and existing clinical/academic theme tokens rather
   than adding a chart-specific visual theme or stylesheet framework.
2. Before coding controls, inspect the installed Astryx component/template
   docs. The inspected project CLI provides `Slider`, `Switch`, and
   `SegmentedControl`; use the project's version, not guessed props.
3. Keep positive/negative direction discernible through position, signs,
   text, and mark style, not color alone. Supply a tabular text equivalent
   for chart values and keyboard-accessible row/factor selection.
4. Localize labels and dynamic messages in English and Persian. Keep the
   quantitative probability/attribution axis explicitly LTR inside RTL
   layouts; allow labels and control layout to follow the application locale.
5. Use responsive SVG view boxes and reserved label space for long factor
   names. Stack controls/charts vertically on narrow screens.
6. Respect reduced-motion settings; short transitions should not obscure
   the stable patient ghost markers.

## 5. Live "What-If" Sandbox & Pruned Causal Pathway Graph

### Step S1 — Mount one shared workspace without touching final-plan state

**Add:** `web/src/features/reasoning-explorer/ReasoningExplorer.tsx` and
`useReasoningSimulation.ts`.

**Modify:** `web/src/features/new-case/NewCasePlanPage.tsx`.

1. Mount the explorer beside/below the original-proposal view, before the
   existing final-plan editing area. Pass encounter ID, workflow, and the
   displayed immutable proposal ID.
2. Keep simulation state entirely inside the explorer. Do not pass simulated
   raw data to either wizard's autosave callbacks, `onProposal`, final-plan
   text generation, clipboard output, or signing requests.
3. Bootstrap using `changes: []`. Capture that response as the patient
   comparison result; select the initial eligible treatment once.
4. A new displayed proposal ID or encounter ID creates a new baseline and
   resets transient controls. A request response must never silently
   re-anchor the baseline to a newly generated proposal.
5. Continue showing the existing proposal's freshness state in its existing
   place. The explorer identifies the exact proposal it compares against;
   it does not create an additional freshness acknowledgment flow.

### Step S2 — Implement the adjacent live control strip

**Add:** `web/src/features/reasoning-explorer/WhatIfControls.tsx`.

Use backend control descriptors and existing assessment definitions, with
workflow-specific source mapping:

| Group | Controls | Calculation path |
| --- | --- | --- |
| PANSS | Item selector and integer slider/stepper, range 1–7; expandable full item list. | `panss.answers[item_id]`; released assessment evaluator. |
| C-SSRS | Five recent-ideation yes/no controls, preserving current visibility/answer semantics. | `cssrs.answers`; existing assessment and independent-section rules. |
| Comorbidities | Effective hepatic/renal/glucose/lipid/prolactin concerns with automatic/manual choice; applicable psychiatric/reproductive controls. | Current workflow's condition/manual/psychiatric source fields; rerun its evaluator. |
| Model-linked concerns | Smoking, cannabis, sedation concern/effect, seizure risk, QTc, BMI category/source and orthostatic concern/effect where the workflow supports them. | Existing raw sources; no independent direct writes to derived BN factors. |
| Follow-up symptoms/effects | Existing movement-effect toggles and sedation choices. | `follow_up.effects` and existing branch priority. |
| Follow-up decision branches | Recovery, adherence, LAI preference, and the currently reached branch answers. | `follow_up.considerations` / `branches`; same `decision()` function. |

1. Render the relevant subset compactly next to charts; use expandable groups
   rather than a second full registration wizard.
2. Show baseline and overridden values, plus an unsaved “simulation” state
   label. A per-control revert removes that override from the change set.
3. For derived conditions, show the lab/inherited source alongside the
   effective override. “Automatic” restores the original calculation, not
   an assumed No answer.
4. New-case movement-effect factors are fixed workflow defaults; display
   their attribution/provenance but do not create editable raw fields that
   the new-case evaluator does not support.
5. Hidden dependent values follow the existing evaluators' semantics. For
   example, a marijuana selection under a disabled substance-use parent
   does not become active evidence by itself.
6. Reached follow-up branch controls appear as soon as a change activates
   that branch. Keep previously entered hypothetical branch answers in the
   local override set so returning to the branch is predictable; only
   executed/active dependencies contribute to the current result.

### Step S3 — Make live updates deterministic and race-resistant

1. Update the visible control value immediately. Debounce continuous input
   approximately 150 ms; discrete switches/branch choices can request at
   once. No Calculate or Apply button is needed.
2. Increment a monotonic local request epoch for every input, selection,
   baseline replacement, and reset. Cancel the previous request with
   `AbortController` and also ignore responses whose epoch/identity no
   longer matches. Cancellation alone does not prevent stale results.
3. Check proposal ID, selected treatment ID, scenario hash/normalized
   changes, and echoed request sequence before committing a response.
4. Commit charts, graph, callout, assessment summary, and field issues from
   one response atomically. Do not mix a new graph with old probabilities.
5. Keep the previous coherent result visible with an “Updating” state while
   a request is pending. Do not move the patient ghost ticks.
6. If a request fails, retain the changed controls and last coherent result,
   visibly mark it as the previous result, and offer an ordinary retry.
   Do not render requested values as if inference succeeded.
7. Profile both cold loading and warm simulation. Aim for a warm API p95
   around 500 ms or better on the documented development hardware, plus the
   short debounce. Record measured values rather than claiming a universal
   instantaneous response. Reuse cached baseline attribution and pinned DDI
   lookups before introducing extra asynchronous infrastructure.

### Step S4 — Render the pruned pathway graph

**Add:** `web/src/features/reasoning-explorer/ActivePathwayGraph.tsx`.

1. Render only the backend's typed graph; do not independently infer active
   rules or probability effects in TypeScript.
2. Use a small SVG layout with ordered columns: source inputs → derived
   factors/assessment rules → selected model outcome/decision → eligibility
   and final selection. Position by stable IDs and semantic group.
3. Existing admin graph code is a drawing reference. Extract a tiny shared
   geometry helper only if genuinely useful to both renderers; do not make
   physician exploration depend on the admin graph API.
4. Distinguish Bayesian dependencies, rule dependencies, and filter edges
   using a legend and dash/shape/text cues. Differentiate patient-only,
   scenario-only, and shared paths without relying only on color.
5. Clicking/focusing a factor in the tornado highlights its source-to-target
   path and matching control. Selecting a graph node reveals the same
   values/provenance, not a separate invented explanation.
6. Preserve nonzero and changed paths when collapsing low-detail sections;
   show expansion counts. Use stable layout positions across related
   scenarios so a small input change does not reshuffle the whole graph.
7. Render missing/partial model results and deterministic-only terminals
   normally: the active decision path remains useful even without bars.

### Step S5 — Build the diff callout from structured result data

**Add:** a small `SimulationDiff.tsx` component and pure formatting helpers
inside the feature; keep dynamic message templates in the existing
localization conventions.

Use separate message variants:

- **Top choice changed:** “QTc concern: No → Yes changes the top eligible
  recommendation from {old drug} ({old probability}) to {new drug}
  ({new probability}).”
- **Same top, changed probability:** “{input change}; {drug} remains top:
  {old probability} → {new probability} ({delta} pp).”
- **Eligibility changed:** identify the changed exclusion/eligibility rule
  even if raw model probabilities are identical.
- **Decision branch changed:** identify the old and new terminal, including
  entering/leaving oral replacement or a direct-treatment result.
- **Independent output changed:** “C-SSRS item 4: No → Yes; care-setting
  section changed; treatment ranking unchanged.” Use actual computed values.
- **Assessment only:** the PANSS-only example in section 1.3.
- **Several inputs changed:** list the actual changed controls compactly;
  describe the result of the **combined scenario**, not a claim that any
  one control alone caused the flip.
- **No change / partial:** explicitly report no output change, or identify
  outputs still unevaluated, without inventing a recommendation.

Use the final eligible candidate list for the “top recommendation,” not
the highest raw probability among excluded drugs. Preserve deterministic
tie-breaking and include tie metadata so an equal-probability ordering is
not described as a decisive probability advantage. Compare unrounded values;
format text only afterward. All numbers come from the response.

### Step S6 — Implement single-click reset as a true local reset

1. **Reset to Patient Data** cancels the debounce/request, increments the
   epoch, removes every override, and restores the captured patient result
   immediately, without a new network request or dialog.
2. Restore all controls, assessments, sections, graph, banner, and probability
   bars. Ghost ticks and bar endpoints coincide again.
3. Restore the bootstrap selected treatment together with its cached patient
   attribution and graph. This makes the entire initial view immediately
   available and avoids mixing a reset chart with another treatment's
   attribution or making reset depend on another request.
4. A response from a pre-reset request cannot overwrite the restored state.
5. Reset does not write to the encounter, proposal, physician availability,
   or final plan. It resets the sandbox, not the patient record.

## 6. Verification & Anti-Drift Checklist

These are implementation checks, not additional clinical approval or
confirmation steps. Add tests beside existing tests and run them from the
smallest calculation outward.

### Step T1 — Numeric attribution and model-profile tests

**Add:** `backend/tests/models/test_oral_attribution.py`.
**Extend:** `backend/tests/models/test_oral_antipsychotic.py` as needed.

- [ ] Tiny 1-, 2-, and 3-parent synthetic CPTs match independently enumerated
  coalition values, prior probabilities, and Shapley values.
- [ ] Unequal root priors are respected; the prior is neither a uniform
  row average nor the all-No CPT row.
- [ ] Known additive and interacting examples demonstrate signs, symmetry,
  null-player behavior, and exact additive reconstruction.
- [ ] Observed False factors can have nonzero contributions.
- [ ] Parent-bit order and outcome-column order match `rank_oral`; include
  asymmetric fixtures that fail if either order is transposed.
- [ ] Patient and scenario reconstruction residuals are within a documented
  tolerance, initially `1e-10` absolute probability units.
- [ ] No explanation call mutates the cached model; ranking before/after is
  identical. Test repeated requests and different selected treatments.
- [ ] Full 22-outcome distributions remain normalized before filtering;
  unavailable/interaction exclusions do not renormalize remaining values.
- [ ] Corrupt/mismatched model bytes retain existing loader behavior.

Inspection probe for a reproducible bundled-model example: with all 16
factors No, all 22 outcomes are tied at `1/22`; current stable ordering puts
Chlorpromazine first. Turning only `QTc_Prolongation_Risk` to Yes makes
Aripiprazole the first raw outcome at `1/7`. Use this as a deterministic
row-lookup check, not evidence for the illustrative PANSS/Olanzapine numbers
or a claim that the tied baseline has a uniquely superior drug.

### Step T2 — Evaluation, controls, and active-path tests

**Add:** `backend/tests/assessments/test_reasoning_exploration.py`.
**Reuse/extend:** existing new-case/follow-up rule and candidate fixtures.

- [ ] Empty overrides reproduce each workflow's frozen proposal evidence,
  distribution, candidate order, sections, and original/final terminal.
- [ ] Smoking, cannabis gating, hepatic/renal/manual overrides, BMI, QTc,
  sedation, and movement effects follow their existing source mappings.
- [ ] Automatic/null condition overrides restore lab/inherited behavior;
  unresolved follow-up factors remain unresolved rather than becoming No.
- [ ] A PANSS-only edit changes the assessment result but not oral ranking.
- [ ] C-SSRS changes modify only currently connected outputs; nested answer
  semantics remain consistent with the workflow.
- [ ] Follow-up effect-priority paths, controlled/no-improvement branches,
  direct clozapine/LAI, and non-model results match existing decisions.
- [ ] Missing newly reached branch answers return partial results and active
  controls, without inventing answers or suppressing unrelated results.
- [ ] Graph paths use existing factors, traces, and section reasons; no
  fabricated PANSS → drug edge appears.
- [ ] Graph nodes/edges remain deterministic, distinguish model/rule/filter
  semantics, retain False evidence, and preserve patient/scenario path union.
- [ ] Zero Shapley contribution is not mistaken for no structural CPT
  dependence; independent rule changes remain visible when ranking is stable.
- [ ] Scenario provenance never masquerades as an actual recorded override.

### Step T3 — Endpoint contract and statelessness tests

**Add:** `backend/tests/http/test_reasoning_simulation.py`.

- [ ] Both workflows use the same route and explicit versioned result model.
- [ ] Request/response examples, enum values, strict Boolean/integer handling,
  null semantics, duplicate overrides, and malformed IDs are covered.
- [ ] Existing author-scoping and session/CSRF behavior are reused; one
  encounter's proposal cannot become another encounter's baseline.
- [ ] Repeated identical calculations return identical semantic hashes.
- [ ] Model/content/assessment/DDI or availability identity changes cannot
  silently mix the two comparison sides.
- [ ] Snapshot domain rows/counts/hashes before and after multiple simulation
  POSTs: encounters and revisions, proposals, final plans, signatures,
  probability reviews, batches/jobs, domain audit records, and idempotency
  records remain unchanged. Account/session transport effects are not
  misreported as simulation-domain persistence.
- [ ] No worker/provider/MCP call occurs; no simulation creates a proposal.
- [ ] `Cache-Control: no-store` is present; responses do not include CPT
  tables, unrelated records, or newly fabricated signed artifacts.
- [ ] Partial/no-model states are explicit and do not emit fake 0%/100%
  probabilities for direct or unevaluated choices.

### Step T4 — Frontend state, race, and presentation tests

**Add:** `web/tests/reasoning-simulation.test.mjs` for pure reducer/hash/
comparison/formatting helpers, following the existing Node test style.

**Add:** `e2e/reasoning-explorer.spec.ts` for rendered interaction; use the
existing Playwright Chromium and Firefox projects.

- [ ] Dotted ticks appear at actual patient probabilities on every rendered
  probability row, including zero and excluded rows when expanded.
- [ ] Scenario bars move while patient ticks remain fixed; reset reunites
  their endpoints exactly.
- [ ] Tornado reference remains the model prior across simulations; bar
  direction, magnitude, signs, percentage-point labels, and reconstruction
  match the backend. A tornado is not rendered as a cumulative waterfall.
- [ ] Slow/fast overlapping requests, rapid slider changes, target changes,
  baseline replacement, request failure, and reset-during-flight cannot
  commit stale or mixed results.
- [ ] Charts, graph, banner, and summaries update as one coherent result.
- [ ] Selected treatment remains stable through recommendation changes;
  excluded/absent treatment rows align by ID, not rank or display name.
- [ ] Banner variants cover top flips, same-top changes, ties, exclusion-only
  changes, multi-input scenarios, PANSS-only changes, and non-model branches.
- [ ] Single-click reset clears every override and result difference without
  autosave, dialogs, final-plan edits, or stale response resurrection.
- [ ] EN/FA, LTR axes in RTL, light/dark, keyboard focus, text equivalents,
  long labels, narrow layout, and reduced motion are exercised.
- [ ] Existing copy/generate/final-plan/sign payloads do not contain transient
  simulation fields and retain their prior behavior.

### Step T5 — Run focused checks, then existing regression suites

Use the repository's Python 3.12 environment and documented Node 22 runtime
for application/browser checks; historical handoffs record unrelated Node
26 browser-worker failures. Configure the usual disposable test database for
database-backed tests. Do not report a skipped/unrun suite as passing.

```bash
# New focused backend tests, after creating the files above.
cd backend
uv run pytest -q \
  tests/models/test_oral_attribution.py \
  tests/assessments/test_reasoning_exploration.py \
  tests/http/test_reasoning_simulation.py

# Existing fixed-model and workflow regressions.
uv run pytest -q \
  tests/models/test_oral_antipsychotic.py \
  tests/models/test_new_case_candidates.py \
  tests/models/test_follow_up_candidates.py \
  tests/assessments/test_new_case.py \
  tests/assessments/test_new_case_rules.py \
  tests/assessments/test_follow_up_state.py \
  tests/assessments/test_follow_up_rules.py \
  tests/assessments/test_follow_up_plan_inputs.py \
  tests/http/test_new_case_plans.py \
  tests/http/test_follow_up_plans.py

cd ..
make test-web
make check

# Against the existing local acceptance stack; both configured browsers.
E2E_BASE_URL=<acceptance-stack-url> npx playwright test \
  e2e/reasoning-explorer.spec.ts \
  e2e/new-case.spec.ts \
  e2e/follow-up-v1.spec.ts

# Broader checks once focused coverage is green.
make test-backend
make test-recovery
```

### Step T6 — Final anti-drift review

- [ ] The legacy `infer_effective` empty-evidence contract is unchanged.
- [ ] Current oral ranking still uses the same pinned CPT and 16 source
  factors; no arbitrary PANSS/C-SSRS probability weights were introduced.
- [ ] Model reference, actual patient baseline, historical follow-up baseline,
  and simulated scenario have distinct names and display semantics.
- [ ] Attribution is exact probability-space Shapley under declared root
  priors, not CPT sensitivity or an unlabeled counterfactual approximation.
- [ ] Candidate eligibility remains workflow-specific and separate from
  model probability attribution; no post-filter renormalization occurred.
- [ ] Every graph edge is backed by model structure or executed application
  logic; the requested pathway title does not introduce a new causal model.
- [ ] Domain state, immutable proposal/signed bytes, v4/v5 snapshot formats,
  backup/restore schemas, and encounter revisions are unchanged.
- [ ] No new dependency, schema migration, approval, confirmation, or research
  development gate was added to deliver this feature.
- [ ] Verification and measured warm/cold response times are recorded in a
  dated progress report; the short progress index links that report.

## 7. Execution order and handoff

Implement in small, verifiable slices:

| Order | Work | Completion evidence |
| --- | --- | --- |
| 1 | B1–B2 plus T1: loader metadata and exact selected-treatment attribution. | Existing ranking parity and independently enumerated numeric tests. |
| 2 | B3–B4 plus T2: source overrides, shared evaluators, candidate policy, active graph. | Empty-override parity and branch/source/graph fixtures. |
| 3 | C1–C3 plus T3: typed endpoint and matching frontend transport. | Contract fixtures and demonstrated simulation-domain statelessness. |
| 4 | V1–V3: probability bars, ghost ticks, and anchored tornado. | Real endpoint values rendered with correct units, reference, and selected treatment. |
| 5 | S1–S3: mount, controls, debounce/cancellation, atomic result state. | Both workflows respond live without touching saved data or final-plan state. |
| 6 | S4–S6: linked pathway graph, structured banner, single-click reset. | Branch changes, factor highlighting, no-op/PANSS cases, and reset races covered. |
| 7 | T4–T6: browser/regression/performance checks and documentation handoff. | Dated command results and remaining unrelated limitations recorded precisely. |

Update the API docs with the new endpoint, add the explorer to the user guide,
and record actual implementation progress under
`project-documents/dev/progress/`. Keep implementation reasoning in this
plan until decisions are implemented; then use existing topic files in
`context/` for any newly settled rationale rather than duplicating this
entire execution plan there.

### Documentation-session verification

This plan was grounded in current source, the listed progress handoffs,
installed package manifests, and the bundled model. A read-only model probe
verified its 16 unequal root priors, the all-No tie, and the QTc-only raw
ranking example. The installed project Astryx CLI confirmed the named
controls. A separate 1–4-parent synthetic calculation verified the proposed
coalition transform and Shapley reconstruction against exhaustive enumeration
for every evidence assignment. Local documentation links, required sections,
existing regression-test paths, and scoped whitespace checks passed.
Unrelated pre-existing agent TOML whitespace warnings were left untouched.
No application code was changed and no application test suite was run as
part of writing this plan; the checklists describe future work.

[^shapley]: Scott M. Lundberg and Su-In Lee, *A Unified Approach to
    Interpreting Model Predictions*, Advances in Neural Information Processing
    Systems 30 (2017), arXiv:1705.07874. Reference for additive Shapley feature
    attribution, not a clinical source or a prescription for this app's
    particular background distribution.
