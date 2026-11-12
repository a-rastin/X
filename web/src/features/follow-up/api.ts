import { request } from "../identity/api";
import type { FieldError } from "../new-case/api";

export type FollowUpStep =
  | "panss" | "harm" | "history" | "medications" | "effects"
  | "considerations" | "proposal";
type Answer = boolean | null;
type Condition = {
  inherited: Answer;
  manual: Answer;
  manual_actor_id: string | null;
  manual_recorded_at: string | null;
};
export interface FollowUpRaw {
  schema_version: "follow-up-state-v1";
  visit_year: number;
  content_pins: Record<string, unknown>;
  baseline: Record<string, unknown>;
  harm: { self: boolean; others: boolean };
  history: {
    psychiatric: { present: Answer; conditions: ("depression" | "ptsd" | "adhd" | "other")[]; other_text: string };
    labs: { present: boolean; values: Record<string, string | null> };
    conditions: Record<"hepatic_failure" | "renal_failure" | "glucose_abnormality" | "hyperlipidemia" | "hyperprolactinemia" | "pregnant", Condition>;
    heart_failure: Answer;
    tobacco: Answer;
    substances: { present: Answer; marijuana: boolean; opioids: boolean; methamphetamine: boolean; other: boolean; other_text: string };
    pregnant: Answer;
    breastfeeding: Answer;
    bmi: { height_cm: string | null; weight_kg: string | null; source: "calculated" | "manual" | null; manual_category: string | null };
    neurological: Record<"focal_neurological_deficit" | "suspected_brain_lesion" | "suspected_autoimmune_encephalitis", Answer>;
    hospitalization_count: number | null;
    current_antidepressant: Answer;
  };
  effects: { sedation: "none" | "tolerable" | "intervention" } & Record<"orthostatic_hypotension" | "akathisia" | "parkinsonism" | "acute_dystonia" | "tardive_dyskinesia" | "anticholinergic", boolean>;
  treatment: { main_catalog_drug_id: string | null; formulation: "oral" | "lai" | null };
  considerations: {
    recovery: "controlled" | "partial" | "none_or_worse" | null;
    adherence: boolean; basic_needs_met: boolean; needs_observation: boolean;
    lai_preference: boolean; seizure_risk: Answer; qtc_risk: Answer;
  };
  branches: Record<"switch_needed" | "dose_reduction_possible" | "treatment_resistant" | "persistent_harm_risk" | "dystonia_resolved", Answer>;
}
export interface FollowUpState {
  raw: { follow_up: FollowUpRaw; panss: unknown; cssrs: unknown; medications: unknown };
  revision: number;
  age: number;
  visit_year: number;
  baseline: Record<string, unknown>;
  calculated_bmi: string | null;
  bmi_category: string | null;
  flags: Record<string, unknown>;
  lab_warnings: Record<string, unknown>[];
  errors_by_step: Record<FollowUpStep, FieldError[]>;
  can_complete_step: Record<FollowUpStep, boolean>;
  can_complete: boolean;
  reached_questions: (keyof FollowUpRaw["branches"])[];
  steps: FollowUpStep[];
  proposal: null;
  decision: Record<string, unknown>;
  model_factors: Record<string, unknown>;
  sections: Record<string, unknown>;
  can_navigate?: boolean;
  navigation_errors?: FieldError[];
}
export interface FollowUpConfig {
  workflow_version: "follow-up-v1";
  state_schema_version: "follow-up-state-v1";
  steps: FollowUpStep[];
  definitions: Record<string, unknown>;
  defaults: Record<string, unknown>;
  lab_fields: Record<string, { unit: string; reference: Record<string, unknown>; source: string; suggestion: unknown }>;
  formulations: unknown[];
  lai_equivalences: unknown;
  hashes: Record<string, unknown>;
}
export const followUpConfig = () => request<FollowUpConfig>("/follow-up/config");
export const preview = (id: string) => request<FollowUpState>(`/encounters/${id}/follow-up`);
export const validateStep = (id: string, destination: FollowUpStep, revision: number) =>
  request<FollowUpState>(`/encounters/${id}/follow-up/validate-step`, {
    method: "POST", csrf: true, body: { destination }, headers: { "If-Match": `"${revision}"` },
  });
