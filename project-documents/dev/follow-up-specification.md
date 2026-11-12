# Follow-up workflow specification

Status: product requirements reconciled on 2026-11-12; implementation pending.
This describes the planned `follow-up-v1` workflow, not current runtime behavior
or clinical validation. [Implementation plan](follow-up-implementation-plan.md) ·
[source files](../changes/follow-up-2026-11-12/README.md) ·
[code investigation and decision record](progress/follow-up-planning-2026-11-12.md).

## 1. Authority and scope

Explicit clarifications in this specification take precedence. The supplied
flowchart governs antipsychotic decisions; the Word document governs visit forms
and other recommendation sections. All additions/overrides are identified below.

Only newly created follow-up encounters use `follow-up-v1`. New case registration
retains `new-case-v1`; Known case registration and existing open follow-up drafts
retain `legacy-v1`. Never rewrite workflow identity, historical answers, signed
bytes, hashes, model packages or old releases to adopt this specification.

New follow-up has no diagnosis step, LLM call, MCP projection, generated CPT,
background reasoning job, probability slider or probability-acceptance gate.
Its oral-network branches reuse the existing hash-pinned, fully observed oral
model and physician-specific unavailable-drug settings. Current LAI treatment
and no main antipsychotic produce physician review without an automated
antipsychotic change. Other applicable plan sections still generate.

Keep author-only drafts, one open draft per patient, revision/idempotency fences,
autosave and recovery, archived-patient restrictions, explicit physician final
confirmation, immutable signed records and append-only addenda. UI remains
English/Persian, light/dark and Persian RTL. Preserve existing research warnings.

## 2. Historical summary and baseline

Resolve the latest signed encounter across all supported workflow/signature
variants, ordered by `signed_at` descending with a stable ID tie-break. Select
and freeze its identity/hash under the patient creation lock. Do not select only
prior New case records. No prior signed record is a valid empty-history case.

Show visit ordinal, patient names/age, and a structured clinical summary:
sex, schizophrenia context, recorded comorbidities, prior hospitalization count,
current medication names, last visit date, prior PANSS total/informational severity
and recorded self/other harm. More details expands abnormal lab values with units,
dates, tobacco and substance history. Generate prose deterministically from
structured data, never an LLM or free-text plan parsing.

Visit ordinal is the number of prior signed application encounters plus one;
discarded/open drafts and self-reported hospitalizations are not visits. Label
this as recorded visits, not a complete lifetime visit count. Prior visit date
uses encounter visit/creation time; signing time may be shown separately.
Reuse source PANSS informational labels when available; never invent cutoffs or
use historical PANSS as the current recovery answer. Missing/partial historical
scores/risk/count are “not recorded,” not zero, mild or no risk.

Freeze the summary source separately from current editable inputs. Historical
values never change when current history is edited. Display previous lab records
with their source visit dates; older signed visits may supply the most recent
recorded measurement per analyte, but never become today's lab entry. Previous
contraception recommendations may be listed from any signed structured proposal,
with dates; absence of a structured record is not a confirmed negative.

Carry history, BMI, pregnancy/lactation, seizure/QT risks and medication list from
the latest signed visit using explicit variant adapters and field provenance.
Legacy Unknown/missing fields stay unset; required applicable values must be
completed. Do not guess values from retired BN context, notes, plan text or a
different clinical concept. New Case's Other-substance free text is retained
without reclassifying it into newly named categories. No historical effect or
assessment answer is copied into the new assessments.

Optional hospitalization count is an integer >=0, defaults to the prior recorded
count, and remains null when unavailable. It is editable in History and is not
calculated from outpatient visit counts or past inpatient recommendations.
For birth-year records, use the frozen follow-up UTC visit year minus birth year
as follow-up age. Age-only legacy records keep the explicitly recorded patient
age; do not infer birth year. Later demographic edits invalidate relevant unsigned
results while signed demographics remain frozen.

## 3. Steps, fields and validation

Every required field has an accessible required indicator. Incomplete data may
autosave; conditional completeness is enforced before forward navigation,
generation and signing, including direct hash navigation. Back navigation preserves
work. Hidden/disabled values may be retained but cannot activate rules. No new
working-note/page-note surface is introduced.

| Step | Contents and defaults | Completion rules |
| --- | --- | --- |
| 1 — PANSS | Existing 30-item instrument; every item initially unanswered | Empty/partial allowed. Preserve each answer and missing state; aggregate only complete item sets. |
| 2 — Self/other harm | Two Yes/No questions, both No. Self-harm magnifier opens five recent C-SSRS Yes/No questions, all No, independently of self-harm answer; live result | Preserve answers even when disclosure closes. No standalone C-SSRS step. Item 4/5 Yes independently affects care setting. |
| 3 — History and physical examination | Fields below; carried defaults where available | Conditional selections, lab rule, applicable carried booleans and effective BMI required. |
| 4 — Medications | Carried editable list; main drug/formulation below | Empty list allowed. Nonblank unmatched search text must be selected or cleared. |
| 5 — Treatment adverse effects | Seven simple questions below; fresh No defaults | No BARS/SAS/AIMS/acute-dystonia questionnaire or severity gate in this workflow. |
| 6 — Treatment considerations | Recovery initially unselected; fresh adherence/basic-needs Yes, observation/LAI preference No; carried seizure/QT risks; branch questions below | Recovery required. Applicable missing carried risks and reached branch answers required. |
| 7 — Proposed plan | Explicit generation, original output, editable physician plan and confirmation | Current successful proposal and saved nonblank physician final text required. |

### 3.1 History/examination

- Other psychiatric disorders: carried Yes/No; Yes requires one or more depression,
  PTSD, ADHD, Other; Other has optional saved text.
- If depression or PTSD is selected, ask the physician “Currently taking an
  antidepressant?” with required Yes/No, initially unselected. Do not infer from
  drug-name substrings or invent a complete drug-class mapping.
- Lab report available: fresh No. Yes reveals the existing CBC/differential,
  renal, liver, clotting, FBS/HbA1c, prolactin, lipid fields and female-only
  Beta-HCG. At least one applicable finite measurement required; partial reports
  allowed. All fields show units/reference values and source-qualified warnings.
- Carried medical conditions: hepatic failure, renal failure, diabetes/prediabetes,
  hyperlipidemia; lab suggestions and overrides as §4. Hyperprolactinemia has
  an effective lab/prior answer and optional manual override; unresolved status
  must be answered when an oral-model branch needs it.
- Heart failure and current tobacco: carried Yes/No, with missing values to complete.
- Substance use: carried Yes/No. Yes requires at least one of marijuana, opioid
  derivatives, methamphetamine, Other. Other has optional saved text. Display
  dated previous selections separately. Only marijuana drives the existing model.
- Pregnancy/lactation: female-only carried Yes/No; male-hidden fields have no
  effect. Pregnancy supports Beta-HCG suggestion/override.
- BMI: carried height (cm)/weight (kg) and explicit calculated/manual mode or
  manual category. Require an effective category; manual category persists until
  explicitly changed to calculated. Use existing six categories and unrounded
  boundaries; measured height/weight must be finite and positive.
- Carried Yes/No neurological findings: focal deficit, suspected brain lesion,
  possible autoimmune encephalitis. Magnifier explains rapid progression within
  three months, working-memory/consciousness changes, lethargy/personality change.
- Optional prior hospitalization count, as §2.

### 3.2 Medication list and current main treatment

Use the pinned Lexicomp catalog. Search English names after >=3 characters;
select stable catalog IDs, preserve qualifiers, prevent duplicates, append a
trailing empty input, allow deleting any/all entries. Show no interaction-check
button, result or interaction discussion on this step.

If a prior visit used another catalog, carry its names/IDs in the frozen source
projection. Copy into the current list only through an established cross-catalog
mapping. Present unmatched prior drugs as pending items to replace with a current
catalog selection or explicitly remove; require those decisions before leaving
Medications. Never silently drop them or put invalid old IDs into the strict
current list. “Remove all” may explicitly resolve all pending items as removed.

Select one main antipsychotic from this list and specify oral or LAI. Only
supported antipsychotic identities may be selected as main; validate against the
versioned formulation inventory rather than arbitrary names. Multiple medication
entries are allowed, but automation concerns the one main drug. Other entries
remain concomitant medications for all interaction checks.

No main drug is allowed and explicitly means physician review, not a failed
empty-medication step. If a main drug is selected, formulation is required.
Removing the main entry clears its selection and branch-derived answers.
Do not collect numeric dose, frequency, injection date or injection interval.
Use “same dose,” “lower dose” and “next injection,” without inventing schedules.

LAI-equivalence checks use a versioned explicit same-ingredient mapping from
oral formulations to the inventory's `lai:` entries. Short/intermediate acting
injections are not LAI equivalents. If no supported equivalence is present,
follow the graph's oral-model fallback and disclose that this is the supported
inventory, not a universal assertion about available products. Missing catalog
coverage for an injectable remains explicit; never fabricate a catalog ID.

### 3.3 Adverse effects

| Field | Choices/default | Requires intervention | Oral-model factor |
| --- | --- | --- | --- |
| Sedation | No / Yes, tolerable / Yes, requires intervention; No | Only requires-intervention choice | `Sedative_Effects_Concern` true only for requires-intervention |
| Orthostatic hypotension | Yes/No; No | Yes | `Orthostatic_Hypotension_Risk` |
| Akathisia | Yes/No; No | Yes | `Akathisia` |
| Parkinsonism | Yes/No; No | Yes | `Parkinsonism` |
| Acute dystonia | Yes/No; No | Yes | `Dystonia` |
| Tardive dyskinesia | Yes/No; No | Yes | `Tardive_Dyskinesia` |
| Anticholinergic effects | Yes/No; No | Yes | No additional parent exists; affects branch selection only |

All present effects are handled, at every recovery status. Tolerable sedation
alone does not enter the intervention branch. Do not add unsupported model
parents for anticholinergic effects, opioids, methamphetamine or pregnancy.

### 3.4 Considerations and reached-branch questions

Recovery: required, initially unselected, exactly controlled/remission,
partial improvement without remission, no improvement/worse. The physician
answer controls the flowchart; PANSS percentage change is not substituted.

Adherence defaults Yes; basic needs met defaults Yes; observation defaults No;
LAI preference for any reason defaults No. Seizure/QT risks are carried, with
missing required values completed. Ask these additional Yes/No questions only
when the decision path reaches them; initially unselected:

- Need to switch medication for any reason?
- Is dose reduction possible? (Controlled continuation branch, or acute dystonia.)
- Are treatment-resistance criteria present? (No improvement/worse only.)
- Does suicide/other-harm risk persist? (Uncontrolled/partial, adherent path.)
- Has acute dystonia resolved after the indicated measures? This records the
  physician's assessed response; software never marks resolution from advice alone.

Retain inactive branch answers for audit/recovery if useful, but exclude them
from active logic and completeness. Source changes must recompute reached
questions; stale answers cannot silently activate a different branch.

## 4. Lab suggestions, inherited answers and overrides

Reuse the exact versioned lab definitions and source limitations from
`content/new-case/v1/labs.json` and [requirements Appendix A](user-requirements.md#appendix-a--medical-reference-content-for-new-case).
Pin bytes/hashes; do not mutate New case content to add follow-up behavior.
Hepatic/renal/glucose/lipid/pregnancy suggestions use those operators, units and
sex qualifications. Prolactin similarly establishes hyperprolactinemia; Beta-HCG
5 to <25 remains indeterminate, >=25 suggests pregnancy. Do not invent new
critical thresholds or silently substitute universal diagnostic criteria.

For each condition store inherited answer/provenance, current measurements and
findings, and current manual override/provenance independently. Priority is
current explicit manual Yes/No > current abnormal suggestion Yes > inherited
answer. If neither inherited nor current evidence establishes an answer, keep
unset until physician completion where required. A current normal prolactin
may establish No when there is no inherited Yes or current override; no prolactin
value and no prior answer cannot establish No.

Previous manual answers are historical baseline values, not newly attributed
actions in this visit. Current manual overrides persist through autosave, reload,
new measurements and report-toggle changes until explicitly reset to automatic.
Turning a new lab report off removes its active triggers but does not erase
current overrides or inherited findings. Show triggering lab/value/unit and the
automatic-change explanation beside the question. Never let recomputation erase
an explicit current No. Preserve all triggering findings, not just the first.

## 5. Decision procedure and priorities

Run a deterministic evaluator with traceable rule/node IDs. Evaluate non-drug
sections independently. For antipsychotic decisions use this order:

1. No main antipsychotic or current main is LAI: physician-review outcome,
   no automatic antipsychotic change. Still show effects and applicable other sections.
2. For current oral treatment, evaluate all effects. Accumulate adjunct advice.
   If any effect requires oral-model replacement, it wins over clozapine, LAI
   conversion, continuation and dose reduction. Record the other branch reasons
   as superseded reasoning, not simultaneous drug recommendations.
3. If effects do not require oral replacement, use the symptom/adherence
   pathway below, retaining adjunct advice. For controlled symptoms with only
   adjunct-treated effects, follow the effect branch's terminal outcome rather
   than inventing an extra switch preference branch.
4. Apply new-antipsychotic availability and D/X exclusion. Excluded direct
   clozapine/LAI outputs become physician review; do not silently substitute
   another chart branch. Ranked oral outcomes use §6.

### 5.1 Original oral pathway

| Condition | Next decision or result |
| --- | --- |
| Controlled, no intervention-requiring effects, switch not needed | Dose reduction possible? Yes: same drug, lower dose. No: same drug, same dose. |
| Controlled, no intervention-requiring effects, switch needed | LAI preference No: oral model. Yes: same-drug LAI if supported equivalent, otherwise oral model. |
| Partial improvement | Adherence decision below; do not ask treatment resistance first. |
| No improvement/worse | Resistance Yes: clozapine. Resistance No: adherence decision. |
| Partial/no-improvement path reaching adherence, not adherent | Same-drug LAI if supported equivalent; otherwise oral model. LAI preference does not suppress this chart branch. |
| Partial/no-improvement path reaching adherence, adherent | Persistent harm risk Yes: clozapine. No: oral model. |

Treatment resistance and persistent risk are physician inputs, not invented
numeric thresholds. A direct clozapine result is a proposal subject to exclusions
and physician confirmation; it supplies no dosing or monitoring protocol.

### 5.2 Effect branches and confirmed extensions

| Effect | Adjunct advice | Antipsychotic replacement |
| --- | --- | --- |
| Acute dystonia | Anticholinergic; dose reduction if physician says possible | If physician says resolved: none from this effect. If unresolved: oral model. |
| Parkinsonism | None added by the supplied graph | Oral model |
| Akathisia | None added by the supplied graph | Oral model |
| Tardive dyskinesia | VMAT2-inhibitor advice | None from this effect alone |
| Sedation requiring intervention | None specified | Oral model |
| Orthostatic hypotension | None specified | Oral model |
| Anticholinergic effects | None specified | Oral model |

Do not choose specific adjunct drugs or doses, or invent adjunct DDI assertions
without selected catalog identities. Merge duplicate adjunct advice. A resolved
dystonia or VMAT2 terminal means no antipsychotic change from that effect, not
termination of care-setting/lab/counseling/interaction sections. Confirmed
extensions are: three missing effect branches go to oral model, effects apply
at every recovery status, adjuncts accumulate, and effect-driven oral replacement
wins over competing direct changes.

## 6. Oral model and medication interactions

Use the existing trusted oral XML SHA-256
`f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`,
16-parent order and 22 outcomes. Exact observed-row lookup has no LLM or
marginalization of missing factors. The full model distribution is retained.

| Parent | Follow-up source |
| --- | --- |
| `Smoking` | Current tobacco answer |
| `Marijuana_Smoking` | Current substance use enabled AND marijuana selected |
| `Hepatic_Failure`, `Renal_Failure` | Effective condition flags |
| `Sedative_Effects_Concern` | Sedation requires intervention |
| `Seizure_Risk` | Carried/completed current seizure-risk answer |
| `Glucose_Abnormalities`, `Hyperlipidemia` | Effective condition flags |
| `Weight_Gain_Risk` | Effective BMI category overweight/any obesity |
| `QTc_Prolongation_Risk` | Carried/completed current QT-risk answer |
| `Orthostatic_Hypotension_Risk` | Current orthostatic-hypotension effect; do not import New case's BP-drug/heart-failure inference |
| `Hyperprolactinemia` | Current lab/prior effective answer or required physician override if unresolved on an oral-model branch |
| `Tardive_Dyskinesia`, `Dystonia`, `Parkinsonism`, `Akathisia` | Corresponding current effect Yes/No |

All 16 factors must be established on a reached oral-model branch. Do not block
continuation/LAI-review outcomes solely for an unused model factor, and do not
replace missing values with New case's four movement defaults.

Interaction policy:

1. Generate a full unordered-pair report for current medications using the pinned
   Lexicomp release, including risk letters and source notes.
2. For a replacement, exclude the current main oral identity from eligible
   replacement candidates: the graph calls for a new drug. Keep it in the current
   list used for candidate-versus-medication interaction checks because overlap
   may occur. Do not assess an identity against itself or change the current list.
3. Over the complete 22-outcome distribution, remove zero-probability, unavailable,
   same-current-drug and any candidate with at least one explicit D or X pair.
   Check all remaining outcomes before selecting up to five; backfill from lower
   ranks. Preserve original probabilities, tie order and original rank; never
   renormalize percentages or invent a nonempty fallback.
4. Check direct new clozapine and same-drug LAI suggestions as well. Formulation
   availability is independent of the oral identity. Use established catalog
   mappings; missing mappings/pairs are coverage unavailable, not explicit A.
5. Direct exclusion or no eligible oral option produces an explained
   physician-review outcome. Missing coverage is prominently flagged and is not
   itself an explicit D/X exclusion. Existing unchanged treatment is shown with
   current interactions rather than silently stopped by a “new suggestion” filter.

Store excluded candidates, reasons, offending pairs and coverage limitations.
Do not turn lexical notes or the edited final plan into additional medication
identities. The system cannot certify drugs typed only into the final text.

## 7. Other proposed-plan sections

| Section | Rule |
| --- | --- |
| Care setting | Inpatient if self-harm, other-harm, recent C-SSRS item 4/5 Yes, basic needs unmet, or observation Yes; otherwise outpatient. Record every triggering reason. |
| Antidepressant | Depression/PTSD selected AND physician confirms no current antidepressant: recommend considering an antidepressant. Otherwise no addition recommendation. No specific antidepressant or dose is chosen. |
| Smoking | Recommend cessation only if current tobacco Yes. |
| Routine labs | All patients: CBC, Diff, Na, K, BUN, Cr, AST, ALT, ALP, PT, PTT, INR, TSH, FBS, HbA1c. Lab recommendations are distinct from the available lab-entry fields. |
| Beta-HCG | Female, not pregnant, age <=55. |
| Imaging | Brain MRI if focal deficit OR suspected brain lesion OR possible autoimmune encephalitis. Include “treatment must not be delayed for imaging.” Age alone does not trigger follow-up MRI. |
| Breastfeeding | Female and breastfeeding: advise stopping breastfeeding and pediatric consultation for formula, as supplied. |
| Pregnancy | Female and pregnant: obstetric consultation during pregnancy, neonatal consultation after birth and lowest effective dose. |
| Contraception | Hide section for males. Show prior structured recommendations with dates. Counsel about pregnancy intention/contraception only when female, nonpregnant, nonbreastfeeding and age <=55. This clarification overrides the Word document's missing pregnancy/age guards. |
| Next visit | Same oral drug: four weeks, including dose reduction or uncontrolled symptoms. New oral drug: 2–4 weeks. Newly proposed LAI or existing LAI: next injection; no invented date. No main drug or excluded/no-eligible replacement: physician determines timing. |

An oral-model candidate list implies a proposed oral change, not a confirmed
prescription. The final physician plan may differ; keep proposal timing and
physician final text distinguishable and never parse edits as structured adoption.

## 8. Proposal, final plan and historical compatibility

Generate one immutable proposal from validated saved inputs/content. Store raw
and effective state, inherited provenance, current manual attribution, all 16
factors when used, full probabilities, chosen action and decision trace, adjuncts,
all rule sections, interactions/exclusions/coverage, availability snapshot and
baseline/model/release/content identities. A non-model branch explicitly records
`not_run` plus reason, rather than fake probabilities.

Freeze the actual used lab/rule/factor/formulation/equivalence definitions and
source metadata in the proposal, not only their hashes. Keep versioned package
files available for supported unsigned drafts and validate archived proposals
against their frozen definitions, not the currently active global package.

Physician-review outcomes are supported successful proposals with explicit limits,
not algorithm failures or blank successes. They can be finalized with a saved
nonblank physician plan and explicit confirmation. Corrupt/missing required
model/content or lookup failures are retryable failures; they cannot be signed
as physician-review outcomes to bypass generation.

Keep original proposal separate from physician text. Seed final text once and
offer explicit replacement on regeneration; preserve edits through reload,
language changes, errors, conflicts and regeneration. Relevant input, demographic,
availability or pinned-content-integrity changes make an unsigned result stale.
Language and final-text edits do not. Read historical proposals using frozen
content, not the latest global release. A later global release does not silently
repin an existing draft.

Signing rechecks author/activity/archive status, encounter/plan revisions, required
inputs and proposal ownership/freshness atomically. Freeze a new supported signed
variant, release the draft slot, and audit once; lost-response retries return the
same result. No legacy probability acceptance or old effects reconciliation gate
is fabricated for the new workflow. Preserve legacy sign requirements on old drafts.

Shared chart, printable report, addenda and full backup/restore must support mixed
legacy/New case/new-follow-up histories without rewriting old snapshots. Exports
show decision traces and fixed-model evidence where applicable, never fictitious
CPT adjustments. Normal packaged startup includes the new versioned content.

## 9. Recorded product clarifications

All following choices were explicitly confirmed on 2026-11-12; they are product
instructions, not independent medical validation:

- Flowchart precedence, oral-only antipsychotic automation, explicit physician
  branch questions and the three-way recovery mapping.
- Seven simple effects, all Yes values intervention-requiring except tolerable
  sedation; three missing branches use oral model; effects apply at every recovery
  state; adjuncts accumulate and oral replacement wins over clozapine/LAI.
- One selected main antipsychotic/formulation; no numeric dose/schedule; no-main
  review. Fixed oral BN and unavailable settings, current effect factors, no LLM.
- D/X exclusion for every new antipsychotic, comparisons including the outgoing
  main drug because overlap may occur; missing coverage remains explicit.
- Latest signed baseline across variants, old-draft/signature preservation,
  missing mandatory history completion and fresh five-No C-SSRS.
- Existing lab definitions, inherited answers plus new suggestions and persistent
  overrides; unresolved hyperprolactinemia requires a physician answer for oral BN.
- Pregnancy/age contraception guards; unchanged oral drug four-week timing;
  optional hospitalization count and conditional physician antidepressant confirmation.

Technical execution choices and verifications are in the implementation plan.
