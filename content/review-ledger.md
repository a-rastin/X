# Content review ledger (S00, 2026-10-04)

Documentation only. Covers every question in plan.md §7.1 plus assessments, history/severity, DDI, and workflow bundles.

- **Reviewer:** content owner. Drafting and release-policy decisions below were confirmed on 2026-10-04; no concrete package is approved.
- **Status values:** `draft` (authored, not review-ready) / `awaiting_review` (dossier complete, owner decision pending). No item is approved. Assessment packages are the exception: tasks.md S08 releases them after validation with no owner review, so they are never marked `awaiting_review`.
- **Approval record shape** (per package `review.json`, plan.md §7.2): `{assumptions, reviewer: "owner", decision, date, source_hashes}`. A bare `reviewed=true` flag is insufficient (tasks.md S22).
- **Medical and BN sources under `project-documents/medical-documents/` are never edited** for review; packages are new derived versions with pinned hashes.
- Product rules reused here (empty evidence, all-CPT estimation incl. roots, full CPT review, exact-result acceptance) are confirmed per plan.md §§1.1, 9.1–9.2; stack/numerical/seam choices are proposals. No clinical thresholds are invented in this ledger.
- No `content/assessments|history|questions/<key>/{manifest.json,network.xml,prompt.txt,template.json,examples.json,review.json}|bundles|ddi` files exist yet (content/ held only this ledger at S00 close); all are missing and due in S08/S12/S18/S25–S39.

## 1. Assessments — wording, periods, scoring (S08; no owner review)

Source leads: `schizophrenia-criteria.md`, `PANSS.md`, `CSSRS.md` (verify actual paths in S08; inherited names only).

| Package | Deliverables | Status |
|---|---|---|
| Diagnosis criteria + threshold/bypass behavior | `content/assessments/diagnosis/` versioned definition + independent threshold examples; served via released-definition routes | draft → S08 validates, releases without owner review |
| PANSS (30 items, subscale arithmetic; all-1 → 7/7/16/30, all-7 → 49/49/112/210) | `content/assessments/panss/` definition + literal fixtures | draft → S08, no owner review |
| C-SSRS (form + time windows selected in S08; severity/intensity/behavior/lethality kept separate, no composite risk score) | `content/assessments/cssrs/` definition + branching/alert examples | draft → S08, no owner review |

No owner questions: S08 selects form/time-window choices, documents gaps and experimental defaults, and releases on schema/rule validation + independent examples (plan.md §§1.4, 5).

## 2. History fields and adverse-effect severity (S12; owner review required)

- **Deliverables:** `content/history/` versioned field inventory (typed fields, periods/windows, tri-state values, provenance, reconciliation rules) + versioned full standardized adverse-effect instrument definitions, item responses/completeness/scoring contracts, and reviewed severity mappings for the four follow-up effects (`present|absent|not_assessed`; severity required iff present; status change clears obsolete severity). FR-14-excluded medication regimen fields (dose, unit, route, frequency, active/stopped; no free-text drugs) stay excluded.
- **Approval record:** `content/history/review.json` in the shape above. Status: `draft`.
- **Owner questions (substantive only):**
  1. Approve the minimum typed history inventory S12 drafts from question mappings, or name additions — proposal: approve the S12 minimum as-is so S27 and S29–S38 can draft against a frozen inventory, with additions as versioned changes.
  2. **Full standardized questionnaires selected (2026-10-04):** use complete standardized instruments for adverse-effect assessment rather than short custom severity definitions or network-state-only inputs. Exact forms, versions, item wording, administration, completeness, scoring and network mappings require source-backed owner review; this is a drafting requirement, not package approval. BARS for akathisia and SAS for parkinsonism have instrument sections in the supplied criteria; the tardive-dyskinesia criteria reference AIMS but do not contain its complete form. Full completion is required only when the corresponding effect is `present`; `absent` and `not_assessed` do not require questionnaire completion. The acute-dystonia form is named **Acute Dystonia Dx Criteria** (confirmed 2026-10-04), with the supplied `acute-dystonia-criteria.md` as the drafting lead; its name does not establish a validated scored scale. Open: identify the complete AIMS source/form and review concrete item/completeness/severity/network definitions, including the acute-dystonia criteria form. Retain explicit not-assessed/missing states and no guessed score or severity bands. The reason for preferring full instruments was not separately supplied.
  3. **FR-14 regimen mismatch — drafting direction confirmed (2026-10-04):** S12 may draft structured exposure, duration, trial-adequacy, prior-response, and monitoring fields for review, while excluding dose, route, and frequency. S27 and S29–S38 consume only approved field definitions. This resolves the redesign direction, not approval of the concrete inventory.

## 3. DDI aliases and coverage (S15–S18; owner review required)

- **Corpus baseline:** 128 `.txt` files in 15 categories under `project-documents/medical-documents/DDI-text/` (verified by `find`/`wc -l` this session); Sitagliptin sha256 `e7c9bc45…4022f` (verified by `sha256sum`; full hashes in `project-documents/dev/progress-tracker.md` hash appendix). Sitagliptin 0/4/92/70 counts with Ofloxacin retained in both applicable categories are source-derived expectations per plan.md §6.2, to be validated during S15 ingestion — not verified this session.
- **Deliverables:** `content/ddi/aliases.json` (controlled concepts + reviewed aliases), corpus report (discovered/processed/pass/fail, counts, checksums, parser version, anomalies), `review.json` manifest (contraindicated/serious evidence, unresolved names, severity/management conflicts), immutable `DatasetRelease` with explicit coverage declarations.
- **Approval record:** release manifest `{reviewer: "owner", decision, date, source_hashes, parser/terminology versions, coverage declarations}`. Status: `draft`.
- **Owner questions (substantive only):**
  1. Review contraindicated/serious assertions, unresolved entity names, and conflicting severity/management rows surfaced by S16–S17 — proposal: approve per-row corrections drafted with source spans; ambiguous aliases stay unresolved, never fuzzy-matched.
  2. **Coverage policy confirmed (2026-10-04):** a reviewed, explicitly **limited-coverage** release is releasable without waiting for full-corpus review. It names excluded/uncovered material; pairs without supported coverage stay `coverage_unavailable`, never "safe". Concrete release evidence and coverage declarations still require owner review.

## 4. Registration questions (5: R2, R4–R7)

Common deliverables (tasks.md S27 and S29–S38): `content/questions/<key>/{manifest.json, network.xml, prompt.txt, template.json, examples.json, review.json}` covering plan.md §7.2 (CPT-context-only patient mappings, fixed empty-evidence execution, true/false/unknown gates, missing/conflict policy, complete CPTs incl. roots, reviewed reference-table provenance). Common status: `draft`, none approved. Prompts estimate only; templates use pinned reviewed mappings (no unreviewed largest-posterior treatment choice).

### R2 `pharmacotherapy` (S27)

- Source lead BN-04 (32 vars, 13 deterministic source-lookup DEFINITIONs, 19 distributions undeclared — template, not executable). **Scope confirmed (2026-10-04): preserve BN-04's established-treatment review scope for registration pharmacotherapy.** Initial medication selection is outside this question; no new product-choice output is added to satisfy the former interpretation.
- Remaining review: S27 documents the retained variables/states/edges and established-treatment review outputs, including metadata-only parents; concrete graph, field meanings and template mappings require review. Excluded dose/route input redesign follows §2 item 3. **Applicability confirmed (2026-10-04):** no established treatment does not make this question `not_applicable`; it remains applicable. S27 must define source-compatible mappings for that case. Missing/unknown required network inputs remain unresolved and pause execution under the existing contract; they are not fabricated to force a result.

### R4 `high_suicide_clozapine` (S29)

- Source lead BN-08 (14 vars, 0 DEFINITIONs — template). Align assessment period and treatment-history meanings with approved C-SSRS/history.
- Owner questions: (a) approve gate definitions (current urgent findings vs high-risk gate vs persistent risk despite prior treatment) with periods and structured provenance; (b) approve result-rendering examples. A scale level alone is not an unreviewed treatment rule; urgent handling is independent of LLM latency.

### R5 `lai_indication_choice` (S30)

- Source lead BN-10 (14 vars, 0 DEFINITIONs — template). **Scope confirmed (2026-10-04): preserve BN-10's existing LAI discussion/review pathway.** Specific LAI product selection and a new product-choice contract are outside this question. The existing `lai_indication_choice` key remains a stable identifier; displayed wording and outputs follow the retained discussion/review scope.
- Remaining review: retained variables/states/edges, preference/prior-exposure missingness, output queries, and discussion/review template mappings. One XMLBIF network, one prompt and one question step; no new product-choice output or runtime LLM recommendation.

### R6 `aggression_clozapine` (S31)

- Source lead BN-09 (14 vars, 0 DEFINITIONs — template).
- Owner questions: (a) approve substantial/persistent aggression + prior-treatment context definitions with source periods — proposal: clinician-reported fields, **do not substitute PANSS hostility for an unreviewed aggression threshold**; (b) approve deterministic-rule vs estimated-table semantics resolution and result-mapping examples. No missing finding maps to "No".

### R7 `established_case_clozapine` (S32)

- Source lead BN-07 (23 vars, 0 DEFINITIONs — template; shared with F5, which gets a **separate** package).
- Owner questions: (a) approve established-status applicability input, trial-adequacy definition, and observation windows — no invented thresholds or forbidden medication details; (b) confirm R7/F5 separation (first-time false-gate vs follow-up no-improvement gate).

## 5. Follow-up questions F1–F6

Each follow-up package uses the same Common deliverables + `review.json` approval shape as §4 (reviewer owner, decision, date, source hashes); status draft, none approved.

### F1 `tardive_dyskinesia` (S33) — lead BN-14 (16 vars, 0 DEFINITIONs)

Owner questions: review the complete AIMS form/version and effect presence/severity mapping (absent ≠ not assessed; no diagnosis inferred from an unreviewed AIMS total) and source-required context/missing-input policy.

### F2 `akathisia` (S34) — lead BN-13 (20 vars, 0 DEFINITIONs)

Owner questions: review the full BARS definition and presence/severity/context mapping — no invented summed BARS severity rule; motor restlessness kept distinct from unreviewed differential assumptions.

### F3 `parkinsonism` (S35) — lead BN-12 (20 vars, 0 DEFINITIONs)

Owner questions: review the full SAS definition and effect/severity/context mapping — no universal mild/moderate/severe SAS score bands; onset/alternative-cause facts via approved structured history, never page notes.

### F4 `acute_dystonia` (S36) — lead BN-11 (12 vars, 0 DEFINITIONs)

Form name confirmed: **Acute Dystonia Dx Criteria**; S12 drafts the form from supplied criteria for review, without inventing a total score. Owner questions: review its full item/completeness/severity/network mapping, presence/onset/context fields and urgent-concern handling — proposal: source-approved urgent messages display without waiting for inference.

### F5 `no_improvement_clozapine` (S37) — lead BN-07 (separate package from R7)

Owner questions: approve the no-improvement definition (baseline encounter/window, instrument/context, adequate-treatment info) — proposal: no percent-change cutoff guessed; missing follow-up score is not "no improvement"; required ambiguity pauses rather than silently skipping.

### F6 `continue_or_adjust` (S38) — leads BN-04/05/06 (overlapping drafts; one network to be drafted)

- **BN-06 retention confirmed (2026-10-04):** retain BN-06-derived content within the experimental/educational scope, with its source limitation documented. The supplied STATEMENT-06 §Quality Measurement Considerations excludes electronic decision-support and quality-measure use; experimental retention does not remove that source limitation or establish clinical validation. Observed status: the current BN-06.xml carries `STATEMENT 6 … Quality Measurement Considerations` as source locator plus the standard experimental-educational properties, but **no literal restriction text** (see S00 discrepancy note in progress-tracker.md) — resolution must therefore cite STATEMENT-06/plan/tasks, not current XML wording. S38 records this owner scope decision in the derived package and cites the actual guideline limitation.
- Remaining review: approve the single drafted graph, experimental/educational limitation wording and continuation-vs-adjustment template branches. Retention is a scope decision, not concrete package approval. No runtime merge of three models.

## 6. Workflow bundles (S39; owner review required)

- **Deliverables:** `content/bundles/registration.json` (5 questions: R2, R4–R7), `content/bundles/followup.json` (6 questions: F1–F6), one LAI discussion/review network, pinned assessment/history/DDI/template/prompt references, content release manifest with exact package hashes/decisions.
- **Approval record:** bundle release manifest `{reviewer: "owner", decision, date, per-package hashes}`; activation only through the registry command as a new versioned event. Status: `draft` (activation blocked until all 11 packages reviewed; S24 uses synthetic complete bundles meanwhile).
- **Owner question:** approve the 11 concrete packages + cross-package assumptions as a batch or per package — proposal: batched S39 review with per-package revise-and-rerun on rejection; no batch-approval on the owner's behalf.

## 7. Reference-table assumptions (all packages)

Per plan.md §7.3: reference tables in a released artifact must be explicitly reviewed; **missing values are never silently filled with uniform probabilities**; because all runtime CPTs are replaced by LLM estimates, each package documents its reference tables' purpose and provenance without presenting them as patient estimates. Owner review resolves draft restrictions and fixed-rule vs estimated-table assumptions in a new version — never by flipping an `inference_enabled` property. No owner question beyond the per-package provenance review already listed.
