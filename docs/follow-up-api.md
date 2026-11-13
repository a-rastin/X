# Follow-up API

New `follow_up` encounters select immutable `workflow_version: follow-up-v1`.
Create them with `POST /api/v1/patients/{patient_id}/encounters` and
`{"kind":"follow_up"}`. The server freezes the latest signed source under the
patient lock; client-supplied baselines are rejected. Existing legacy encounters
retain their routes and contracts.

Authenticated physicians read `/api/v1/follow-up/config` for labels, laboratory
references, formulations and pinned identities. Draft routes are author-only;
administrators and other physicians cannot read draft clinical data.

| Method | Path after `/api/v1` | Contract |
| --- | --- | --- |
| GET | `/encounters/{id}/follow-up` | Saved raw/effective state, frozen baseline, live C-SSRS result, reached questions, per-step errors and encounter revision |
| PATCH | `/encounters/{id}` | Full `draft_data`; `If-Match` encounter revision and optional `Idempotency-Key`; incomplete valid inputs may autosave |
| POST | `/encounters/{id}/follow-up/validate-step` | `{"destination":"proposal"}` or another configured step; checks saved prerequisites, returns `can_navigate` and field errors; requires `If-Match` |
| POST | `/encounters/{id}/follow-up/proposals` | Empty JSON body, encounter `If-Match`, optional stable idempotency key; creates/reuses an immutable proposal |
| GET | `/encounters/{id}/follow-up/proposals/latest` | Current matching proposal when available, otherwise latest historical proposal; `stale` is recomputed. `signed_patient_id` permits recovery after a lost signing response |
| GET | `/encounters/{id}/follow-up/proposals/{proposal_id}` | Owned encounter/proposal lookup with current freshness |
| GET/PATCH | `/encounters/{id}/secondary-plan` | Existing independent final-text revision contract; PATCH body `{"text":"Physician plan","expected_plan_revision":1}` with matching `If-Match` |
| POST | `/encounters/{id}/follow-up/sign` | `{"proposal_id":"<uuid>","expected_plan_revision":2}` with encounter `If-Match` and optional stable idempotency key |

Reuse the same key, payload and revisions after a lost command response. A
changed command needs a new key. `412 STALE_REVISION` preserves server state and
requires reconciliation. Invalid raw state is `422`; incomplete generation is
`422 FOLLOW_UP_INCOMPLETE`. Stale proposals produce `409 STALE_INPUTS`;
unavailable or altered pinned content produces `409 FOLLOW_UP_CONTENT_UNAVAILABLE`.
No infrastructure failure is represented as a successful physician-review plan.

The fingerprint includes saved raw/effective inputs, baseline references,
demographics/frozen visit year, exact assessment bytes, content/model/DDI
identities and physician availability revision/IDs. Language and final text do
not enter it. Regeneration never updates an earlier proposal or the final text.

Confirmation locks patient, encounter, physician settings and final text;
rechecks ownership, account/archive state, revisions, completeness, freshness
and outcome; writes one `signed-encounter-v5` snapshot and releases the draft
slot atomically. Identical concurrent confirmations replay the same signature.
Shared chart, printable report and append-only addenda include v5 alongside
legacy v1–v3 and New case v4. Schema `0032` archives validate all three reference
variants; older archives may omit the follow-up table/reference.

Generation and signing execute synchronously without a provider or worker.
Legacy generation, clinical-write, probability-adjustment and signing commands
cannot mutate this workflow. The [specification](../project-documents/dev/follow-up-specification.md)
defines the clinical behavior; the [checkpoint](../project-documents/dev/progress/follow-up-bfg-2026-11-13.md)
records actual verification and remaining release work.
