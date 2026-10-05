/* History + adverse-effects page-state HTTP client (S12,
 * plan.md §§2.2, 5; FR-14, FR-20–21).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Reads need any active authenticated session; author-only
 * enforcement stays server-side (403 strangers, 404 missing/released).
 *
 * Server-authoritative contract (no client-side evaluation duplication —
 * the browser never computes completeness or scores, so the display cannot
 * drift from the versioned drafts under content/history/):
 * - field inventory comes from the embedded HISTORY_FIELDS below, typed from
 *   content/history/history.v1.json (12 ids, periods, permitted values);
 *   there is no GET /content/history route, so the file is the contract and
 *   undeclared/excluded ids are never rendered or sent;
 * - the verdict (status / missing_item_ids / item_errors) comes from
 *   GET /encounters/{id}/history, which runs the same evaluate_history() as
 *   the backend (history has no scores, only completeness);
 * - the four effect states come from GET /encounters/{id}/effects, which
 *   runs the same questionnaire evaluators as the backend (BARS 4 items,
 *   SAS 10 items, AIMS awaiting_source, Acute Dystonia Dx Criteria 5 items);
 * - answers travel through the shared S07 autosave PATCH
 *   (draft_data["history"] = {values, provenance, reconciliation,
 *   phone_update}, draft_data["effects"][key] = {status, severity,
 *   questionnaire: {answers}}) — see features/encounters/api.ts.
 * - The strict POST .../history and POST .../effects/{effect}/status
 *   commands stamp provenance and enforce the present/severity contract for
 *   direct HTTP consumers; the wizard keeps one persistence mechanism (the
 *   S07 autosave path) and enforces the same rules client-side: severity is
 *   required if and only if status is present, and a status change clears an
 *   obsolete severity in the same edit (never sent stale).
 */

import { ApiError } from "../identity/api";

export interface HistoryEvaluation {
  status: "unanswered" | "partial" | "complete";
  missing_item_ids: string[];
  item_errors: Record<string, string[]>;
  definition_version: string;
}

export interface HistoryProvenanceEntry {
  source?: string;
  author_id?: string;
  recorded_at?: string;
  baseline_encounter_id?: string;
  [key: string]: unknown;
}

export interface HistoryReconciliation {
  status: "not_required" | "pending" | "reconciled";
  baseline_encounter_id?: string | null;
  [key: string]: unknown;
}

export interface HistoryPreview {
  values: Record<string, unknown>;
  provenance: Record<string, HistoryProvenanceEntry>;
  reconciliation: HistoryReconciliation | null;
  phone_update: string | null;
  evaluation: HistoryEvaluation;
  definition_version: string;
  revision: number;
  analysis_visible: boolean;
  analysis_visible_label: string;
  server_timestamp?: string;
}

export type EffectKey =
  | "tardive_dyskinesia"
  | "akathisia"
  | "parkinsonism"
  | "acute_dystonia";

export type EffectStatus = "present" | "absent" | "not_assessed";

export interface QuestionnaireEvaluation {
  status: string;
  missing_item_ids: string[];
  item_errors: Record<string, string[]>;
  scores: Record<string, number | null> | null;
  findings: Record<string, unknown>;
  definition_version: string;
}

export interface EffectState {
  status: EffectStatus | null;
  severity: unknown;
  questionnaire: {
    answers: Record<string, unknown>;
    definition_version: string;
    evaluation: QuestionnaireEvaluation;
  };
  urgent: boolean | null;
}

export interface EffectsPreview {
  effects: Record<EffectKey, EffectState>;
  definition_versions: Record<string, string>;
  revision: number;
  server_timestamp?: string;
}

async function request<T>(path: string): Promise<T> {
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

/** Author-only history preview: values + provenance + server evaluation. */
export async function getHistory(encounterId: string): Promise<HistoryPreview> {
  return request<HistoryPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/history`,
  );
}

/** Author-only effects preview: four effect states + questionnaires. */
export async function getEffects(encounterId: string): Promise<EffectsPreview> {
  return request<EffectsPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/effects`,
  );
}
