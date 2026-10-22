# Proposal generation

Step 7 checks the saved draft's proposal readiness before creating a generation
batch. Core History completion still covers the original twelve fields; it does
not establish that the proposal-context answers are ready.

## Complete proposal context

History exposes 53 registration or 76 follow-up context controls, using the
reviewed definitions and response categories. Nothing is preselected. The
separate **Proposal readiness** summary lists every unresolved requirement with
a readable field label, a reason and a link to its exact History control.
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

1. Follow the proposal-readiness links to History. Review every unresolved field
   and save the assessed responses. Confirm the save succeeds and readiness
   reflects the saved revision.
2. Return to Step 7. If an earlier run exists, use **Start new run with current
   inputs** explicitly. Reloading or returning to the step restores that
   immutable run; it does not regenerate or rewrite it.
3. Both retry and **Start new run with current inputs** from an existing run
   preserve that batch's frozen question packages, use current saved inputs and
   must pass readiness again. An initial start without `source_batch_id` resolves
   the active release. A worker must be running to process any queued questions.

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
order. A missing or changed dependency produces `WORKFLOW_CONTENT_UNAVAILABLE`
before provider access. The older Networks administration pointers select XML
versions; they do not describe complete question packages and do not replace
this release. New content needs a new reviewed bundle and pinned inventory.

Local source runs resolve `content/` beside the repository. Deployments can set
`X_INSIGHT_CONTENT_DIR` to the shipped content directory. The application image
includes workflow bundles, question packages, history definitions and assessments
for API and worker. Apply the updated source by restarting API and worker and
serving the rebuilt UI; history definitions are cached per process. The
2026-10-22 readiness fix adds no migration or clinical-content changes. Its
verification did not restart the application runtime or call a live provider.
