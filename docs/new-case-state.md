# New-case state and validation API

This is the backend-only `new-case-v1` contract. Proposal persistence/signing
(Package F), physician unavailable-drug settings and the new wizard (Package G)
are not implemented by this change. The clinical content is experimental, not
clinical approval. The existing age-based UI continues to create legacy drafts.

## Installation and identity

Run the normal backend migration setup (`python -m x_insight.operations.migrate`
from `backend/`) to apply schema `0030` and install the bundled Lexicomp release
and fixed oral model. New-case creation fails closed if their immutable identities
do not match `content/new-case/v1/manifest.json`.

`POST /api/v1/patients` accepts the existing demographics with `birth_year` in
place of `age`, `clinical_status: "first_time"`, and sex `M` or `F`. The server
computes age as UTC registration year minus birth year, accepts 18–100 inclusive,
and atomically creates the patient and `new-case-v1` registration draft. Supplying
both birth year and age is invalid. Existing age-only requests remain legacy and
retain their 18–99 contract. Existing rows migrate to `legacy-v1`, with no inferred
birth year or fabricated clinical answers. Workflow identity cannot be updated.

The registration draft freezes its UTC creation year and pins the workflow
manifest, laboratory/BP/formulation content hashes, assessment versions, exact
model version/hash and Lexicomp release/version/content hash. Updating patient
status does not change an existing draft's workflow. A new registration for a
birth-year patient uses the new workflow only while their status is `first_time`.

## Read and autosave

- `GET /api/v1/content/new-case` returns authenticated versioned content and
  provenance, including source-retrieval limitations.
- `GET /api/v1/encounters/{id}` returns the full raw draft, revision and workflow.
- `PATCH /api/v1/encounters/{id}` retains the existing author, CSRF, quoted
  `If-Match` and optional `Idempotency-Key` contract. Send the full `draft_data`
  returned by the server, not a sparse new-case object. Stale revisions return
  `412`; structural validation failures return `422` with field errors.

Fresh new-case drafts have empty diagnosis/PANSS, five explicit recent C-SSRS
`no` answers, and explicit defaults for the new-case namespace. Autosave permits
incomplete assessments and conditionally incomplete steps; completion is a
separate operation. Measurements become finite nonnegative decimal strings;
height/weight must be positive and differential percentages must be 0–100.
Zero remains distinct from missing. Selected medications must belong to the
pinned Lexicomp release; duplicate IDs are removed by the medication owner.

Each condition and orthostatic-risk override has `manual: null | true | false`.
Null means automatic; either Boolean overrides suggestions. Changing an override
records server actor/time. Changing its source measurements or medications does
not erase it. Returning to automatic clears attribution. Clients cannot forge
attribution, identity, pins, registration year or diagnosis assertion records.

## Validation and diagnosis

`GET /api/v1/encounters/{id}/new-case/preview` is author-only and returns the
saved revision as an ETag, canonical raw inputs, effective flags, lab warnings,
triggering values/units/sources, six-step errors and completion flags, the exact
16 oral factors with provenance, and deterministic proposal-section decisions.
It does not save, call a provider, create interactions or generate candidates.

`POST /api/v1/encounters/{id}/new-case/validate` accepts
`{"destination":"considerations"}` with CSRF and a quoted `If-Match` revision.
Destinations are `diagnosis`, `panss`, `harm`, `history`, `medications`,
`considerations`, or `proposal`. It evaluates the saved draft and returns
`can_navigate` plus prerequisite `navigation_errors`; it does not autosave.
The field error contract is `field`, stable `code`, and bilingual `message_key`.

Ordinary progression past diagnosis requires a complete criteria-satisfied
evaluation. Negative, partial and unknown results are blocked. The explicit
physician action is
`POST /api/v1/encounters/{id}/new-case/diagnosis-assertion` with
`{"confirm":true}`, CSRF, quoted `If-Match`, and optional `Idempotency-Key`.
It stamps actor/time/assessment version/answer hash/source revision, records an
audit and advances the shared encounter revision. For nonpositive evaluations,
it clears active diagnosis answers; positive answers are retained. Later answer
edits invalidate the assertion; unrelated saves preserve it. Existing diagnosis
reads expose the same authoritative gate. Legacy acknowledgment/bypass routes
cannot bypass this new workflow.

## Projection and downstream boundaries

Hidden or disabled inputs never activate a rule. Female-only pregnancy and
breastfeeding are ignored for males; disabled lab and substance sections cannot
trigger findings. Orthostatic suggestions combine heart failure with exact
catalog membership from the pinned BP classification. No suffix stripping or
free-text drug-name guessing is used. BMI supports calculated and explicit
manual-category modes. Four movement-disorder factors are explicit workflow
false defaults, not measurements. Contraception recommendations for valproate
and childbearing potential remain separate outputs.

Oral candidate and interaction sections report `not_generated`. LAI candidates
report `model_unavailable`, rather than misusing the oral model. Legacy medication
strict-save, reasoning generation and signing routes refuse new-case drafts;
the generic typed PATCH is the supported edit path until Package F exists.

Backup archives include identity, raw state and pins through existing tables.
Restore validates new-case structure, frozen year, attribution, diagnosis record,
catalog membership and content relationships. Only the exact bundled oral-model
SHA uses its bounded dedicated validator; uploaded profile metadata cannot grant
that exception. Older archives retain legacy defaults on import.
