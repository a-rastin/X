# Content review ledger (S00, 2026-10-04)

Documentation only. Covers every question in plan.md §7.1 plus assessments, history/severity, DDI, and workflow bundles.

- **Reviewer:** content owner (named, no approval claimed — nothing below is approved).
- **Status values:** `draft` (authored, not review-ready) / `awaiting_review` (dossier complete, owner decision pending). No item is approved. Assessment packages are the exception: tasks.md S08 releases them after validation with no owner review, so they are never marked `awaiting_review`.
- **Approval record shape** (per package `review.json`, plan.md §7.2): `{assumptions, reviewer: "owner", decision, date, source_hashes}`. A bare `reviewed=true` flag is insufficient (tasks.md S22).
- **Sources under `project-documents/` are never edited** for review; packages are new derived versions with pinned hashes.
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

- **Deliverables:** `content/history/` versioned field inventory (typed fields, periods/windows, tri-state values, provenance, reconciliation rules) + reviewed severity definitions for the four follow-up effects (`present|absent|not_assessed`; severity required iff present; status change clears obsolete severity). FR-14-excluded medication regimen fields (dose, unit, route, frequency, active/stopped; no free-text drugs) stay excluded.
- **Approval record:** `content/history/review.json` in the shape above. Status: `draft`.
- **Owner questions (substantive only):**
  1. Approve the minimum typed history inventory S12 drafts from question mappings, or name additions — proposal: approve the S12 minimum as-is so S26–S38 can draft against a frozen inventory, with additions as versioned changes.
  2. Approve one severity definition per effect — proposal: single present/absent/not_assessed tri-state plus a short reviewed severity scale per effect, with no mandatory full BARS/SAS/AIMS questionnaire (plan.md §5).
  3. **FR-14 regimen mismatch:** several BN drafts assume exposure/duration/adequate-trial/same-drug-experience/monitoring inputs that drug-only catalog entries cannot supply. Proposal: resolve by content redesign (structured history fields carrying exposure/duration/adequacy, still without dose/route/frequency fields) rather than hidden form fields; S12 proposes the fields, S26–S38 consume only approved ones.

## 3. DDI aliases and coverage (S15–S18; owner review required)

- **Corpus baseline:** 128 `.txt` files in 15 categories under `project-documents/medical-documents/DDI-text/` (verified by `find`/`wc -l` this session); Sitagliptin sha256 `e7c9bc45…4022f` (verified by `sha256sum`; full hashes in `project-documents/dev/progress-tracker.md` hash appendix). Sitagliptin 0/4/92/70 counts with Ofloxacin retained in both applicable categories are source-derived expectations per plan.md §6.2, to be validated during S15 ingestion — not verified this session.
- **Deliverables:** `content/ddi/aliases.json` (controlled concepts + reviewed aliases), corpus report (discovered/processed/pass/fail, counts, checksums, parser version, anomalies), `review.json` manifest (contraindicated/serious evidence, unresolved names, severity/management conflicts), immutable `DatasetRelease` with explicit coverage declarations.
- **Approval record:** release manifest `{reviewer: "owner", decision, date, source_hashes, parser/terminology versions, coverage declarations}`. Status: `draft`.
- **Owner questions (substantive only):**
  1. Review contraindicated/serious assertions, unresolved entity names, and conflicting severity/management rows surfaced by S16–S17 — proposal: approve per-row corrections drafted with source spans; ambiguous aliases stay unresolved, never fuzzy-matched.
  2. Coverage policy for the release — proposal: publish an explicitly **limited-coverage** release naming excluded/uncovered material (pairs without supported coverage stay `coverage_unavailable`, never "safe"), rather than blocking all use until the full corpus is reviewed.

## 4. Registration questions R1–R7

Common deliverables (tasks.md S26–S38): `content/questions/<key>/{manifest.json, network.xml, prompt.txt, template.json, examples.json, review.json}` covering plan.md §7.2 (CPT-context-only patient mappings, fixed empty-evidence execution, true/false/unknown gates, missing/conflict policy, complete CPTs incl. roots, reviewed reference-table provenance). Common status: `draft`, none approved. Prompts estimate only; templates use pinned reviewed mappings (no unreviewed largest-posterior treatment choice).

### R1 `hospitalization` (S26)

- Gap: **no matching executable network**; current BNs supply nothing for this question.
- Owner questions: (a) approve the hospitalization question scope, setting/time horizon, and disposition policy — proposal: registration-encounter scope with predefined review wording per output/gap and no universal risk cutoff; (b) approve observed inputs/output meanings S26 enumerates.
- Note: no urgent guidance may depend on waiting for a successful LLM request.

### R2 `pharmacotherapy` (S27)

- Source lead BN-04 (32 vars, 13 deterministic source-lookup DEFINITIONs, 19 distributions undeclared — template, not executable). **BN-04 reviews established treatment; initial selection needs explicit redesigned scope**, including catalog-tied medication output identifiers.
- Owner questions: (a) approve the redesigned initial-selection scope and graph S27 drafts (which BN-04 nodes/edges are kept vs dropped, incl. metadata-only parents); (b) resolve excluded dose/route inputs by content redesign (see §2 item 3 — FR-14 regimen mismatch above).

### R3 `involuntary_care` (S28)

- Gap: **no matching network**; jurisdiction and criteria must be supplied/reviewed — **no legal rules invented**.
- Owner questions: supply jurisdiction and authoritative criteria — proposal: owner names the jurisdiction + primary sources; until attached, the package stays a nonactivatable draft enumerating exactly what is missing (never a silent "not applicable").

### R4 `high_suicide_clozapine` (S29)

- Source lead BN-08 (14 vars, 0 DEFINITIONs — template). Align assessment period and treatment-history meanings with approved C-SSRS/history.
- Owner questions: (a) approve gate definitions (current urgent findings vs high-risk gate vs persistent risk despite prior treatment) with periods and structured provenance; (b) approve result-rendering examples. A scale level alone is not an unreviewed treatment rule; urgent handling is independent of LLM latency.

### R5 `lai_indication_choice` (S30)

- Source lead BN-10 (14 vars, 0 DEFINITIONs — template). **LAI choice gap: BN-10 supplies a discussion/review pathway only; it does not establish a complete product-choice contract** (admissible catalog identifiers, indication→choice rendering conditions).
- Owner questions: (a) approve the drafted choice contract (when choice renders after indication; what absent/uncertain preference or prior exposure means); (b) approve product-choice assumptions S30 records. One XMLBIF network, one prompt, one question step — not two runs, not a runtime LLM recommendation.

### R6 `aggression_clozapine` (S31)

- Source lead BN-09 (14 vars, 0 DEFINITIONs — template).
- Owner questions: (a) approve substantial/persistent aggression + prior-treatment context definitions with source periods — proposal: clinician-reported fields, **do not substitute PANSS hostility for an unreviewed aggression threshold**; (b) approve deterministic-rule vs estimated-table semantics resolution and result-mapping examples. No missing finding maps to "No".

### R7 `established_case_clozapine` (S32)

- Source lead BN-07 (23 vars, 0 DEFINITIONs — template; shared with F5, which gets a **separate** package).
- Owner questions: (a) approve established-status applicability input, trial-adequacy definition, and observation windows — no invented thresholds or forbidden medication details; (b) confirm R7/F5 separation (first-time false-gate vs follow-up no-improvement gate).

## 5. Follow-up questions F1–F6

Each follow-up package uses the same Common deliverables + `review.json` approval shape as §4 (reviewer owner, decision, date, source hashes); status draft, none approved.

### F1 `tardive_dyskinesia` (S33) — lead BN-14 (16 vars, 0 DEFINITIONs)

Owner questions: approve effect presence/severity mapping (absent ≠ not assessed; no diagnosis inferred from an unreviewed AIMS total) and source-required context/missing-input policy.

### F2 `akathisia` (S34) — lead BN-13 (20 vars, 0 DEFINITIONs)

Owner questions: approve presence/severity/context mapping — proposal: no invented summed BARS severity rule; motor restlessness kept distinct from unreviewed differential assumptions.

### F3 `parkinsonism` (S35) — lead BN-12 (20 vars, 0 DEFINITIONs)

Owner questions: approve effect/severity/context mapping — proposal: no universal mild/moderate/severe SAS score bands; onset/alternative-cause facts via approved structured history, never page notes.

### F4 `acute_dystonia` (S36) — lead BN-11 (12 vars, 0 DEFINITIONs)

Owner questions: approve presence/severity/onset/context fields and urgent-concern handling — proposal: source-approved urgent messages display without waiting for inference.

### F5 `no_improvement_clozapine` (S37) — lead BN-07 (separate package from R7)

Owner questions: approve the no-improvement definition (baseline encounter/window, instrument/context, adequate-treatment info) — proposal: no percent-change cutoff guessed; missing follow-up score is not "no improvement"; required ambiguity pauses rather than silently skipping.

### F6 `continue_or_adjust` (S38) — leads BN-04/05/06 (overlapping drafts; one network to be drafted)

- **BN-06 restriction:** S38 requires presenting BN-06's electronic decision-support restriction to the owner for explicit scope/source resolution. Observed status: the current BN-06.xml carries `STATEMENT 6 … Quality Measurement Considerations` as source locator plus the standard experimental-educational properties, but **no literal restriction text** (see S00 discrepancy note in progress-tracker.md) — resolution must therefore cite STATEMENT-06/plan/tasks, not current XML wording. Copying the draft is not resolution.
- Owner questions: (a) resolve the BN-06 restriction explicitly (proposal: adopt the experimental-educational scope with reviewed wording, or exclude BN-06-derived content — owner decides); (b) approve the single drafted graph and continuation-vs-adjustment template branches. If unresolvable, the package is marked blocked without claiming FR-30 complete. No runtime merge of three models.

## 6. Workflow bundles (S39; owner review required)

- **Deliverables:** `content/bundles/registration.json` (7 questions: R1–R7), `content/bundles/followup.json` (6 questions: F1–F6), one combined LAI network, pinned assessment/history/DDI/template/prompt references, content release manifest with exact package hashes/decisions.
- **Approval record:** bundle release manifest `{reviewer: "owner", decision, date, per-package hashes}`; activation only through the registry command as a new versioned event. Status: `draft` (activation blocked until all 13 packages reviewed; S24 uses synthetic complete bundles meanwhile).
- **Owner question:** approve the 13 concrete packages + cross-package assumptions as a batch or per package — proposal: batched S39 review with per-package revise-and-rerun on rejection; no batch-approval on the owner's behalf.

## 7. Reference-table assumptions (all packages)

Per plan.md §7.3: reference tables in a released artifact must be explicitly reviewed; **missing values are never silently filled with uniform probabilities**; because all runtime CPTs are replaced by LLM estimates, each package documents its reference tables' purpose and provenance without presenting them as patient estimates. Owner review resolves draft restrictions and fixed-rule vs estimated-table assumptions in a new version — never by flipping an `inference_enabled` property. No owner question beyond the per-package provenance review already listed.
