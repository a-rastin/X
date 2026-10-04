# X-INSIGHT implementation plan

## 1. Basis, status, and content inputs

Aligned 2026-10-03 to [user-requirements.md](user-requirements.md), [system-design.md](system-design.md), and [system-architecture.md](system-architecture.md). This is a future implementation plan, not implemented behavior or completed verification. Product rules and owner clarifications come from those documents; stack, numerical policy, queue limits, test seams, and capacity figures remain implementation proposals.

### 1.1 Governing rules

Use the four logical subsystems in the architecture within the proposed modular monolith and worker. Patient variables inform LLM estimation of **every CPT**, including roots; Bayesian execution uses an **empty evidence set**. One patient has at most one open draft across authors and encounter types. Only its author can view clinical draft content or derived artifacts and edit, adjust, reset, retry, accept, discard, or sign it. Any active physician can append an attributed addendum to any signed encounter. Failed initial generation cannot be bypassed by manual-plan signing.

### 1.2 Proposed implementation and verification

The technical structure in §3 and public verification seams in §12 implement the detailed design. Pin and validate actual dependency versions during implementation; no compatibility, approval, or benchmark result is implied by this plan.

### 1.3 Remaining policies

Carry forward system-design.md §15: archive behavior, discarded-content retention, physician printable-report permission, name/phone validation details, runtime/provider compatibility, and capacity/recovery targets remain inputs or proposals. Where this plan describes read-only archived drafts, retained discarded artifacts, or physician printing, that behavior is provisional. Do not treat it as a new confirmed product rule.

### 1.4 Clinical content

S08 authors and releases versioned experimental assessment instruments and examples from supplied sources without owner review, approval, sign-off or response dependencies. Other clinical content retains its existing review process. The content owner supplies history/severity definitions, medication catalog/DDI data, network manifests, prompts, gates, templates, and expected examples. References below to medical sources, `BNs/`, particular BN numbers, source counts, and parsing examples are inherited drafting leads, not an inventory verified from the three baseline documents. Locate and verify supplied material before using it; missing content stays explicit. Do not invent clinical thresholds or executable placeholder networks.

## 2. Product contract

### 2.1 Users, roles, and scope

One administrator and fewer than ten physicians share a patient pool. English, desktop, latest Chrome and Firefox. Self-hosted Linux or cloud VPS. No self-registration, external record integration, prescribing action, autonomous treatment, PDF generation, graphical network editing, or permanent patient deletion.

Seed exactly one `admin` account with password `admin` once. Store only a password hash; the username cannot change. No password complexity requirement, mandatory initial change, or application session timeout. Logout, credential change, deactivation, and restore revoke sessions. Changing credentials must not recreate the default password at the next startup.

After **every** successful physician login show: “This application is a research prototype and must not be used as the sole basis for treating patients.” Register displays “Contact administrator”. The server derives privileges from the account, not the selected login role.

| Action | Administrator | Physician |
|---|---|---|
| Own password/theme | Yes | Yes |
| Accounts, provider settings, models, audit, backup/restore | Yes | No |
| Read patient directory, demographics and signed charts | Yes | Yes |
| View clinical draft content and derived artifacts | No ordinary clinical route | Author only |
| Register patient, update demographics, start follow-up | No | Yes |
| Edit/adjust/reset/retry/accept/discard/sign draft | No | Author only |
| Addendum to any signed encounter | No | Any active physician |
| Archive/unarchive | Yes | No |
| Patient/physician list CSV | Yes | No |
| Printable signed patient report | Yes | Proposed yes; see §1.3 |

Deactivation offers retain-read-only or explicitly confirmed discard for the reviewed draft set. A changing draft set invalidates that confirmation. Reactivation restores author access to retained drafts, never resurrects discarded drafts, and never changes historical attribution.

Retained drafts remain private and occupy the patient's single open-draft slot until signing or confirmed discard. Required administrator audit/backup access and minimal identifiers for discard confirmation do not grant ordinary clinical draft viewing or editing.

### 2.2 Registration and follow-up

Registration pages: demographics → diagnosis → severity → suicide → history/medications/DDI → proposal review → secondary plan/sign. Every page supports attributed notes and autosave once a draft exists. The demographic form before initial creation remains local until valid; do not claim its unsent values are saved.

- Names: Unicode letters after NFC normalization, nonempty, no whitespace/digits/punctuation; preserve casing. Sex `M|F`; integer age 18–99. Patient ID is exactly ten **ASCII digits stored as text**, globally unique even among archived patients. Clinical status `first_time|established` is required. Phone is optional text, not a number; do not invent country validation.
- Valid demographics atomically create patient plus registration draft. A duplicate race yields one creation and a clear conflict for the other; never automatically merge records.
- Diagnosis is distinct from a calculated diagnostic claim: unanswered, partial, complete, or bypassed. Live threshold display follows reviewed criteria. Complete below-threshold answers can proceed after an attributed warning acknowledgment. Bypass is allowed without a reason. Partial answers cannot masquerade as below-threshold completion.
- PANSS and C-SSRS begin unanswered. Skip records `not_assessed`, not zero/minimum. Scores/results exist only when the definition's required items are complete.
- History is structured, with only expressly defined analysis-visible fields. Medications contain bundled demo-catalog references only; no free-text medication addition, dose, unit, route, frequency, or active/stopped fields. A catalog medication can lack interaction coverage and must show “coverage unavailable”.
- Entry into proposal review flushes saves and automatically requests generation once prerequisites are met. Re-entry must not duplicate a run. Show configuration or required-input errors rather than inventing defaults.
- The initial proposal is immutable system output. Per-question original CPTs/results remain separate from locally adjusted CPTs/results. The secondary plan is a separately revisioned physician edit, with probability acceptance, changes, and sign-off recorded.

Follow-up starts from a chosen current signed baseline, copies relevant history/medications with provenance, requires reconciliation, updates phone, captures new PANSS/C-SSRS, and records all four adverse effects. Prior scores are shown as historical, never as newly completed answers. Each effect has `present|absent|not_assessed`; severity is required only when present, under the reviewed severity definition. Do not add a standardized scale merely because a reference discusses one.

### 2.3 Records, notes, and concurrency

The shared chart includes demographics, signed chronology, signed assessment completeness, medication/DDI history, original and final plans/probability results, signature provenance, and addenda. Clinical draft content and derived artifacts are visible only to the author, including through nested chart, report, search, and download responses. Search by name/Patient ID and filter clinical status; archive is an additional explicit filter.

Autosave: approximately one-second debounce and save on page transition. Show saving/saved/failed; only acknowledged writes are durable. Preserve in-memory edits on error and warn on navigation with pending edits. Offline editing is out of scope. Use revisions for patient, draft, plan, and administrative pointers; a stale update returns `412` and requires reconciliation, never silent overwrite.

Page notes carry page, author ID/display snapshot, server timestamp, and text. They never influence applicability, patient projections, inference evidence, prompts, CPTs, DDI, or proposal selection. Enforce this by serialization allowlists. Signed notes are frozen; corrections use addenda. Notes can change the encounter revision without changing the analysis fingerprint.

Enforce at most one open draft per patient with a partial database uniqueness constraint on lifecycle `draft`, plus a patient-row lock during creation. Generation/review readiness is separate state; failures and deactivation do not release the slot. Concurrent creation, including two requests by the same author, permits one draft only. Another physician receives a generic open-draft conflict without contents; the author can resume it. Signing or confirmed discard releases the slot atomically.

Relevant patient or applicability changes invalidate only affected questions and their acceptances. Regeneration preserves earlier runs/history, establishes new originals under the encounter's pinned bundle, and does not carry forward old adjustments. Unaffected questions retain their valid references; notes and plan edits do not trigger inference.

## 3. Architecture and repository structure

### 3.1 Deployment

One modular application release, one PostgreSQL database, one worker process initially with two provider slots, and one HTTPS edge. The worker owns private MCP subprocesses; the provider has no inbound connection to them. PostgreSQL holds authoritative records, immutable content/XML, and durable jobs. Do not add Redis, a broker, vector storage, a graph database, or separate DDI web deployment.

```mermaid
flowchart LR
    B[React browser] --> E[HTTPS edge]
    E --> H[FastAPI HTTP interface]
    H --> D[(PostgreSQL)]
    W[Worker: ordered question jobs] --> D
    W --> M[Private stdio MCP server]
    M -->|Read scoped saved projection| D
    W --> L[Configured LLM endpoint]
    W --> I[Bounded deterministic inference]
    I --> T[Versioned template rendering]
    T --> D
    C[Offline DDI CLI] --> R[Reviewed versioned dataset]
    R --> D
```

The browser uses relative `/api/v1` routes at the same origin. Only the edge is externally reachable; database and MCP have no public ports. LLM downtime must not disable ordinary chart work.

### 3.2 Deep modules and interfaces

Use **module**, **interface**, **implementation**, **seam**, and **adapter** in their codebase-design meanings. The four source subsystems remain logical ownership groups; they do not require four applications. Each row below presents one cohesive interface, which may contain several operations. Avoid wrappers that merely forward calls and generic repositories with one implementation.

| Module / ownership | Interface and invariants | Hidden implementation |
|---|---|---|
| Identity / S4 | Authenticate, authorize, manage own credentials/admin accounts; active role and revocation checks | Hashing, sessions, credential revisions, throttling |
| Cases / S1 | Register/read/update patients; save/read/discard drafts; notes, final plans, sign/addenda | Transactions, revisions, ownership, snapshots, baseline checks |
| Assessments / S2 definitions, S1 answers | `evaluate(definition, answers) → AssessmentResult`; no I/O or invented defaults | Completeness, conditional items, approved scoring |
| DDI ingestion / S2 | `build(source_dir, terminology, review_manifest) → CandidateDataset + Report`; CLI equivalent | Preprocessing, parsing, source offsets, count checks, conflicts |
| DDI checker / S1 consumes S2 | `check(medications, dataset_version) → DDIReport` | Resolution, unique pairs, indexed lookup, coverage, evidence aggregation |
| Models / S2 | Import/validate/version/activate/export; `infer(effective_artifact, query, engine_config)` and replay; evidence always empty | Safe XML, semantic checks, pgmpy conversion, resource isolation |
| Reasoning / S3 | Start/read/retry current run; worker `run_once()` executes eligible durable work | Snapshots, gates, ordered steps, attempt budgets, leases, templates |
| Probability review / S1 intent, S3 artifacts | Read original/current CPTs; adjust/reset/retry local calculation; S1 accepts exact current results | Integer units, deterministic redistribution, immutable revisions, local queue, stale-result fencing |
| Scoped record access / S3 | Real MCP `get_question_patient_inputs({})` | Authorization binding, immutable projection lookup, output limits |
| Provider / S4 config, S3 use | `estimate_cpts(request, permitted_tool_bridge) → candidate response`; explicit error taxonomy | Endpoint protocol, tool messages, limits, capability verification |
| Operations / S4 | Read audit; export; create/validate/commit recovery artifacts | Consistent snapshots, staging, maintenance, checksums, process restart |

Accept real external dependencies (provider, clock, process runner) at composition time. Use a deterministic provider test adapter and controlled clock alongside their production adapters. PostgreSQL tests use real PostgreSQL; do not add an in-memory database abstraction solely for tests. HTTP and workers call the owning module, not each other's routes or foreign mutable tables. Read views may join immutable references.

### 3.3 Target layout

Create directories only when a task needs them; this is a location contract, not an instruction to fill empty layers.

```text
backend/
  pyproject.toml, uv.lock
  src/x_insight/
    app.py, config.py, db.py, contracts.py
    identity/       # domain commands, HTTP routes, schemas
    cases/          # patients, drafts, notes, signing, reports
    assessments/    # definitions and pure evaluation
    ddi/            # ingestion CLI and deterministic runtime
    models/         # registry, validation, inference, replay
    reasoning/      # snapshots, provider, queue, coordination, rendering
    probability_review/ # CPT revisions, redistribution, local calculation
    mcp_server/     # private process entry point and tool
    operations/    # audit, backups, staged restore
  migrations/      # incremental Alembic migrations
  tests/{http,assessments,ddi,models,mcp,provider,worker,probability_review,recovery}/
web/
  package.json, package-lock.json
  src/{app,features,shared}/
  tests/           # browser journeys and necessary browser-only behavior
content/
  assessments/     # reviewed definitions and reference examples
  history/         # structured fields and adverse-effect definitions
  questions/<question_key>/
    manifest.json, network.xml, prompt.txt, template.json, examples.json, review.json
  bundles/         # ordered immutable workflow selections
  ddi/             # controlled aliases, review decisions, released manifests
tests/fixtures/    # synthetic records/models; clearly separated from released content
e2e/              # Playwright Chrome/Firefox scenarios
deploy/           # Dockerfiles, edge config, operations scripts
compose.yaml
Makefile
project-documents/dev/{plan,tasks,progress-tracker}.md
```

`BNs/` and original medical sources remain unchanged unless the owner explicitly requests a source correction. Derived content references source hashes and carries its own version. Runtime imports released content into PostgreSQL; running encounters do not read mutable repository files.

Use Python with FastAPI, Pydantic, SQLAlchemy/Alembic, psycopg, lxml, pgmpy, an MCP SDK, and a tested password-hashing implementation. React uses Vite, TypeScript, routing, and ordinary accessible form controls. Use pytest and Playwright; add a browser unit runner only for behavior that cannot be sensibly covered at existing seams. Pin actual compatible versions and container digests in the bootstrap session; this document does not invent future dependency versions. Use Python `uv` and npm lockfiles consistently.

## 4. Persistence, transactions, and HTTP contracts

### 4.1 Storage model

Use relational columns for identifiers, ownership, state, uniqueness, revisions, foreign keys, and scheduling. Use schema-validated JSONB for versioned answers, definitions, snapshots, and variable response shapes. Times are UTC with explicit display timezone; age is reported age, not an inferred birth date. Immutable artifacts have SHA-256 hashes and schema versions.

| Records | Required fields and constraints |
|---|---|
| Users/sessions | UUID, normalized unique username, immutable admin identity, role, active, hash, credential revision, theme; hashed opaque session token, created/revoked time, no timeout |
| Patients/revisions | Unique ten-digit text ID, names, sex, age, clinical status, phone, archive flag, revision; changed fields/actor/time history |
| Encounters | Patient, kind, author, visit time, prior signed source, lifecycle (`draft`, `signed`, `discarded`), revision; partial unique constraint on patient for `draft`; derived generation/review readiness |
| Assessments/history/medications/effects | Encounter and definition version; explicit completeness/status, typed answers and nullable results; drug-only entries; effect status plus nullable severity |
| Notes | Encounter/page, immutable note ID, author snapshot, time/text; separate from analysis-visible history |
| Plans/signed snapshots/addenda | Immutable initial proposal reference; revisioned secondary text/change history; full signed snapshot/hash, signer/time; append-only attributed correction |
| Content/network versions/bundles | Immutable payload/XML/manifest, source hash, status and review record; versions retained; activation pointer revision per workflow |
| DDI data | Dataset release, concepts/aliases, source documents/bytes or immutable raw source artifacts, directional evidence, coverage declarations, review decisions/import report |
| GenerationBatch / QuestionRun | Owner, source revision, ordered work list and pinned bundle/configuration; immutable question projection/hash, applicability dependencies, network/prompt/template/engine versions, status and attempts |
| OriginalBaseline | Raw returned percentages, validated complete CPTs, effective XML/hash, query/output and templated recommendation; immutable and created only after successful execution/rendering |
| CPTRevision / QuestionReviewState | Complete immutable adjustment/reset CPT set/hash, parent/sequence, actor/time, direct edit and redistributed before/after row values; current pointers, calculation state, input freshness and optimistic revision |
| CalculationAttempt / Result / ProbabilityAcceptance | Exact baseline/revision/hash/configuration association, output/recommendation/error; author acceptance of current matching successful result and inputs; invalidated by later changes |
| ProposalSnapshot / SignedEncounterSnapshot | Immutable original proposal/DDI references; signed record freezes original and accepted CPTs/results, adjusted recommendations, plan edits, inputs, versions/configuration and attribution |
| Jobs/attempts | Separate generation/local-calculation classes; question/stage/revision/hash, batch/attempt indices, availability, lease token/hash/deadline, last heartbeat, fencing generation, bounded diagnostics, unique artifact keys |
| Configuration/audit/idempotency | Encrypted provider credentials and version; append-only event metadata; actor/operation/key/request hash/result reference for retried commands |
| Recovery jobs | Backup/staging identity, manifest/checksums, status, uploader, expiration, confirmation digest, deployment generation |

Add migrations with the feature using them. Use foreign keys and uniqueness to enforce invariants even under simultaneous requests. The normal application role cannot update/delete signed snapshots, accepted artifacts, or audit events. Migration/recovery privileges are separate; this is not protection against the database owner.

### 4.2 Hash and freshness rules

Hash canonical UTF-8 JSON with sorted object keys, preserved semantically ordered arrays, explicit null/missing status, schema version, and finite numeric values. Never hash a presentation string. For XML keep both exact source bytes/hash and a parsed ordered contract/hash; do not assume arbitrary XML serialization preserves metadata or CPT ordering. A content approval references the hash of the immutable content files; keep the approval record outside that hash to avoid a self-referential digest. Changing content invalidates its prior approval.

The analysis fingerprint includes analysis-visible patient/draft facts, reconciled medications, baseline clinical facts, and pinned content. It excludes notes, names/identifier/phone unless an explicitly permitted analytical field is needed (identifiers and names are never model inputs), UI state, and secondary-plan edits. A run additionally pins network/bundle, DDI, prompt/template/mapping, provider configuration, and engine versions. Signed snapshots preserve the full displayed record, including notes and demographics, even when those fields are not analytical.

A new active bundle/configuration does not silently rebase a pinned encounter or its saved runs. Compare per-question typed patient projections and applicability dependencies, not just the encounter timestamp. Changed relevant facts invalidate the affected run/adjustments and acceptance; regenerate only affected applicable questions, preserving unaffected current artifacts and the encounter's pinned bundle. A note-only change does not invalidate probability acceptance. DDI-only changes refresh the proposal and final review without an unrelated LLM call.

### 4.3 HTTP conventions

All routes below are under `/api/v1`. Internal IDs are UUIDs. JSON has explicit schema versions and decimal percentage strings; list queries use bounded cursor pagination (default 25, maximum 100), stable ordering, and validated filters. Mutations require session/role checks and CSRF protection. Use `If-Match`/ETag for mutable revisions and `Idempotency-Key` for create/generate/adjust/reset/retry/accept/sign/note/addendum/recovery commands. Same key and body returns the original result; changed body with the same key is `409`.

```json
{"code":"STALE_REVISION","message":"The draft changed. Reload and reconcile your edits.","field_errors":{},"request_id":"uuid","retryable":false}
```

Use `401` missing/revoked session, `403` denied role/ownership, `404` inaccessible/missing entity where appropriate, `409` duplicate/state conflict, `412` stale revision, `422` invalid content, `429` queue/admission limit, and `503` unavailable dependency. Never return secret values or tracebacks.

| Routes | Behavior |
|---|---|
| `POST /auth/login`, `/auth/logout`; `GET /me`; `PATCH /me/preferences`; `POST /me/password` | Identity, research-warning requirement, preference, revocation |
| `GET/POST /physicians`; `PATCH /physicians/{id}`; `POST .../deactivate`, `.../reactivate` | Admin-only credentials/state; reviewed draft-set confirmation on discard |
| `GET/POST /patients`; `GET/PATCH /patients/{id}`; `POST .../archive`, `.../unarchive` | Shared reads, physician writes, admin archive; create returns patient and registration draft |
| `POST /patients/{id}/encounters`; `GET/PATCH /encounters/{id}`; `POST .../notes`, `.../discard` | Single-draft creation; generic `409 OPEN_DRAFT_EXISTS`; author-only draft reads/writes and artifacts; shared authorized read after signing |
| `GET /content/assessments/{type}`; `GET /content/history`; `GET /drugs?query=...` | Authenticated released definitions and catalog for forms; expose version IDs |
| `POST /ddi/check` | Local drug-only check, explicit dataset version; report is not an LLM response |
| `POST /encounters/{id}/generation-batches`; `GET /generation-batches/{id}`; `POST /question-runs/{id}/retry-original` | Automatic author-context generation, durable progress/transparency, exact failed-question resume |
| `GET /question-runs/{id}/review` | Original/current CPTs and results, revision IDs, freshness, state, history and permissions |
| `POST /question-runs/{id}/cpt-adjustments`, `/reset`, `/retry-calculation` | Author-only saved revision; server redistribution, audited reset/baseline reuse, local-only retry |
| `POST /question-runs/{id}/acceptance` | Exact current baseline/revision/result/input hash; reject stale, pending or failed current results |
| `PATCH /encounters/{id}/secondary-plan`; `POST .../sign`, `.../addenda` | Separate plan revision; exact acceptances/current proposal; any active physician may append to a signed encounter |
| `GET/POST /networks`; `GET /networks/{id}/versions`; `POST /networks/{id}/versions` | Admin import and immutable editing |
| `POST /network-versions/{id}/validate`; `GET .../graph`, `.../xml` | Validation report, read-only graph, exact export |
| `POST /model-bundles/activate`, `/model-bundles/rollback` | Atomic workflow pointer, compatibility/review validation |
| `GET/PUT /api-settings`; `POST /api-settings/test` | Masked reads, explicit unchanged/replace/clear secret semantics, capability result |
| `GET /exports/patients.csv`, `/exports/physicians.csv`; `GET /patients/{id}/report` | Authorized exports and escaped printable HTML |
| `GET /audit-events`; `POST /backups`; `GET /backups/{id}` | Admin inspection and asynchronous consistent download |
| `POST /restores/validate`; `GET /restores/{id}`; `POST /restores/commit` | Staging, progress/impact report, confirmed replacement |

Generation creation carries the expected source revision and returns `202` with a batch ID and ordered question progress. Original retry identifies the failed QuestionRun/stage and expected revision; it cannot swap patient inputs or pinned settings. Adjustment carries expected review revision, node, complete parent assignment, state and target decimal percentage. Acceptance references the exact current run/baseline/CPT revision/result/input hash. Sign carries encounter/plan/review revisions, proposal ID and per-question acceptance references. Server recomputes eligibility; client flags never grant authority. Review payloads distinguish current CPT revision, displayed-result revision, whether they match, calculation state and input freshness.

## 5. Assessment and clinical-content contracts

An assessment definition contains stable item IDs, source locators, version, permitted answers, required/conditional rules, period fields, completion rules, result rules, and independent examples. Do not evaluate arbitrary Python/JavaScript from JSON. Use a small allowlisted declarative rule set or explicit typed implementations per versioned instrument; do not build a general rules platform.

`evaluate(definition, answers)` returns `{status, missing_item_ids, item_errors, scores, findings, definition_version}`. Partial/skipped answers have null unavailable scores. Keep observed negatives distinct from unanswered. The server is authoritative; the browser may preview the same validated rules, with contract fixtures preventing drift.

For the experimental assessments in S08, the implementing agent selects form/time windows and documents source gaps, assumptions and experimental defaults. Schema/rule validation and independent reference examples determine release; reviewer identity, approval records and `awaiting_review` are not assessment-release requirements. These releases satisfy S09–S11 and later assessment dependencies without owner review. History, adverse-effect, DDI and question-package reviews retain their existing scope.

Source-specific drafting instructions:

- Diagnosis: represent the six criteria and their subconditions in `schizophrenia-criteria.md`; a symptom count alone cannot satisfy the full document. Include unknown and bypass behavior. Derive and validate independent threshold examples.
- PANSS: source contains 30 items and subscale arithmetic. Fully answered all-1 fixture has positive 7, negative 7, general 16, total 30; all-7 has 49, 49, 112, 210. Do not initialize answers to 1. One missing required item suppresses the total. Any follow-up change formula must specify its denominator and zero case; no treatment threshold is inferred from score bands.
- C-SSRS: select and document exact administration form and time windows in S08. Ideation severity, intensity dimensions, behavior, and lethality remain separate; do not produce a composite risk score. Do not infer lower-level recorded answers from a higher-level answer. Define and validate branching and alerts in S08 without owner review; keep unanswered items distinct from negative answers.
- History: derive the minimum typed field inventory from approved question mappings, with explicit source provenance, dates/windows, tri-state values, and reconciliation. Drug regimen details excluded by FR-14 remain excluded even when source network drafts mention them; resolve that mismatch in model design.
- Adverse effects: present/absent/not-assessed plus reviewed severity definitions. Existing BARS/SAS discussion is supporting source material, not permission to invent common severity bands or add mandatory full scales to FR-21.

When a form is skipped, optional analysis inputs remain explicitly missing. A required input can stop generation for clarification; the UI must explain the specific question/field instead of silently undoing the skip or generating a score.

## 6. DDI: offline ingestion and deterministic checking

### 6.1 Dataset contract

Use the application's PostgreSQL database at runtime. A separate command-line build converts sources into an immutable, reviewable dataset. No LLM, RAG, external terminology request, or raw text parsing occurs during patient interaction checking.

Minimum records:

- `DrugConcept`: stable ID, canonical/normalized name, concept type (`ingredient|combination_drug|herbal|food|substance|other`), catalog availability. Catalog availability is not a medication active/stopped field.
- `DrugAlias`: controlled normalized alias, concept ID, source/review record. Ambiguous aliases never resolve silently.
- `SourceDocument`: relative source path, source URL/date when present, checksum, parser version, original text and location mapping.
- `InteractionEvidence`: canonical unordered pair; directional subject/object when explicit, otherwise null/unknown; severity; separate management/action; source document/span; raw text; optional source-derived mechanism/effect; review status.
- `DatasetRelease`: hashes, source inventory, parser/terminology versions, category-count report, unresolved/conflict counts, reviewed coverage declarations, reviewer/time.
- `PairCoverage`: explicit reviewed basis for whether a pair is adequately covered by this dataset. Having two recognized drugs or one monograph is not sufficient.

Retain multiple assertions per pair and both monograph directions. Deduplicate exact repeated extraction within the same source/span/hash; do not erase independent evidence from another source. Sort by severity `contraindicated > serious > monitor_closely > minor > unknown`; these are display priorities, not clinical scores. Unknown severity remains visible, not a low-severity reassurance. Do not infer a management action or mechanism that the source does not support; retain raw text.

### 6.2 Ingestion sequence

1. Discover all `.txt` files recursively; stable relative path order and byte hashes. Preserve originals, including Unicode/BOM handling.
2. Preprocess page headers, timestamps, repeated URLs, and line wraps while preserving an original line-span map. Do not globally remove similar clinical prose.
3. Identify the actual interaction section, severity headings with declared counts, entry headers, continuation paragraphs, and section end. Summary navigation headings are not entries.
4. Parse a state machine. For every category, declared and parsed counts must agree. A mismatch fails that document's release eligibility. It is never repaired by truncation or padding.
5. Resolve concepts using exact normalized canonical names and controlled aliases. Unknown terms enter a review queue. No silent fuzzy matching, suffix stripping, or combination-drug splitting.
6. Preserve directional evidence, conflicts, duplicates, and uncertain extraction. Generate machine-readable and readable reports.
7. Agent drafts explicit alias/parse corrections with source spans; owner reviews all contraindicated/serious evidence, unresolved names, parser anomalies, and conflicting severities/management as required by the DDI design.
8. Publish an atomic immutable dataset only after its declared review gate passes. A deliberately partial release requires explicit owner acceptance and unavailable coverage for excluded material. Never label a partial corpus complete.

The Sitagliptin monograph supplies an initial independent parser fixture: declared counts are 0 contraindicated, 4 serious, 92 monitor closely, 70 minor. Ofloxacin appears in more than one severity category; keep both assertions. These are source parsing expectations, not new treatment guidance.

CLI target: `uv run python -m x_insight.ddi build --sources ../docs/medical-docs/DDI-text --terminology ../content/ddi/aliases.json --output <staging-dir>` from `backend/`. Exit nonzero for structural/count failures; still write an anomaly report. A separate `publish --manifest <reviewed-manifest>` validates approvals and imports a release. No patient data enters this command.

### 6.3 Runtime interface

Input: `{medications:[{catalog_drug_id}], dataset_version}` from the saved demo-catalog medication set. No free-text unknown-label input is added. Deduplicate equivalent concepts while preserving display provenance. Catalog drugs lacking interaction coverage remain selectable and visibly uncovered. Combination products remain distinct concepts unless a reviewed rule authorizes a specific decomposition. Unresolved source names belong to ingestion review, not a new patient medication-entry feature.

Generate each unique unordered pair once and batch-fetch evidence. Return:

```text
DDIReport
  dataset_version, catalog_version, medication_fingerprint
  resolved_medications[], coverage_unavailable_medications[]
  pairs[]
    drug_a, drug_b
    status: interaction_found | covered_no_listed_interaction | coverage_unavailable
    highest_known_severity, has_unknown_severity, conflicts[]
    evidence[]: source_severity, direction, raw_text, management, source_path/span/hash
    coverage_basis
  limitations[], generated_at
```

A catalog medication without interaction coverage displays “coverage unavailable”; pairs without supported coverage remain uncovered. A recognized pair with no evidence row is uncovered unless explicit coverage permits “no listed interaction in this dataset”. Never say “safe” or “no interaction”. Zero or one unique medication has no pairs, with unavailable-coverage warnings still visible. Invalid catalog identifiers are rejected, not accepted as free-text medications.

Recompute for changed reconciled medications, pin the dataset for each run, and retain the report in the proposal/signed snapshot. DDI is informational; the source requirements do not authorize a new hard stop on signing based solely on interaction severity. A missing/broken dataset is distinct from valid limited coverage and must not appear as a successful empty report.

## 7. Bayesian models and question packages

### 7.1 Required inventory and ordering

Each row is a distinct clinical question with one network and prompt. Candidate sources below are **drafting leads**, not approved assignments. Preserve exactly seven registration questions and six follow-up questions. Do not add independent LAI indication and choice runs.

| Order/key | Applicability to draft for review | Source starting point / known gap |
|---|---|---|
| R1 `hospitalization` | All registration encounters | No matching executable network; draft a new question package |
| R2 `pharmacotherapy` | All registration encounters | BN-04 reviews established treatment; initial selection needs explicit redesigned scope |
| R3 `involuntary_care` | All registration encounters | No matching network; jurisdiction and criteria must be supplied/reviewed; do not invent legal rules |
| R4 `high_suicide_clozapine` | Reviewed high-risk/persistence gate | BN-08; align assessment period and treatment-history meanings |
| R5 `lai_indication_choice` | Evaluate indication; choice only when permitted | BN-10 has discussion/review outputs; it does not establish a complete product-choice contract |
| R6 `aggression_clozapine` | Reviewed aggression gate | BN-09; do not substitute PANSS hostility for an unreviewed aggression threshold |
| R7 `established_case_clozapine` | Established clinical status and reviewed criteria | BN-07 is a lead; define trial adequacy and inputs without excluded regimen fields |
| F1 `tardive_dyskinesia` | Effect present | BN-14 and tardive-dyskinesia criteria |
| F2 `akathisia` | Effect present | BN-13 and akathisia criteria |
| F3 `parkinsonism` | Effect present | BN-12 and parkinsonism criteria |
| F4 `acute_dystonia` | Effect present | BN-11 and acute-dystonia criteria |
| F5 `no_improvement_clozapine` | Reviewed no-improvement gate | BN-07 may inform a **separate** package; define baseline/window/adequate trial |
| F6 `continue_or_adjust` | All follow-up encounters | BN-04/05/06 overlap; draft one network. BN-06 records an electronic decision-support restriction that must be resolved during review |

True gate executes; false gate records `not_applicable` with reason. Unknown is not false. Required unknown/missing/conflict yields `needs_clarification`, stopping later questions. Optional unknown behavior must be explicit in the manifest and visible in the final proposal. No implicit chaining of one question's posterior into the next question's inputs.

### 7.2 Package shape

Each package includes:

```text
manifest
  schema_version, question_key, title, workflow, version, review_status
  network_file, network_hash, source_refs[], declared_node_order[]
  variables[]: node_id, XMLBIF kind, patient_value_type, states[], ordered_parents[]
  patient_mappings[]: node_id, allowed_source_paths[], typed transform, time_window
                      usage: cpt_context; missing_policy
  applicability: allowlisted expression, required_fields, unknown_policy
  cpt_contract: ordered node/parent/state rows; all nodes mandatory
  query_nodes[], execution_evidence: empty
  prompt_version, template_version, reviewed_result_mapping
  numerical_tolerances, context/output/resource limits, engine configuration
  review: assumptions, reviewer, decision, date, source hashes
```

Prompts instruct estimation only: fixed definitions, current-question inputs, every CPT in percentage units, strict response schema. Prompt versions must not add unrepresented patient facts, ask for full-record extraction, ask the LLM to choose applicability, or ask it to write a plan. Templates contain approved wording and deterministic result mappings, with explicit missing/uncertainty/coverage disclosures. Never infer “recommend” from whichever posterior state happens to be largest unless that mapping was reviewed.

All patient mappings are CPT-estimation context only. Execution has no hard, soft, likelihood, or virtual patient evidence; content review cannot enable it under the confirmed product contract. Unknown/conflicting facts remain explicit inputs or clarification states, never fabricated negative findings.

### 7.3 Registry validation and activation

Store source XML exactly and validate in layers:

1. Parse safely: reject DTD/external entities, network resolution, oversized XML, unexpected references, and resource abuse.
2. XSD structure using the versioned `BNs/schema.xml`; keep the distinction between structural validity and executability.
3. Semantic shape: one question network per admitted version; unique nodes/states, valid parents, acyclic graph, nonempty outcomes, complete definitions, finite normalized reference tables with correct dimensions/order. Initial execution profile supports discrete `nature` nodes only; decision/utility artifacts can be inspected as unsupported drafts but cannot activate. Supporting influence diagrams is a future design decision, not an implicit conversion.
4. Contract completeness: reviewed patient mappings, fixed types/relevance, prompts, all-CPT schema, gates, missingness, queries, templates, and examples. Unknown executable expressions and note mappings are rejected.
5. Admission: inference fixtures, full-CPT payload size, configured context budget, execution time/memory limits, and review status.

Reference tables in a released source artifact must be explicitly reviewed; missing values cannot be silently filled with uniform probabilities to pass. Drafts may remain incomplete and viewable. Because all runtime CPTs are replaced, document the purpose and provenance of reference tables without presenting them as patient estimates. Owner review must resolve draft restrictions and fixed-rule versus estimated-table assumptions in a new version, not merely flip an `inference_enabled` property.

Admin XML editing creates a new immutable version. Validation returns separate structural/semantic/content/admission reports. Read-only graph displays nodes, states, edges and validation state; no drag-to-edit semantics. Activation atomically changes a complete workflow bundle, with expected revision and audit event. Rollback is a new activation event selecting old valid versions. Existing runs retain pinned versions.

### 7.4 CPT contract and deterministic inference

For each applicable question require:

```json
{
  "question_key":"synthetic_example",
  "network_version":"v1",
  "network_hash":"sha256",
  "tables":[
    {"node_id":"A","parent_ids":[],"states":["no","yes"],
      "rows":[{"parent_states":[],"percentages":["80","20"]}]},
    {"node_id":"B","parent_ids":["A"],"states":["no","yes"],
      "rows":[{"parent_states":["no"],"percentages":["90","10"]},
              {"parent_states":["yes"],"percentages":["30","70"]}]}
  ]
}
```

This is a mathematical test network, not clinical content. Require exact identity/hash, node/parent/state order, one table per node, one row per ordered Cartesian parent configuration, and no extra fields. Roots have one empty-parent row. Follow system-design.md §7.4's proposed numerical policy: decimal strings with at most six decimal places; store integer units with `100%=100,000,000` units. Reject malformed decimals, booleans/nulls/nonfinite values, out-of-range values, excess precision, and any row not totaling exactly 100%. Do not silently normalize, clip, complete, or repair invalid estimates. Version the policy and engine-boundary conversion.

Persist the exact accepted percentages and request/response provenance. Divide by 100 without presentation rounding; populate **every** table in a run-local XML artifact, preserve fixed structure and ordering, and freeze its hash before inference. Registered XML never changes.

The XMLBIF ordering contract is child state fastest, last declared parent next, first parent slowest. Explicitly map this to the pinned engine's array order; do not trust an incidental iteration order or a reader/writer round trip. Preserve metadata outside any engine conversion that drops properties.

Use deterministic exact inference with pinned engine/runtime, query, numeric dtype, and deterministic elimination order/tie-breaking, always with empty evidence. Patient inputs are retained provenance and freshness dependencies, not observations supplied to the engine. Fail numerical/resource errors clearly; never silently approximate or soften probabilities. Replay reads stored CPTs/query/configuration without provider or MCP access. Any engine comparison tolerance must be selected and validated during implementation and pinned with the execution policy.

Independent mathematical fixtures: with the CPTs above, empty-evidence `P(A=yes)=0.20` and `P(B=yes)=0.22`. A second fixture must have at least two parents with asymmetric probabilities to detect transpose/order bugs. Expected literals/worked calculations are checked independently, never produced by the implementation under test. An observed patient value in the projection must not clamp the corresponding node.

## 8. MCP, provider, and sequential reasoning

### 8.1 Snapshot and projection

Creating a generation batch is one transaction: authenticate active author, validate draft/revision/prerequisites and declared archive policy, freeze source/version references, create ordered question work, enqueue the first eligible question, and append audit. Repeated automatic triggers reuse eligible work; failed questions use explicit retry. Relevant input changes create new QuestionRuns only for affected applicable questions, mark their old work stale, and retain unaffected current baselines/adjustments. Keep the encounter's pinned bundle.

For each question, persist its typed allowlisted projection before provider access. Values include node ID, patient type, observed/not-assessed/missing/conflict status, source path/revision, and explicit null when unavailable. Only represented variables appear. Applicability may use additional saved fields internally, but those fields are not automatically sent to the model. Exclude notes, names, Patient ID, unrelated history, other questions' fields/results, and secrets.

### 8.2 Private MCP server

There is **one MCP server implementation**, exposing one tool:

```text
get_question_patient_inputs
  inputSchema: {type: object, properties: {}, additionalProperties: false}
  output: {question_key, network_version, projection_hash, variables[]}
```

The worker is the MCP host/client. It obtains the initial projection through a real stdio `tools/call` and passes that result to the provider. Optional provider-requested reads traverse the same transport and return the same persisted projection. Do not maintain a second direct-call implementation that bypasses MCP in production.

Concrete context isolation: launch a worker-owned MCP subprocess for an active question with a short-lived opaque authorization grant supplied through its protected process environment, never a model argument or command-line secret. Bind it once to actor/encounter/run/question/snapshot/manifest/job lease/deployment generation. The grant refers to database authorization state created by the worker; MCP uses a read-only database role. On question completion/retry-context replacement, stop the process and revoke the grant. Concurrent provider slots have separate processes/grants. This lifecycle avoids a mutable global “current patient”; a later optimization may reuse processes only with equivalent isolation tests.

Validate active account, eligible encounter/run, live lease/grant, projection hash, tool name/empty arguments, and output size on each read. Reject forged, expired, mismatched, stale, archived, or terminal contexts. Secrets and unrelated record existence never appear in errors. Log protocol output only to stdout and diagnostic metadata only to stderr. MCP cannot search patients, read notes, submit CPTs, alter data, execute inference, or expose shell/SQL/files/URLs. No extra MCP servers for DDI or network administration are needed.

Pin and test the actual SDK/protocol combination. Authorization binding is application state, not a dependency on a particular MCP session protocol. Use the SDK's supported discovery/lifecycle behavior rather than copying outdated handshake examples. See the [official MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture) for host/client/server and transport roles.

### 8.3 Provider configuration and adapter

Admin settings store key, base URL, model and a configuration revision. Encrypt the key using a standard authenticated-encryption implementation and a deployment-held key outside the database; do not design a custom cipher. Masked reads distinguish configured/absent. Saving/testing never echoes the secret. Explicit replace/clear semantics avoid turning a redacted placeholder into a real key. Old credentials remain available while referenced runs require them; removing one cancels/restarts affected work explicitly.

Use an OpenAI-compatible chat/tool exchange through a provider adapter; do not assume every compatible endpoint supports structured outputs or tools. A capability check uses synthetic input and proves tool exchange plus usable JSON responses. Prefer schema-constrained responses if supported; strict JSON parsing/validation remains mandatory either way. Credentials/model/capability failure is a configuration error, not a blind retry.

The request contains only the pinned question prompt, ordered fixed network contract, scoped patient projection, strict CPT response contract, and the one permitted tool declaration. A tool bridge validates model-requested name/arguments, calls MCP, returns its bounded result, and preserves required tool correlation IDs. The model cannot supply its own authorization metadata. Reject disallowed calls, excessive tool loops, oversized requests/responses and ambiguous multiple final outputs.

Keep all tool exchanges within one estimation attempt; do not grant a new retry budget for each tool call. Maximum ten tool calls per attempt, 60 seconds per provider HTTP request, and ten minutes per active question batch excluding queue wait/manual pause. Set explicit prompt/output byte/token limits after measuring admitted models and endpoint capabilities. If complete CPT output cannot fit, reject admission or fail clearly; no silent truncation.

Never trust model explanations as patient facts, template text, SQL, executable code, or new instructions. Retain restricted request/response artifacts for provenance, but ordinary logs/audit contain only safe metadata and references.

### 8.4 Durable queue and execution

Run states: `queued → preparing_question → estimating_cpts → validating_cpts → inferring → rendering → succeeded`, with `needs_clarification|failed|stale|cancelled` exits. Each question has the same stage states plus `pending` and `not_applicable`; run success requires all questions succeeded or validly not applicable and a valid assembled proposal/DDI artifact.

Only one question is eligible per run. Complete and persist its result **and rendered section** before atomically enabling its successor. No later question runs while the current one fails, retries, or waits for clarification. Two provider calls may run across different encounters; one active generation per encounter. Database-backed admission and global concurrency must remain correct if a second worker is started.

Original success atomically creates an immutable OriginalBaseline with validated CPTs, output and recommendation. A validated-but-unexecuted CPT set is not adjustable. Use separate job classes and bounded capacity for original estimation and local recalculation; provider congestion cannot consume all local calculation slots. Local jobs contain exact saved QuestionRun/revision/hash/configuration identifiers and need no provider or MCP context.

Queue implementation uses short claim transactions, row locks with `SKIP LOCKED`, lease expiration/heartbeat, random fencing token, and a deployment-generation check on commit. Do not hold database locks or transactions while awaiting the provider. Select eligible physicians round-robin and oldest eligible job within physician; persist enough scheduling state to avoid starvation. Keep the scheduler small at this user count. PostgreSQL documents `SKIP LOCKED` as appropriate for queue-like contention; it does not replace the application's lease and fencing rules. [PostgreSQL SELECT documentation](https://www.postgresql.org/docs/current/sql-select.html)

Initial operational defaults: heartbeat every 15 seconds, lease 120 seconds, two global provider slots, at most 100 queued runs. Treat these as tunable engineering settings; the admission benchmark verifies them. Check run/account/archive state both before expensive work and in the committing transaction. On lost lease, the old worker must not commit even if its provider call finishes. Provider billing can repeat after ambiguous failures; only committed application artifacts are idempotent.

### 8.5 Retries and resume

| Condition | Required behavior |
|---|---|
| Timeout, transient 5xx/429, transient MCP failure, invalid CPT JSON/content | One shared budget: initial attempt plus **two retries**, three total; persist attempt start before outbound work |
| Retry delay | Exponential delay with bounded jitter/Retry-After, maximum 60 seconds; persist next eligibility, do not sleep holding a lease transaction |
| Bad credential/model/capability | Fail configuration immediately; settings repair creates a new pinned run |
| Required missing/conflicting input or unknown gate | Needs clarification, no guessed value or blind retry; corrected inputs regenerate affected questions only |
| Inference failure | Retain accepted CPT artifact; resume inference without re-estimating |
| Rendering failure | Retain inference result; resume template rendering without provider/inference |
| Exhausted budget | Clear failed-question message, later questions pending, drafts and completed sections retained |
| Explicit manual retry | Author-only, matching run revision and unchanged inputs; new bounded batch at failed stage, prior history retained |
| Crash/restart | Resume from persisted artifacts/attempt counter after lease recovery; an uncertain in-flight request consumes its recorded attempt |
| Discard/deactivation/stale input | Cancel or mark ineligible, preserve history, reject late commits and signing; apply archive restrictions only under the declared provisional policy |
| Slider recalculation failure | Preserve current saved CPTs and separately labeled earlier successful result; author may retry locally or reset; no LLM/MCP request |

## 9. Proposal review, signing, and UI

After all eligible questions finish, assemble the stored template sections and pinned DDI report into one immutable initial proposal. Preserve skipped/unknown explanations and coverage limitations. Never ask the LLM to draft or rewrite prose. A partial run can be inspected but is labeled incomplete and cannot make an encounter review-ready.

### 9.1 Complete CPT review and adjustment

Each completed question exposes every CPT value, including roots, grouped by node and full parent-state combination. Show original/current exact percentages, differences, row totals, pinned network version and recommendation. Outputs are read-only calculated results. Show original and latest successfully adjusted outputs/recommendations side by side; unchanged recommendation text after adjustment is valid.

On a completed slider interaction, the server preserves selected value `X` and redistributes `100-X` among other states in that row in their immediately preceding committed proportions. If their prior total is zero, divide equally. Single-state rows remain 100%. Follow system-design.md §8.2: integer units, floor allocations, then largest fractional remainders with network state-order tie breaking. Other rows remain unchanged. Example `[20,30,50]` edited to first value 40 becomes `[40,22.5,37.5]`; `[100,0,0]` becomes `[40,30,30]`.

Atomically save the complete immutable revision, direct/redistributed before-and-after values, actor/time, cleared acceptance, calculation state, local job and audit. Queue completed commands in browser order and enforce expected server revisions across tabs. Save acknowledgment precedes calculation success; navigation/restart/failure preserves committed values and state. Sliders remain responsive while jobs run.

Local validation/execution uses only that QuestionRun's fixed snapshot, network, query/execution configuration and template. It does not call the LLM or MCP, rerun DDI, restart generation, or execute another question. Bind every result to its exact saved revision/hash and prevent an older response from replacing latest pointers, including browser responses. States are unchanged, recalculating, successfully recalculated, or failed; track input freshness separately. Mark previous results as earlier revisions while current values are unsolved.

Reset creates an audited revision restoring all of that question's original CPTs, with explicit verified baseline-result reuse under the design's default. It clears acceptance, fences older responses and retains history; other panels remain unchanged. Local retry targets the current revision only. Neither action can make changed patient inputs current. Regeneration establishes a new original baseline without transferring old adjustments.

### 9.2 Acceptance, signing, and surfaces

For every question display together: question, network version, **exact saved patient inputs supplied**, returned CPT percentages, and deterministic network result. Include source paths, missingness, prompt/template versions, status and retry action. Label CPT percentages separately from posterior probabilities. Paginate/expand large tables without hiding whether they are complete. Read persisted artifacts, not recomputed current chart values. Poll active runs roughly every two seconds, back off in hidden tabs, stop at terminal states.

The author explicitly accepts each question's exact current probabilities and matching successful result, including unchanged originals. Probability edits/reset, relevant input changes and regeneration invalidate affected acceptance. Pending, failed or stale current values cannot be accepted; successful recalculation or reset and renewed review are required.

Signing is one transaction locking patient/encounter: check active author, single-draft lifecycle, expected encounter/plan/review revisions, acknowledged saves, a complete current original proposal/DDI report, and exact current per-question acceptances and successful matching results. Recheck projection/applicability freshness and the declared archive policy. Freeze original and accepted CPTs/outputs, adjusted recommendations, patient inputs, network/prompt/template/execution versions, plan edits and acceptor/signer/timestamps in the signed snapshot. Release the draft slot and append audit atomically. Idempotent repeats return the same signed record. An unrelated note change needs the latest encounter revision, without rerunning inference or invalidating probability acceptance.

After signing, no clinical/plan/note/probability mutation is allowed through any route. Any active physician may append dated correction text with their own attribution to any signed encounter; original signer attribution remains intact. Changed structured assessments belong in a new follow-up.

Required browser surfaces:

| Surface | Required states and behavior |
|---|---|
| Login/research notice | Role/username/password, generic failure, Register message, every-login physician notice |
| Role dashboards | Shared searchable directory, own resumable drafts; admin navigation to all governance tools |
| Registration/follow-up wizard | Validated progression, autosave state, notes, explicit skip/bypass, source dates, conflict/retry/readonly states |
| History/DDI | Drug-only demo-catalog input, reconciliation, severity/conflict/evidence and uncovered catalog drugs |
| Proposal/final plan | Sequential progress, all transparency fields, every CPT slider/row, original-adjusted comparison, reset/local retry, calculation/freshness labels, acceptance, separate plan edits and sign eligibility |
| Patient chart | Signed snapshots versus current demographics, author-only draft content, chronology, any-physician addenda, authorized print |
| Model administration | XML import/edit/version history, validation report, read-only graph, workflow activation/rollback |
| Administration | Physicians/deactivation confirmation, masked provider settings/test, theme, archive, audit filters, CSV, backup and staged restore |

Use X-INSIGHT wordmark, restrained teal, dense clinical layout, semantic state labels, and the UI context's typography/spacing. Design dark tokens deliberately; do not invert colors. Both themes must support keyboard navigation, labels/error association, focus visibility, zoom, reduced motion, and text/non-text contrast. Existing teal/white contrast is insufficient for ordinary small button labels; choose a darker compliant action fill or compliant label treatment. Do not claim boldness alone fixes small text contrast. Urgent findings persist visibly; status never relies on color alone.

## 10. Exports, audit, and recovery

### 10.1 Exports and audit

CSV: stable English headers, UTF-8, proper quoting, spreadsheet-formula neutralization for text fields, no credentials. Preserve Patient ID as ten digit bytes; document importing the column as text because CSV has no type information. Do not use spreadsheet formulas to preserve zeros.

Printable HTML: current demographics and signed chronology, assessment completeness, history, medications/DDI coverage, original proposal and secondary plans, signatures and attributed notes/addenda. For every clinical question include complete original CPTs/output/recommendation and final accepted CPTs/output/recommendation, network/template versions, adjustment indicators and accepting physician/timestamps. Report final equality separately from adjustment history when adjusted then reset. Exclude private drafts from shared reports; physician printing remains proposed under §1.3. Escape text and provide legible page breaks for complete CPT tables. No PDF dependency.

Append audit for login successes/failures, administrator actions, record/model changes, original runs/retries/tool access, completed slider adjustments and automatic redistribution, resets, calculation outcomes, probability acceptance, signing/addenda, exports and recovery. Include actor/time/patient/encounter/question/run/revision and required before-and-after CPT values, including redistributed states. Successful mutations and audit commit together. Failure/historical outcomes must not claim current success. Stable attribution survives renaming/deactivation; admin can view/filter, and application roles cannot edit/delete events. Exclude keys and unrelated clinical text.

### 10.2 Backup creation

Full backup includes a consistent database snapshot and network XML, original baselines/raw estimates, complete CPT revisions and adjustment history, calculation results/failures, acceptances, signed snapshots, audit, prompts/templates/manifests, numerical policies and pinned engine/runtime/configuration/dependency-lock metadata. Preserve content/DDI provenance and all historical definitions needed for saved execution replay. Export XML from **that snapshot**, not mutable live files. Reproduction uses saved CPTs without new LLM estimates.

Exclude usable sessions from portable recovery; revoke sessions on restore regardless. Encrypted provider credentials can be included, but the deployment encryption key is never included in the downloadable archive. Manifest explains that migration without the old key requires key re-entry. Secure key escrow is an operator responsibility, not a plaintext backup feature. Authorized downloads are private/no-store and bounded; clean temporary artifacts after a documented retention window without deleting clinical history.

### 10.3 Restore workflow

1. Admin uploads to staging. Enforce compressed/uncompressed limits, reject traversal/absolute paths/symlinks, verify checksums/schema/application compatibility before any live mutation.
2. Restore only a supported app-generated format into isolated database/storage with a restricted role and resource limits. Uploaded SQL/archive content is untrusted; do not run it as database superuser or allow host-file/program access.
3. Validate singleton admin, references, content/network hashes, effective CPT artifacts, and report health. Present timestamp, backup identity, current replacement impact, missing-key consequence and staging digest.
4. Admin explicitly confirms that exact staged artifact. Enter maintenance mode; reject new writes, stop workers, fence old deployment generation, and take a pre-restore backup.
5. Swap the validated database selection under the maintenance lock, restart application/worker processes against it, revoke sessions/grants, clear caches, and reconcile restored pending work against exact saved revisions/lifecycle. Preserve failed/unsolved CPTs and history; fence old attempts before resuming eligible durable jobs. Completed numerical failures remain explicit local-retry/reset work. Add restore audit and an operator recovery record outside the replaced database.
6. Health-check login/read/replay. Reopen only on success; otherwise switch back to the pre-restore database/configuration and verify it. Keep external maintenance control independent of either database.

This is replacement, never a merge. Application-level atomicity means no mixed live traffic across old/new databases; PostgreSQL cannot transactionally combine a database switch and process restart. Implement explicit phases, durable operator status, and rollback. Old provider results must fail generation fencing after restore. A successful restore is demonstrated by a disposable restore drill, not merely producing an archive.

## 11. Security, operations, capacity, and trade-offs

Prototype-basic controls: server-side role/ownership validation, standard password hashing, opaque hashed sessions, HttpOnly/SameSite cookies (Secure under HTTPS), CSRF defense, login throttling, parameterized SQL, escaped output, secret redaction and request/file limits. No session timeout. HTTPS required outside localhost. This scope does not establish PHI hardening or clinical validation, and it does not impose a synthetic-only patient policy.

Provider destinations use operator allowlists, permitted schemes, DNS/resolved-address checks, and redirect restrictions. Local model endpoints require explicit host configuration; browser traffic outside localhost still requires HTTPS. Never allow arbitrary metadata or internal-network targets through a setting. Test the selected HTTP client's actual redirect/DNS behavior.

Configuration inventory: database URLs/roles, encryption key, public origin/TLS, allowed provider hosts, worker/provider limits, lease/heartbeat/retry budgets, XML/request/context limits, inference CPU/memory/time budget, backup staging path/limits, application/deployment generation, environment mode. `.env.example` contains placeholders only. Readiness requires database/schema; missing LLM/content disables generation, not basic record access. Separate live/readiness endpoints must not disclose configuration.

Planning load: up to nine physicians plus admin, 10,000 synthetic patients, roughly 20 ordinary HTTP requests/second, two simultaneous provider requests, seven sequential registration or six follow-up questions. Start benchmarking on 2 vCPU/4 GB RAM/20 GB persistent disk; these are assumptions, not proven capacity. Ordinary reads/acknowledged saves and patient search target p95 below one second at this load. Measure provider latency separately. Model admission must benchmark CPT size and inference complexity; user count alone is insufficient.

Record request latency/errors, save failure, database/disk health, queue age, heartbeat, provider attempt/validation failures, inference duration/resource failures, and last successful backup. Safe structured logs use request/run/question IDs. Initial alerts: missing heartbeat over two minutes, eligible queue age over five minutes, repeated provider auth failure, disk over 80%. Manual backups satisfy requirements; no automatic schedule, numerical RPO, or high-availability promise is implied. Proposed restore drill target is under 30 minutes on the reference dataset, subject to measurement.

Measure local calculation queue delay, duration/memory, stale-response rejection, and acknowledged adjustment recovery separately. Verify responsive sliders and independent local progress under provider saturation; local recalculations consume no LLM requests or MCP reads.

Cache immutable parsed definitions/catalogs by hash with bounded memory. Keep patient CPTs/projections scoped by run/question; never cache them under a base-model key. Authenticated records/exports use private/no-store. No cross-patient LLM response caching.

| Choice | Benefit | Cost and revisit trigger |
|---|---|---|
| Modular monolith + worker | Local transactions and one coherent release | Shared deployment coupling; split only with measured independent scaling/ownership |
| PostgreSQL queue | Atomic snapshot/job creation, no broker | Implement leases/fairness; add broker only if queue contention is measured |
| All-CPT estimation + exact inference | Matches requirements and permits stored-artifact replay | Full-table size/latency; admit bounded networks, do not silently narrow scope |
| Sequential questions | Durable section-level progress and explicit resume | Per-run latency is additive; scale across encounters |
| One private MCP tool | Small readable access contract | Subprocess lifecycle overhead; reuse only after isolation verification |
| Offline DDI release | Fast traceable runtime | Source parsing/review effort; expand sources only with coverage policy |
| Versioned content + signed snapshots | Historical interpretation remains inspectable | Storage growth; measure before designing archival tiers |
| Single host | Practical self-hosting | Single point of failure; increase resources first, replicas/failover when availability warrants |

## 12. TDD, verification, and release

### 12.1 Planned test seams

These proposed seams organize future verification through public interfaces; no new approval is asserted by this alignment. DDI ingestion and checking are separate. Tests and callers use the same interface; private-helper or database-side-channel checks do not replace observable behavior.

| ID | Planned seam | Observable behavior and real dependencies |
|---|---|---|
| T1 | Authenticated HTTP | Permissions, record lifecycle, revisions, signing, audit, exports, admin operations; real PostgreSQL |
| T2 | Assessment evaluation | Definition/answers to completeness and results; reviewed independent examples |
| T3 | DDI build/publish | CLI/library input to report/dataset; real source files and reviewed manifests |
| T4 | DDI checking | Medication list/dataset version to coverage and evidence; real PostgreSQL |
| T5 | Model validation/inference/replay | XML/contract/effective artifacts to validation and empty-evidence output; deterministic CPT redistribution fixtures; real pinned engine |
| T6 | Scoped MCP transport | Real subprocess discovery/tool calls, context denial, scoped result and lifecycle |
| T7 | Provider adapter | Protocol-shaped requests/tool exchanges/response/error behavior through controlled external endpoint; no internal collaborator mocks |
| T8 | Durable worker | Generation and revision-bound local start/read/retry; real queues/database/inference, MCP for estimation only, controlled provider/clock |
| T9 | Browser workflows | Visible user actions, accessibility, autosave, review/sign, admin and themes; real app for integration journeys |
| T10 | Backup/restore commands | Actual archives, staging/switch lifecycle and read/replay after restore in disposable environment |

Deployment verification exercises HTTP health, browser workflows, worker/recovery entry points, and their process restart behavior. It does not introduce tests of hidden internals. Do not create an extra per-file seam just because a function exists.

For each behavioral slice: write one failing test, verify its failure, implement the minimum behavior, rerun it, then move to the next slice. Prefer real owned dependencies; fake only the external provider, controlled time/randomness, or necessary OS failures. Keep expected values independent and review/refactor after green.

Do not mock internal module calls or assert call order instead of outcomes. For strict question order, observe public run status and requests captured by the external provider test endpoint. For persistence, read through HTTP/module interfaces rather than querying rows behind the interface. Fixture setup may use migrations/setup helpers; behavior assertions must use the planned public seam.

### 12.2 Command contract

Session S01 creates these commands. They are target commands, not claims they run today. Narrow checks accept a selector; full checks run only at milestones or when affected behavior warrants them.

```text
make setup                 # locked dependencies and development prerequisites
make dev                   # documented local stack
make migrate               # explicit migration command; never destructive reseeding
make check                 # formatting/lint/types/build, excluding network access
make test-backend TEST=tests/<seam>/test_file.py
make test-web TEST=<selector>
make test-e2e TEST=<journey>
make test-recovery         # disposable database/volumes only
make test-load             # synthetic representative data, records environment/results
make verify               # defined offline CI gate; no live provider credentials
```

Normal CI uses a deterministic provider test endpoint; live-provider smoke is explicit, separately reported, and uses synthetic inputs. A mock-only pipeline is insufficient: the acceptance run must cross real MCP transport, PostgreSQL, XML validation, exact inference, templates, and HTTP/UI. Test migrations on both fresh and previous schema state.

### 12.3 Mandatory release evidence

- Both end-to-end workflows through successful signing, with every required question covered by true/false/unknown gate fixtures and reviewed packages.
- Duplicate registration and single-open-draft races, author-only draft/artifact reads and writes, stale autosave, note noninterference, save/sign/deactivation/demographic-edit races, immutable signed records, any-physician attributed addenda; verify archive handling against the declared provisional policy.
- Every CPT required; exact parent/state ordering; invalid percentages/structural edits rejected; original XML unchanged; deterministic replay from stored artifacts without provider access.
- Real MCP access isolation across simultaneous patients and successive questions, forged context denial, no notes/unrepresented fields in any request/tool result.
- Three total mixed failure attempts; failed step retains prior results; later steps wait; manual retry resumes the failed stage; crash/lease fencing preserves attempt counters and unique commits.
- DDI count/traceability review, conflicts retained, source-backed alias resolution, all three coverage states, no runtime LLM/network dependency, released data reproducibility.
- All five transparency fields beside each recommendation; partial/stale/failed runs cannot sign even through direct HTTP.
- Every CPT/root/parent row reachable by keyboard; deterministic proportional/equal redistribution, endpoints/single-state/rounding; local-only affected-question calculation, comparison/reset/history and persistence across failures.
- Out-of-order results, adjustment/reset/sign races, failure with retained unsolved values, exact acceptance and immutable original/final signed snapshots; affected-only regeneration with no silent carry-forward and no note-triggered estimation.
- Printable original CPT history and backup/restore replay for adjusted signed encounters; local responsiveness while the provider queue is saturated.
- Both themes, Chrome/Firefox, keyboard/zoom/contrast, clear unsaved state, safe CSV/HTML, no secret disclosure.
- Fresh Linux install, locked versions, migrations, restart recovery, representative load/inference admission, full backup and destructive restore drill with rollback and session revocation.

*

### 12.4 Requirement coverage

The session-level mapping is in tasks.md. This matrix prevents scope loss.

| Requirement | Specification |
|---|---|
| FR-01 | §2.1 login, role dashboard, every-login notice, Register message |
| FR-02 | §2.1 singleton admin/admin, immutable username, no complexity |
| FR-03 | §2.1, §4.3, §7.3, §8.3, §9–11 all admin capabilities and themes |
| FR-04 | §2.1 physician administration, revocation, confirmed discard |
| FR-10 | §2.2 demographics/uniqueness, §4 constraints and concurrent creation |
| FR-11 | §2.2 and §5 diagnosis/threshold/warning/reason-free bypass |
| FR-12 | §5 unanswered PANSS, completeness, skip |
| FR-13 | §5 unanswered C-SSRS, completeness, skip |
| FR-14 | §2.2, §5–6 structured history, drug-only catalog, DDI coverage |
| FR-15 | §7–9 original proposal, CPT review/adjusted recommendations, separate secondary plan/signature |
| FR-16 | §2.3, §8.1, §9.1 notes, saved draft/adjustment state and confirmed discard |
| FR-20 | §2.2 follow-up, §7.1 follow-up questions, §9 sign/chronology |
| FR-21 | §2.2, §5 four tri-state effects and severity |
| FR-22 | §2.1–2.3, §4, §9 ownership, immutable signatures, addenda |
| FR-23 | §2.3 shared directory, clinical-status/search/archive |
| FR-30 | §7.1 thirteen questions, one combined LAI network |
| FR-31 | §7.2–7.3 question prompt/XMLBIF/XSD |
| FR-32 | §7–8 fixed definitions, sequential processing, scoped input |
| FR-33 | §7.4, §8 MCP-mediated all-CPT estimation |
| FR-34 | §7.4, §8.4–9 application inference and templates |
| FR-35 | §8.1–8.2 automatic run/internal MCP, §9 persisted transparency |
| FR-36 | §8.5 bounded retries, preserved results, failed-step resume |
| FR-37 | §7.3 registry graph/XML/version/activation/rollback |
| FR-40 | §10.1 CSV and print HTML |
| FR-41 | §10.2–10.3 full backup/restore |
| FR-42 | §4, §10.1 append-only audit |
| FR-43 | §8.3–8.5 settings/queued provider calls; §9.1 isolated local calculations without LLM/MCP |
| FR-50–51 | §9.1 every CPT/root/parent row, original/current labels and read-only outputs |
| FR-52 | §7.4, §9.1 exact deterministic redistribution and probability constraints |
| FR-53–54 | §9.1 affected-only local execution, pinned inputs/versions, original/adjusted comparison and states |
| FR-55–56 | §4.1, §9.1 reset, immutable history, durable revision/actor/calculation state |
| FR-57 | §4.3, §9.2 exact acceptance and atomic original/final signed snapshot |
| FR-58 | §8.4–8.5, §9.1 revision fencing, earlier-result labels and local retry/reset |
| FR-59 | §2.3, §4.2, §8.1, §9.1 affected-only regeneration/new baseline, no adjustment transfer |
| NFR-01 | §3, §11 Linux/VPS concurrent deployment |
| NFR-02 | §2.1, §11 authentication/no timeout/HTTPS |
| NFR-03 | §9 browser/theme/accessibility/validation/confirmations |
| NFR-04 | §2.3, §7.4, §8.4–8.5 durable drafts and replay |
| NFR-05 | §4, §7, §10 versioned artifacts/XSD/audit |
| NFR-06 | §8.4, §9.1, §11 responsive sliders, isolated capacity and no stale acceptance |

Technical references: pgmpy's [reading/writing index](https://pgmpy.org/api/readwrite.html) documents XMLBIF reader/writer support; [VariableElimination](https://pgmpy.org/api/generated/inference/pgmpy.inference.VariableElimination.html) documents exact inference and ordering controls. These references support adapter selection, not compatibility or clinical validation of the supplied networks. The bootstrap/admission tasks must verify the pinned releases.
