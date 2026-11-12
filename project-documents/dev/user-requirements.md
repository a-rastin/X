# User Requirements

## X-INSIGHT

X-INSIGHT is a research prototype helping physicians/psychiatrists explore treatment options for schizophrenia cases.

**Users:** One administrator and fewer than 10 physicians, with a shared patient pool. The application supports English and Persian; English is the default.

**New case physician flow:** Register patient → establish diagnosis → optionally complete PANSS → assess self/other harm → enter medical history/examination → select current medications → enter treatment considerations → generate proposed plan → review and edit physician plan → explicitly confirm, save, and sign final plan.

**Revision scope (2026-11-12; follow-up implementation pending):** New case registration uses its implemented `new-case-v1` contract. Newly created follow-ups will use the reconciled `follow-up-v1` contract in FR-70–FR-79 and the [follow-up specification](follow-up-specification.md); see the [implementation plan](follow-up-implementation-plan.md). Until implemented, runtime follow-up remains legacy. Known case registration and existing open legacy follow-up drafts retain their workflow. Existing signed records, historical datasets and legacy runs remain readable and unchanged. The version-specific requirements take precedence over legacy LLM, CPT-slider and questionnaire rules within their scopes.

**Planned follow-up flow:** Historical summary → optional PANSS → self/other harm with nested C-SSRS → history/examination → medications and main antipsychotic/formulation → seven simple adverse effects → treatment considerations and conditional physician decisions → deterministic proposed plan → edit and explicitly confirm/sign the physician plan. No diagnosis step, LLM or probability sliders in `follow-up-v1`. Existing legacy drafts retain their older flow.

The follow-up [research development policy](follow-up-specification.md#research-development-policy)
removes legal confirmation, author evidence submission, source confirmation and
external medical/clinical approval requirements for implementing or changing this
workflow and its medical content. Older approval/dossier instructions do not gate
this work. Required visit inputs and final-plan signing retain their specified
application behavior.

### Terminology

- **Initial treatment proposal:** The system-generated proposal incorporating drug–drug interaction findings and Bayesian-network recommendations.
- **Secondary treatment plan:** The final plan reviewed, edited, accepted, and signed by the physician.
- **Original CPT values (legacy workflows only):** The validated conditional probability table values estimated by the LLM through the MCP-enabled workflow for a particular clinical-question run.
- **Adjusted CPT values (legacy workflows only):** Encounter-specific CPT values produced by physician slider changes and automatic proportional redistribution.
- **Original result (legacy workflows only):** The network output and templated recommendation generated using the original CPT values.
- **Adjusted result (legacy workflows only):** The network output and templated recommendation generated using the adjusted CPT values.

For New case, the application supplies patient data directly to the fixed oral-antipsychotic Bayesian network and obtains its results. There is no LLM, generated CPT, MCP reasoning request, or physician probability-slider adjustment. The physician edits final-plan text, not network probabilities. The generated proposal remains separate from the confirmed physician plan.

For Known case registration and existing `legacy-v1` follow-up drafts, MCP provides the tool-access environment and the LLM estimates CPT values; the application validates those values and executes the Bayesian networks. The legacy requirements below remain scoped to those workflows.

## Functional Requirements

### Users and Login

**FR-01:** Users log in with a role, username, and password and are redirected to the corresponding role dashboard. After a psychiatrist logs in successfully, display a warning that the application is a research prototype and must not be used as the sole basis for treating patients. The Register button displays “Contact administrator”.

**FR-02:** The application has one administrator account with the immutable username `admin` and the default password `admin`. No password complexity rules apply.

**FR-03:** The administrator can change their own password, toggle the dark/light theme, manage API settings (key, base URL, model), manage Bayesian networks, manage physician accounts, view/export patients, perform backup/restore, and view the audit log.

**FR-04:** The administrator creates physician accounts; self-registration is unavailable. The administrator can edit physician credentials and deactivate accounts. Deactivation retains existing records; unfinished drafts are discarded only after confirmation. Physicians can change their own password but cannot otherwise manage account credentials. For each new physician, the administrator supplies a Persian name and/or English name; at least one nonblank name is required, along with username and password. Existing unnamed accounts remain usable; do not fabricate historical names.

**FR-05 — Shared language support:** Labels, validation messages, help text, and new plan sections support Persian and English. English is the default. A language-switch button remains at the upper left in either language. Persian uses right-to-left layout, with readable drug names, lab abbreviations, units, numeric values, and IDs. User-entered names remain as entered; source Lexicomp notes retain their original text.

**FR-06 — Unavailable drugs:** Add an “Unavailable drugs” physician settings section listing oral and injectable antipsychotic formulations. The physician may mark formulations that must not be recommended. Settings are persistent, physician-specific, and formulation-specific; excluding an injectable formulation does not automatically exclude its oral counterpart. Apply these settings to New case and planned `follow-up-v1` new antipsychotic recommendations. Existing legacy clinical workflows are unchanged.

### New Case Registration

The following seven input steps apply to clinical status New case. Known case registration retains its existing clinical workflow. Required fields have a red asterisk and accessible required labels. Missing or invalid required inputs block advancing and completion with an explanatory warning. Retain the unfinished patient/draft. Apply these gates to direct navigation, generation, and sign-off as well as ordinary Continue.

**FR-10 — Demographics (Step 1):** Collect separate required first/last names, Patient ID, sex, Gregorian birth year, and clinical status. Names may contain Persian or English letters and ordinary spaces only. Sex is Male/Female, initially unselected. Clinical status is New case/Known case, initially unselected and required. Patient ID is exactly ten digits stored as text, preserving leading zeros, and unique across all patients including archived records. Record visit datetime automatically. The birth-year list permits ages **18–100 inclusive**. Calculate and display age as registration-encounter Gregorian year minus birth year; do not request an independent New case age or silently change a saved draft's age at New Year.

**FR-11 — Diagnosis (Step 2):** Use the existing DSM-5-TR schizophrenia criteria with Yes/No/Unknown, initially Unknown. Ordinary Continue requires completed criteria confirming diagnosis. Negative, partial, entirely unanswered, or Unknown assessments block ordinary continuation, generation, and completion. Keep the unfinished patient/draft. Provide the explicit **“Diagnosis is definite”** physician action. If criteria are positive, retain answers and continue normally. For unanswered/partial/nonpositive criteria, this action clears active criteria answers and records only the physician's explicit diagnosis assertion as the active diagnostic basis, with physician/time attribution. Later criteria edits invalidate that assertion until criteria are positive or the physician explicitly asserts again. Warning-only continuation and unrecorded bypass do not apply to New case.

**FR-12 — PANSS (Step 3):** Items start unanswered. The physician may continue with no answers or partial answers. Preserve each entered value and unanswered state. Compute a score only when every item required for that score is complete; do not default missing answers to minimum scores.

**FR-13 — Self/other harm (Step 4):** Provide separate Yes/No self-harm and other-harm questions, both default No. A magnifier beside self-harm has help text “Use C-SSRS to assess the risk of self-harm”; clicking reveals the recent C-SSRS questionnaire independently of the self-harm answer. Its five recent Yes/No items default No. Preserve responses. Recent C-SSRS item 4 or 5 Yes independently triggers inpatient care under FR-65. This replaces the previous standalone, initially unanswered C-SSRS step only in New case.

**FR-14 — Medical history/examination (Step 5):** Yes/No questions default No unless specified otherwise. Collect:

- Other psychiatric disorders: Yes requires depression, PTSD, ADHD, and/or Other. Other permits optional saved free text.
- Laboratory report available: Yes requires at least one entered test result; partial reports are allowed. Include CBC with differential; creatinine/BUN; AST/ALT/ALP; PT/PTT/INR; FBS/HbA1c; prolactin; LDL/HDL/triglycerides/total cholesterol; and female-only Beta-HCG. Show units, references, and warnings under FR-60.
- Hepatic failure, renal failure, diabetes/prediabetes, hyperlipidemia, hyperprolactinemia: support automatic lab suggestions and persistent physician overrides under FR-61.
- Heart failure and current tobacco use. **Tobacco is Yes/No only; no Unknown option.**
- Current substance use: Yes requires marijuana and/or Other substances. Other permits optional saved free text.
- BMI: require either a category calculated from height (cm) and weight (kg), or an explicitly selected category on a horizontal category bar. No category is selected initially. Categories: underweight, normal, overweight, obesity I/II/III. Persist calculated/manual mode; manual selection remains until explicitly returned to calculated mode. Above-normal means overweight/obesity, corresponding to **BMI ≥25**, calculated before display rounding.
- Pregnancy and breastfeeding: female-only, initially No. Pregnancy supports a Beta-HCG suggestion and persistent override. Hidden male-inapplicable values do not affect outputs.
- Focal neurological deficit, suspected brain lesion, possible autoimmune encephalitis: initially No. The last has a magnifier explanation covering rapid progression within three months, memory/consciousness changes, lethargy, and personality change.

Retained hidden/disabled conditional values do not affect calculations or impose requirements until applicable again.

**FR-15 — Current medications (Step 6):** Use the supplied **Lexicomp-cleaned.csv** catalog instead of the demo/DDInter catalog. Search English drug names after at least three characters. Selecting a catalog suggestion stores its ID and appends another empty input. Zero current medications is allowed. Reject nonblank unmatched text: select a catalog entry or clear the input. Preserve formulation qualifiers and prevent duplicate selections. Do not collect dose, unit, route, frequency, or active/stopped status. Do not show a separate interaction-check button or report here; show candidate interactions in the proposed plan.

**FR-16 — Drafts:** Automatically save/resume inputs, assessment states, overrides, proposal references, and editable physician plan. Preserve work on validation or generation failure. Explicit Discard requires confirmation. Do not add a working-note card or restore page-note capture in New case. Retain historical note data and immutable signed payloads; note behavior outside this workflow is unchanged and notes never influence algorithms.

**FR-17 — Treatment considerations (Step 7):** In order, ask: ability to meet basic needs alone/with family help (default Yes); need for observation for any reason (No); intention to use LAI (No); increased seizure risk (No); preference to avoid sedation (No); QT/QTc prolongation risk (No); orthostatic-hypotension risk (No). Heart failure or selected BP-lowering medication suggests orthostatic risk Yes with reasons, subject to persistent override. Identify BP-lowering drugs through a medically sourced catalog mapping, not name guessing. After validation, provide an explicit Generate proposed plan action.

**FR-18 — Review/edit/confirm:** Present the original generated proposal separately from an editable physician plan. The physician can modify proposed content, then explicitly confirm the final text. Only after confirmation save the immutable signed final plan. Keep the original proposal, final edits, physician identity, and timestamps distinguishable. New case requires neither CPT-slider acceptance nor a provider API key. Relevant input/availability changes make an unsigned proposal stale and require regeneration before confirmation; language changes and final-text edits do not. Preserve edits on failure.

**FR-19 — Compatibility:** Do not call the legacy LLM coordinator, start LLM jobs, estimate CPTs, or expose sliders for New case. Keep Known case registration and existing legacy follow-up behavior. New follow-up scope is defined in FR-70–FR-79. Readers and reports support both legacy and new signatures without rewriting historical records or inventing old questionnaire answers from new data.

### Follow-up and Records

**FR-20 — Existing legacy follow-up:** A `legacy-v1` follow-up encounter allows the physician to update the phone number, re-assess severity and suicide, update history and medications, record adverse effects, review the initial proposal, inspect and adjust CPT values, edit the plan, and sign the secondary treatment plan. The encounter is logged.

**FR-21 — Existing legacy follow-up effects:** Adverse effects include tardive dyskinesia, akathisia, parkinsonism, and acute dystonia. Each supports present, absent, and not-assessed states, plus severity. Require the full adverse-effect questionnaire only when its corresponding effect is present, with versioned item responses, completeness and source-defined scoring. Instrument forms, administration requirements and mappings to network variables require review; incomplete/not-assessed responses do not produce guessed scores. The acute-dystonia form is named “Acute Dystonia Dx Criteria”; concrete form/severity mappings and the complete AIMS source remain subject to review in `content/review-ledger.md` §2. Absent/not-assessed effects do not require questionnaire completion.

**FR-22:** Any physician may create encounters and update demographics. Only the draft author may edit or sign that draft, including changing its CPT sliders, resetting its probabilities, or accepting its results. Signed encounters are immutable, including their accepted CPT values and network results. Corrections use dated, attributed addenda; follow-ups are new encounters.

**FR-23:** The patient list supports search by name or Patient ID and filtering by clinical status. The administrator can archive and unarchive patients. Permanent deletion is unavailable in v1.

### New Follow-up — Reconciled 2026-11-12, Implementation Pending

The [follow-up specification](follow-up-specification.md) is the complete normative contract; the following IDs provide requirement traceability. Explicit product clarifications override the attachments, then the flowchart controls antipsychotic decisions and the Word document controls visit forms/other recommendations. These rules do not change existing drafts or assert implementation/clinical validation.

**FR-70 — Identity and baseline:** Server-created new follow-ups use immutable `follow-up-v1`. Freeze the latest signed visit across all supported variants and a dated historical summary, separately from current editable values. Carry specified structured history/medications with provenance; missing/Unknown mandatory history requires completion. Preserve old workflows, signatures and hashes. Optional hospitalization count distinguishes missing from zero.

**FR-71 — Seven-step visit:** PANSS (optional/unanswered), harm (two No-default booleans with five nested recent No-default C-SSRS questions), history/examination, medications, effects, considerations, proposal. No repeat diagnosis or mandatory old effect questionnaires. Enforce applicable prerequisites on forward/direct navigation, generation and signing while allowing incomplete autosave.

**FR-72 — History and labs:** Use the specified psychiatric/substance selections, female-only pregnancy/lactation, required calculated/manual BMI, neurological findings, and carried seizure/QT risks. Reuse pinned New case lab units/references/warnings. Separate inherited values, new lab suggestions and persistent attributed current overrides. Require current-antidepressant confirmation when depression/PTSD is selected. Derive hyperprolactinemia from lab/prior data or require a physician answer on a reached oral-model branch.

**FR-73 — Medication and effect inputs:** Use the pinned Lexicomp catalog with three-character English suggestions and editable carried list; empty list allowed. Select one main antipsychotic and oral/LAI formulation; no numeric dose/injection schedule. Collect sedation (No/tolerable/requires intervention) and six Yes/No effects, fresh No. Every Yes requires intervention except tolerable sedation. No main/current LAI produces physician review without automated antipsychotic change.

**FR-74 — Decision pathway:** Require the three-way physician recovery answer and reached explicit switch/dose-reduction/resistance/persistent-risk/dystonia-resolution questions. Follow the supplied oral graph plus confirmed extensions: handle all effects at every recovery status, accumulate adjunct advice, route sedation requiring intervention/orthostasis/anticholinergic effects to oral replacement, and prioritize effect-driven oral replacement over clozapine/LAI/continuation. Do not invent clinical thresholds or specific adjunct doses.

**FR-75 — Fixed model:** Reuse the trusted 16-factor/22-outcome oral model only on reached branches, with current effects and unavailable-drug settings. No LLM, MCP, generated CPT, worker or probability sliders. Preserve the complete original distribution and factor/source map. Unsupported branches record honest physician-review outcomes rather than fake results.

**FR-76 — Interactions and exclusions:** Show current-list and candidate Lexicomp evidence on the proposal. Exclude every newly suggested antipsychotic with an explicit D/X interaction, including direct clozapine/LAI suggestions. Compare replacements against all current drugs, including the outgoing main because overlap may occur. Filter the full oral distribution before taking up to five, excluding the outgoing main as a replacement; preserve probabilities and removal reasons. Flag missing coverage distinctly. Excluded direct/no-eligible outcomes request physician review.

**FR-77 — Other recommendations:** Apply care-setting triggers, antidepressant-use check, smoking cessation, routine labs, eligible Beta-HCG, neurological-trigger MRI without age-only trigger, and pregnancy/lactation advice per the specification. Contraception counseling requires female, nonpregnant, nonbreastfeeding and age <=55; show prior structured recommendations. Same oral drug means four weeks even with lower dose/uncontrolled symptoms; new oral drug means 2–4 weeks; LAI means next injection without a fabricated date.

**FR-78 — Proposal and confirmation:** Preserve immutable original proposal, decision trace/exclusions, inputs/provenance, content/model/release/settings pins and independently edited final text. Revalidate ownership, required inputs, encounter/plan revisions and freshness before atomic explicit signing. Preserve edits/replays and supported physician-review outcomes; infrastructure failures cannot become a signing bypass. No fabricated legacy acceptance requirement.

**FR-79 — Shared surfaces and recovery:** Support mixed old/New case/new-follow-up chronology, escaped reports, addenda, audit and full backup/restore with unchanged historical bytes. Package versioned content for normal offline startup. Retain bilingual/theme/accessibility, privacy, one-draft and concurrency contracts. See [packages A–H](follow-up-implementation-plan.md) for implementation and verification.

### Legacy Reasoning Pipeline — Known Case and Existing Legacy Follow-up

FR-30–FR-36 apply to the legacy pipeline, **not New case registration or planned `follow-up-v1`**. FR-37 remains shared administrator network management; physician CPT adjustments remain legacy-only.

**FR-30:** Each clinical question has one Bayesian network.

- **Registration:** Pharmacotherapy (established-treatment review, preserving BN-04 scope), high-suicide Clozapine, LAI discussion/review, aggression Clozapine, and established-case Clozapine.
- **Follow-up:** Tardive dyskinesia, akathisia, parkinsonism, acute dystonia, no-improvement Clozapine, and continue-versus-adjust. BN-06-derived continuation content is retained within the experimental/educational scope, with its guideline source limitation documented; concrete packages remain subject to review.

**FR-31:** Each clinical question has a predefined prompt and a corresponding Bayesian network stored as XMLBIF. XSD validation covers structure only, including nodes, states, and CPT syntax. Separate application validation checks CPT completeness, correspondence to network states and parent-state combinations, numeric validity, probability bounds, and row totals before execution.

**FR-32:** The application processes applicable clinical questions sequentially. For each question, it sends the LLM the question-specific prompt, the structure of the corresponding Bayesian network, and only the patient variables represented in that network. Network variables, variable types, states, and relevance remain fixed. Slider adjustments do not alter these definitions or the patient inputs.

**FR-33:** In the MCP-enabled environment, the LLM estimates the network’s CPT percentages from the supplied patient-variable values, such as age and underlying conditions, and returns those estimates to the application. The LLM does not execute the network or modify its structure.

**FR-34:** The application validates and inserts the returned CPT values into the corresponding network and executes it deterministically. The resulting question-specific output is converted into the relevant recommendation using predefined templates. The pipeline repeats until all applicable questions have been processed. Each completed question exposes its original CPT values and result for physician review and adjustment under FR-50–FR-59.

**FR-35:** The application runs an internal MCP server exposing patient-record tools. The internal database is the single source of truth. Each initial run starts automatically and records the clinical question, network version, prompt and template versions, patient inputs supplied to the LLM, returned CPT percentages, validated CPT values used for execution, and network result. These details are available alongside the recommendation. The original successful run is retained as an immutable baseline for subsequent slider adjustments.

**FR-36:** If an LLM request fails or returns invalid CPT values, the application retries two to three times, then stops the affected clinical-question step with a clear error. Saved patient data and completed question results are retained, and the physician may retry the failed step later. A question without a successful original execution does not expose an adjustable result. Slider-triggered recalculation failures are handled separately under FR-58 and do not automatically invoke the LLM.

**FR-37:** Network management in v1 supports graph viewing, XML import/edit/export, validation, versioning, activation, and rollback. Graphical editing is unavailable. Physician slider changes are specific to an encounter and run; they do not modify, activate, or create versions of the administrator-managed network definition.

### Reporting and Operations

**FR-40:** Exports include CSV patient/physician lists and a printable HTML report for each patient. The report includes signed treatment plans and, for each clinical question, the original CPT values and result, final accepted CPT values and result, whether probabilities were physician-adjusted, and the associated physician and timestamps. For New case, reports instead include the original proposal, final confirmed physician plan, input/override snapshot, fixed-model result/version, Lexicomp findings/version, availability exclusions, and signer/time; do not fabricate legacy CPT adjustments. PDF export is unavailable in v1.

**FR-41:** The administrator can download a full backup containing the database and network XML and restore it from a file. The backup preserves original runs, CPT adjustments, calculation results, signed snapshots, audit records, and the versioned definitions required to reproduce saved network executions. Also preserve New case drafts, physician names/settings, fixed source model XML, pinned Lexicomp release/evidence, versioned clinical rules, original proposals, and final plans.

**FR-42:** The audit log is append-only and viewable by the administrator. It records logins, network runs, plan sign-offs, and administrator actions. It also records completed slider adjustments, automatic probability redistribution, resets, recalculation outcomes, and acceptance of final probabilities, with the actor, timestamp, patient, encounter, clinical question, and relevant run or revision identifiers. Adjustment records preserve the before-and-after CPT values. For New case, also audit diagnosis assertions, override/reset actions, proposal generation/invalidation, availability changes, physician plan edits, and confirmation; do not fabricate legacy LLM/CPT events.

**FR-43:** API settings support an OpenAI-compatible API key, base URL, and model name. Concurrent users are supported through queued LLM calls and fail-soft errors. Slider-triggered network recalculations run within the application and do not require additional LLM calls or MCP tool requests.

**New case provider independence:** FR-43 API settings remain for legacy workflows; New case generation requires no API key, LLM call, or MCP request.

### Legacy Interactive CPT Review and Adjustment

FR-50–FR-59 apply only to Known case and existing legacy follow-up clinical-question runs. New case uses FR-18/FR-62; planned new follow-up uses FR-70–FR-79. Neither deterministic workflow uses CPT sliders or physician probability acceptance.

**FR-50 — Decision-specific probability controls:** At the point where the initial treatment proposal is presented, display a dedicated review panel for each completed clinical question and its Bayesian network. Each panel shows the question, recommendation, network version, and a grouped slider menu covering every CPT value, including root-node probability distributions. No CPT value is hidden or excluded from review and adjustment.

**FR-51 — Probability labels and grouping:** Group sliders by node and, for conditional distributions, by the complete parent-state combination defining each CPT row. Each slider displays the relevant state, its original percentage, and its current percentage. Display the row total. Clearly distinguish editable CPT input probabilities from calculated output probabilities; network outputs are shown as results, not as editable CPT inputs.

**FR-52 — Automatic proportional redistribution:** Each slider has a range of 0%–100%. When the physician changes one probability in a CPT row, retain the selected value and automatically redistribute the remaining percentage among the other states in that same row in proportion to their values immediately before the change. Other CPT rows remain unchanged.

- For an edited value of `x`, the remaining states must total `100 − x`.
- If the other states previously totalled more than zero, preserve their relative proportions.
- If the other states previously all equalled zero, distribute the remaining percentage equally among them.
- A row containing only one state necessarily remains at 100%; display its value and explain the mathematical constraint.
- Handle rounding deterministically so that the stored distribution remains valid and the displayed row total is 100%. Do not introduce negative values or values greater than 100%.

**FR-53 — Automatic local recalculation:** After the physician completes a slider adjustment, automatically validate the updated CPTs and re-execute only the corresponding Bayesian network. Use the same patient-input snapshot, network version, and template version as the original run. Do not call the LLM again, restart the initial reasoning pipeline, or re-execute unrelated clinical questions. Generate the adjusted recommendation using the predefined template. A probability change may or may not change the resulting recommendation.

**FR-54 — Original and adjusted comparison:** Display the original and latest successfully adjusted network outputs and recommendations side by side. Identify physician-adjusted values and show their differences from the original values. Label the original percentages as LLM-estimated and the current values as physician-adjusted where applicable. Display the calculation state clearly: unchanged, recalculating, successfully recalculated, or failed.

**FR-55 — Reset to original values:** Each clinical-question panel provides a “Reset to original values” action. Reset restores all CPT values for that question to its immutable original baseline and refreshes the current result accordingly, without an LLM request. Other clinical-question panels remain unchanged. The reset is recorded and does not delete the adjustment history.

**FR-56 — Persistence and scope:** Automatically save completed adjustments, including the directly edited value, automatically redistributed values, physician identity, timestamp, and calculation revision. Preserve them when the physician navigates away, resumes a draft, or encounters a failure. Changes apply only to the current encounter and clinical-question run; they do not change other patients, encounters, shared network definitions, or prior signed plans. Unchanged questions retain their original CPT values and results.

**FR-57 — Acceptance and sign-off:** Before signing the secondary treatment plan, the physician reviews and accepts the final probability values and corresponding results used for that plan. Sign-off stores an immutable snapshot of the original CPT values and result, final accepted CPT values and result, network and template versions, patient-input snapshot, and physician identity and timestamps. The original system proposal, physician-adjusted recommendations, and physician-authored plan edits remain distinguishable. A pending or failed recalculation cannot be represented as an accepted updated result; the physician must successfully recalculate the current values or reset them to the original baseline before signing.

**FR-58 — Recalculation integrity and failure handling:** Associate each recalculation with the exact saved CPT revision that produced it. If the physician makes another adjustment while a calculation is in progress, an older response must not overwrite the latest result. While recalculating, clearly mark any displayed previous result as belonging to an earlier revision. On failure, preserve the adjusted values and previous successful result, show a clear error, and allow local retry or reset. Never display an earlier result as if it were calculated from the current unsolved CPT values.

**FR-59 — Patient-data changes:** CPT adjustment operates on a fixed patient-input snapshot. If patient variables relevant to a completed clinical question change before sign-off, mark that question’s existing results and adjustments as out of date and require regeneration for the updated inputs before accepting that question’s result. Preserve the earlier run and its adjustment history; regenerated results establish a new original baseline, and prior physician adjustments are not silently carried forward. Changes to notes do not trigger regeneration because notes never influence algorithms.

### New Case Clinical Computation and Proposal Rules

**FR-60 — Laboratory content:** Use medically sourced, versioned units, reference/decision values, and critical warnings, preserving source dates, assay context, sex applicability, and outpatient/inpatient qualifications. Show abnormal-result warnings; use stronger critical warnings only where a sourced threshold exists. Do not invent “twice normal” cutoffs. Warnings do not block legitimate values or erase overrides. Missing results remain missing. Reference abnormalities suggest the requested condition answers; they do not prove organ failure. Appendix A records the researched defaults. Na, K, and TSH are recommended orders, not added result-entry questions.

**FR-61 — Suggestions/overrides:** Automatically suggested Yes answers display their causes. For five condition flags, pregnancy, and orthostatic risk, a saved manual Yes/No overrides the current suggestion; without a manual answer use the current source-derived suggestion, default No. Persist manual choices across reloads and later lab/medication/source changes until explicitly reset to automatic. Record physician/time. Use these independent triggers:

| Flag | Trigger |
|---|---|
| Hepatic failure | Any AST, ALT, ALP, PT, or PTT above its upper reference |
| Renal failure | BUN or creatinine above its applicable upper reference |
| Diabetes/prediabetes | FBS ≥100 mg/dL or HbA1c ≥5.7% |
| Hyperlipidemia | LDL ≥100, triglycerides ≥150, or total cholesterol ≥200 mg/dL |
| Hyperprolactinemia | Prolactin above its sex-specific upper reference |
| Pregnancy | Female and Beta-HCG ≥25 mIU/mL; 5 to <25 is indeterminate, warning without automatically setting Yes |
| Orthostatic risk | Heart failure Yes or a medically classified BP-lowering current medication |

INR and low HDL are not extra condition triggers; they can still produce abnormal-result warnings.

**FR-62 — Fixed oral BN:** Use the supplied `BN-PO-Antipsychotic-Recommender.xml` directly with its fixed structure/states/CPTs. The application feeds all sixteen binary factors below and obtains the oral distribution; no LLM estimates or alters probabilities. Add no movement-disorder questions: tardive dyskinesia, dystonia, parkinsonism, and akathisia are **No** by default for New case, with default provenance retained.

| Network factor | Input |
|---|---|
| Smoking | Current tobacco |
| Marijuana_Smoking | Substance use Yes and marijuana selected |
| Hepatic_Failure / Renal_Failure | Effective hepatic / renal answers |
| Sedative_Effects_Concern / Seizure_Risk | Avoid sedation / seizure-risk answers |
| Glucose_Abnormalities / Hyperlipidemia | Effective glucose / lipid answers |
| Weight_Gain_Risk | Effective overweight/obesity BMI category (≥25) |
| QTc_Prolongation_Risk / Orthostatic_Hypotension_Risk | QT/QTc / effective orthostatic answers |
| Hyperprolactinemia | Effective prolactin answer |
| Tardive_Dyskinesia / Dystonia / Parkinsonism / Akathisia | No / No / No / No |

Rank by descending original probability, ties by source-model order. Exclude zero-probability and physician-unavailable oral formulations, then take up to five; replace excluded high-ranking drugs with subsequent eligible candidates. Show fewer than five, including none, if necessary. Keep original percentages without renormalization. Show model fit percentages and candidate/current-medication interactions. Interactions annotate; they do not rerank/exclude candidates. Percentages are model fit, not measured probabilities of clinical treatment success.

**FR-63 — LAI:** When LAI is intended, leave its medication recommendations empty and explain that the LAI BN is not ready. Do not substitute oral/legacy models, an LLM, or invented drug choices. Generate other applicable sections and permit physician editing/confirmation. Intentional LAI model unavailability is supported; a missing/corrupt required oral model is a separate generation error requiring retry.

**FR-64 — Lexicomp:** Replace DDInter with supplied `Lexicomp-cleaned.csv` for New case catalog/interactions. Use `Drug_1`, `Drug_2`, `Lexicomp_level`, `Lexicomp_note`; preserve source names, qualifiers, ratings, notes. Compare each alternative candidate against each distinct current medication, never against other alternative candidates. Pair lookup is order-independent; suppress self-pairs. Missing pair means **coverage unavailable**, never A or reassuring “no interaction”; no DDInter fallback. Preserve historical releases/readers and draft source pins. Store exact findings/source versions in proposals and signed snapshots.

| Rating | Action | Explanation |
|---|---|---|
| A | No known interaction | Data have not demonstrated pharmacodynamic or pharmacokinetic interaction for the pair. |
| B | No action needed | Interaction may occur, with little/no evidence of clinical concern during concomitant use. |
| C | Monitor therapy | Clinically significant interaction possible; benefits usually outweigh risks with monitoring and occasional dose adjustment. |
| D | Consider therapy modification | Assess patient-specific benefit/risk and take action, such as closer monitoring, empiric dose adjustment, or alternative agents. |
| X | Avoid combination | Risks usually outweigh benefits; concurrent use should generally be avoided. |

Show source management notes and all explicit ratings, including A/B; dataset catalog membership alone does not establish pair coverage.

**FR-65 — Deterministic proposal:** Evaluate each section independently using effective inputs. No additional BNs or LLM are used.

| Section | Rule |
|---|---|
| Care setting | Inpatient if self-harm Yes, other-harm Yes, recent C-SSRS item 4 or 5 Yes, basic-needs answer No, or observation Yes. Otherwise outpatient. Show all matching reasons. |
| Route/candidates | LAI only if explicitly intended; otherwise oral. Use FR-62 or the empty LAI list in FR-63. |
| Antidepressant | Consider addition if depression or PTSD selected. ADHD/Other alone does not trigger it. Do not choose a drug/dose. |
| First contraception rule | Female, age ≤55, not pregnant: recommend contraception. No breastfeeding condition. |
| Tobacco | Recommend cessation if tobacco Yes. |
| Baseline tests | Always recommend CBC, Diff, Na, K, BUN, Cr, AST, ALT, ALP, PT, PTT, INR, TSH, FBS, HbA1c. Add Beta-HCG if female, age ≤55, not pregnant. Entered results do not suppress orders. |
| Brain imaging | Brain MRI if age >55, focal neurological deficit, suspected brain lesion, or possible autoimmune encephalitis. Treatment must not wait for imaging. |
| Breastfeeding | If female and breastfeeding, advise stopping breastfeeding and pediatric consultation about formula. |
| Pregnancy | If female and pregnant, advise obstetric consultation during pregnancy, neonatal consultation after birth, and lowest effective medication dose; do not calculate a dose. |
| Second contraception rule | Female, not pregnant, not breastfeeding: discuss pregnancy intention and recommend contraception if pregnancy undesired. No age restriction. |
| Interactions | Present FR-64 candidate/current-medication ratings, notes, and unavailable coverage. |
| Next visit | Oral: 2–4 weeks. LAI: next injection; do not invent an injection interval/date without a drug/schedule. |

**Keep both contraception rules separate.** A nonpregnant breastfeeding woman aged 30 receives the first recommendation only. A nonpregnant nonbreastfeeding woman aged 60 receives the second counseling recommendation only. Men and pregnant women receive neither.

**FR-66 — Confirmation integrity:** Snapshot raw/effective inputs, overrides, diagnosis/assertion, default-factor provenance, fixed model/version/results, dataset/version/pair evidence, clinical-rule version, availability settings, original proposal, final physician text, and signer/time. Original proposals and signed plans are immutable. Block confirmation of stale or failed oral results while preserving drafts. Do not parse final free text as structured medication data or claim free-text added drugs have been interaction-checked. Enforce draft authorship, revision checks, and sign-off permissions server-side.

## Non-Functional Requirements

**NFR-01 — Deployment:** The application is deployable in self-hosted environments and on Linux cloud VPS infrastructure and supports concurrent users. It remains a flexible/adaptive research prototype.

**NFR-02 — Security:** Prototype-basic security includes authentication, role-based access, and draft-author restrictions. Sessions have no timeout. HTTPS is required outside localhost. PHI hardening is outside v1 scope. Probability modification and sign-off permissions are enforced by the application, not only by hiding interface controls.

**NFR-03 — Usability:** The application supports English and Persian, defaults to English, and targets desktop use in the latest Chrome and Firefox versions. It supports a theme toggle, explicit validation, and confirmations. CPT controls use readable labels, show exact percentages alongside sliders, support keyboard adjustment, and make automatic changes to other states visible. Large CPTs remain navigable through node/row grouping in legacy workflows. New case uses bilingual forms, proposal review, and editable final text, with no probability sliders.

**NFR-04 — Reliability and reproducibility:** Drafts and CPT adjustments survive failures. Bayesian-network execution is reproducible for the same patient-input snapshot, complete CPT values, network version, and execution configuration. Reproducing recommendation text additionally requires the same template version. Reproduction uses saved CPT values rather than requesting new estimates from the LLM. Original runs and signed snapshots remain immutable. New case reproduction uses the fixed model, effective inputs, pinned Lexicomp source, availability snapshot, and versioned deterministic rules, without an LLM.

**NFR-05 — Maintainability:** Networks, prompts, and templates are versioned. XSD validation is complemented by runtime probability validation. The application maintains an audit trail for original runs, physician adjustments, recalculations, resets, and accepted results. Legacy probability redistribution remains deterministic. New case validation/calculation rules have one consistent definition, with versioned lab references, drug classifications, model assets, and interaction content.

**NFR-06 — Responsiveness and isolation:** Slider interaction remains responsive while recalculations run. The interface displays calculation progress and prevents acceptance of stale results. Recalculations remain isolated to their encounter and clinical question, support concurrent users, and do not consume LLM requests. New case generation shows progress/errors, preserves drafts on failure, prevents stale acceptance, and consumes no LLM requests.


## Appendix A — Medical Reference Content for New Case

Carried forward from the medical-source research prepared for the 2026-11-09 plan. These are versioned default reference/decision values, not universal diagnostic thresholds. Preserve assay, sex, and care-setting context; physician overrides remain available.

CBC/differential, renal, liver, and clotting reference values use the [ABIM January 2026 reference ranges](https://www.abim.org/media/e2wdwdqu/laboratory-reference-ranges.pdf). F/M refer to entered sex.

| Field ID | Unit | Default reference |
|---|---|---|
| `wbc` | /µL | 4000–11000 |
| `rbc` | million/µL | 4.2–5.9 |
| `hemoglobin` | g/dL | F 12–16; M 14–18 |
| `hematocrit` | % | F 37–47; M 42–50 |
| `platelets` | /µL | 150000–450000 |
| `mcv` | fL | 80–98 |
| `mch` | pg | 28–32 |
| `mchc` | g/dL | 33–36 |
| `rdw` | % | 9–14.5 |
| `mpv` | fL | 7–9 |
| `neutrophils_pct` | % | 50–70 |
| `bands_pct` | % | 0–5 |
| `lymphocytes_pct` | % | 30–45 |
| `monocytes_pct` | % | 0–6 |
| `basophils_pct` | % | 0–1 |
| `eosinophils_pct` | % | 0–3 |
| `creatinine` | mg/dL | F 0.50–1.10; M 0.70–1.30 |
| `bun` | mg/dL | 8–20 |
| `ast`, `alt` | U/L | 10–40 |
| `alp` | U/L | 30–120 |
| `pt` | seconds | 11–13 |
| `aptt` | seconds | 25–35 |

Additional fields and distinct decision thresholds:

- `inr`, dimensionless, reference 0.9–1.1 from [Mayo PT/INR](https://www.mayocliniclabs.com/test-catalog/Overview/40934).
- `fbs`, mg/dL: show 70 to <100 as normal; set the glucose suggestion at ≥100. `hba1c`, %: show 4.0 to <5.7 as reference; set the glucose suggestion at ≥5.7. The [NIDDK diagnostic table](https://www.niddk.nih.gov/health-information/diabetes/overview/tests-diagnosis) distinguishes prediabetes from diabetes and discusses confirmation. The UI is a suggestion/override workflow, not a new standalone diagnostic test.
- `prolactin`, ng/mL: M 4.0–15.2; F 4.8–23.3, from [Mayo Prolactin](https://www.mayocliniclabs.com/test-catalog/Overview/800091). Preserve its assay context and note that the ordinary reference is not a pregnancy/lactation-specific interval.
- `ldl`, `hdl`, `tg`, `cholesterol`, mg/dL: LDL desirable <100; TG normal <150; total cholesterol desirable <200. HDL low below 40 in men or 50 in women. Use the [MedlinePlus cholesterol reference](https://medlineplus.gov/cholesterollevelswhatyouneedtoknow.html) and [NHLBI triglycerides](https://www.nhlbi.nih.gov/health/high-blood-triglycerides). These are desirable/decision levels, not one general “normal range” for all cardiovascular risk categories.
- `beta_hcg`, mIU/mL, female-only: <5 negative; 5 to <25 indeterminate; ≥25 suggests pregnancy. The continuous interval notation avoids gaps between the integer ranges shown by [OhioHealth's assay interpretation](https://ohiohealth.testcatalog.org/show/LAB144-1). Indeterminate results show a warning but do not automatically set pregnancy Yes. The physician can still set or override the answer.

For critical warnings use the [University of Iowa critical-values policy](https://www.healthcare.uiowa.edu/path_handbook/appendix/common/un_crit_lab_val.html), preserving its operators/qualifications: glucose <50 or >450 mg/dL; WBC ≤1000 or ≥50000/µL; platelets ≤10000 or ≥1000000/µL; hematocrit ≤18% or ≥55%; hemoglobin ≤6 or ≥22 g/dL, with separate outpatient concern below 7; INR ≥4; and outpatient aPTT ≥50 seconds. If care setting is unknown, label the outpatient qualification. Do not invent critical thresholds for other analytes.

BMI uses unrounded `weight_kg / (height_cm / 100)^2`: underweight <18.5; normal 18.5 to <25; overweight 25 to <30; obesity I 30 to <35; II 35 to <40; III ≥40. The adult ≥25 boundary follows [WHO](https://www.who.int/en/news-room/fact-sheets/detail/obesity-and-overweight).

Use a curated exact catalog-name/ID mapping for BP-lowering drugs, supported by [WHO ATC cardiovascular classifications](https://atcddd.fhi.no/atc_ddd_index/?code=C02&showdescription=yes), including antihypertensive C02/C03/C07/C08/C09, and [AAFP orthostatic-hypotension medication guidance](https://www.aafp.org/afp/2022/0100/p39), including relevant nitrates, PDE5 inhibitors, and alpha-1 antagonists. Preserve formulation/route qualifications and explicit synonyms. Do not guess classes from names, interaction notes, or an LLM at runtime.

## Appendix B — Supplied Source Assets

- Oral model: `project-documents/BNs-new/PO-Antipsychotic-Recommender/BN-PO-Antipsychotic-Recommender.xml`; SHA-256 `f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`. Sixteen binary inputs and twenty-two antipsychotic outcomes.
- Supplied `Lexicomp-cleaned.csv`; SHA-256 `31ce2fb78b0bbf364786e25b2c5f26ccca29ea50657f7faba02bb9fd18828255`. 127,756 explicit pair records; 1,632 distinct drug names. Catalog membership does not establish pair coverage.
- LAI model is unavailable; leave its recommendation list empty under FR-63.
