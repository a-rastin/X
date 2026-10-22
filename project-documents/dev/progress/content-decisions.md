## Content Q/A decisions — 2026-10-04

- S12 may draft structured exposure, duration, trial-adequacy, prior-response, and monitoring fields for review; dose, route, and frequency remain excluded. Concrete inventory and severity definitions still require review.
- Reviewed limited-coverage DDI releases are permitted before full-corpus review; explicit coverage declarations and `coverage_unavailable` behavior remain required.
- Current inventory after scope revisions: five registration questions (R2, R4–R7) and six follow-up questions. Active question sessions are S27 and S29–S38; remaining identifiers retain their meanings. Development requirements, architecture/design, plan, tasks, ledger and blocker counts reflect eleven packages. No concrete package was approved.

### Content Q/A follow-up — 2026-10-04

- The short per-effect severity-definition proposal needs clarification: the earlier “No” was interpreted as rejection, and a subsequent question asks why. No rejection rationale or replacement severity/instrument contract is established. Full instruments and severity removal have not been authorized.
- The earlier scope reduction resulted from an unavailable Bayesian network; subsequent removal leaves five registration and six follow-up questions. Scope rationale and its history are recorded in `context/content-scope.md`.

### Current content scope — 2026-10-04

- Registration contains five questions (R2, R4–R7), with six follow-up questions and eleven packages total. Active authoring sessions are S27 and S29–S38. Removed questions have no workflow, review, activation, or generation dependency; remaining identifiers remain stable.
- Severity proposal and its rejection rationale require clarification; no clinical reason for rejecting it has been established.

### Adverse-effect questionnaire decision — 2026-10-04

- Full standardized questionnaires are selected over short custom severity definitions and network-state-only inputs. S12 authors complete versioned instrument contracts and independent examples for owner review; no concrete instrument package is approved. This review is outside the S08 exception.
- Supplied criteria contain BARS/SAS instrument sections; the tardive-dyskinesia criteria reference AIMS without a complete form. Complete AIMS sourcing, acute-dystonia instrument/source and required-completion applicability remain open. No new score bands, diagnostic thresholds or missing-data rules were chosen.

### Adverse-effect form applicability and naming — 2026-10-04

- Full questionnaire completion is required only for the corresponding present effect; absent/not-assessed effects have no completion requirement. This resolves the earlier applicability question.
- The acute-dystonia form name is “Acute Dystonia Dx Criteria”. Supplied acute-dystonia criteria are the drafting lead; concrete items, completeness, severity and network mappings remain for review. Naming does not establish a validated numeric scale or authorize an invented score.
- Complete AIMS sourcing and concrete instrument-package review remain open. No concrete package was approved.

### Pharmacotherapy scope and BN-06 retention — 2026-10-04

- Registration pharmacotherapy preserves BN-04's established-treatment review scope; initial medication selection is outside this question. Retained graph/mappings and applicability when no established treatment exists still require review.
- BN-06-derived continuation content is retained within the experimental/educational scope. Derived packages record the owner decision and the supplied STATEMENT-06 electronic decision-support/quality-measure limitation; the limitation remains explicit. Concrete graph, templates and wording still require review; no package was approved.

### Pharmacotherapy applicability and LAI scope — 2026-10-04

- No established treatment does not mark registration pharmacotherapy not_applicable. The question remains applicable; S27 defines source-compatible mappings for that case. Missing/unknown required network inputs still pause rather than being guessed.
- LAI preserves BN-10's existing discussion/review scope; specific product selection and a new product-choice contract are outside the question. Existing package identity is retained, while displayed wording and template outputs use the preserved scope. Concrete packages still require review.

### Requested History field correction — 2026-10-22 (preceding contract)

- The current core History inventory has nine fields. Lifetime antipsychotic
  exposure is Yes/No/Unknown, default Unknown. Onset and prior treatment appear
  only for Yes; prior response appears only for Inadequate adequacy. All four
  revised questions default to Unknown. All five explicitly listed onset choices
  are retained despite the request's stated count of four. See the
  [current contract](../../../docs/proposal-generation.md#core-history).
- The three exposure-duration/timeline questions are retired from active current
  History. Historical signed evidence is preserved. Hidden current answers and
  their provenance are cleared rather than restored on redisplay.
- This explicit field correction does not approve the complete History inventory,
  BARS, SAS, AIMS or Acute Dystonia Dx Criteria drafts. Their existing pending
  owner-review states remain. Separately reviewed versioned question packages and
  proposal-readiness gates are unchanged; no clinical rationale or validation is
  inferred from this implementation request.

### History cleanup and derived pharmacotherapy contract — 2026-10-22

- The subsequent implementation uses seven core fields, with movement-related
  children conditional on persistence Yes. All applicable core fields default to
  Unknown. Baseline movement examination and functional impact are additionally
  retired. See the [current contract](../../../docs/proposal-generation.md#core-history).
- The application pharmacotherapy definition retains physical vulnerability,
  substance contribution and exposure-conditional established antipsychotic.
  Six other pharmacotherapy patient mappings and readiness requirements are
  retired. Network structure, full CPT estimation and empty inference evidence
  remain unchanged; omitted patient inputs do not assert clinical states.
- These statements describe the recovered implementation. No new owner clinical
  approval is inferred from resuming it. The derived manifest is `awaiting_review`,
  and original package review remains historical provenance. Older frozen runs
  remain readable but require an explicit new run with the current input contract.

### LAI, aggression and C-SSRS input cleanup — 2026-10-22

- The explicit application request removes all high-suicide History controls;
  generation instead receives the complete released Step 4 C-SSRS assessment as
  structured context. This does not define a categorical risk mapping, clinical
  applicability rule, treatment-persistence answer or new clinical threshold.
- LAI retains five controls, including exposure-conditional prior NMS; aggression
  retains five, including renamed Substantial aggression-related risk with
  Yes/No/Unknown and Unknown default. Exact choices, removed controls and
  defaults are in the [current contract](../../../docs/proposal-generation.md#complete-proposal-context).
  Defaults and normalized legacy uncertainty are not clinician-attributed entries.
- Application contract `history-inputs-2026-10-22-v3` pins the derived high-suicide,
  LAI and aggression v2 definitions alongside pharmacotherapy v3. The request
  authorizes these application changes; it does not establish clinical approval
  for changed input contracts. Derived manifests remain `awaiting_review`, original
  approvals/artifacts remain historical, and signed records are not rewritten.

