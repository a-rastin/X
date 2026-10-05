/* Shared chart + follow-up baseline HTTP client (S14, plan.md §§2.2-2.3; FR-20-23).
 *
 * Reads the physician-shared chart (`GET /patients/{id}/chart`:
 * demographics plus signed chronology plus an occupancy-only open-draft
 * badge — no draft content, no author, no revision) and the author-only
 * follow-up baseline (`GET /encounters/{id}/followup-baseline`: copied
 * history/medications plus historical prior scores, never current answers).
 * Writes still go through the encounters client (createEncounter) with a
 * fresh Idempotency-Key per submit. `patientChartHash` builds the
 * `#/patients/:id/chart` route; the draft-id helpers only remember this
 * browser session's own creations so the chart can offer the author a resume
 * link without ever learning another physician's draft id.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
} from "../../identity/api";
import type { EncounterReference } from "../../encounters/api";

export interface ChartPatient {
  id: string;
  identifier: string;
  given_name: string;
  family_name: string;
  sex: string;
  age: number;
  clinical_status: string;
  phone: string | null;
  archived: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface SignedEncounterReference {
  id: string;
  patient_id: string;
  kind: string;
  lifecycle: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface ChartProposal {
  status: string;
  reason?: string;
}

export interface ChartPayload {
  patient: ChartPatient;
  signed_encounters: SignedEncounterReference[];
  open_draft: { exists: boolean };
  chronology: SignedEncounterReference[];
  proposal: ChartProposal;
}

export interface FollowupBaselineSnapshot {
  history_values: Record<string, unknown>;
  medications: { catalog_drug_id: string }[];
  provenance_note: string | null;
  baseline_encounter_id: string | null;
}

export interface FollowupReconciliation {
  status: string;
  baseline_encounter_id: string | null;
}

export type PriorScoresDisplay = Record<
  string,
  { value: number | null; historical: boolean }
>;

export interface FollowupBaselinePayload {
  encounter: EncounterReference;
  revision: number;
  baseline: FollowupBaselineSnapshot | null;
  reconciliation: FollowupReconciliation;
  prior_scores_display: PriorScoresDisplay;
}

async function request<T>(path: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  const response = await fetch(`/api/v1${path}`, {
    method: "GET",
    credentials: "include",
    headers,
  });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const body = (payload ?? {}) as {
      code?: string;
      message?: string;
      field_errors?: Record<string, string[]>;
    };
    throw new ApiError(
      response.status,
      body.code ?? "REQUEST_FAILED",
      body.message ?? `Request failed (${response.status}).`,
      body.field_errors ?? {},
    );
  }
  return (payload ?? {}) as T;
}

/** Shared chart read: physician-only (admin 403, anonymous 401). */
export async function getChart(patientId: string): Promise<ChartPayload> {
  return request<ChartPayload>(
    `/patients/${encodeURIComponent(patientId)}/chart`,
  );
}

/** Author-only follow-up baseline read (403 strangers, 404 missing/released). */
export async function getFollowupBaseline(
  encounterId: string,
): Promise<FollowupBaselinePayload> {
  return request<FollowupBaselinePayload>(
    `/encounters/${encodeURIComponent(encounterId)}/followup-baseline`,
  );
}

export function patientChartHash(patientId: string): string {
  return `#/patients/${patientId}/chart`;
}

const OPEN_DRAFT_KEY_PREFIX = "xi_open_draft_";

/** Remember a draft id this session created (author resume link only). */
export function rememberOpenDraft(
  patientId: string,
  encounterId: string,
): void {
  try {
    sessionStorage.setItem(OPEN_DRAFT_KEY_PREFIX + patientId, encounterId);
  } catch {
    // Private mode etc: the resume link simply stays hidden.
  }
}

/** Draft id this session created for the patient, if any. */
export function recallOpenDraft(patientId: string): string | null {
  try {
    return sessionStorage.getItem(OPEN_DRAFT_KEY_PREFIX + patientId);
  } catch {
    return null;
  }
}
