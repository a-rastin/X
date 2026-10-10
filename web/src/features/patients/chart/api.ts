/* Shared chart + follow-up baseline HTTP client (S14, plan.md §§2.2-2.3; FR-20-23).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). `GET /patients/{id}/chart` is readable by any active
 * authenticated user (physician or administrator) and never carries draft
 * clinical content — only slot occupancy (`open_draft.exists`) for badges,
 * so stranger-draft content never leaks (transitive privacy, server-side).
 * `GET /encounters/{id}/followup-baseline` is author-only (403 strangers,
 * 404 missing/released); prior scores display with `historical: true` and
 * are never filled as answers.
 *
 * Reads need no CSRF/Idempotency-Key (GET only); mutations reuse
 * features/encounters/api.ts (POST with fresh Idempotency-Key + CSRF +
 * credentials:include). No secrets in logs.
 */

import { ApiError } from "../../identity/api";
import type { Patient } from "../api";
import type { AddendumRow, SignedSnapshotRow } from "../../plans/api";

export interface SignedEncounterReference {
  id: string;
  patient_id: string;
  kind: string;
  lifecycle: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface ChartResponse {
  patient: Patient;
  signed_encounters: SignedEncounterReference[];
  /** Immutable frozen records per signed encounter (S49/S50, shared read). */
  signed_snapshots?: SignedSnapshotRow[];
  /** Append-only corrections across signed encounters (S49/S50). */
  addenda?: AddendumRow[];
  open_draft: { exists: boolean };
  chronology: SignedEncounterReference[];
  proposal: { status: string; reason?: string };
}

export interface FollowupBaseline {
  history_values: Record<string, unknown>;
  medications: Array<{ catalog_drug_id: string }>;
  provenance_note: string | null;
  baseline_encounter_id: string | null;
}

export interface FollowupReconciliation {
  status: "not_required" | "pending" | "reconciled";
  baseline_encounter_id?: string | null;
}

export interface PriorScoreDisplay {
  value: unknown;
  historical: boolean;
}

export interface FollowupBaselineResponse {
  encounter: {
    id: string;
    patient_id: string;
    kind: string;
    author_id: string;
    lifecycle: string;
    revision: number;
    created_at: string;
    updated_at: string;
  };
  revision: number;
  baseline: FollowupBaseline | null;
  reconciliation: FollowupReconciliation;
  prior_scores_display: Record<string, PriorScoreDisplay>;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: "GET",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
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

/** Shared chart read: demographics + signed chronology + slot badge. */
export async function getChart(patientId: string): Promise<ChartResponse> {
  return get<ChartResponse>(
    `/patients/${encodeURIComponent(patientId)}/chart`,
  );
}

/** Author-only follow-up baseline preview (baseline + reconciliation + history). */
export async function getFollowupBaseline(
  encounterId: string,
): Promise<FollowupBaselineResponse> {
  return get<FollowupBaselineResponse>(
    `/encounters/${encodeURIComponent(encounterId)}/followup-baseline`,
  );
}
