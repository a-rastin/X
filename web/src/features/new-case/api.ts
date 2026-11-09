import { request, newIdempotencyKey } from "../identity/api";
export { request };
export type Manual = {
  manual: boolean | null;
  manual_actor_id: string | null;
  manual_recorded_at: string | null;
};
export interface NewCaseRaw {
  schema_version: string;
  registration_year: number;
  content_pins: { lexicomp_version: string; [key: string]: unknown };
  harm: { self: boolean; others: boolean };
  psychiatric: { present: boolean; conditions: string[]; other_text: string };
  labs: { present: boolean; values: Record<string, string | null> };
  conditions: Record<string, Manual>;
  heart_failure: boolean;
  tobacco: boolean;
  breastfeeding: boolean;
  substances: {
    present: boolean;
    marijuana: boolean;
    other: boolean;
    other_text: string;
  };
  bmi: {
    height_cm: string | null;
    weight_kg: string | null;
    source: "calculated" | "manual" | null;
    manual_category: string | null;
  };
  focal_neurological_deficit: boolean;
  suspected_brain_lesion: boolean;
  suspected_autoimmune_encephalitis: boolean;
  considerations: {
    basic_needs_met: boolean;
    needs_observation: boolean;
    lai: boolean;
    seizure_risk: boolean;
    avoid_sedation: boolean;
    qtc_risk: boolean;
    orthostatic_risk: Manual;
  };
}
export interface ClinicalContent {
  registration_year: number;
  medication_names: Record<string, string>;
  "labs.json": {
    labels: Record<string, { en: string; fa: string }>;
    sources: Record<string, string>;
    fields: Record<
      string,
      {
        unit: string;
        reference: Record<string, unknown>;
        sex?: string;
        source: string;
      }
    >;
  };
}
export interface State {
  revision: number;
  age: number;
  calculated_bmi: string | null;
  bmi_category: string | null;
  flags: Record<
    string,
    {
      suggested: boolean;
      effective: boolean;
      mode: string;
      triggering_findings: {
        field?: string;
        value?: string | boolean;
        unit?: string;
        name?: string;
        source?: unknown;
      }[];
    }
  >;
  lab_warnings: { field: string; value: string; code: string }[];
  can_navigate?: boolean;
  navigation_errors?: FieldError[];
  errors_by_step: Record<string, FieldError[]>;
}
export interface FieldError {
  field: string;
  code: string;
  message_key: string;
}
export interface Section {
  value?: boolean | string | string[];
  reasons?: string[];
  actions?: string[];
  must_not_delay_treatment?: boolean;
  status?: string;
}
export interface Proposal {
  id: string;
  stale?: boolean;
  payload: {
    sections: Record<string, Section>;
    ranking: {
      status: string;
      candidates: { name: string; probability: number }[];
    };
    candidate_interactions: {
      pairs: {
        drug_a: string;
        drug_b: string;
        risk_rating: string | null;
        evidence: { raw_text: string }[];
      }[];
      resolved_medications: {
        catalog_drug_id: string;
        canonical_name: string;
      }[];
    } | null;
    [key: string]: unknown;
  };
}
export const newCaseConfig = () => request<ClinicalContent>("/new-case/config");
export const preview = (id: string) =>
  request<State>(`/encounters/${id}/new-case`);
export const latestProposal = (id: string) =>
  request<{ proposal: Proposal | null }>(
    `/encounters/${id}/new-case/proposals/latest`,
  );
export function command<T>(
  id: string,
  action: string,
  revision: number,
  body: unknown,
  key = newIdempotencyKey(),
) {
  return request<T>(`/encounters/${id}/new-case/${action}`, {
    method: "POST",
    csrf: true,
    body,
    headers: { "If-Match": `"${revision}"`, "Idempotency-Key": key },
  });
}
