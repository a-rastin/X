# Follow-up workflow analysis and planning — 2026-11-12

## Status and scope

Requirements reconciliation and documentation are complete. The
[normative specification](../follow-up-specification.md) and
[implementation plan](../follow-up-implementation-plan.md) are the implementation
handoff. All eight implementation packages remain pending. Application code is
unchanged; this session does not claim runtime or clinical validation.

Source baseline: `b153b7b32f624a0c907362a7fe0bac7a6a67b181`.
Read the [progress index](README.md), [New case handoff](new-case-implementation-2026-11-09.md),
[UI handoff](ui-implementation-2026-11-11.md), original [follow-up checkpoint](s14.md),
and [reviewed workflow release](s39.md#follow-up-2026-10-22--owner-confirmed-workflow-integration-release).
Older handoffs describe their original scope; current source was inspected.

## Supplied evidence

- `follow-up.docx`: seven follow-up steps, historical summary, defaults,
  carry-forward history, labs, medication entry, seven adverse effects,
  treatment considerations and proposal sections. SHA-256:
  `a416959cad757f7aa1ae13f8e767cf7e80fcf4385e256e368f645750b449de20`.
- `diagram (2).mmd`: oral-treatment decision graph and an unconnected current-LAI
  branch. SHA-256:
  `a2ace28013c72a24d90fd9037e1f92869dfe25948ecbf5ba6721603cae02c40a`.
- The DOCX has no external hyperlink target for its reference to the oral-network
  procedure. Model selection therefore needs explicit reconciliation.

Original source bytes and the supplied text extraction (trailing whitespace
normalized) are preserved in the
[source archive](../../changes/follow-up-2026-11-12/README.md); archive hashes
match the attachments. Clarifications below resolve the missing model reference.

## Confirmed product decisions

Confirmed in the requirements discussion on 2026-11-12:

1. The flowchart controls antipsychotic decisions. The Word document controls
   visit forms and other recommendations.
2. Implement the supplied oral-treatment pathway. A patient currently treated
   with LAI receives physician review without an automated drug-change recommendation.
3. Add explicit physician questions for missing branch inputs, including need
   to switch, ability to reduce dose, treatment resistance and resolution of
   acute dystonia. The required three-option recovery question maps to controlled
   symptoms, partial improvement and no improvement/worsening. PANSS is not a
   substitute for this explicit answer.
4. Every Yes adverse effect requires intervention except tolerable sedation.
   Handle all present effects. Sedation requiring intervention, orthostatic
   hypotension and anticholinergic effects route to the oral network.
5. Select one main antipsychotic from the current medication list and record
   oral or LAI formulation. Do not collect numeric doses or injection schedules.
   Use same-dose/lower-dose/next-injection wording. With no main antipsychotic,
   request physician review rather than inventing a treatment pathway.
6. Oral-network branches reuse the fixed oral model, current follow-up
   adverse-effect inputs and physician-specific unavailable-drug settings.
   No LLM estimation or probability sliders apply to this new workflow.
7. D/X exclusion applies to every newly suggested antipsychotic, including
   direct clozapine and LAI outputs. Excluded direct outputs explain the reason
   and request physician review. Missing interaction coverage stays explicit.
8. Automatically use the latest signed visit across all workflow types. Existing
   drafts and signatures keep their original workflow. Missing/Unknown mandatory
   historical fields require physician completion, not a manufactured No.
9. New follow-ups use five recent C-SSRS Yes/No items, initially No, and only
   the seven simple adverse-effect questions. Legacy questionnaire gates do not
   apply to the new workflow.
10. Reuse versioned New case lab units, reference ranges and critical warnings.
    Carry prior condition answers forward; new abnormal measurements suggest Yes;
    explicit physician overrides remain authoritative. Hyperprolactinemia derives
    from prolactin with an optional manual override.
11. Accumulate adjunct actions for simultaneous effects. A switch result takes
    precedence over continuing the same drug. Address effects for every recovery
    status, extending the original graph's controlled-symptoms-only placement.
12. Contraception counseling applies only to nonpregnant, nonbreastfeeding women
    aged 55 or younger. Show any prior recorded contraception recommendation.
13. Recommend four weeks whenever the oral drug itself stays the same, including
    dose reduction and uncontrolled symptoms. Oral drug replacement retains the
    document's 2–4-week advice; LAI retains next-injection wording.
14. D/X checks include all currently listed medications, including the main
    antipsychotic being replaced because treatment overlap may occur. Show the
    offending pairs.
15. Add an optional carried-forward hospitalization count, with missing distinct
    from zero. When depression/PTSD is selected, obtain physician confirmation
    of current antidepressant use rather than inventing a catalog classification.
16. Effect-driven oral replacement always takes precedence over clozapine and
    LAI conversion; keep applicable adjunct advice.
17. Model sedation concern means sedation requiring intervention. Orthostatic
    risk and the four movement-effect factors use their current effect answers.
    On a reached oral-model branch, missing lab/prior hyperprolactinemia requires
    an explicit physician answer; do not default the missing factor to No.

## Requested visit shape

Before the steps, show visit ordinal, names, age and a clinical summary of
comorbidities, admission history, current medications, last visit, prior PANSS
and harm risk. Expand details for abnormal labs and tobacco/substance use.
Historical information needs visit dates and must remain distinct from current findings.

1. PANSS: initially unanswered; partial or absent answers may be saved and continued.
2. Self/other harm: two No-default booleans; self-harm magnifier opens C-SSRS
   independently of the answer and calculates the instrument result.
3. History/physical: carry previous answers; psychiatric and substance conditional
   selections; new lab report defaults No, at least one value when Yes;
   dated previous labs; lab-triggered condition suggestions with manual override;
   female-only pregnancy/lactation; required measured or manually selected BMI;
   neurological questions and encephalitis help.
4. Medications: editable carried list, catalog suggestions after three English
   characters, trailing blank entry, empty list permitted; interaction results
   belong on the proposal step.
5. Adverse effects: sedation (No/tolerable/requires intervention), plus orthostatic
   hypotension, akathisia, parkinsonism, acute dystonia, tardive dyskinesia and
   anticholinergic effects (Yes/No, initially No).
6. Considerations: required initially unselected recovery answer; adherence and
   basic needs default Yes; observation and LAI preference default No;
   seizure/QT risks carried from the previous visit; conditional graph questions.
7. Proposal: care setting, graph outcome, antidepressant assessment, tobacco,
   routine/pregnancy labs, conditional MRI, pregnancy/lactation counseling,
   Lexicomp interactions and D/X exclusions, contraception and next-visit advice.

Incorrect references to step 5/7 in the Word document must be replaced with
the corresponding named field in the reconciled specification.

## Verified current code and gaps

| Area | Current implementation | Planning consequence |
| --- | --- | --- |
| Workflow dispatch | `web/src/features/encounters/EncounterPage.tsx` dispatches `new-case-v1` separately; `FOLLOW_UP_STEPS` is Diagnosis/PANSS/C-SSRS/History/Effects/Medications/Proposal. | New follow-up needs explicit versioned dispatch and the requested step order; it cannot be implemented by reordering labels alone. |
| Follow-up creation | `cases/encounters.py:create_open_draft` automatically selects a latest snapshot only among rows with `new_case_proposal_id`; carried New case history values are empty. | Resolve the latest signed encounter across all supported variants; adapt history explicitly rather than selecting an older New case or inventing legacy answers. |
| Baseline preview | `cases/chart.py:read_followup_baseline_for_author` returns current editable History values under the baseline label. | Historical summary requires a frozen signed-source projection, separately from editable current values. |
| Carry-forward | `build_followup_draft_data` carries catalog IDs/history provenance and fresh PANSS/C-SSRS. | Preserve historical/current distinction; define missing historical values and per-field dates. |
| Assessments | PANSS reports complete aggregates only, informational severity bands; legacy C-SSRS normalizes missing to Unknown; New case explicitly defaults recent items No. | Keep optional PANSS; confirm follow-up C-SSRS defaults and summary labels without creating treatment thresholds. |
| Effects | `cases/effects.py` collects four present/absent/not-assessed effects, questionnaires and severity. | Requested seven simple questions differ materially; resolve whether legacy questionnaire gates survive for new follow-ups. |
| Shared form logic | `cases/new_case.py`, `new_case_rules.py`, `web/src/features/new-case/NewCaseSections.tsx` implement typed raw/effective state, labs, BMI, overrides and conditional forms. | Reuse focused primitives, with follow-up-specific rules; New case is guarded to registration and cannot simply receive a follow-up encounter. |
| Rule differences | New case MRI includes age over 55, antidepressant advice ignores existing antidepressants, contraception differs, next visit is 2–4 weeks. | Follow-up requires its own section policy rather than calling New case rules unchanged. |
| Oral model | `models/oral_antipsychotic.py` validates a fixed/hash-pinned 16-factor model, preserves all 22 probabilities and chooses five nonzero available outcomes. | Model reuse requires explicit confirmation; current follow-up effects must replace New case's movement-effect defaults. |
| Candidate interactions | `cases/new_case_candidates.py` annotates the top five without D/X filtering. | Follow-up must check/filter the full eligible distribution before selecting the requested number, preserving probability/rank provenance. |
| Lexicomp | `ddi/checker.py` exposes risk letters and notes; a missing pair is `coverage_unavailable`, not an explicit A. | Keep missing coverage distinguishable and decide direct-output exclusion policy. |
| Treatment identity | Medication entries have catalog IDs only; formulation inventory distinguishes oral, short/intermediate injection and LAI groups. | Need explicit current main antipsychotic/formulation and a supported LAI-equivalence mapping; generic injection is not sufficient. |
| Proposals/signing | `new_case_plans.py` provides immutable proposals, freshness and v4 signing; legacy signing requires successful/accepted LLM runs. | Follow-up graph output needs its own supported immutable proposal/signing variant or a carefully scoped shared extension, not a legacy gate bypass. |
| Recovery/reporting | `cases/tables.py`, `operations/backup.py`, `operations/restore.py`, `cases/reporting.py`, chart and signed views support existing variants. | Any new proposal/signature needs migration, API/report/read support, archive validation and mixed-history recovery coverage. |

Paths under `cases/`, `models/`, `ddi/`, `operations/` above are relative to
`backend/src/x_insight/`.

## Final documents and implementation boundary

All presented product MCQs are resolved. No unresolved product branch is delegated
to an implementation agent as an invented clinical rule.

- Specification: field/default/completeness rules, frozen historical/current
  separation, original decision paths and confirmed overlays, factor mapping,
  full-distribution exclusions, independent recommendations and final confirmation.
- Plan: explicit `follow-up-v1` identity, proposed raw/API/proposal/v5 signature
  contracts, additive migration and eight dependency-ordered packages A–H, each
  with files, ordered steps, meaningful tests and completion gates.
- Entry points: requirements FR-70–FR-79, system design/architecture, plan/tasks,
  root README and current user/New case/legacy proposal docs link the future
  scope without claiming it has shipped. The later research-development revision
  below removes inherited human approval gates from this new work; historical
  ledger entries retain their original record.
- Rationale: [follow-up context](../../../context/follow-up.md) records historical
  versioning and why outgoing-drug interaction checks remain. Two investigated
  source summaries/index entries were refreshed with verified source hashes.

## Next work and handoff

An implementation agent should start with package A in the linked plan, after
reading current Git status and source/migration head. Packages A–H are not
implemented. The current follow-up stays legacy until those changes are built.
Preserve existing drafts and signed records. Database operations, commits and
deployment remain within the requested implementation scope; no separate legal,
source, author-evidence or medical-approval step is required. The research policy
below supersedes earlier clinical/source approval language for this work.

Documentation verification: whitespace, local Markdown link/anchor targets,
source-archive SHA-256, preserved original graph, FR IDs, rationale record fields
and memory JSON/source hashes pass local checks. Final validation covered 22
Markdown files and 379 local links/anchors, consistent new table columns,
FR-70–FR-79, all 16 model factors and all eight package headings. The optional Keep the Why
linter was not installed/run at the explicit request to skip installation;
`local-lint: no` was recorded in the local personal settings outside Git.
No application tests/browser journeys are claimed for this documentation-only
change. No database migration, implementation, commit or deployment performed.

## Commit handoff

The documentation and source-archive changes are prepared for one conventional
commit on `main`, as explicitly requested after reconciliation. Documentation
links, attachment hashes, memory metadata and whitespace are rechecked before
committing. The staged check found a trailing tab in the derived text extraction;
only that extraction was whitespace-normalized, retaining original DOCX and graph
bytes. Application tests are not rerun for this documentation-only scope;
implementation packages A–H remain pending. No push or deployment was requested.

## Research development policy revision — 2026-11-12

The user specified that this is a research app and that implementing or changing
its medical decisions/knowledge must require no legal confirmation, author-supplied
evidence, source confirmation or approval from another person. The specification,
implementation plan, source-archive guide, requirements and plan/task entry points
now state that policy. The content-review ledger explicitly excludes this new
work from inherited approval/dossier gates. Package A includes a regression check
that structurally valid content works without human approval metadata.

References and rationale metadata remain descriptive; missing bibliographic
information is recorded as unavailable without blocking development. Automated
schema/hash/behavior checks and the previously selected visit questions,
physician-review outcomes and final-plan signing behavior remain in scope.
Original attachments and historical approval records remain unchanged. No runtime
code or database schema was changed. Implementation packages A–H remain pending.

Validation: edited Markdown local links/anchors, rationale record fields and
whitespace checked without installing the optional linter. Application tests
were not run for this documentation-only revision.
