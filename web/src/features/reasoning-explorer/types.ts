export type OralTreatmentId =
  | "oral:chlorpromazine"
  | "oral:fluphenazine"
  | "oral:haloperidol"
  | "oral:loxapine"
  | "oral:molindone"
  | "oral:perphenazine"
  | "oral:pimozide"
  | "oral:thioridazine"
  | "oral:thiothixene"
  | "oral:trifluoperazine"
  | "oral:aripiprazole"
  | "oral:asenapine"
  | "oral:brexpiprazole"
  | "oral:cariprazine"
  | "oral:clozapine"
  | "oral:iloperidone"
  | "oral:lurasidone"
  | "oral:olanzapine"
  | "oral:paliperidone"
  | "oral:quetiapine"
  | "oral:risperidone"
  | "oral:ziprasidone";

export type FactorId =
  | "Smoking"
  | "Marijuana_Smoking"
  | "Hepatic_Failure"
  | "Renal_Failure"
  | "Sedative_Effects_Concern"
  | "Seizure_Risk"
  | "Glucose_Abnormalities"
  | "Hyperlipidemia"
  | "Weight_Gain_Risk"
  | "QTc_Prolongation_Risk"
  | "Orthostatic_Hypotension_Risk"
  | "Hyperprolactinemia"
  | "Tardive_Dyskinesia"
  | "Dystonia"
  | "Parkinsonism"
  | "Akathisia";

type PanssItemId = `P${1 | 2 | 3 | 4 | 5 | 6 | 7}`
  | `N${1 | 2 | 3 | 4 | 5 | 6 | 7}`
  | `G${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16}`;
type CssrsItemId = `css_i${1 | 2 | 3 | 4 | 5}_recent`;

export type ControlValue = boolean | number | string | null;
export type Change =
  | { kind: "panss"; item_id: PanssItemId; value: number }
  | { kind: "cssrs"; item_id: CssrsItemId; value: boolean }
  | { kind: "boolean"; control_id: string; value: boolean }
  | { kind: "nullable_boolean"; control_id: string; value: boolean | null }
  | { kind: "choice"; control_id: string; value: string | null };
export interface SimulationRequest {
  schema_version: "reasoning-simulation-v1";
  baseline_proposal_id: string; // UUID
  selected_treatment_id: OralTreatmentId | null;
  client_request_id: number; // nonnegative JavaScript safe integer
  changes: Change[];
}

export type Workflow = "new-case-v1" | "follow-up-v1";
export type EvaluationStatus = "complete" | "partial" | "non_model";
export type ModelStatus = "available" | "model_unavailable" | "non_model"
  | "no_eligible_oral_candidate" | "direct_available" | "direct_excluded"
  | "unsupported_direct_mapping" | "partial";
export type Terminal = "oral_replacement" | "clozapine" | "same_drug_lai"
  | "physician_review" | "continue_same" | "reduce_dose" | "adjunct_only"
  | "oral" | "lai";
export type Exclusion = "zero_probability" | "unavailable" | "current_main"
  | "interaction_dx" | "unsupported_direct_mapping";
export type Impact = "assessment" | "section" | "model" | "decision";
export type ControlKind = Change["kind"];
export type JsonValue = string | number | boolean | null | JsonValue[]
  | { [key: string]: JsonValue };
export type Delta<T> = { patient: T | null; scenario: T | null };

export interface Identity {
  schema_version: "reasoning-simulation-v1";
  algorithm_version: "oral-root-prior-shapley-v1";
  client_request_id: number;
  workflow: Workflow;
  baseline_proposal_id: string;
  baseline_input_hash: string;
  baseline_payload_hash: string;
  content_hash: string;
  model_sha256: string;
  assessment_hashes: Record<string, string>;
  assessment_hash_origin: "historical_byte_pin" | "resolved_from_pinned_versions";
  ddi_release_id: string;
  availability_snapshot_hash: string;
  scenario_hash: string;
  selected_treatment_id: OralTreatmentId | null;
}

export interface Control {
  control_id: string;
  kind: ControlKind;
  label_key: string; // stable lookup key for the later localized UI
  source_path: string;
  patient_value: ControlValue;
  scenario_value: ControlValue;
  choices: ControlValue[];
  minimum: number | null;
  maximum: number | null;
  active: boolean; // scenario visibility, e.g. reached branch question
  affected_outputs: Impact[];
}

export interface ProbabilityRow {
  treatment_id: OralTreatmentId;
  probability: number;
  raw_rank: number;
  eligible_rank: number | null;
  exclusion_reasons: Exclusion[];
}
export interface Direct {
  treatment_id: string | null;
  exclusion_reasons: Exclusion[];
}
export interface Decision {
  terminal: Terminal | null;
  reason: string | null;
}
export interface Factor {
  value: boolean | null;
  source: string;
  simulated_override: string[];
}
export interface Assessment {
  status: "unanswered" | "partial" | "complete" | "not_assessed";
  definition_version: string;
  missing_item_ids: string[];
  item_errors: Record<string, string[]>;
  scores: Record<string, number | null>;
  findings: Record<string, JsonValue>;
}
export interface Issue { field: string; message_key: string }
export interface Side {
  model_status: ModelStatus;
  original_decision: Decision | null;
  final_decision: Decision | null;
  distribution: ProbabilityRow[];
  candidates: ProbabilityRow[];
  direct: Direct | null;
  sections: Record<string, JsonValue>;
  assessments: { panss: Assessment; cssrs: Assessment };
  factors: Record<FactorId, Factor>;
}
export interface ComparisonRow {
  treatment_id: OralTreatmentId;
  patient_probability: number | null;
  scenario_probability: number | null;
  patient_raw_rank: number | null;
  scenario_raw_rank: number | null;
  patient_eligible_rank: number | null;
  scenario_eligible_rank: number | null;
  patient_exclusion_reasons: Exclusion[];
  scenario_exclusion_reasons: Exclusion[];
  probability_delta: number | null;
}

export interface FactorAttribution {
  factor_id: FactorId;
  contribution: number;
  source: string;
}
export interface AttributionSide {
  posterior_probability: number;
  contributions: FactorAttribution[];
  reconstruction_residual: number;
}
export interface Attribution {
  treatment_id: OralTreatmentId;
  model_reference_probability: number;
  patient: AttributionSide | null;
  scenario: AttributionSide | null;
}

export interface Selection {
  top: ProbabilityRow | null;
  tied_eligible_ids: OralTreatmentId[];
  candidate_ids: OralTreatmentId[];
  original_decision: Decision | null;
  final_decision: Decision | null;
  direct: Direct | null;
}
export type BannerKind = "no_change" | "top_choice_changed" | "top_probability_changed"
  | "eligibility_changed" | "decision_branch_changed" | "assessment_changed"
  | "section_changed" | "inputs_changed";
export interface Diff {
  raw_inputs: Record<string, Delta<ControlValue>>; // keyed by public control_id
  factors: Partial<Record<FactorId, Delta<boolean>>>;
  assessments: Partial<Record<"panss" | "cssrs", Delta<Assessment>>>;
  sections: Record<string, Delta<JsonValue>>;
  probabilities: Partial<Record<OralTreatmentId, Delta<number>>>;
  exclusions: Partial<Record<OralTreatmentId, Delta<Exclusion[]>>>;
  selection: { patient: Selection; scenario: Selection };
  banner_kind: BannerKind;
  message_parameters: {
    changed_control_ids: string[];
    patient_top: ProbabilityRow | null;
    scenario_top: ProbabilityRow | null;
  };
}

export type PathState = "patient" | "scenario" | "both";
export type ScenarioState = "active" | "superseded";
export type NodeKind = "source" | "factor" | "assessment" | "rule" | "section"
  | "treatment" | "selection";
export type EdgeKind = "input_derivation" | "bn_dependency" | "rule_dependency"
  | "candidate_filter";
export interface Provenance {
  kind: "source" | "recorded_override" | "simulated_override"
    | "workflow_default" | "derived";
  path?: string;
  source?: string;
  paths?: string[];
  overrides?: string[];
}
export interface Observation { value: JsonValue; provenance: Provenance | null }
export interface GraphNode {
  id: string;
  kind: NodeKind;
  subject: string;
  patient: Observation | null;
  scenario: Observation | null;
  path_state: PathState;
  scenario_state: ScenarioState;
}
export interface GraphEdge {
  id: string;
  kind: EdgeKind;
  source: string;
  target: string;
  path_state: PathState;
  scenario_state: ScenarioState;
}
export interface ActiveGraph {
  selected_treatment_id: OralTreatmentId | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  pruning_counts: Record<string, number>;
}
export interface Evaluation {
  status: EvaluationStatus;
  issues: Issue[];
  unevaluated_reason: ModelStatus | null;
}
export interface SimulationResponse {
  identity: Identity;
  controls: Control[];
  patient: Side;
  scenario: Side;
  comparison_rows: ComparisonRow[];
  selected_attribution: Attribution | null;
  diff: Diff;
  active_graph: ActiveGraph;
  evaluation: { patient: Evaluation; scenario: Evaluation };
}
