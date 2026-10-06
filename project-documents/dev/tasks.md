# X-INSIGHT coding sessions

Aligned 2026-10-03 to [user-requirements.md](user-requirements.md), [system-design.md](system-design.md), and [system-architecture.md](system-architecture.md), with implementation detail in [plan.md](plan.md). Sessions and checks are future work, not completed implementation. Stack/test seams/numerical limits remain proposals; carry forward the unresolved policies in system-design.md §15 rather than claiming approval.

Path shorthand: `B/` = `backend/src/x_insight/`, `BT/` = `backend/tests/`, `W/` = `web/src/`. Project documents are under `project-documents/dev/`. Medical/BN paths, counts, and named-source examples below are inherited drafting leads: verify actual supplied content before use. They are not findings established by this document review.

Recorded progress and handoffs are indexed in [progress/README.md](progress/README.md).
Read the relevant session reports; write outcomes to their session/checkpoint files
and add new reports to the index.

The session IDs remain stable; active question sessions are S27 and S29–S38. Execute S25 before S24, S44 before S41, and the added S48a–S48d probability work before signing. Clinical content gates do not block generic mechanics using clearly separate synthetic fixtures.

## Foundation and identity

### S00 — Establish the execution and content review ledger

**Depends:** none. **Requirements:** all, especially FR-30–37/NFR-05. **Seams:** no new tests.

**Read:** plan.md §§1, 5–7, 12; source requirements/design; `BNs/schema.xml`; medical file inventory. **Write:** [S00 progress report](progress/s00.md), [progress index](progress/README.md), and content review ledger under `content/` when that directory is first needed; no application code.

1. Record the confirmed product rules from the three baseline documents: empty inference evidence, private single draft, any-physician addenda, full CPT review and exact-result acceptance. Label stack, numerical choices and test seams as proposals; carry forward remaining policy inputs.
2. Inventory actual supplied XML, clinical content and DDI files with hashes, structural versus executable status, and missing prompts/mappings/templates. Verify inherited counts (11 XML/128 DDI) only if that material exists; do not report them as current inventory without evidence.
3. Create review entries for assessment wording/periods, history fields/severity, DDI aliases/coverage, each clinical question, and workflow bundles. Include the BN-06 restriction, LAI scope decision, FR-14 regimen mismatch, and reference-table assumptions.
4. Define each review package's deliverables and exact approval record; prepare owner questions only for substantive missing content, with concrete proposed choices. Infrastructure remains eligible while responses are pending.

**Verify/exit:** ledger covers every question in plan.md §7.1 and names the owner as reviewer for content requiring review, without claiming approval. S08 assessments have no owner reviewer or approval requirement. Handoff identifies the first engineering session and content tasks.

### S01 — Bootstrap a reproducible development loop

**Depends:** S00. **Requirements:** NFR-01, NFR-05. **Seams:** T1/T9 smoke only.

**Read:** plan.md §§3, 11–12. **Files:** `backend/pyproject.toml`, `backend/uv.lock`, `web/package.json`, npm lockfile, `B/app.py`, minimal `W/app/`, `compose.yaml`, `Makefile`, `.env.example`, minimal CI configuration.

1. Select compatible Python/Node/PostgreSQL/library versions using primary documentation and a small installation smoke; pin them. Verify pgmpy and MCP SDK compatibility explicitly; do not copy an untested version from this plan.
2. Create the minimum FastAPI process and React/Vite page. Add a failing public health check, then implement the response. Add a browser smoke that loads the X-INSIGHT shell; no fake clinical screens.
3. Create disposable local PostgreSQL and test databases, locked install commands and the Make targets in plan.md §12.2. Targets for not-yet-created suites must report that clearly rather than claim success.
4. Wire formatting, lint/types, build and offline CI startup. Document local ports, environment placeholders and how to stop the stack. Do not seed clinical content.

**Verify:** fresh locked install, health/browser smoke, `make check`; no credentials in tracked files. **Exit/handoff:** another agent can start the same stack from documented commands and knows the pinned runtime versions.

### S02 — Establish real persistence and request contracts

**Depends:** S01. **Requirements:** NFR-04–05, cross-cutting FR-42. **Seams:** T1.

**Read:** plan.md §4. **Files:** `B/db.py`, `B/contracts.py`, `B/operations/audit.py`, initial Alembic migrations, `BT/http/test_contracts.py`.

1. Create application/migration/read-only database roles, connection lifecycle, schema migration entry point and isolated test fixture lifecycle. Do not create every future domain table now.
2. Through public health/readiness, fail on unavailable/incompatible schema and return safe readiness state; implement request correlation and the standard error body without tracebacks.
3. Establish shared revision/idempotency conventions and transaction-scoped audit insertion for subsequent command implementations. Add only storage needed now; first meaningful mutation behavior is tested in S03/S06, not with a test-only production endpoint.
4. Document canonical JSON/hash behavior and UTC serialization at their actual public uses. Add size limits and structured validation errors to the HTTP interface.

**Verify:** migrated fresh database, unavailable-database readiness, no secret traceback, `make check`. **Exit:** all future modules can use one transaction context; audit permissions are prepared without adding a generic repository layer.

### S03 — Implement login, sessions, and own credentials

**Depends:** S02. **Requirements:** FR-01–02, FR-04, NFR-02. **Seams:** T1. **Tests:** `BT/http/test_identity.py`.

**Files/read:** plan.md §§2.1, 4.3, 11; `B/identity/`, identity migration.

1. Red: a fresh database permits `admin/admin`, a second initialization preserves a changed password, and no second admin can be provisioned. Implement singleton seeding and standard password hashing.
2. Red: login returns an opaque cookie session and `/me`; wrong credentials or selected role mismatch cannot grant access. Implement generic errors, active-account checks, CSRF and login throttling.
3. Red: logout/password change revokes prior sessions; advancing a controlled clock does not cause idle/absolute session expiry. Implement credential revision checks without a timeout or complexity rule.
4. Red: admin username mutation/self-registration is denied; empty password input fails without silently trimming passwords. Audit successes/failures with no credentials in event payloads.

**Verify/exit:** HTTP identity suite with real PostgreSQL, cookie behavior on local/HTTPS configuration, masked error inspection. Handoff records session revocation behavior and the authenticated test-fixture interface.

### S04 — Implement physician account administration

**Depends:** S03. **Requirements:** FR-03–04, FR-42. **Seams:** T1. **Tests:** `BT/http/test_physicians.py`.

**Files/read:** plan.md §2.1; `B/identity/` account commands/routes and migration.

1. Red: admin creates/edits a physician and retrieves safe fields; physician cannot list credentials/manage accounts or elevate their role. Preserve stable actor IDs across rename.
2. Red: reset/deactivation revokes existing sessions immediately and inactive users cannot authenticate. Implement revision requirements and reactivation.
3. Implement the deactivation command's explicit retain/discard choice and draft-set revision contract. Until drafts exist, the reviewed set is empty; S51 adds the real draft/job race cases. Do not fabricate draft tables solely for this test.
4. Red: repeated account commands respect idempotency/conflicts, audit has safe attributed events, and no response includes a hash or raw password.

**Verify/exit:** account authorization/revocation checks pass. Handoff specifies the later Cases integration point for confirmed draft discard, explicitly marked incomplete until S51.

### S05 — Build role navigation and both themes

**Depends:** S03–S04. **Requirements:** FR-01, FR-03, NFR-03. **Seams:** T1/T9. **Tests:** `e2e/identity.spec.ts`, `e2e/themes.spec.ts`.

**Read/files:** `project-documents/dev/ui-context.md`, plan.md §9; `W/app/`, `W/shared/theme.css`, `W/features/identity/`, theme preference route.

1. Red browser journey: login → correct role dashboard; Register shows “Contact administrator”; every physician login shows the exact research warning before proceeding.
2. Add role-owned navigation, identity/sign-out, own password form and admin physician table/forms. Route guards complement server checks, never replace them.
3. Design deliberate light/dark semantic tokens consistent with the existing visual character and required theme toggle. Persist theme via `/me/preferences`; check contrast, especially small labels on teal. Palette values are implementation choices, not an additional unresolved product scope decision.
4. Exercise keyboard focus, associated error labels, reduced motion and generic login errors. Render loading/empty/error states from real endpoints; no placeholder medical recommendations.

**Verify:** both browsers for identity; theme contrast/keyboard evidence and build. **Exit:** administrator can create a physician through the browser; both can log in and change own password/theme.

## 4. Records and assessments

### S06 — Register and find a patient

**Depends:** S03, S05. **Requirements:** FR-10, FR-23. **Seams:** T1/T9. **Tests:** `BT/http/test_patients.py`, `e2e/registration.spec.ts`.

**Files/read:** plan.md §§2.2–2.3, 4; `B/cases/patients.py`, routes/schemas, patient/encounter migration, `W/features/patients/`.

1. Red: valid physician creation atomically returns patient plus registration draft, server timestamp and revision; admin cannot create. Patient ID `0012345678` round-trips unchanged.
2. Red: ages 17/100, decimal ages, non-ASCII digits, malformed ID, missing sex/status, and invalid name characters fail with field errors. Include a valid Unicode-letter name after normalization.
3. Red: simultaneous same-ID requests produce exactly one patient; a duplicate archived ID also conflicts. Repeated idempotent creation returns original IDs.
4. Add directory search by name/ID, clinical-status filter, bounded pagination and demographics form with Next disabled until valid. Results remain shared across physicians.

**Verify/exit:** real concurrent HTTP uniqueness check, browser create/search, migration upgrade. Do not add delete/merge or store the identifier numerically.

### S07 — Persist, resume, and discard author-owned drafts

**Depends:** S06. **Requirements:** FR-16, FR-22, NFR-04. **Seams:** T1/T9. **Tests:** `BT/http/test_drafts.py`, `e2e/autosave.spec.ts`.

**Files/read:** plan.md §§2.3, 4; `B/cases/encounters.py`, `W/features/encounters/` wizard/autosave.

1. Red: author saves/retrieves a private draft after restart; other physicians/admin cannot use ordinary routes to read its clinical content or derived artifacts or mutate it. Enforce transitive privacy and lifecycle states distinct from calculation/readiness state.
2. Red: stale-tab PATCH fails `412`; client retains unsaved edits and offers reload/reconcile. Implement debounced save, page-transition flush, saving/saved/failed states.
3. Red: failed network/database save never shows Saved; navigation warns while local edits remain. Unsaved initial demographics are not advertised as durable.
4. Red: simultaneous open-draft creates, including by the same author, allow one patient draft only; other authors receive a generic conflict. Discard requires confirmation/current revision, releases the slot and does not delete the patient. Physical draft-content retention remains proposed; signing/discard release and deactivation retention are mandatory. Integrate job cancellation when jobs exist.

**Verify/exit:** multi-tab browser case and restart recovery. Handoff documents revision ownership so later assessment pages use the same autosave path, not separate persistence mechanisms.

### S08 — Define and release experimental assessments and implement the evaluation interface

**Depends:** S00, S07. **Requirements:** FR-11–13, NFR-05. **Seams:** T2/T1. **Tests:** `BT/assessments/test_definitions.py`.

**Read/files:** plan.md §5; all three assessment source documents; `content/assessments/`, `B/assessments/`, released-definition routes.

1. Author versioned item/schema/period/branching/completeness/result definitions from supplied sources, with source hashes and uncertainty explicitly identified. The implementing agent selects form/time-window choices and documents source gaps and experimental defaults without waiting for owner input. Preserve C-SSRS paraphrase status.
2. At T2 implement validated definition loading and `evaluate(definition, answers)` using a tiny synthetic definition first. Red: unanswered/partial/complete/not-assessed are distinguishable and no missing answer becomes zero.
3. Reject undeclared item IDs, invalid values, unknown rule operators and arbitrary executable expressions. Release definitions after schema/rule validation and passing independent reference examples, recording version, source hashes, assumptions and validation results. Expose released definitions through authenticated content routes; reviewer identity and approval records are not required for assessment release.
4. Prepare independently derived examples for S09–S11 and verify the concrete forms/rules against them. Release each validated assessment package for its implementation session. Record gaps and chosen experimental defaults in the definitions and handoff; they do not create an owner-response dependency.
5. Resolve wording, form/version, periods, scoring, branching, completeness, source-derived diagnostic thresholds and alerts autonomously within the experimental scope. Do not turn these choices or source gaps into owner questions or approval requests. Correct failed validation and rerun checks; do not substitute owner sign-off. Later consumers use the released definitions without requiring assessment approval again.

**Exit:** executable definition contract and all three versioned assessment packages are released with passing validation/reference examples. S08 completes without any owner review, approval, sign-off, or response; neither S08 nor its assessment packages may be marked `awaiting_review`. The project is experimental, so implementation validation replaces owner approval for these assessments. This does not establish clinical validation or introduce treatment thresholds. This exception also governs S00's ledger and later assessment dependencies; other content-review gates retain their existing scope.

### S09 — Implement diagnosis, threshold warning, and bypass

**Depends:** S08 diagnosis package released after validation; S07. **Requirements:** FR-11, FR-16. **Seams:** T2/T1/T9. **Tests:** `BT/assessments/test_diagnosis.py`, `e2e/diagnosis.spec.ts`.

**Files/read:** S08 released definition, `schizophrenia-criteria.md`, plan.md §§2.2, 5; assessment evaluator and `W/features/assessments/diagnosis/`.

1. Red: source-derived qualifying worked case satisfies every required criterion; a case with only symptom count satisfied does not. Implement the S08-defined full criterion logic and live preview.
2. Red: partial answers have no completed threshold result. Save incomplete work without mislabeling it as below threshold.
3. Red: a completed below-threshold case continues only after an attributed warning acknowledgment tied to the assessed revision. Later relevant edits invalidate the acknowledgment.
4. Red: bypass succeeds without any reason field, records actor/time/status, and survives resume. Implement UI that makes bypass distinct from completion and uses the shared autosave contract.

**Verify/exit:** evaluator reference cases, direct HTTP enforcement, browser threshold/bypass/resume.

### S10 — Implement PANSS without implicit minimum answers

**Depends:** S08 PANSS released after validation; S07. **Requirements:** FR-12, FR-20. **Seams:** T2/T1/T9. **Tests:** `BT/assessments/test_panss.py`, `e2e/panss.spec.ts`.

**Files/read:** `PANSS.md`, S08 released definition; evaluator, PANSS UI and saved answers.

1. Red: fresh form has 30 unanswered items and null total; Skip persists `not_assessed`. Implement explicit selection only.
2. Red: fully answered all-1 yields 7/7/16/30 and all-7 yields 49/49/112/210; implement source-defined arithmetic and value validation.
3. Red: one missing required item suppresses total, invalid/out-of-range/noninteger input is rejected server-side, resumed answers preserve completeness.
4. Show subscales/items, assessment window and prior encounter score as historical. Add only S08-defined and validated interpretation/change rules; do not derive a treatment gate from approximate score bands.

**Verify/exit:** independent literal fixtures, browser skip/partial/resume and source-version persistence. No default “1” values or hidden zero scores.

### S11 — Implement C-SSRS form and distinct results

**Depends:** S08 C-SSRS released after validation; S07. **Requirements:** FR-13, FR-20. **Seams:** T2/T1/T9. **Tests:** `BT/assessments/test_cssrs.py`, `e2e/cssrs.spec.ts`.

**Files/read:** `CSSRS.md`, S08-selected form/time windows, plan.md §5; C-SSRS evaluator/UI.

1. Red: all unanswered and skipped produce no assessed result; complete explicit negatives produce the S08-defined no-ideation result. Implement selected branching/completeness.
2. Red: source-derived worked example with level 3 endorsed gives severity 3 without auto-filling lower responses; intensity/behavior/lethality remain separate.
3. Red: historical versus current answers retain their periods; incomplete required branch has missing-item guidance, not a guessed negative. Implement the alert logic defined and validated in S08.
4. Browser renders persistent review/urgent messages with text and keyboard access; switching pages/autosave cannot erase responses or turn a skip into zero.

**Verify/exit:** independent source-derived examples, no composite risk score, direct HTTP value/period validation and browser resume.

### S12 — Draft and implement structured history and adverse effects

**Depends:** S08, S07; field review before clinical activation. **Requirements:** FR-14, FR-20–21. **Seams:** T2/T1/T9. **Tests:** `BT/http/test_history.py`, `e2e/history.spec.ts`.

**Read/files:** plan.md §5 and question-input leads, four adverse-effect criteria documents; `content/history/`, `B/cases/` history/effects, history UI.

1. Draft the minimum typed history fields, clinical periods and analysis-visible text label for owner review. The 2026-10-04 owner decision selects full standardized adverse-effect questionnaires. Draft versioned full instrument definitions with item wording, administration/completeness/scoring contracts, source hashes, independent examples and severity/network mappings for owner review. Confirm the complete AIMS form/source. Name the acute-dystonia form “Acute Dystonia Dx Criteria” and draft its full criteria-based definition from the supplied source for review; do not invent missing definitions or a total score. Full completion applies only when the corresponding effect is present. Include mapping gaps required by networks; the 2026-10-04 owner decision permits drafting exposure, duration, trial-adequacy, prior-response, and monitoring fields for review. Do not add FR-14-excluded medication regimen fields.
2. Red after review: history values persist with provenance, unknown/not-assessed distinct from false; undeclared/excluded fields fail validation.
3. Red: each of four effects accepts present/absent/not-assessed; present requires the corresponding complete reviewed questionnaire and reviewed severity; absent/not-assessed has null severity and no questionnaire-completion requirement. Changing status must explicitly clear an obsolete severity.
4. Add optional phone update and history reconciliation state for follow-up; clearly distinguish analysis-visible history from page notes. Implement complete standardized questionnaires under the reviewed S12 definitions. Preserve item responses, definition version, completeness and nullable results; missing required items suppress calculated results unless a reviewed source explicitly defines a missing-data rule. Keep urgent handling independent of questionnaire completion. Enforce questionnaire completeness only for present effects, including the “Acute Dystonia Dx Criteria” form.

**Verify/exit:** author/revision rules inherited from S07, reviewed field fixtures, browser saved-state behavior. Mapping additions later must update the content version and these public checks.

### S13 — Add attributed page notes and prove separation

**Depends:** S07. **Requirements:** FR-16, FR-22. **Seams:** T1/T9. **Tests:** `BT/http/test_notes.py`, `e2e/notes.spec.ts`.

**Files/read:** plan.md §2.3; note storage/commands and reusable wizard note control.

1. Red: author adds a timestamped note to any wizard page and sees it after resume; a retried idempotent command creates one note.
2. Red: another physician/admin cannot author/edit the draft's notes; actor/time are server-derived. Treat notes as append-only entries; correction in a draft can be a new note.
3. Render notes separately from history, with page/author/time, and support the demographics page after initial draft creation. Escape text; test literal markup rendering.
4. Define the explicit serializer exclusion used by future snapshots, but do not create speculative snapshot machinery here. Record S40/S41/S59 as mandatory end-to-end note-noninterference checks.

**Verify/exit:** public HTTP persistence/ownership and browser placement pass. A page note is never relabeled algorithm-visible history.

### S14 — Build shared chart and follow-up draft entry

**Depends:** S09–S13. **Requirements:** FR-20–23. **Seams:** T1/T9. **Tests:** `BT/http/test_followup.py`, `e2e/followup.spec.ts`.

**Files/read:** plan.md §§2.2–2.3; `B/cases/` chart/follow-up commands, `W/features/patients/chart/`. Use a test-only signed baseline fixture until S49 implements signing; S49 is not a dependency of this session.

1. Red: any active physician can read demographics/signed chart and create a follow-up if the single-draft slot is free. Only its author can read/edit the draft and its derived artifacts; do not expose other authors' draft content.
2. Red: history/medications copy with baseline provenance and reconciliation required, but PANSS/C-SSRS start unanswered. Display prior scores/dates distinctly.
3. Red: two physicians concurrently creating a draft for one patient produce one success and one generic conflict, without leaking draft content. Different patients can have independent drafts; relevant shared demographic changes mark the author's affected results stale in S48d/S51.
4. Build chronology, draft badges, phone/history/effect pages, and navigation to review. Until reasoning exists, show unavailable generation honestly; no fake successful proposal.

**Verify/exit:** follow-up creation/resume and shared-read journey. Temporary signed fixtures stay test-only and do not add a bypass-sign production route.

## 5. DDI ingestion, review, and checking

### S15 — Parse one source through the ingestion interface

**Depends:** S02. **Requirements:** FR-14, NFR-05. **Seam:** T3. **Tests:** `BT/ddi/test_ingestion.py`.

**Read/files:** plan.md §6; DDI design §§5–8/17–19; actual `docs/medical-docs/DDI-text/Antidiabetic Agents/Sitagliptin.txt`; `B/ddi/ingestion.py`, CLI entry point, candidate output schema.

1. Record the fixture's original checksum, real category headings, expected counts and representative source spans. Keep the original source unchanged; fixture references/copies must record provenance.
2. Red at `build(...)`: discover the monograph and identify its actual interaction section, ignoring earlier navigation/summary headings. Implement minimal section/state handling.
3. Red: preprocessing preserves original line spans while removing repeated page chrome, handles BOM/line wrapping, and stops before Adverse Effects/Warnings. Do not test the private preprocessor separately.
4. Red: candidate report contains exactly 0/4/92/70 entries by source category, raw text and traceable spans. A deliberately removed entry fails count validation and still produces an anomaly report.

**Verify/exit:** build interface and CLI agree; no SQL/runtime checker yet. Expected counts come from the monograph, not the parser's own totals. Do not call an LLM to obtain passing counts.

### S16 — Extend the parser across real source formats

**Depends:** S15. **Requirements:** FR-14, NFR-05. **Seam:** T3. **Tests:** extend `BT/ddi/test_ingestion.py` only for distinct observed formats.

**Read/files:** representative monographs discovered from at least three different source groups; `B/ddi/ingestion.py` and fixture/source manifest.

1. Inspect the corpus for real heading/continuation/entry variants; select one failing representative at a time. Add a public build assertion for the expected source-derived entries, then support that format.
2. Red: page-break continuation and wrapped entity headings preserve complete text and original span references; next sections are never swallowed as interactions.
3. Red: repeated pair entries and contradictory severity assertions survive ingestion; Sitagliptin/ofloxacin retains both categories. Preserve direction only when explicitly supported, otherwise mark unknown.
4. Run the entire corpus in report-only mode. Every discovered file has passed/failed status, category counts, checksum and diagnostic location. Failures cannot quietly disappear from denominators.

**Verify/exit:** every actually supplied source is accounted for; verify inherited counts rather than requiring an assumed 128 files. Enumerate all anomalies and unsupported formats with source evidence for review/repair. Split into S16.a/b if real variants exceed a bounded session.

### S17 — Resolve controlled medication concepts and aliases

**Depends:** S15–S16. **Requirements:** FR-14. **Seam:** T3 through resolved/unresolved concepts in build output; T4 repeats runtime checks in S19. **Tests:** `BT/ddi/test_terminology.py`.

**Files/read:** plan.md §6.1; `content/ddi/aliases.json`, concept schema, normalization implementation inside `B/ddi/`.

1. Draft concepts for bundled selectable medications and interacting entities, separating ingredients, combinations, herbs/foods/substances. Record source-backed brand aliases; request review for uncertain equivalences.
2. Red: canonical/case/whitespace/approved alias variants resolve to one stable ID; ambiguity yields unresolved rather than choosing the first row.
3. Red: look-alike names remain distinct; unknown name stays unknown; salt removal, strength stripping and combination splitting are not applied without reviewed rules. Patient inputs remain drug-only.
4. Red: canonical unordered pair identity is stable under input reversal while the source assertion's subject/object remain unchanged. Export unresolved names and alias collisions for review.

**Verify/exit:** approved aliases, explicit pending decisions, no fuzzy matcher/external terminology dependency. Do not count an unresolved entity as proof of complete source coverage.

### S18 — Build, review, and publish an immutable DDI release

**Depends:** S16–S17, S02. **Requirements:** FR-14, NFR-05. **Seams:** T3/T1. **Tests:** `BT/ddi/test_publish.py`.

**Files/read:** plan.md §6.2; `B/ddi/` publication CLI, DDI migrations, `content/ddi/` review manifest and report.

1. Red: publication rejects count failures, unresolved entities needed by the release, absent provenance and required unreviewed evidence. Implement dataset staging/import as one coherent operation.
2. Draft the corpus report: discovered/processed/pass/fail documents, severity counts, unique pairs, duplicates/conflicts, unknown names, hashes and parser version. Give the owner concrete high-risk/conflict/anomaly review records.
3. Incorporate approved corrections without modifying originals. Preserve independent duplicate-direction evidence and separate source severity from management. Publish only an explicitly approved complete or limited-coverage release. The 2026-10-04 owner decision permits a reviewed limited-coverage release before full-corpus review; uncovered pairs remain `coverage_unavailable`.
4. Red: repeated publication of identical content is idempotent; changing a source creates a new candidate version; interrupted/invalid publication leaves the previous released dataset available.

**Verify/exit:** owner-reviewed release hash, reproducible import report and source/alias inventory. If review is pending, mark awaiting_review and continue with synthetic DDI fixtures; do not claim the corpus is ready or omit excluded sources from limitations.

### S19 — Implement deterministic coverage-aware DDI checking

**Depends:** S17–S18 (synthetic release acceptable for mechanics). **Requirements:** FR-14–15. **Seams:** T4/T1. **Tests:** `BT/ddi/test_checker.py`, `BT/http/test_ddi.py`.

**Read/files:** plan.md §6.3; `B/ddi/checker.py`, indexed lookup and `/ddi/check`, catalog search route.

1. Red: A/B and B/A yield the same pair/evidence; three unique drugs yield three pairs; duplicate aliases do not create self-pairs. Implement batched indexed lookup.
2. Red: known evidence produces `interaction_found` with every assertion, highest known severity and conflict/unknown indicators; directional descriptions are retained.
3. Red: explicit coverage without evidence produces `covered_no_listed_interaction`; catalog drugs/pairs lacking coverage produce `coverage_unavailable`. Zero/one-drug reports retain uncovered-catalog warnings. Unresolved source concepts stay in ingestion review, not patient free-text input.
4. Red: report pins dataset/catalog/fingerprint, refuses invalid dataset/configuration, rejects excluded fields/noncatalog patient entries, and executes with external network access disabled. Unresolved ingestion concepts are not a new free-text medication input feature.

**Verify/exit:** source-backed checker fixture plus all three status fixtures; role/session enforcement. Never use “safe” or synthesize an absence-of-interaction claim from missing rows.

### S20 — Integrate medications and DDI into encounter history

**Depends:** S12, S19. **Requirements:** FR-14–16, FR-20. **Seams:** T1/T9. **Tests:** `BT/http/test_medications.py`, `e2e/ddi.spec.ts`.

**Files/read:** plan.md §§2.2, 6, 9; medication draft storage, history UI, DDI evidence panel.

1. Red browser journey: select demo-catalog medications, including one without interaction coverage, and save/resume the drug-only list. Server rejects free-text unknown-label additions and dose/unit/route/frequency/status fields even if forged.
2. Red: changed medications trigger a fresh versioned report and show pending/error state until the current fingerprint matches. Do not present a stale report as current.
3. Display severity, raw evidence/source location, conflicts, and “coverage unavailable” for uncovered catalog drugs/pairs, including when no interaction rows exist. Escape source and user text.
4. Follow-up requires explicit reconciliation of copied medications; previous signed lists/reports remain unchanged. Persist current report reference for later run snapshots.

**Verify/exit:** browser covered/uncovered/conflict path and HTTP readback; DDI works while provider configuration is absent. Content approval for S18 still gates release of real data.

## 6. Model machinery and content packages

### S21 — Safely import and inspect XMLBIF drafts

**Depends:** S02. **Requirements:** FR-31, FR-37, NFR-05. **Seam:** T5. **Tests:** `BT/models/test_xml_validation.py`.

**Read/files:** plan.md §7.3; `BNs/schema.xml`, existing `BNs/test_schema.py`, sample BN-04 and BN-08; `B/models/validation.py`.

1. Red through validation interface: valid synthetic XML returns ordered parsed nodes/edges and a separate XSD report. Reuse the supplied schema; preserve source bytes/hash.
2. Red: DTD/entity, remote resolution, excessive input, malformed XML and unsupported content fail safely with bounded locations/messages. Configure the parser explicitly; do not rely on defaults.
3. Red: existing draft networks can be stored/inspected as drafts despite missing definitions; graph display must not convert `proposed_parent` properties into authoritative edges.
4. Read/export retains original state/parent order and metadata. Report multiple-network documents or unsupported kinds as nonactivatable under v1 rather than silently selecting/dropping nodes.

**Verify/exit:** new public validation tests plus existing schema checks. XSD success is never labeled executable/clinically valid. Do not alter source XML to make it fit the application.

### S22 — Enforce model semantics and admission limits

**Depends:** S21. **Requirements:** FR-31–32, FR-37, NFR-04. **Seam:** T5. **Tests:** `BT/models/test_model_admission.py`.

**Files/read:** plan.md §7.3; model validation and small synthetic XML fixtures.

1. Red: a valid XSD with a cycle, missing CPT, wrong dimensions, invalid normalization or empty outcomes is nonexecutable with distinct semantic errors. Implement checks after structure validation.
2. Red: valid root/parent ordering and complete finite probabilities pass; unsupported decision/utility nodes remain drafts. Supplied BNs all remain inactive.
3. Red: missing question mappings/prompts/templates, note source paths, unreviewed package, undeclared query/state or unsafe expression prevents activation. Do not make a JSON `reviewed=true` field alone sufficient without a review record.
4. Add configured XML/node/CPT-cell limits and deterministic admission diagnostics. Numeric thresholds are engineering limits selected from actual model measurements, not clinical thresholds.

**Verify/exit:** XSD-valid-but-semantic-invalid regression fixtures and clear import-versus-activation distinctions. Handoff identifies the contract validation hooks S25 will fill without inventing patient mappings.

### S23 — Validate every CPT and run reproducible exact inference

**Depends:** S22. **Requirements:** FR-33–34, NFR-04. **Seam:** T5. **Tests:** `BT/models/test_cpt_inference.py`.

**Files/read:** plan.md §7.4; `B/models/inference.py`, effective-artifact schema, pinned engine adapter.

1. Red: full two-node response yields empty-evidence `P(A=yes)=.20` and `P(B=yes)=.22`. An observed patient value must not clamp a node. Implement exact inference without base-table fallbacks or hard/soft/virtual evidence.
2. Red: omitted/duplicate/extra root/table/row, changed ordering/structure, malformed decimals, booleans/null/nonfinite/out-of-range values, excess precision and inexact totals are rejected. Follow proposed six-decimal decimal-string/integer-unit policy; never silently normalize.
3. Red: asymmetric multi-parent fixture detects XMLBIF-to-engine transpose bugs; registered XML/hash stays unchanged while effective XML contains all accepted tables.
4. Red: replay of frozen complete CPTs/query/configuration matches under the pinned runtime with provider unavailable and always empty evidence. Numerical/resource failures return explicit errors without approximation. Use a bounded child process where limits require it.

**Verify/exit:** independently worked numerical fixtures, metadata/order preservation, pinned runtime record. If resource isolation is too large, checkpoint S23.a validation/conversion and S23.b bounded inference; both must pass before integration.

### S24 — Build model version administration and read-only graph

**Depends:** S21–S23, S25, S05; execute S25 first. **Requirements:** FR-03, FR-37. **Seams:** T1/T5/T9. **Tests:** `BT/http/test_networks.py`, `e2e/networks.spec.ts`.

**Files/read:** plan.md §7.3; `B/models/registry.py`, registry migrations/routes, `W/features/admin/networks/`.

1. Red: admin imports/edits/exports XML as immutable versions; physician is denied; editing preserves prior bytes/hash and validation reports.
2. Red: graph renders actual ordered nodes/edges/readable states and validation status; no graphical edit operation exists. Use a small established layout dependency only if needed for readable networks.
3. Red: activation rejects incomplete/unreviewed workflow bundles, and valid activation/rollback is atomic with pointer revision and audit. Use synthetic complete bundles until S39.
4. Red: activation of a new version leaves a previously referenced version retrievable; stale pointer mutation fails. Display version history, validation details and explicit activation/rollback confirmation.

**Verify/exit:** admin browser import→inspect→version→activate/rollback journey and direct authorization checks. No draft BN is active by default.

### S25 — Define the reusable question-package contract and review harness

**Depends:** S21–S23; S08/S12 draft content inventory. **Requirements:** FR-30–35. **Seam:** T5. **Tests:** `BT/models/test_question_packages.py`.

**Read/files:** plan.md §§5, 7.1–7.2; `content/questions/` schema/review template, package loader/validator, synthetic package fixtures.

1. Red: a complete synthetic package validates fixed variables/types/states/order, typed patient mappings, gate/missingness rules, all-CPT contract, query, prompt, template, and review references.
2. Red: unknown/note source paths, implicit posterior chaining, any patient-evidence mapping, incomplete CPT schema, or arbitrary expression is rejected. Patient mappings are estimation context only; review cannot enable evidence under the confirmed contract.
3. Define the review dossier used in S27 and S29–S38: source comparison, explicit graph, complete reference-table provenance, estimation instructions, result mapping/wording, independent numerical and clinical examples, admission measurements and open assumptions.
4. Define template content with escaped-value slots and explicit branches; no LLM prose or unreviewed argmax treatment selection. Through package validation, reject references to undeclared output states and unsupported operators. Runtime section rendering is implemented and tested through the pipeline in S45.

**Verify/exit:** future question sessions change content, not eleven separate Python pipelines. Draft packages can be validated without being activated; owner review is a separate recorded decision.

### Common contract for S27 and S29–S38

These are **content-authoring sessions with implementation-ready outputs**, not permission to activate clinical models. Each depends on S25, the relevant S08 validated released assessments (no owner review), and owner-reviewed history/adverse-effect meanings. If required content is pending, draft alternatives and flag them instead of silently choosing; do not add an owner-approval dependency for S08 assessments.

For each session create `content/questions/<key>/{manifest.json,network.xml,prompt.txt,template.json,examples.json,review.json}`. Preserve supplied original BNs. Cover plan.md §7.2: CPT-context-only patient mappings, fixed empty-evidence execution, true/false/unknown gates, missing/conflict policy, templates, complete CPTs including roots, and reviewed source/reference-table provenance. Propose necessary history additions as versioned changes for review.

Run the S25 validator and S23 inference harness on independently worked examples through T5. Add one meaningful behavior fixture at a time when expected behavior is agreed. Agent-drafted clinical expected outputs are review candidates until approved; they cannot validate themselves. Hand off a readable rationale and the exact package hash. Use `awaiting_review` until the owner approves. Approval may be batched in S39 after all dossiers are concrete. Unanswered clinical/legal choices block package release, not generic engineering.

### S27 — Draft pharmacotherapy question

**Key:** `pharmacotherapy` (R2). **Requirements:** FR-14, FR-30–35. **Sources:** BN-04, STATEMENT-04, reviewed catalog/history.

1. Preserve BN-04's established-treatment review scope under the 2026-10-04 owner decision. Document retained variables/states/edges and output meanings, including metadata-only parents. The question stays applicable when registration has no established treatment (owner decision 2026-10-04). Define source-compatible mappings for that case; unknown required inputs pause execution rather than being guessed; do not redesign this question for initial medication selection.
2. Draft a fixed graph and source-backed treatment-context definitions consistent with established-treatment review; catalog identifiers are used only where the retained network outputs require them. Resolve excluded dose/route/etc. inputs through content redesign, not hidden form fields.
3. Draft outcome-to-template mapping, missing-data behavior and all-CPT estimation instructions. DDI findings remain a separate report; do not substitute DDI lookup for pharmacotherapy inference.
4. Supply worked cases for different relevant patient contexts and unknown required input, with proposed clinical expectations labeled for review and separate independent mathematical fixtures.

**Exit:** new versioned package and source-diff rationale; original BN-04 remains untouched. Do not call the existing draft a complete initial-choice network.

### S29 — Draft high-suicide clozapine question

**Key:** `high_suicide_clozapine` (R4). **Requirements:** FR-13, FR-30–35. **Sources:** BN-08, STATEMENT-08, S08 validated released C-SSRS (no owner approval), owner-reviewed history.

1. Distinguish current urgent findings, high-risk gate, persistent risk despite prior treatment, and the network's review output. Specify periods and structured provenance; a scale level alone is not an unreviewed treatment rule.
2. If BN-08 is supplied, resolve missing priors and fixed-table assumptions under all-CPT estimation. Record retained/changed states and CPT-context-only mappings; no observation evidence is permitted.
3. Draft complete prompt/template mapping preserving parallel urgent/review concerns. A false gate is not a negative posterior; unknown required gate pauses the question.
4. Prepare true/false/unknown gate examples and result-rendering examples reviewed by the owner; include a mathematical fixture separate from clinical correctness.

**Exit:** complete package ready for owner review, with urgent assessment handling independent of LLM latency and no fabricated risk percentage presented as scale output.

### S30 — Draft LAI discussion/review question

**Key:** `lai_indication_choice` (R5). **Requirements:** FR-30–35. **Sources:** BN-10, STATEMENT-10, reviewed catalog/history/preferences.

1. Preserve BN-10's existing discussion/review scope under the 2026-10-04 owner decision. Document retained variables/states/edges and output meanings. No specific LAI product selection or new product-choice contract is required; retain the existing package key for stable references.
2. Keep one XMLBIF network, one prompt and one question step. Define source-compatible discussion/review template mappings and what absent/uncertain preference or prior exposure means.
3. Resolve source input needs without introducing excluded medication regimen fields. Draft full CPT estimation, explicit state ordering and result-to-template branches for the retained BN-10 discussion/review outputs.
4. Prepare independent fixtures for retained outputs and required-missing inputs, keeping all relevant review findings visible. Record the owner scope decision and submit concrete graph/template mappings for review.

**Exit:** one reviewed LAI discussion/review package preserving BN-10 scope.

### S31 — Draft aggression clozapine question

**Key:** `aggression_clozapine` (R6). **Requirements:** FR-30–35. **Sources:** BN-09, STATEMENT-09, reviewed history.

1. Specify substantial/persistent aggression and prior-treatment context with source periods and clinician-reported fields. Do not silently equate PANSS hostility with the gate.
2. Draft graph/reference tables and the all-CPT contract, including root distributions and explicit missingness. Resolve deterministic-rule versus estimated-table semantics for review.
3. Draft a predefined review template preserving urgent and parallel concerns, separate from an autonomous treatment decision.
4. Supply true/false/unknown gate and result-mapping examples, with source-based clinical review expectations and independent numerical fixtures.

**Exit:** source-linked package hash and review dossier, including any new approved history field definitions. No missing finding is mapped to “No”.

### S32 — Draft established-case clozapine question

**Key:** `established_case_clozapine` (R7). **Requirements:** FR-10, FR-30–35. **Sources:** BN-07, STATEMENT-07, reviewed treatment-history definitions.

1. Make established clinical status an explicit applicability input. Define the further clinical review criteria, trial adequacy and observation windows without inventing thresholds or forbidden medication details.
2. Distinguish this registration question from F5 no-improvement follow-up; do not share one ambiguous package identity even if sources overlap.
3. Draft complete graph/prompt/CPT/template and source-diff rationale, including monitoring/willingness inputs only when defined and collected.
4. Prepare first-time false-gate, established applicable, and missing trial-context cases. Expected outcomes require owner review; mathematical fixtures remain independently calculable.

**Exit:** distinct registration package and reviewed input inventory. A first-time case does not receive a fake negative clozapine posterior when skipped.

### S33 — Draft tardive-dyskinesia follow-up question

**Key:** `tardive_dyskinesia` (F1). **Requirements:** FR-20–21, FR-30–35. **Sources:** BN-14, STATEMENT-14, tardive-dyskinesia criteria and approved severity definition.

1. Consume the reviewed full AIMS instrument definition and item responses. Map explicit effect presence and reviewed severity to the network; distinguish absent from not assessed. Do not infer the diagnosis from an unreviewed AIMS total.
2. Resolve source-required context/alternative explanations and missing input policy. Draft any new field for review before using it in a projection.
3. Draft fixed graph, full CPT estimation and templates with all relevant findings retained, avoiding unsupported treatment thresholds.
4. Provide effect-present/absent/not-assessed gate cases and severity/result cases. Validate explicit node/state ordering and one complete package, not copied BN-14 activation metadata.

**Exit:** review dossier and independent examples.

### S34 — Draft akathisia follow-up question

**Key:** `akathisia` (F2). **Requirements:** FR-20–21, FR-30–35. **Sources:** BN-13, STATEMENT-13, akathisia criteria.

1. Consume the reviewed full BARS instrument definition and item responses. Define how the approved present/absent/not-assessed and severity fields relate to source concepts. BARS item discussion does not authorize an invented summed severity rule.
2. Draft required context, fixed graph/states, missingness and CPT-context-only mappings, distinguishing motor restlessness from unreviewed differential assumptions.
3. Create full-CPT prompt and deterministic result/template mapping. Preserve source reasoning provenance and parallel review needs.
4. Provide true/false/unknown gates and approved severity/result examples plus numerical checks; request owner resolution for unsupported scoring or treatment mappings.

**Exit:** one distinct reviewable package without requiring a new mandatory scale beyond approved FR-21 content.

### S35 — Draft parkinsonism follow-up question

**Key:** `parkinsonism` (F3). **Requirements:** FR-20–21, FR-30–35. **Sources:** BN-12, STATEMENT-12, parkinsonism criteria.

1. Consume the reviewed full SAS instrument definition and item responses. Map reviewed effect/severity/context fields. The SAS source does not define universal mild/moderate/severe score bands; do not create them from historical thresholds.
2. Resolve onset/alternative-cause and other required facts through approved structured history, never page notes. Missing context remains explicit.
3. Draft fixed graph, every CPT contract, prompt, query and result-to-template branches with source-provenance rationale.
4. Prepare present/absent/not-assessed and severity/unknown fixtures. Keep numerical engine evidence separate from owner review of clinical interpretation.

**Exit:** package ready for review; source restrictions and proposed assumptions are visible instead of embedded silently in code.

### S36 — Draft acute-dystonia follow-up question

**Key:** `acute_dystonia` (F4). **Requirements:** FR-20–21, FR-30–35. **Sources:** BN-11, STATEMENT-11, acute-dystonia criteria.

1. Consume the reviewed full “Acute Dystonia Dx Criteria” definition and item responses, drafted from the supplied acute-dystonia criteria. Require completion only when acute dystonia is present; do not invent a total score. Define reviewed presence/severity/onset/context fields and explicit urgent concerns. Do not require completion of inference before displaying a source-approved urgent assessment message.
2. Draft the fixed graph and distinguish source review flags from probabilistic output semantics. Resolve missing priors/all-CPT replacement assumptions in the dossier.
3. Write scoped estimation prompt and predefined result mapping, preserving multiple concerns and no autonomous intervention.
4. Supply effect present/absent/not-assessed and required-context-missing cases, with reviewed clinical expectations and independent CPT/inference fixtures.

**Exit:** acute-dystonia package and exact unresolved content questions.

### S37 — Draft no-improvement clozapine follow-up question

**Key:** `no_improvement_clozapine` (F5). **Requirements:** FR-20, FR-30–35. **Sources:** BN-07, treatment-resistance guidance, S08 validated released PANSS (no owner approval), owner-reviewed history/baseline rules.

1. Draft an explicit no-improvement definition: baseline encounter/window, instrument/context and adequate-treatment information. Do not guess a percent-change cutoff or treat missing follow-up score as no improvement.
2. Create a package separate from R7, with its own gate, prompt, network identity and template even if source concepts are reused.
3. Define how outdated baselines and not-assessed severity affect applicability; required ambiguity pauses rather than silently skipping.
4. Supply improvement/no-improvement/unknown and differing-baseline cases, reviewing expected clinical mapping and independently checking mathematics.

**Exit:** precise follow-up gate and package, with source constraints.

### S38 — Draft continue-versus-adjust follow-up question

**Key:** `continue_or_adjust` (F6). **Requirements:** FR-20, FR-30–35. **Sources:** BN-04/05/06, STATEMENT-04/05/06, approved history/effects/preferences.

1. Compare overlapping source drafts and propose one clinical question/graph. Apply the 2026-10-04 owner decision retaining BN-06-derived content within the experimental/educational scope. Record that decision and the supplied STATEMENT-06 §Quality Measurement Considerations limitation in the derived package; current XML lacks its literal restriction text. Retention does not remove the source limitation or approve the concrete graph/templates.
2. Draft review outcomes for continuation versus adjustment, with fixed states, CPT-context-only patient mappings and required missingness. Execution has no patient evidence or causal intervention conditioning.
3. Supply complete all-CPT prompt and template branches retaining adverse effects/preferences/parallel concerns. No runtime merge of three models.
4. Prepare continuation/adjustment/insufficient-information and stale-baseline cases, plus independent numerical examples.

**Exit:** one package with the documented owner retention decision, source limitation and reviewed experimental/educational wording. Pending concrete graph/template review blocks release; no runtime merge or unreviewed activation.

### S39 — Review and release both complete workflow bundles

**Depends:** S24–S25, S27, S29–S38; owner decisions on all content. **Requirements:** FR-30–37, NFR-05. **Seams:** T1/T5. **Tests:** `BT/models/test_bundles.py`.

**Files/read:** plan.md §§1.4, 7; all question dossiers; `content/bundles/registration.json`, `followup.json`, content release manifest.

1. Present the eleven concrete packages and cross-package assumptions for owner review. Record exact hashes/decisions; revise rejected packages and rerun their scoped checks. Do not batch-approve on the owner's behalf.
2. Red: a workflow with a missing/unreviewed question, duplicate key, incompatible mapping/content, unresolved query/template or nonexecutable network cannot activate.
3. Build ordered bundles with five/six question identities, one LAI discussion/review network, no implicit result chaining, and pinned assessment/history/DDI/template/prompt references.
4. Run admission measurements and independent fixtures for every package; add explicit gate coverage inventory and reviewed reference-table provenance. Activate only through the registry command, recording a new versioned event.

**Verify/exit:** both reviewed complete bundles, reproducible hashes and admission report. If content review is pending, S40–S58 may proceed synthetically; S59/release remains blocked, with specific missing package IDs.

## 7. Snapshots, MCP, provider, and reasoning

### S40 — Freeze analysis snapshots and project one question's inputs

**Depends:** S12–S14, S20, S25; synthetic bundle permitted. **Requirements:** FR-16, FR-32, FR-35, NFR-04. **Seam:** T1 through start/read commands; T8 consumes these snapshots later. **Tests:** `BT/worker/test_snapshots.py`.

**Read/files:** plan.md §§4.2, 8.1; `B/reasoning/snapshots.py`, GenerationBatch/QuestionRun/projection migrations and generation read/start contract. Queue execution arrives in S44.

1. Red: start from a saved private author-owned revision freezes allowed facts/version references; notes/names/ID/phone are absent from model-facing projection. Wrong-author reads of projections/artifacts and invalid/stale starts are denied without partial creation.
2. Red: each question receives exactly its represented variables with typed missing/conflict state and source references; a mapping trying to read notes/unrelated history is rejected regardless of prompt wording.
3. Red: note-only edits leave fingerprints unchanged; relevant analytical/applicability edits invalidate affected question results and acceptance. Immutable old snapshots remain author-readable history. S48d implements affected-only regeneration without adjustment transfer.
4. Evaluate gates against saved facts: true/false/required-unknown produce ready/not-applicable/clarification. Persist explicit reasons and forbid undeclared cross-question result inputs.

**Verify/exit:** public run/projection behavior with mixed-content sentinel data. Do not expose a general patient snapshot endpoint to the model or make a temporary direct-record provider path.

### S41 — Implement the real private MCP transport and context grants

**Depends:** S40, S44, S03; execute queue/grant mechanics before this session. **Requirements:** FR-32–35, NFR-02. **Seam:** T6. **Tests:** `BT/mcp/test_scoped_transport.py`.

**Read/files:** plan.md §8.2 and source MCP design; `B/mcp_server/`, worker-side MCP host adapter, grant storage/read-only role configuration.

1. Red: a real SDK client starts the stdio subprocess, discovers exactly the allowed tool, calls with `{}`, and receives the bound stored projection. No direct in-process shortcut satisfies this test.
2. Red: extra arguments/unknown tools, missing/expired/forged grants, mismatched snapshot and inactive actor fail safely. Supply context only from protected worker state, never model arguments.
3. Red: simultaneous patient A/B processes and successive question contexts cannot read one another's sentinel values or notes. Stop/revoke a context before reuse; stderr diagnostics cannot corrupt stdout protocol.
4. Red: oversized projection and database/transport failure return bounded errors; process cleanup revokes access. Final lease/deployment fencing is exercised again in S47.

**Verify/exit:** actual protocol lifecycle on pinned SDK/version, tool schema and scoped-output assertions. No public MCP listener, patient search, write, inference, shell or file tool exists.

### S42 — Store and test provider settings securely

**Depends:** S03–S05, S02. **Requirements:** FR-03, FR-43, NFR-02. **Seams:** T1/T7/T9. **Tests:** `BT/http/test_api_settings.py`, `e2e/provider-settings.spec.ts`.

**Files/read:** plan.md §§8.3, 11; `B/reasoning/provider_config.py`, config migration, admin settings form.

1. Red: admin saves key/base URL/model revision; GET shows masked status only, physician is denied, replacing/clearing key is explicit, stale writes fail. Encrypt stored keys with deployment-held key.
2. Red: forbidden schemes/hosts/redirect targets/resolved addresses fail; explicitly allowed local model endpoint succeeds under operator configuration. Test the actual HTTP client's behavior.
3. Implement the minimum synthetic capability exchange in the provider adapter that S43 will extend: credential/model/tool/JSON support and safe diagnostic. Saving settings is not a claim that capabilities passed; do not create a second permanent provider client path.
4. Red: new config version leaves prior referenced version available; removal has explicit affected-run handling. Browser reflects configured/untested/verified/failed states without exposing secrets.

**Verify/exit:** request/response/log/audit redaction, allowlist behavior, UI replace/clear flow. No live paid model request is part of ordinary CI.

### S43 — Implement bounded provider CPT estimation and tool bridging

**Depends:** S23, S25, S41–S42. **Requirements:** FR-32–33, FR-36, FR-43. **Seam:** T7 with real T6 bridge. **Tests:** `BT/provider/test_estimation.py`.

**Files/read:** plan.md §§8.2–8.3; `B/reasoning/provider.py`, deterministic external HTTP test endpoint.

1. Red: endpoint receives only question prompt, fixed network/CPT contract and persisted projection; returns a strict candidate CPT response. Inspect captured external request, not an internal mock call.
2. Red: permitted tool call crosses real MCP and returns required correlation metadata; disallowed name/args and spoofed context are rejected. Initial patient read also uses MCP.
3. Red: schema-capable and JSON-only endpoints follow their verified capability modes but share the same strict validator. Reject extra prose, ambiguous responses and malformed/truncated/oversized output.
4. Red: timeout/auth/model/capability/rate-limit errors map to explicit retryability; ten-tool-call and context/output budgets are enforced. One adapter attempt never implements hidden nested retries.

**Verify/exit:** deterministic endpoint matrix and secret/field-exclusion checks. Templates never pass through the provider for drafting. Record runtime capability constraints for model admission.

### S44 — Implement durable leased jobs and global admission

**Depends:** S02, S40. **Requirements:** FR-35–36, FR-43, NFR-04, NFR-06. **Seam:** T8. **Tests:** `BT/worker/test_queue.py`.

**Files/read:** plan.md §§8.4–8.5; `B/reasoning/queue.py`, `worker.py`, jobs/attempt/grant migrations. Execute this session before the scoped MCP work in S41.

1. Red: run creation and its first eligible job are atomic; repeated same-fingerprint triggers reuse the run; simultaneous triggers cannot create two active generations for an encounter.
2. Red: two worker instances cannot exceed two global provider slots or execute two questions of one run. Implement short claim transactions, lease/heartbeat and persistent admission.
3. Red: eligible work rotates fairly across physicians and then FIFO within physician; saturation returns a visible busy state without deleting saved drafts/jobs.
4. Red: expired lease is reclaimed; old token cannot commit; restarting a worker retains recorded attempts and terminal artifacts. Implement one `run_once()` entry point used by tests and the polling process. At this stage exercise preparation/claim/recovery with real snapshot/gate logic and a controlled external provider adapter; complete the protocol and estimation stages in S43/S45, without mocking the queue's own collaborators.

**Verify/exit:** concurrent real-PostgreSQL worker checks via public run status; no SQL-row assertions or external request under an open claim transaction. Handoff includes configuration defaults and deterministic clock adapter behavior.

Reserve separate job classes and capacity for local calculation. S48b proves provider saturation cannot block local progress and that local jobs require no MCP/provider context.

### S45 — Run one full synthetic clinical question end to end

**Depends:** S23, S40–S44. **Requirements:** FR-32–35. **Seams:** T1/T8, exercising T5–T7. **Tests:** `BT/worker/test_single_question.py`.

**Files/read:** plan.md §§7.4, 8–9; `B/reasoning/coordinator.py`, artifact/result/section migrations, template schema from S25 and a small local renderer.

1. Red: public generation start → worker → real MCP → controlled provider → all-CPT validation → effective XML → empty-evidence inference → template section is observable through `GET /generation-batches/{id}` and question review.
2. Persist request/projection/prompt/model/attempt provenance, raw/validated percentages, effective XML/hash and query/result; atomically create immutable OriginalBaseline only after successful execution/rendering. Failed originals expose no adjustable baseline.
3. Red: provider structure mutation or missing root table leaves the step unsuccessful and base network bytes unchanged; no registered default enters inference.
4. Red: template output comes only from the reviewed mapping and stored result; returned LLM prose is never used as the recommendation. Expose five required transparency fields from persisted data.

**Verify/exit:** exact mathematical expected values from plan.md's synthetic example with all real owned modules. No clinical release package is required for this engineering proof, but no fake “succeeded” endpoint is acceptable.

### S46 — Execute ordered workflows and assemble a complete proposal

**Depends:** S45, S19, S25; S39 needed only for real content. **Requirements:** FR-15, FR-30–35. **Seams:** T1/T8. **Tests:** `BT/worker/test_workflows.py`.

**Files/read:** plan.md §§7.1, 8.4, 9; coordinator, proposal assembly and DDI snapshot integration.

1. Red: synthetic five/six-question workflows emit external provider requests in pinned order; next request is absent until prior inference and section commit. No intra-run parallelism.
2. Red: false gate records not-applicable without a provider call; required-unknown stops progression; unrelated applicability facts and previous posteriors are absent from later requests.
3. Red: final proposal succeeds only after every applicable question and a valid pinned DDI report; include skipped reasons and coverage warnings. Partial sections remain readable but incomplete.
4. Red: changed medications/data cannot use an old report or proposal; activating new content does not mutate an already pinned run. One LAI step renders only the retained discussion/review outputs under its reviewed mapping.

**Verify/exit:** sequential external-request evidence, persisted proposal/section identities, valid limited-coverage DDI versus failed/unavailable dataset distinction. No LLM proposal-writing request exists.

### S47 — Bound failures and recover at the exact failed stage

**Depends:** S46. **Requirements:** FR-36, FR-43, NFR-04. **Seams:** T1/T8. **Tests:** `BT/worker/test_recovery.py`.

**Files/read:** plan.md §8.5; retry coordinator, attempt ledger, stage resume and fencing.

1. Red: timeout → invalid CPT → rate-limit exhausts three total attempts, retains prior sections and draft, and leaves later questions pending. Implement one shared budget/backoff/Retry-After cap, no nested adapter retries.
2. Red: author retry of unchanged failed stage starts a new bounded batch; inference retry reuses CPTs and rendering retry reuses result. Successful earlier questions produce no new provider requests.
3. Red: worker crash after attempt start or accepted-artifact persistence cannot reset counts or duplicate committed sections; old worker/grant/deployment generation cannot commit after reclaim.
4. Red: analytical edit, discard, archive or deactivation during an outbound request prevents late acceptance/signing; configuration replacement starts a new pinned run. Clarification never triggers guessed values or blind retries.

**Verify/exit:** controlled external failure matrix and process restart, with public run history proving retention and resume. Split into S47.a retry policy/S47.b process races if needed; both are release-critical.

### S48 — Build automatic proposal review and transparency UI

**Depends:** S14, S20, S46–S47. **Requirements:** FR-15–16, FR-35–36, NFR-03. **Seams:** T1/T9. **Tests:** `e2e/proposal-review.spec.ts`.

**Files/read:** plan.md §9; `W/features/reasoning/`, review-entry trigger and run HTTP presentation.

1. Red browser journey: entry flushes autosave and automatically creates/reuses a run; no extra Generate click, no run per keystroke, no trigger while prerequisite acknowledgment/save is missing.
2. Display ordered pending/running/skipped/clarification/failed/completed states, partial retained sections and precise failed-question retry. Poll only active runs with hidden-tab backoff.
3. Show all five transparency fields beside each recommendation, plus sources/missingness/versions. CPT tables and posterior values are clearly different; lazy expansion fetches persisted data without recomputing from current chart.
4. Render final proposal with DDI coverage and separate secondary-plan entry. Failed/stale/partial runs visibly block signing and preserve draft editing/retry.

**Verify/exit:** two-question failure→retry browser case, refresh/resume, exact saved-input display, no note leakage. The subsequent sign session uses this status but enforces eligibility independently server-side.

### S48a — Persist complete CPT adjustments and deterministic redistribution

**Depends:** S23, S45, S07. **Requirements:** FR-50–52, FR-56, FR-42, NFR-04–05. **Seams:** T1/T5. **Tests:** `BT/probability_review/test_adjustments.py`, `BT/http/test_probability_review.py`.

**Read/files:** system-design.md §§5, 7.4, 8.1–8.2; plan.md §9.1; `B/probability_review/`, CPTRevision/QuestionReviewState migrations and question review/adjustment routes.

1. Red: author reads every root/conditional row with original/current values, full parent assignments and read-only outputs. Wrong-author/admin draft reads and adjustments are denied; failed originals expose no adjustable baseline.
2. Red: `[20,30,50]` edited to first value 40 yields `[40,22.5,37.5]`; `[100,0,0]` yields `[40,30,30]`. Keep selected value, redistribute from immediately preceding committed values, and leave other rows unchanged.
3. Red: single-state 100%, 0/100 endpoints, zero-other-total and deterministic largest-remainder ties preserve exact integer-unit totals. Four-state adjustment of `[100,0,0,0]`'s first value to 0 yields `[0,33.333334,33.333333,33.333333]` in declared order. Invalid target/precision/revision fails rather than repairing silently.
4. Red: completed commands create immutable full revisions with direct/redistributed before-after values, parent/sequence, actor/time and atomic audit. Repeated idempotent commands do not duplicate revisions; multi-tab stale writes conflict. Original baseline/shared XML remain unchanged; acknowledged values survive navigation/restart.

**Verify/exit:** independent numerical fixtures and public permission/persistence checks; no mirrored private-helper tests. Local jobs and calculation outcomes arrive in S48b.

### S48b — Recalculate locally with revision integrity, retry, and reset

**Depends:** S48a, S44, S47. **Requirements:** FR-53, FR-55–56, FR-58, FR-43, NFR-04, NFR-06. **Seams:** T1/T8. **Tests:** `BT/worker/test_local_calculation.py`.

**Read/files:** system-design.md §§8.3–8.4, 9; local job handlers, CalculationAttempt/Result storage, reset/retry routes.

1. Red: completed adjustment atomically clears acceptance, marks recalculating, queues the exact saved revision/hash and runs only its fixed network/template/configuration with empty evidence. Provider/MCP access disabled must not affect this path; unrelated questions/DDI/generation remain unchanged.
2. Red: newer adjustment or reset fences an older response from replacing current result/state; superseded results remain historical. Return exact current/displayed-result revision IDs and freshness; never label an earlier result as solving current CPTs.
3. Red: numerical failure retains current CPTs and separately labeled earlier success. Local retry solves that revision without re-estimation. Reset creates an audited baseline-equal revision with explicit verified original-result reuse, clears acceptance and retains history; stale inputs still require regeneration.
4. Red: restart resumes eligible durable jobs with leases/idempotency; deactivation/discard prevents late publication. Provider saturation permits independent local progress across patients and does not consume provider calls. Pending superseded jobs may coalesce without deleting revisions.

**Verify/exit:** out-of-order/failure/reset/restart and isolated-capacity evidence through public status/results; pending/failed/stale current results cannot be accepted.

### S48c — Build complete CPT review, comparison, and acceptance UI

**Depends:** S48, S48a–S48b. **Requirements:** FR-50–57, FR-58, NFR-03, NFR-06. **Seams:** T1/T9. **Tests:** `BT/http/test_probability_acceptance.py`, `e2e/probability-review.spec.ts`.

**Read/files:** system-design.md §§8.1, 8.5; ui-context.md; question acceptance routes and `W/features/reasoning/` CPT panels.

1. Red browser journey: every completed question exposes all CPTs, including roots/large grouped tables, original/current exact percentages, row totals/differences and direct/redistributed highlights. Outputs are read-only; single-state constraint is explained. Keyboard adjustments and readable labels work in both themes.
2. Show original and latest successfully adjusted outputs/recommendations side by side, even when wording is unchanged. Display unchanged/recalculating/successfully recalculated/failed plus separate out-of-date state. Previous results visibly identify earlier revisions.
3. Completed slider edits autosave and remain responsive during calculation; refresh/resume restores values/state. Reset/local retry affect one panel and retain history. Ignore obsolete browser responses using saved identifiers.
4. Red through HTTP and UI: only the author can accept each exact current successful result/revision/input hash, including unchanged originals. Reject wrong author, pending/failed/stale or mismatched references. Editing/reset/regeneration invalidates affected acceptance; sign UI consumes explicit acceptance references.

**Verify/exit:** full reachability, keyboard, comparison, failed/retry/reset/resume and forged-acceptance journeys. Signing enforcement belongs in S49 as well.

### S48d — Regenerate only questions affected by patient-data changes

**Depends:** S48a–S48c, S40, S46. **Requirements:** FR-59, FR-57–58, FR-16, NFR-04–05. **Seams:** T1/T8/T9. **Tests:** `BT/worker/test_question_freshness.py`, `e2e/question-freshness.spec.ts`.

**Read/files:** system-design.md §7.5; per-question fingerprints, regeneration coordinator and stale review UI.

1. Red: represented patient-variable or applicability changes mark only affected runs/adjustments out of date and invalidate acceptance. Another physician's permitted demographic edit has the same effect without disclosing draft content.
2. Regenerate affected applicable questions sequentially under the encounter's pinned bundle, retaining old baselines/adjustments/history and unaffected valid references. New QuestionRuns establish new originals; no old physician adjustments transfer silently.
3. Red: applicability transitions refresh the proposal/question set; DDI-only changes refresh original proposal/final review without unrelated LLM requests. Model activation does not rebase existing encounters.
4. Red: note-only and plan-text edits do not regenerate inference; note edits preserve probability acceptance. Reset/retry of a stale run cannot restore current-input eligibility. Require review of regenerated originals before accepting/signing.

**Verify/exit:** captured affected-only provider requests, retained prior artifacts, no carry-forward, and notes noninterference; refresh/resume preserves truthful freshness labels.

## 8. Final plans, shared records, and reporting

### S49 — Implement atomic plan signing and immutable snapshots

**Depends:** S46–S47, S48a–S48d, S14. **Requirements:** FR-15, FR-22, FR-42, FR-57, NFR-04. **Seam:** T1. **Tests:** `BT/http/test_signing.py`.

**Read/files:** plan.md §§4, 9; `B/cases/signing.py`, secondary-plan/signed-snapshot migrations and routes.

1. Red: author edits a separate secondary plan and signs only with a complete current original proposal, acknowledged saves and exact per-question acceptance/result references, including unchanged originals.
2. Red: absent/failed/partial/stale originals, pending/failed/stale/mismatched current calculations, forged manual plans, wrong author/admin or outdated encounter/plan/review revisions are rejected. Recheck freshness inside the locked transaction.
3. Red: atomic sign freezes full record, original proposal/CPTs/results, final accepted CPTs/results, adjusted recommendations, separate plan edits, inputs/versions/configuration and acceptor/signer/timestamps; releases the slot and audits. Idempotent repeats preserve one signature; failures cannot leave partial sign state.
4. Red: signed data/probabilities remain immutable while current demographics can change. Any active physician can append their own attributed dated addendum to any signed encounter; original signer/content remain intact.

**Verify/exit:** direct HTTP success and denial matrix using real completed synthetic pipeline results; no test shortcut creating a signable success flag. S51 adds concurrent race coverage.

### S50 — Build final-plan editing, comparison, sign, and addenda UI

**Depends:** S48–S49. **Requirements:** FR-15–16, FR-22, NFR-03. **Seam:** T9. **Tests:** `e2e/signing.spec.ts`.

**Files/read:** plan.md §9; `W/features/plans/`, chart signed-view/addendum UI.

1. Red browser journey: successful proposal appears unchanged beside editable secondary plan; changes persist through reload and visible comparison shows physician edits.
2. Explicit Sign flushes saves, verifies reviewed per-question probabilities/results and final plan, and submits exact acceptance/encounter/plan/review references. Pending/failed/stale calculations block signing without losing plan text.
3. Signed view is read-only, including original/final probabilities and exact attribution/history; print permission follows the declared provisional policy. Another physician can read signed content but not alter it.
4. A different active physician appends an attributed correction; chronology shows original signer and new addendum author/date separately. Double-click/retry does not duplicate signatures/addenda.

**Verify/exit:** sign with adjusted and unchanged questions, refresh, separate physician signed read/addendum, immutable CPTs, and failed-generation/unsolved-result denial through UI/server.

### S51 — Close archive, deactivation, and multi-user race cases

**Depends:** S04, S14, S47, S49–S50. **Requirements:** FR-04, FR-22–23, NFR-04. **Seams:** T1/T8/T9. **Tests:** `BT/http/test_record_races.py`, `e2e/shared-records.spec.ts`.

**Files/read:** plan.md §§2.1–2.3, 8.5–9; patient archive, account/deactivation-to-Cases integration and final eligibility checks.

1. Verify admin-only archive/unarchive and no permanent deletion. Under the explicitly provisional archive policy, test blocked create/edit/sign and retained private read-only drafts, resumable by author after unarchive; do not mark this policy owner-confirmed.
2. Red: deactivate with retained drafts revokes access and queued work; confirmed discard applies only to the reviewed draft-set revision. Changing that set invalidates confirmation; reactivation does not resurrect discarded drafts.
3. Red: simultaneous draft creates, sign/save, sign/slider/reset, sign/demographic edit, and sign/deactivation have one valid serial outcome. Test archive races under the declared policy. No second draft or stale signature is possible.
4. Red: relevant demographic edits mark affected results/adjustments stale without granting draft access; notes do not rerun inference or invalidate probability acceptance. Retained deactivated-author drafts occupy the slot until reactivation or confirmed discard; signed/discarded drafts release it.

**Verify/exit:** race suite plus admin archive/deactivation confirmation browser flow. All pending integration items from S04/S07/S14 are closed explicitly.

### S52 — Complete append-only audit and administration views

**Depends:** S24, S42, S49–S51. **Requirements:** FR-03, FR-42. **Seams:** T1/T9. **Tests:** `BT/http/test_audit.py`, `e2e/audit.spec.ts`.

**Files/read:** plan.md §10.1; `B/operations/audit.py`, admin filter/view interface, normal-role database grants.

1. Inventory all required actions and add missing events at the actual transaction/command path. Red: successful mutation plus event are atomic; failed command produces accurate failure metadata without false success.
2. Red through audit HTTP: stable attribution persists after rename/deactivation. Include original runs, completed slider edits/redistribution, resets, calculation outcomes, exact acceptance and sign events with actor/time/patient/encounter/question/run/revision and required before-after CPT values; exclude unrelated clinical bodies/keys.
3. Red: physician cannot inspect audit; admin filters by actor/action/time/target with stable pagination; no update/delete interface exists. Verify append-only database grants as a migration/operations check, not a hidden-state behavioral assertion.
4. Build readable admin table/detail view with correlation references, timestamp timezone and bounded metadata. Record backup/restore event hooks for S54–S56.

**Verify/exit:** required-event coverage list and safe audit browser path. Report accurately that database-owner access/restore can replace history; do not claim tamper-proof storage.

### S53 — Export lists and printable longitudinal patient reports

**Depends:** S20, S49–S52. **Requirements:** FR-03, FR-40. **Seams:** T1/T9. **Tests:** `BT/http/test_exports.py`, `e2e/reporting.spec.ts`.

**Files/read:** plan.md §10.1; `B/cases/reporting.py`, administrative CSV endpoints, print CSS.

1. Red: admin downloads patient/physician CSV with stable headers/UTF-8/quoting and no credentials; physicians are denied list exports. Patient ID `0012345678` remains exact text bytes.
2. Red: formula-like user text is neutralized without formula wrappers; quotes/newlines render correctly. Explain spreadsheet text import rather than promising CSV column typing.
3. Red: administrator, and physicians if the proposed permission is adopted, receive escaped signed patient HTML with chronology/clinical content/plans/signatures/addenda. Every question includes complete original and accepted CPTs/results/recommendations, versions, adjustment indicators and physician/timestamps. Adjusted-then-reset records retain history indication; reports exclude private drafts.
4. Browser print view has readable page breaks and research labeling in both browsers; authenticated responses are private/no-store. Avoid PDF libraries and dynamic LLM report prose.

**Verify/exit:** malicious text remains inert, export permissions/audit pass, print preview inspected with a multi-encounter fixture.

## 9. Recovery and Linux operation

### S54 — Produce consistent full backups

**Depends:** S39 or representative complete synthetic content; S49, S52–S53. **Requirements:** FR-41–42, NFR-04–05. **Seams:** T10/T1/T9. **Tests:** `BT/recovery/test_backup.py`.

**Read/files:** plan.md §10.2; `B/operations/backup.py`, recovery-job migration, archive manifest, admin backup UI.

1. Red: backup includes database/network XML, original baselines/raw estimates, all CPT revisions/results/failures/acceptances, signed snapshots/audit, prompts/templates/manifests, numerical policies and pinned runtime/configuration/dependency locks. Include adjusted signed, failed-revision and retained private-draft fixtures.
2. Red: concurrent mutation cannot make exported XML inconsistent with the database snapshot. Derive exports from the same consistent snapshot, using isolated staging where needed.
3. Red: session credentials and deployment encryption key are excluded as specified; encrypted provider values have an explicit key-reentry/recovery note. Administrator authorization, bounded asynchronous progress/download and safe audit are required.
4. Red: interrupted dump/export produces a failed job and no downloadable “complete” archive; retry does not overwrite a good prior backup. Clean only documented temporary recovery artifacts.

**Verify/exit:** inspect through backup interface/manifest and restore into disposable staging for coherence checks. A downloadable zip alone is insufficient evidence of recoverability.

### S55 — Validate and stage restores without touching live state

**Depends:** S54. **Requirements:** FR-41, NFR-02/04. **Seams:** T10/T1/T9. **Tests:** `BT/recovery/test_restore_validation.py`.

**Files/read:** plan.md §10.3; `B/operations/restore.py` staging phase, restricted restore role, validation/progress UI.

1. Red: valid app-generated archive stages into isolated database, reports backup date/schema/content/checksums/impact, and returns an immutable confirmation digest.
2. Red: path traversal, absolute path/symlink, oversized expansion, corrupt hash, unsupported schema, missing artifact and malformed database content fail before live mutation.
3. Restore untrusted data with restricted permissions and resource limits; uploaded content must not execute as superuser or access host files/programs. Validate singleton identity, references and model/effective-artifact consistency through supported inspection commands.
4. Red: failed staging leaves current patients/login/jobs unchanged; replacing upload or expiry invalidates prior confirmation. UI exposes validation errors and key-reentry consequences, never an enabled premature Commit.

**Verify/exit:** malicious/corrupt archive suite against disposable storage and real live-side read checks. No restore-over-live action exists until S56 implements the whole switch/rollback protocol.

### S56 — Commit restore with maintenance, fencing, and rollback

**Depends:** S55, S47, S52. **Requirements:** FR-41–42, NFR-04. **Seams:** T10/T1/T8/T9. **Tests:** `BT/recovery/test_restore_commit.py`.

**Files/read:** plan.md §10.3; restore coordinator, `deploy/` maintenance/switch scripts, external operator recovery log.

1. Red: only admin confirmation of the exact validated staged digest starts replacement; stale confirmation fails. Quiesce writes/workers, fence deployment generation and take pre-restore backup before switching.
2. Implement phased database-selection switch and coordinated process restart behind maintenance. Red: no mixed old/new read/write traffic is possible; old provider work cannot commit.
3. Red: restore revokes sessions/grants, fences old workers and clears caches. Reconcile pending jobs against exact revision/lifecycle before eligible resumption; preserve failed unsolved CPTs and earlier successful results for local retry/reset. Audit recovery externally and in restored database, then verify login/read/replay before reopening.
4. Inject failures before switch, during restart and at health check. Red: rollback restores the prior database/configuration and readable records; maintenance remains until verified healthy.

**Verify/exit:** actual disposable destructive restore/rollback drill, UI confirmation/progress and documented operator recovery procedure. Never test against the owner's live database or claim multi-database/process switching is one SQL transaction.

### S57 — Package one-host Linux deployment and upgrades

**Depends:** S48–S56; synthetic content allowed for installation mechanics. **Requirements:** NFR-01–02, NFR-05. **Seams:** T1/T8/T9/T10 operational entry points.

**Read/files:** plan.md §§3, 11; `deploy/` Dockerfiles/edge config, `compose.yaml`, environment example, setup/upgrade/runbook documentation.

1. Build pinned production images and Compose topology: edge, HTTP app, worker, PostgreSQL; worker spawns private MCP. Persist database volume; no public DB/MCP ports; browser uses relative origin routes.
2. Verify fresh Linux startup, explicit migrations and one-time admin seed. Missing provider or bundle yields unavailable generation while charts/admin remain usable. HTTPS is enforced outside localhost.
3. Document environment/key handling, backup-before-upgrade, compatible application/schema rollback, migration failure recovery and stopping processes. No default development override or test provider is enabled in production.
4. Restart HTTP/worker/database independently and verify acknowledged draft preservation, queue recovery and health semantics. Container logs contain no record text or secrets.

**Verify/exit:** clean disposable-host install and upgrade from preceding schema state, documented commands and image/runtime versions. Do not deploy to an external/live host as an implicit final step.

### S58 — Measure capacity and expose useful operational status

**Depends:** S57, S47, S48b. **Requirements:** FR-43, NFR-01/04/06. **Seams:** T1/T8/T10.

**Files/read:** plan.md §11; structured metrics/logging, `make test-load` runner, operator runbook.

1. Exercise public interfaces with 10,000 synthetic patients and representative concurrent saves/searches/polls; record host, dataset, duration and p95 latency. Separate provider latency from ordinary requests.
2. Measure every admitted model's complete CPT request/output sizes, exact-inference CPU/memory/time and failure behavior. Configure justified admission/resource limits and preserve rejection diagnostics.
3. Expose safe metrics for save failure, queue age, heartbeat, provider retries/auth failure, inference limits, disk and backup success; demonstrate initial alert conditions without clinical payloads.
4. Verify global provider concurrency/fairness and independent local calculation progress under saturation, responsive sliders/saves, revision integrity and bounded caches across patients. Local calculations must produce no provider/MCP requests. Tune measured bottlenecks only.

**Verify/exit:** measured report against provisional targets, explicit misses and remediation tasks. No unmeasured SLA/availability claim; no fabricated benchmark numbers.

## 10. Integrated release verification

### S59 — Verify complete registration and follow-up with CPT review

**Depends:** S39, S48a–S48d, S50, S53, S57. **Requirements:** FR-10–16, FR-20–22, FR-30–37, FR-50–59, NFR-03–06. **Seams:** T1/T5–T9. **Tests:** `e2e/clinical-workflows.spec.ts` plus existing scoped suites.

1. Run registration and follow-up through assessment, catalog medications/DDI, automatic sequential original generation, all-CPT inspection, optional adjustment/comparison, explicit acceptance and signing. Use S08 validated released assessments without owner approval and reviewed released packages for other content, with a controlled external provider; missing content is a named blocker.
2. Cover unchanged, adjusted, adjusted-then-reset and unchanged recommendation cases. Verify complete original/final signed snapshots and printable tables with versions, inputs and attribution.
3. Change a relevant patient variable before sign: affected-only regeneration retains history/new original baseline, with no silent adjustment transfer. Note-only edits leave algorithms/acceptance unchanged. Follow-up creates a new encounter without modifying prior accepted CPTs.
4. Verify refresh/restart resumes saved adjustments/state, pending/failed/stale values cannot sign, and other physicians see only demographics/signed records with any-physician attributed addenda. The patient's one draft remains private.

**Verify/exit:** dated evidence for both full workflows and all question gates; no completed release claim based solely on synthetic content or planned checks.

### S60 — Run cross-user, failure, and security acceptance

**Depends:** S59, S51, S56. **Requirements:** FR-04, FR-16, FR-22, FR-36, FR-41–43, NFR-02/04. **Seams:** T1/T6–T10.

**Tests:** existing race/recovery suites plus `e2e/failure-recovery.spec.ts`; add only missing observable scenarios.

1. Two physicians edit drafts for different patients while one provider run fails; saves/sliders remain responsive and acknowledged revisions survive restarts. Wrong-author clinical draft/artifact reads and writes are denied; shared demographics/signed records remain available.
2. Re-run mixed three-attempt exhaustion, stage resume, lease theft/crash, archive/deactivation and restore generation fencing with externally controlled timing. Completed question artifacts remain unique and preserved.
3. Inspect all actual model/MCP payloads for notes, unrelated sentinel fields and secret leakage; forge tool args/grants, HTTP role/CSRF/revisions, XML entities, provider destinations and archive paths. Assert observable denial/no mutation.
4. Verify failed/stale/manual-sign and pending/mismatched-current-result denials, stale response/reset/sign races, exact acceptance, and output escaping/CSV formula behavior. Record reproducible defects and rerun affected checks after fixes.

**Exit:** cross-cutting gate passes with real owned dependencies. Do not replace a failing concurrency test with sleeps or remove the adversarial case to get green.

### S61 — Verify desktop usability, both themes, and complete administration

**Depends:** S60. **Requirements:** FR-01–04, FR-23, FR-37, FR-40–42, FR-50–59, NFR-03, NFR-06. **Seam:** T9.

**Tests/files:** existing Playwright journeys, accessibility evidence and any targeted fixes in owning UI module.

1. Walk physician registration/follow-up and all admin capabilities in current Chrome/Firefox: accounts, password/theme, provider test, network XML/graph/validate/version/activate/rollback, archive, audit, exports, backup/restore staging.
2. Inspect light/dark contrast, keyboard sliders/focus, exact percentage labels, full root/parent-row reachability, original-adjusted comparison, calculation/freshness states, zoom and complete CPT print tables. State uses text cues; urgent banners persist.
3. Exercise empty/loading/error/read-only/unsaved/conflict/partial/failed states, not just happy-path screenshots. Verify explicit discard/deactivate/restore confirmations and every-login research warning.
4. Fix actual usability defects without introducing a second UI runtime or decorative redesign. Add a browser test only where it guards meaningful behavior; avoid brittle full-page snapshots.

**Exit:** dated browser/version checklist, contrast/keyboard/print observations, no fake implemented feature or disabled unexplained action. All NFR-03 conditions covered.

### S62 — Complete the release rehearsal and agent handoff

**Depends:** S58–S61. **Requirements:** all. **Seams:** planned public integration/operational seams only.

**Files/read:** plan.md §12, this document's coverage matrix, [progress index](progress/README.md) and relevant session reports, release/operations documentation.

1. From a clean checkout and empty disposable storage run locked install, migrations, seeding, required suites and production build. Record exact commit/worktree state, locks, images and content hashes.
2. Rehearse full backup → mutation → staged restore → confirmed replacement → login/read/artifact replay, plus failed-switch rollback. Confirm session revocation and unavailable-key reentry behavior; retain safe evidence.
3. Audit every FR/NFR against concrete tests/manual checks below. All sessions and content reviews must be complete; identify any explicitly unrun live-provider/host-specific check. Fix missing requirements before claiming the application ready.
4. Finalize quick start, operator configuration, content update/approval procedure, draft/run failure recovery, restore/key escrow instructions and known limits. Handoff distinguishes engineering acceptance from clinical validation and does not claim high availability.

**Exit:** reproducible self-hosted application build and full requirement evidence, with no unresolved release-critical gate.

## 11. Requirement-to-session acceptance index

Every requirement has an implementation owner and an acceptance location. A grouped session does not mean the grouped requirements can be marked complete together without their individual evidence.

| Requirement | Primary sessions | Final observable evidence |
|---|---|---|
| FR-01 | S03, S05 | Role login, Register contact text, research notice on each physician login; S61 |
| FR-02 | S03 | Singleton admin/admin seed, unchanged username, changed password survives restart |
| FR-03 | S04–S05, S24, S42, S51–S58 | Entire admin capability walkthrough; S61 |
| FR-04 | S03–S04, S51 | Admin-only credentials, revocation, confirmed draft disposition, preserved attribution |
| FR-10 | S06 | Exact demographics, disabled invalid Next, leading-zero ID and duplicate race |
| FR-11 | S08–S09 | Full criteria, live threshold, saved below-threshold warning, reason-free bypass |
| FR-12 | S08, S10 | Unanswered/partial/skip versus completed PANSS reference results |
| FR-13 | S08, S11 | Selected C-SSRS form/periods/completeness and distinct results |
| FR-14 | S12, S15–S20 | Structured history, drug-only persistence, reviewed deterministic DDI and unavailable coverage |
| FR-15 | S46, S48–S50, S48a–S48d | Original proposal, local CPT review/adjusted recommendations, separate plan/sign-off |
| FR-16 | S07, S13, S40–S41, S48a–S48d | Saved draft/CPT/calculation state, confirmed discard and end-to-end notes exclusion; S59–S60 |
| FR-20 | S14, S33–S38, S50, S59 | Complete new follow-up assessment/history/medications/effects/proposal/sign/chronology |
| FR-21 | S12, S33–S36 | Four tri-state effects with full standardized questionnaires and reviewed severity mappings |
| FR-22 | S07, S14, S48a–S48c, S49–S51 | Private single draft and author-only probability/sign operations, immutable snapshots, any-physician addenda |
| FR-23 | S06, S51 | Name/ID search, clinical-status/archive filters, archive/unarchive without deletion |
| FR-30 | S25–S39, S46, S59 | All five registration/six follow-up questions and one LAI discussion/review model |
| FR-31 | S21, S25–S39 | One predefined prompt/XMLBIF per question; separate structural validation |
| FR-32 | S22, S40, S43, S46 | Fixed structures/types/states/relevance, scoped inputs and sequential progression |
| FR-33 | S23, S41, S43, S45 | MCP-mediated estimation of every CPT, LLM cannot modify graph or execute it |
| FR-34 | S23, S25, S45–S46 | Effective CPT insertion, exact inference, template section before next question |
| FR-35 | S40–S41, S45, S48 | Automatic run, authoritative scoped MCP, saved/displayed five transparency fields |
| FR-36 | S43–S44, S47–S48 | Three total mixed attempts, retained data/results, explicit failed-stage resume |
| FR-37 | S21–S24, S39 | Graph/XML import/edit/export/validation/version/activation/rollback |
| FR-40 | S53 | Safe CSV; complete original/accepted CPT/results, adjustment indicators and attribution in signed HTML |
| FR-41 | S54–S56, S62 | Consistent complete artifact backup and actual restore/rollback drill |
| FR-42 | S02–S04, S48a–S48c, S52, S54–S56 | Append-only runs/adjustments/redistribution/reset/outcome/acceptance/sign events with before-after values |
| FR-43 | S42–S44, S47, S48b, S58 | Queued compatible provider and isolated local calculation without LLM/MCP |
| FR-50–51 | S48a, S48c, S61 | Every CPT/root/parent row, exact original/current labels/totals and read-only outputs |
| FR-52 | S48a | Deterministic proportional/equal redistribution, endpoints, single-state and rounding/ties |
| FR-53–54 | S48b–S48c | Affected-only pinned local execution; original-adjusted comparison, identical wording and truthful states |
| FR-55–56 | S48a–S48c, S54–S56 | Reset without history deletion, durable revision/actor/calculation state and encounter isolation |
| FR-57 | S48c–S48d, S49–S50 | Exact current acceptance and immutable original/final snapshot; unresolved/stale results rejected |
| FR-58 | S48b–S48c, S51, S60 | Older responses fenced; unsolved values/prior results retained with local retry/reset |
| FR-59 | S48d, S51, S59 | Affected-only new baseline, prior history retained, no adjustment transfer or notes-triggered regeneration |
| NFR-01 | S01, S57–S58, S62 | Clean Linux/VPS install and measured concurrent load |
| NFR-02 | S03, S41–S43, S55–S57, S60 | Auth/no timeout/HTTPS and actual transport/role/input protections |
| NFR-03 | S05, S48, S50, S53, S61 | English Chrome/Firefox workflows, themes, keyboard/contrast/validation/confirmations |
| NFR-04 | S07, S23, S44, S47, S49, S56, S62 | Acknowledged draft survival, exact stored-artifact replay, fenced recovery |
| NFR-05 | S01–S02, S08, S18, S21–S25, S39, S52, S62 | Locked/versioned content/models/templates, XSD, retained provenance/audit/recovery |
| NFR-06 | S44, S48b–S48c, S58, S60–S61 | Responsive local review under provider saturation; isolated revision-correct results and no stale acceptance |

## 12. Ready-to-paste session instruction

```text
Implement session Sxx from `project-documents/dev/tasks.md`.
Preserve unrelated changes. Use the session's planned public verification seams.
Work one observable red → green slice at a time; review/refactor after green.
Finish with what works, what was checked, remaining blockers and the next session.
```
