# User Requirements

## X-INSIGHT

X-INSIGHT is a research prototype helping physicians/psychiatrists explore treatment options for schizophrenia cases.

**Users:** One administrator and fewer than 10 physicians, with a shared patient pool. The application is English only.

**Physician flow:** Register patient → assess → review the initial treatment proposal and its Bayesian-network probabilities → optionally adjust probabilities and compare recalculated results → finalize and sign the secondary treatment plan.

**Follow-up flow:** Re-assess → review the initial proposal and its Bayesian-network probabilities → optionally adjust probabilities and compare recalculated results → finalize and sign the secondary treatment plan.

### Terminology

- **Initial treatment proposal:** The system-generated proposal incorporating drug–drug interaction findings and Bayesian-network recommendations.
- **Secondary treatment plan:** The final plan reviewed, edited, accepted, and signed by the physician.
- **Original CPT values:** The validated conditional probability table values estimated by the LLM through the MCP-enabled workflow for a particular clinical-question run.
- **Adjusted CPT values:** Encounter-specific CPT values produced by physician slider changes and automatic proportional redistribution.
- **Original result:** The network output and templated recommendation generated using the original CPT values.
- **Adjusted result:** The network output and templated recommendation generated using the adjusted CPT values.

MCP provides the tool-access environment. The LLM estimates CPT values; the application validates those values and executes the Bayesian networks.

## Functional Requirements

### Users and Login

**FR-01:** Users log in with a role, username, and password and are redirected to the corresponding role dashboard. After a psychiatrist logs in successfully, display a warning that the application is a research prototype and must not be used as the sole basis for treating patients. The Register button displays “Contact administrator”.

**FR-02:** The application has one administrator account with the immutable username `admin` and the default password `admin`. No password complexity rules apply.

**FR-03:** The administrator can change their own password, toggle the dark/light theme, manage API settings (key, base URL, model), manage Bayesian networks, manage physician accounts, view/export patients, perform backup/restore, and view the audit log.

**FR-04:** The administrator creates physician accounts; self-registration is unavailable. The administrator can edit physician credentials and deactivate accounts. Deactivation retains existing records; unfinished drafts are discarded only after confirmation. Physicians can change their own password but cannot otherwise manage account credentials.

### New Patient Registration

**FR-10:** The Demographics page collects required first and last names (letters only), sex (M/F), age (18–99), and Patient ID (a 10-digit string). Patient IDs are unique application-wide, and leading zeros are preserved. Before creating a patient, the application checks the ID against all existing patient records, including archived records, and rejects duplicate registration. Visit datetime is logged automatically. Patient status is recorded as first-time or established. Next remains disabled until required values are valid.

**FR-11:** The Diagnosis page provides standard DSM-5-TR schizophrenia diagnostic criteria and a live threshold indicator. Below-threshold assessments can be saved and treatment generation can proceed with a warning. Bypassing the assessment without a reason is allowed.

**FR-12:** The Severity page provides the standard PANSS questionnaire. Items start unanswered. Skipping the assessment records “not assessed”. Scores are computed only when all items required for the corresponding score are complete; unanswered items receive no default minimum score.

**FR-13:** The Suicide page provides the standard C-SSRS questionnaire. Items start unanswered. Skipping the assessment records “not assessed”. Scoring or assessment results are computed only when the required items are complete.

**FR-14:** The History page uses structured fields. Medications are selected from a bundled demo catalog, without dose, unit, route, frequency, or active/stopped status. Interaction reports use a local bundled database. Drugs without interaction coverage are marked “coverage unavailable”.

**FR-15:** The initial treatment proposal is system-generated and incorporates the DDI checker and Bayesian-network recommendations. For each completed clinical question, the physician can inspect all CPT values, adjust them using sliders, and review the recalculated recommendation before finalizing the secondary treatment plan. The application records the physician’s plan edits and sign-off, while retaining the original system-generated proposal separately from physician-adjusted recommendations.

**FR-16:** Every page supports timestamped, physician-attributed notes. Notes never influence algorithms. Drafts, including CPT adjustments and their calculation state, are automatically saved and resumable. Explicit Discard requires confirmation.

### Follow-up and Records

**FR-20:** A follow-up encounter allows the physician to update the phone number, re-assess severity and suicide, update history and medications, record adverse effects, review the initial proposal, inspect and adjust CPT values, edit the plan, and sign the secondary treatment plan. The encounter is logged.

**FR-21:** Adverse effects include tardive dyskinesia, akathisia, parkinsonism, and acute dystonia. Each supports present, absent, and not-assessed states, plus severity. Require the full adverse-effect questionnaire only when its corresponding effect is present, with versioned item responses, completeness and source-defined scoring. Instrument forms, administration requirements and mappings to network variables require review; incomplete/not-assessed responses do not produce guessed scores. The acute-dystonia form is named “Acute Dystonia Dx Criteria”; concrete form/severity mappings and the complete AIMS source remain subject to review in `content/review-ledger.md` §2. Absent/not-assessed effects do not require questionnaire completion.

**FR-22:** Any physician may create encounters and update demographics. Only the draft author may edit or sign that draft, including changing its CPT sliders, resetting its probabilities, or accepting its results. Signed encounters are immutable, including their accepted CPT values and network results. Corrections use dated, attributed addenda; follow-ups are new encounters.

**FR-23:** The patient list supports search by name or Patient ID and filtering by clinical status. The administrator can archive and unarchive patients. Permanent deletion is unavailable in v1.

### Reasoning Pipeline

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

**FR-40:** Exports include CSV patient/physician lists and a printable HTML report for each patient. The report includes signed treatment plans and, for each clinical question, the original CPT values and result, final accepted CPT values and result, whether probabilities were physician-adjusted, and the associated physician and timestamps. PDF export is unavailable in v1.

**FR-41:** The administrator can download a full backup containing the database and network XML and restore it from a file. The backup preserves original runs, CPT adjustments, calculation results, signed snapshots, audit records, and the versioned definitions required to reproduce saved network executions.

**FR-42:** The audit log is append-only and viewable by the administrator. It records logins, network runs, plan sign-offs, and administrator actions. It also records completed slider adjustments, automatic probability redistribution, resets, recalculation outcomes, and acceptance of final probabilities, with the actor, timestamp, patient, encounter, clinical question, and relevant run or revision identifiers. Adjustment records preserve the before-and-after CPT values.

**FR-43:** API settings support an OpenAI-compatible API key, base URL, and model name. Concurrent users are supported through queued LLM calls and fail-soft errors. Slider-triggered network recalculations run within the application and do not require additional LLM calls or MCP tool requests.

### Interactive CPT Review and Adjustment

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

## Non-Functional Requirements

**NFR-01 — Deployment:** The application is deployable in self-hosted environments and on Linux cloud VPS infrastructure and supports concurrent users. It remains a flexible/adaptive research prototype.

**NFR-02 — Security:** Prototype-basic security includes authentication, role-based access, and draft-author restrictions. Sessions have no timeout. HTTPS is required outside localhost. PHI hardening is outside v1 scope. Probability modification and sign-off permissions are enforced by the application, not only by hiding interface controls.

**NFR-03 — Usability:** The application is English only and targets desktop use in the latest Chrome and Firefox versions. It supports a theme toggle, explicit validation, and confirmations. CPT controls use readable labels, show exact percentages alongside sliders, support keyboard adjustment, and make automatic changes to other states visible. Large CPTs remain navigable through node and row grouping without excluding values.

**NFR-04 — Reliability and reproducibility:** Drafts and CPT adjustments survive failures. Bayesian-network execution is reproducible for the same patient-input snapshot, complete CPT values, network version, and execution configuration. Reproducing recommendation text additionally requires the same template version. Reproduction uses saved CPT values rather than requesting new estimates from the LLM. Original runs and signed snapshots remain immutable.

**NFR-05 — Maintainability:** Networks, prompts, and templates are versioned. XSD validation is complemented by runtime probability validation. The application maintains an audit trail for original runs, physician adjustments, recalculations, resets, and accepted results. Probability redistribution and execution rules are deterministic and consistently applied across all clinical questions.

**NFR-06 — Responsiveness and isolation:** Slider interaction remains responsive while recalculations run. The interface displays calculation progress and prevents acceptance of stale results. Recalculations remain isolated to their encounter and clinical question, support concurrent users, and do not consume LLM requests.