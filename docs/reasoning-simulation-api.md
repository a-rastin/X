# Reasoning simulation API

`POST /api/v1/encounters/{encounter_id}/reasoning/simulate` compares a stored
New case or follow-up proposal with transient changes. Use the proposal ID
currently displayed in the interface. The request uses the proposal's frozen
inputs and pinned model, content, assessments, interaction release and
availability. Each request starts from that same proposal.

## Send a request

Use an active physician session belonging to the encounter's author. Send
the session cookie, `X-CSRF-Token` and `Content-Type: application/json`.
The route takes an encounter UUID. The session and CSRF token are sufficient
for request authorization alongside encounter ownership. A successful
response is JSON with `Cache-Control: no-store`.

Send an empty `changes` array and a null `selected_treatment_id` for the initial
request. The server uses the first eligible patient candidate or the first
raw patient outcome. When only the scenario has a distribution, it uses the
scenario's candidates and outcomes in the same order.

```json
{
  "schema_version": "reasoning-simulation-v1",
  "baseline_proposal_id": "54a78184-1f35-4a6d-8baf-521b639ce1ec",
  "selected_treatment_id": "oral:aripiprazole",
  "client_request_id": 7,
  "changes": [
    {"kind": "panss", "item_id": "P1", "value": 3},
    {"kind": "boolean", "control_id": "qtc_risk", "value": true}
  ]
}
```

Every field is required. The contract accepts up to 128 changes and a
`client_request_id` from 0 to 9007199254740991. The server echoes that ID so
clients can correlate responses. Select a treatment ID from the response's
oral distribution for subsequent modeled scenarios. Null is also valid
when either side has no distribution or the overrides have no effective
changes. The schema lists all 22 oral IDs.

| Change `kind` | Identifier | Accepted `value` |
| --- | --- | --- |
| `panss` | `item_id` in `P1`–`P7`, `N1`–`N7` or `G1`–`G16` | JSON integer 1–7 |
| `cssrs` | `item_id` in `css_i1_recent`–`css_i5_recent` | JSON Boolean |
| `boolean` | Workflow-specific `control_id` | JSON Boolean |
| `nullable_boolean` | Workflow-specific `control_id` | JSON Boolean or null |
| `choice` | Workflow-specific `control_id` | Listed string choice, or null if listed |

Use the returned `controls` to discover each workflow's valid identifiers,
variant, range and choices. For example, `qtc_risk` maps to the workflow's
considerations answer. The `conditions.hepatic_failure.manual` control maps
to the current manual override. Sending null for that override restores
automatic evaluation. The `source_path` field describes the source for display.
Send `control_id` or `item_id` as shown in the table. Duplicate IDs, extra keys
and implicit Boolean, string or integer coercions are rejected. Send the
complete set of simulated overrides on each call.

Use the existing authenticated web client to pass through an abort signal.

```ts
import { simulateReasoning } from "./features/reasoning-explorer/api";

const controller = new AbortController();
const result = await simulateReasoning(encounterId, {
  schema_version: "reasoning-simulation-v1",
  baseline_proposal_id: displayedProposalId,
  selected_treatment_id: null,
  client_request_id: 0,
  changes: [],
}, controller.signal);
```

## Read the response

| Field | Contents |
| --- | --- |
| `identity` | Schema and attribution algorithm versions, echoed request ID, workflow, proposal digests, pinned model/content/assessment/DDI and availability identities, selected treatment and scenario hash. |
| `controls` | Stable IDs with a label key, source path, patient/scenario values, allowed range or choices, scenario activity and the output kinds affected. |
| `patient`, `scenario` | Model status, original and final decisions, raw distribution, eligible candidates, direct choice if applicable, assessments, sections and model factors. |
| `comparison_rows` | Union of both oral distributions, with patient/scenario probabilities, ranks, exclusions and scenario-minus-patient probability delta. |
| `selected_attribution` | Model-reference probability plus each evaluated side's posterior, signed contributions for all 16 factors and reconstruction residual. Null when neither side has an attribution. |
| `diff` | Typed patient/scenario differences in source inputs, factors, assessments, sections, probabilities, exclusions and selection. Includes a banner kind and its parameters. |
| `active_graph` | Stable node and edge identifiers, observations, provenance, path state and pruning counts for the selected explanation. |
| `evaluation` | A patient and scenario status, field issues and an optional reason for an unevaluated model. |

The scenario hash tracks the normalized effective overrides and frozen
identities. Changing request ID, selected treatment, override order or a
no-op override preserves this hash. The selected treatment can change the
attribution and graph. Simulation calls do not write clinical domain records.

Probabilities use the interval 0–1, at full precision. A delta of `0.03`
represents 3 percentage points. Raw rank follows the unfiltered distribution.
Eligible rank follows the distribution after exclusions. The candidate list
contains the workflow's capped recommendations. A direct decision carries a
formulation ID and applicable exclusions. Missing modeled probabilities and
ranks appear as null in comparison rows. Partial results can still contain
assessment and section updates.

This response excerpt shows the evaluation of an oral patient baseline and
a New case scenario that entered the LAI branch.

```json
{
  "evaluation": {
    "patient": {"status": "complete", "issues": [], "unevaluated_reason": null},
    "scenario": {
      "status": "non_model",
      "issues": [],
      "unevaluated_reason": "model_unavailable"
    }
  }
}
```

| Field | Values |
| --- | --- |
| Workflow | `new-case-v1`, `follow-up-v1` |
| Attribution algorithm | `oral-root-prior-shapley-v1` |
| Assessment hash origin | `historical_byte_pin`, `resolved_from_pinned_versions` |
| Evaluation status | `complete`, `partial`, `non_model` |
| Model status | `available`, `model_unavailable`, `non_model`, `no_eligible_oral_candidate`, `direct_available`, `direct_excluded`, `unsupported_direct_mapping`, `partial` |
| Decision terminal | `oral_replacement`, `clozapine`, `same_drug_lai`, `physician_review`, `continue_same`, `reduce_dose`, `adjunct_only`, `oral`, `lai` |
| Exclusion | `zero_probability`, `unavailable`, `current_main`, `interaction_dx`, `unsupported_direct_mapping` |
| Assessment status | `unanswered`, `partial`, `complete`, `not_assessed` |
| Affected output | `assessment`, `section`, `model`, `decision` |
| Banner kind | `no_change`, `top_choice_changed`, `top_probability_changed`, `eligibility_changed`, `decision_branch_changed`, `assessment_changed`, `section_changed`, `inputs_changed` |
| Path state | `patient`, `scenario`, `both` |
| Scenario path state | `active`, `superseded` |
| Node kind | `source`, `factor`, `assessment`, `rule`, `section`, `treatment`, `selection` |
| Edge kind | `input_derivation`, `bn_dependency`, `rule_dependency`, `candidate_filter` |
| Provenance kind | `source`, `recorded_override`, `simulated_override`, `workflow_default`, `derived` |

## Handle errors

Errors use the normal JSON envelope with `code`, `message`, `request_id`,
`field_errors` and `retryable`. Read `field_errors` for invalid fields.
Unresolved clinical inputs within a valid scenario are reported in a
successful response with `evaluation.scenario.status` set to `partial`.

| HTTP | Code | Cause |
| --- | --- | --- |
| 401 | `UNAUTHENTICATED` | Missing or invalid session |
| 403 | `FORBIDDEN` | CSRF, role or encounter author check failed |
| 404 | `NOT_FOUND` | Encounter absent or proposal does not belong to it |
| 409 | `WORKFLOW_MISMATCH` | Encounter workflow does not support simulation |
| 409 | `REASONING_BASELINE_UNAVAILABLE` | The frozen proposal cannot be verified or replayed |
| 413 | `REQUEST_TOO_LARGE` | Request body exceeds the shared API limit |
| 422 | `VALIDATION_FAILED` | Malformed request, unsupported control/value, duplicate ID or required treatment selection |

For example, an unrecognized Boolean control produces HTTP 422.

```json
{
  "code": "VALIDATION_FAILED",
  "message": "Unsupported reasoning change.",
  "request_id": "example-request",
  "field_errors": {"unknown_control": ["Unsupported control, kind or value."]},
  "retryable": false
}
```

See the companion proposal APIs for [New case](new-case-state.md) and
[follow-up](follow-up-api.md).
