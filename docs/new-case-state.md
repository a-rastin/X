# New-case workflow and API

New registrations entered as **New case** use the deterministic `new-case-v1`
wizard. Known case and follow-up keep the legacy workflow. The fixed oral model
and clinical reference content are experimental; integration is not clinical validation.

Normal migration/startup applies schema **0031**, installs the pinned Lexicomp
release beside historical DDInter, and stores the fixed oral XML in the model
registry. This path needs no provider credential, capability check, LLM, worker,
private MCP grant, generated CPT or probability acceptance.

## Registration and physician settings

`GET /api/v1/new-case/config` returns the server registration year, clinical
reference definitions, formulation inventory and pinned medication names.
`POST /api/v1/patients` accepts `birth_year` instead of `age` for New case:
integer age is the frozen UTC encounter year minus Gregorian birth year,
18–100 inclusive. Names are NFC-normalized, outer ordinary spaces are trimmed,
and only letters/ordinary spaces are accepted. Identifiers remain ten ASCII
digits. Existing age-only clients retain the legacy contract; the browser sends
the new birth-year variant for New case. Historical records receive no inferred
birth year and keep `legacy-v1` workflow identity.

A recorded birth year can be corrected through patient PATCH with its patient
revision; age is recomputed using the registration year. Independent age edits
on these records are rejected. Signed snapshots retain their original demographics.

New physician accounts require `name_fa` and/or `name_en` (trimmed, at most 150
characters each). Existing unnamed users remain valid. Explicit name edits also
require at least one name. Preferred-language display falls back to the other
name, then username. Credential/session behavior remains separate.

Physician-only `GET /api/v1/me/unavailable-drugs` returns `catalog`, `selected_ids`
and profile `revision`. PATCH accepts `{"selected_ids":["oral:haloperidol"]}`,
with CSRF, `If-Match` and optional `Idempotency-Key`. The session determines the
physician; unknown IDs and cross-user payload fields are rejected. Injectable
IDs are distinct and cannot exclude oral outcomes. Stale writes return 412.

## Saved inputs and validation

Raw state is stored under `draft_data.new_case`, with the existing diagnosis,
PANSS, C-SSRS and medications slices. The initial state explicitly stores all
workflow defaults, five C-SSRS No answers, empty diagnosis/PANSS, frozen year
and exact model/release/assessment/content pins. Encounter workflow identity
cannot be changed by PATCH or SQL UPDATE.

Generic encounter PATCH retains author access, CSRF, quoted `If-Match`, optional
idempotency, payload bounds and save-conflict behavior. It accepts incomplete
states for autosave, but rejects unknown new-case fields, invalid measurements,
unknown catalog IDs and forged pins/attribution/assertion records. Measurements
are canonical finite decimal strings; blank is null, zero is distinct, differential
percentages are 0–100, and height/weight must be positive.

Manual answers use `manual: null | true | false`. Null means automatic; explicit
booleans survive reloads and later source edits. Only the server stamps actor/time.
Returning to automatic clears that attribution. Disabled sources and male-only
hidden pregnancy/lactation data never activate a rule. Movement factors are
explicit modeling defaults, not examined negative findings.

- `GET /encounters/{id}/new-case` returns authoritative evaluated state, step
  errors, BMI, effective flags, all triggering findings, evidence, rule sections
  and the latest proposal/freshness. The `/new-case/preview` alias remains supported.
- `POST /encounters/{id}/new-case/validate-step`, body `{"step":"history"}`,
  validates earlier prerequisites after acknowledged autosave; CSRF and encounter
  `If-Match` are required. The `/validate` destination-based alias remains supported.
- `POST /encounters/{id}/new-case/diagnosis-confirmation`, body `{"confirm":true}`,
  records the attributed physician assertion. Nonpositive/partial/unknown active
  answers are cleared; positive answers remain. Later edits invalidate the assertion.
  CSRF, encounter revision and optional idempotency apply. The `/diagnosis-assertion`
  alias remains supported. Ordinary continuation requires positive criteria.

Incomplete PANSS is allowed. Conditional selections, at least one entered lab
when lab reporting is Yes, and an effective BMI category are validated before
forward navigation, generation and signing. Direct hashes do not waive validation.

## Proposals, final text and confirmation

All paths below are under `/api/v1/encounters/{id}/new-case` and author-only:

| Method/path | Contract |
| --- | --- |
| POST `/proposals` | Empty body, CSRF, encounter `If-Match`, optional idempotency. Validate and generate, or reuse identical immutable inputs/content. |
| GET `/proposals/latest` | Latest stored proposal and independently calculated freshness, or null. |
| GET `/proposals/{proposal_id}` | A proposal belonging to this encounter and its freshness. |
| POST `/sign` | Body `{proposal_id, expected_plan_revision}`, CSRF, encounter `If-Match`, optional idempotency. Atomic confirmation. |

`new_case_proposals` freezes canonical fingerprints, effective demographics,
raw/effective inputs and override provenance, all 22 probabilities, filtered
candidates, candidate/current-medication interactions, availability revision/IDs,
source references, lab/BP/formulation definitions, factor mapping and rule identifiers.
The model source remains in the registry. Interaction letters annotate candidates;
they never remove or rerank them. Missing coverage is distinct from an explicit A.

Oral calculation selects the fully observed 22-value CPT row, filters zero and
unavailable oral IDs before taking five, and preserves original percentages/tie
order. LAI returns an explicit `model_unavailable` result and no candidates, while
all remaining rule sections still generate. A missing/corrupt oral model is a
retryable operation failure, not a supported empty LAI result.

The browser reuses secondary-plan GET/PATCH storage and its separate revision.
It seeds the text once, preserves edited text across regeneration/language changes,
and offers explicit replacement. Unacknowledged text is retained locally for
session/reload recovery; 412 reconciliation keeps the editor until an explicit choice.
Navigation and confirmation flush both input and plan saves.

Signing locks/rechecks encounter, patient, physician and plan, validates diagnosis
and required inputs, recomputes proposal/settings freshness and requires nonblank
final text. Stale requests fail without partial signing. Concurrent identical
confirmations and lost-response replays return one snapshot. The transaction writes
`signed-encounter-v4`, releases the open-draft slot and audits. It references the
new proposal, with null legacy batch/proposal FKs; the database enforces exactly
one signed-reference variant. Legacy generate/sign/diagnosis-acknowledgment paths
refuse new-case drafts, and new-case commands refuse legacy drafts.

Charts and printable reports retain the original deterministic proposal and final
physician text separately. Notes are escaped plain text. Original interaction
evidence does not certify drugs typed into the final text. Existing addenda remain
append-only. Follow-up keeps its existing form, carries the explicit medication
pin, shows historical assessment data and the prior final plan, and does not invent
legacy History answers from new-case conditions.

## Packaging and recovery

Deployment ships `content/new-case/`, `content/ddi/lexicomp/` and the generic XML
schema, using `X_INSIGHT_CONTENT_DIR` consistently. Source hashes remain fixed.
Backups include 33 tables, ordered with model/DDI parents before proposals before
signatures. Restore accepts older archive schemas with nullable/legacy defaults;
new proposals/signatures receive FK, fingerprint, variant and source-hash checks.
Only the independently trusted oral XML hash uses its dedicated bounded loader.
Uploaded profile metadata cannot grant larger generic XML admission.

A real mixed-data archive with both DDI releases, model, proposal and v4 signature
was backed up, staged and restored on an isolated database; signed JSON and hash
were unchanged. See the [implementation checkpoint](../project-documents/dev/progress/new-case-implementation-2026-11-09.md)
for exact measurements, verification results and remaining limitations.
