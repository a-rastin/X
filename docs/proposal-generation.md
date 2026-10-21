# Proposal generation

Step 7 resolves the reviewed workflow on the server. A new browser does not
need question packages or a previous generation run in local storage.
Registration uses five ordered questions; follow-up uses six. The release
record is `content/bundles/active-release.json`, with separate reviewed bundles
pinning the approved `s39-reviewed-v1` packages and exact dependency file hashes.
The approval basis is the owner's 2026-10-22 confirmation; it is not a claim of
independent clinical validation. Historical content artifacts remain unchanged.

Complete the structured proposal-context questions in the History step. They
use the reviewed definitions and response categories. Nothing is preselected.
Unknown, not assessed, conflicting, invalid or missing required context pauses
the affected question before provider estimation. A false applicability gate
is recorded only when all its required context is known. After editing inputs
for an existing run, return to Step 7 and use **Start new run with current
inputs**. Earlier runs remain readable history.

The browser automatically starts generation after saving and checking diagnosis
and medication prerequisites. The server pins the content, patient projection,
provider settings and DDI release into the batch. A worker must be running to
process queued questions. Completed proposals still require probability review
and the existing signing checks.

Reloading restores a cached run; a browser without that cache discovers the
latest run through `GET /encounters/{id}/generation-batches/latest`. Both reads
and retries enforce draft ownership. Retry sends `source_batch_id`; the server
uses that batch's immutable packages and release identity with current saved
patient inputs, preserving the existing carry-forward and failed-stage behavior.
Browser storage is optional for this path.

`POST /encounters/{id}/generation-batches` accepts `{}` for the current released
workflow, or `{ "source_batch_id": "…" }` for an exact-package retry. The
existing inline `package`/`packages` contract remains available for engineering
clients. All starts retain CSRF, `If-Match`, idempotency and admission checks.

The server loads only the checked-in release inventory, verifies dependency
hashes, and validates package approval, network semantics and workflow order.
A missing or changed dependency produces `WORKFLOW_CONTENT_UNAVAILABLE` before
provider access. The older Networks administration pointers select XML versions;
they do not describe complete question packages and do not replace this release.
New content needs a new reviewed bundle and pinned release inventory.

Local source runs resolve `content/` beside the repository. Deployments can set
`X_INSIGHT_CONTENT_DIR` to the shipped content directory. The application image
includes the workflow bundles, question packages, history definitions and
assessments for both API and worker. Restart API and worker when installing a
new release; history definitions are cached per process. No database migration
is needed for the 2026-10-22 integration.
