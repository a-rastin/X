# Proposal generation

Step 7 checks the saved draft's proposal readiness before creating a generation
batch. Core History completion covers only the applicable fields from the seven-field
inventory below; it does not establish that the proposal-context answers are ready.

## Core History

Registration Step 5 (History) uses these current choices and conditions. Follow-up
History uses the same contract.

| Question | Choices | Shown when / default |
| --- | --- | --- |
| History of lifetime antipsychotic exposure | Yes, No, Unknown | Always; Unknown |
| Onset timing relative to exposure or withdrawal | During exposure; Within 4 weeks after oral withdrawal; Within 8 weeks after LAI withdrawal; Other; Unknown | Lifetime exposure is Yes; Unknown |
| Adequacy of prior treatment trial | Adequate, Inadequate, Unknown | Lifetime exposure is Yes; Unknown |
| Response to prior treatment | Some level of response, No response, Unknown | Lifetime exposure is Yes and adequacy is Inadequate; Unknown |
| Involuntary movements persist over at least 4 weeks | Yes, No, Unknown | Always; Unknown |
| Alternative explanations ruled out | Yes, No, Unknown | Movement persistence is Yes; Unknown |
| Falls or functional limitation from parkinsonian signs | Yes, No, Unknown | Movement persistence is Yes; Unknown |

Other means a time outside the three named onset timings. The Prior treatment
heading and navigation link appear only for lifetime exposure Yes. Changing a
controlling answer clears hidden answers and their provenance; revealing them
again starts at Unknown. A default is not attributed as a clinician entry.

The baseline movement-examination and functional-impact questions are retired,
alongside the three previously removed exposure-duration/timeline questions.
Core completeness starts at **2** applicable questions, adds **2** for lifetime
exposure Yes, **1** for Inadequate adequacy when exposure is Yes, and **2** for
movement persistence Yes (maximum **7**). An allowed Unknown counts as answered
for core completion; proposal readiness remains a separate gate.

The seven core IDs are `h_exposure_dopamine_blocker`,
`h_movement_persistence_4w`, `h_onset_timing`, `h_trial_adequacy_prior`,
`h_prior_response`, `h_alternative_cause_considered` and `h_falls_or_limitation`.

Strict History writes reject new retired IDs and null/Not assessed values for the
seven core questions with HTTP `422`. Older drafts normalize Not assessed on
these questions to Unknown; malformed legacy nulls remain visible as validation
errors. Reads and resaves clear retired/hidden current answers and their
provenance. The general draft PATCH retains its legacy compatibility behavior.
Follow-up copies normalize current History while preserving trusted same-patient
baseline provenance. Existing signed records and analysis snapshots are immutable;
new current-History projections and signatures use the cleaned inventory.
Historical `followup_baseline.history_values` can retain retired IDs as evidence,
including when embedded in a newer analysis snapshot's fingerprint payload.

## Complete proposal context

History exposes up to **28 registration** or **76 follow-up** context controls.
Registration has **26** applicable controls when lifetime exposure is No or Unknown,
and **28** when it is Yes: established antipsychotic and prior NMS become applicable.
These counts exclude core History. Registration readiness additionally requires
the completed Step 4 C-SSRS assessment, giving **27 or 29** required context inputs.
The revised pharmacotherapy contract retains physical vulnerability and substance
contribution as Yes/No/Unknown, default Unknown. Established antipsychotic appears
only for lifetime exposure Yes and defaults to Unknown. Legacy Present/Absent
answers map to Yes/No; Not assessed and NoEstablishedTreatment map to Unknown.
Six pharmacotherapy questions are retired: prior treatment difficulty, preference
barrier, exposure modifier, trial duration review, urgent safety concern and
monitoring gap. Their network nodes remain in full CPT estimation without patient
input mappings; retirement does not assign those nodes clinical values.

The revised LAI contract retains five controls:

| Question | Choices | Shown when / default |
| --- | --- | --- |
| Scenario preference for an LAI or oral treatment | Prefers LAI, Prefers oral, Undecided, Declines LAI, Unknown | Always; Unknown |
| Antipsychotic adherence | Adequate, Poor, Unknown | Always; Unknown |
| Additional scenario context for exploring an LAI, such as treatment gaps, care transitions, repeated hospitalizations or emergency visits. | Present, Absent, Unknown | Always; Unknown |
| History of neuroleptic malignant syndrome represented in the scenario. | Yes, No, Unknown, Not assessed | Lifetime exposure is Yes; Unknown |
| Scenario barriers involving access, transport, scheduling, cost, staffing or resources for LAI delivery. | Present, Absent, Unknown | Always; Unknown |

The schizophrenia-status, candidate oral-antipsychotic experience, product-plan
review and current/previous/considered LAI-use controls are retired. Not assessed
is removed from preference, adherence, discussion context and delivery barriers;
Uncertain is also removed from adherence. Those legacy values normalize to
unattributed Unknown. Hiding prior NMS clears its current answer and provenance.

The aggression contract retains five controls: **Substantial aggression-related
risk** (Yes, No, Unknown; default Unknown), clozapine treatment status, willingness
to discuss clozapine, monitoring-plan availability and medical-review completion.
The latter four retain their existing choices and lack implicit defaults.
Schizophrenia status, persistence despite other treatments, urgent safety concern,
adherence/exposure uncertainty and modifiable risk-factor controls are retired.

High-suicide clozapine has **no History controls**. Its context comes from the
saved complete, released Step 4 C-SSRS assessment: released item responses,
completeness and results flow through the frozen patient projection, provider
context and scoped private MCP. The assessment is an input prerequisite, not a
new categorical suicide-risk mapping or clinical applicability rule. No threshold,
treatment-persistence value or other missing clinical state is inferred from it.
Assessment changes participate in fingerprint freshness and selective regeneration.

Other workflow controls retain their existing categories and defaults. The
separate **Proposal readiness** summary lists every unresolved requirement with
a readable field label, a reason and a link to its History control or Step 4 assessment.
Controls wait for the server's field inventory to load before accepting edits.

Response counts distinguish **answered** from **execution-ready**. Unknown and
Not assessed can count as answered while still requiring clarification. Missing,
empty, conflicting, invalid and explicitly unresolved required values also
block execution. Follow-up gates additionally identify known answers that fail
their existing baseline, trial or PANSS prerequisites. Assess the actual facts;
do not substitute an answer merely to make the gate pass.

Readiness uses the same backend manifests, exact versioned source paths and gate
rules as execution. It returns all blockers instead of only the first one.
Required uncertainty is checked before false applicability: a question becomes
not applicable only after all required context is known and its gate permits it.
Readiness is input validation, not a completed proposal or a signing permission.

History labels its summary with the saved revision and shows when changes are
pending. Before every automatic start, manual new run or retry, Step 7 flushes
edits, checks the live save state and revision, then reads fresh readiness,
diagnosis and medication prerequisites. Failed saves, conflicts, stale readiness
and unreadable checks prevent a start. **Check again** repeats the checks.

## Recover an earlier clarification run

1. Follow the proposal-readiness links to History or Step 4 C-SSRS. Review every
   unresolved input and save the assessed responses. Confirm the save succeeds and readiness
   reflects the saved revision.
2. Return to Step 7. If an earlier run exists, use **Start new run with current
   inputs** explicitly. Reloading or returning to the step restores that
   immutable run; it does not regenerate or rewrite it.
3. Both retry and **Start new run with current inputs** from an existing run
   preserve that batch's frozen question packages, use current saved inputs and
   must pass readiness again. An initial start without `source_batch_id` resolves
   the active release. A worker must be running to process any queued questions.
   Exception: a frozen pharmacotherapy, high-suicide, LAI or aggression application
   package with an obsolete History input contract
   returns `HISTORY_INPUT_CONTRACT_CHANGED`. Use **Start new run with current
   History** to resolve the current contract without `source_batch_id`; the old
   recorded run remains readable.

An old run can still show `needs_clarification`, its earlier source revision,
or a raw first-blocker reason. **Current inputs** describes snapshot freshness,
not completeness. When no successful question result exists, there is no CPT
result to accept. The UI omits the redundant acceptance panel and explains that
transparency is available after completion. In this context, a computed
“baseline” is the question's original successful result; it is distinct from
the previous encounter needed by some follow-up gates.

Completed proposals retain the existing probability-review, result-acceptance,
freshness and signing checks. Input readiness does not bypass them.

## API contract

Routes below use the `/api/v1` prefix and enforce the draft author's access.

- `GET /encounters/{id}/proposal-readiness` reads current saved inputs against
  the active reviewed workflow without creating a batch, job or snapshot.
  Optional `?source_batch_id={batch_id}` checks those inputs against the source
  batch's immutable packages for retry. The batch must belong to this encounter.
- The response includes `workflow`, `source_revision`, `ready`, `status`,
  `required_count`, `answered_count`, `execution_ready_count`, `blockers` and
  `questions`. Each question includes its gate status, reason and counts.
  Each blocker includes `question_key`, `question_label`, `source_path`,
  `label`, `status` and `reason`. A question-wide blocker can have a null path.
- History reads and saves include the same `proposal_readiness` object. A new
  idempotent History save and its replay retain that response field.
- `POST /encounters/{id}/generation-batches` accepts `{}` for the current
  released workflow or `{ "source_batch_id": "…" }` for an exact-package retry.
  Reviewed starts and retries reject incomplete context with HTTP `422`, code
  `PROPOSAL_INPUTS_INCOMPLETE`, and field errors keyed by source path, before
  batch persistence, admission or DDI pinning. A readiness read cannot reserve a
  future revision: the POST independently validates inputs and `If-Match`.
- `GET /encounters/{id}/generation-batches/latest` discovers an existing run
  without browser storage. Cached and server-discovered runs remain readable.

Starts retain CSRF, `If-Match`, idempotency and admission checks. The existing
inline `package`/`packages` contract remains available for engineering clients,
including synthetic clarification scenarios. Execution gates and backend
acceptance checks remain enforced.

## Reviewed content and installation

Step 7 resolves the reviewed workflow on the server. A new browser does not need
question packages or a previous generation run in local storage. Registration
uses five ordered questions; follow-up uses six. The release record is
`content/bundles/active-release.json`, with separate reviewed bundles pinning
the approved `s39-reviewed-v1` packages and exact dependency file hashes. The
approval basis is the owner's 2026-10-22 confirmation; it is not a claim of
independent clinical validation. Historical content artifacts remain unchanged.

The server pins the content, patient projection, provider settings and DDI release
into each batch. It loads only the checked-in release inventory, verifies
dependency hashes, and validates package approval, network semantics and workflow
order. After validating the original release, it derives application contract
`history-inputs-2026-10-22-v3`, with exact hash-pinned definitions
`history.pharmacotherapy.v3.json`, `history.high_suicide_clozapine.v2.json`
(empty fields), `history.lai_indication_choice.v2.json` and
`history.aggression_clozapine.v2.json` under `content/history/`. Original artifacts
remain available as historical records. The derived manifest records
`awaiting_review`; original clinical approval is provenance and does not approve
the changed inputs. Network bytes, CPT contracts, queries, templates and empty
inference evidence remain unchanged. Each batch pins the derived contract's
version and hash as well as the original bundle identity.
A missing or changed dependency produces `WORKFLOW_CONTENT_UNAVAILABLE`
before provider access. The older Networks administration pointers select XML
versions; they do not describe complete question packages and do not replace
this release. New content needs a new reviewed bundle and pinned inventory.

Local source runs resolve `content/` beside the repository. Deployments can set
`X_INSIGHT_CONTENT_DIR` to the shipped content directory. The application image
includes workflow bundles, question packages, history definitions and assessments
for API and worker. Apply the updated source by restarting API and worker and
serving the rebuilt UI. History inventory caching detects file revision changes;
all four active derived definitions are integrity checked on access. Basic draft
normalization remains available during content outages; generation stays blocked.
The 2026-10-22 cleanup adds no migration. Its saved verification logs do not claim
an application runtime restart or external provider completion. It is not blanket approval of the
History/adverse-effect instrument drafts; their review status remains pending.
