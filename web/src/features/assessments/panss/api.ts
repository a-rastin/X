/* PANSS page-state HTTP client (S10, plan.md §§2.2, 5; FR-12, FR-20).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Reads need any active authenticated session; author-only
 * enforcement stays server-side (403 strangers, 404 missing/released).
 *
 * Server-authoritative contract (no client-side arithmetic duplication — the
 * browser never sums subscales or totals, so the display cannot drift from
 * the S08 evaluator):
 * - item prompts come from GET /content/assessments/panss (released v1,
 *   30 items P1-P7/N1-N7/G1-G16 scale_1_7);
 * - the verdict (status / scores / findings.total_band / missing_item_ids /
 *   item_errors) comes from GET /encounters/{id}/panss, which runs the same
 *   T2 evaluate() as the backend;
 * - answers travel through the shared S07 autosave PATCH
 *   (draft_data["panss"]["answers"]) — see features/encounters/api.ts.
 * - Skip is answers {"__skipped": true} (evaluates to not_assessed). There
 *   is no ack/bypass, no new migration, no treatment gate from score bands.
 */

import { ApiError } from "../../identity/api";

export type PanssAnswer = 1 | 2 | 3 | 4 | 5 | 6 | 7;

export interface PanssItem {
  id: string;
  prompt: string;
  value_type: string;
  required: boolean;
}

export interface PanssDefinition {
  id: string;
  version: string;
  items: PanssItem[];
}

export interface PanssScores {
  positive: number | null;
  negative: number | null;
  general: number | null;
  total: number | null;
}

export interface PanssTotalBand {
  range: string;
  label: string;
  informational_only: boolean;
  not_a_treatment_gate: boolean;
}

export interface PanssEvaluation {
  status: "unanswered" | "partial" | "complete" | "not_assessed";
  missing_item_ids: string[];
  item_errors: Record<string, string[]>;
  scores: PanssScores;
  findings: {
    assessment_window?: string;
    total_band?: PanssTotalBand;
    skipped?: boolean;
    [key: string]: unknown;
  };
  definition_version: string;
}

export interface PanssPreview {
  answers: Record<string, unknown>;
  evaluation: PanssEvaluation;
  definition_version: string;
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

/** Released definition (prompts + required flags) for rendering the form. */
export async function getPanssDefinition(): Promise<PanssDefinition> {
  const result = await request<{
    type: string;
    version: string;
    definition: PanssDefinition;
  }>("/content/assessments/panss");
  return result.definition;
}

/** Author-only live preview: answers + server evaluation (same T2). */
export async function getPanss(encounterId: string): Promise<PanssPreview> {
  return request<PanssPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/panss`,
  );
}
