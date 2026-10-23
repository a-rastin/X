/* C-SSRS page-state HTTP client (S11, plan.md §§2.2, 5; FR-13, FR-20).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Reads need any active authenticated session; author-only
 * enforcement stays server-side (403 strangers, 404 missing/released).
 *
 * Server-authoritative contract (no client-side arithmetic — the browser
 * never computes severity or flags):
 * - prompts may come from GET /content/assessments/cssrs. The worksheet
 *   renders only css_i1_recent .. css_i5_recent, each yes / no / unknown,
 *   even when that payload still lists other items or value_type yes_no;
 * - the verdict (status / scores.ideation_severity_recent / findings.flags /
 *   missing_item_ids / item_errors) comes from
 *   GET /encounters/{id}/cssrs, which runs the same T2 evaluate() as the
 *   backend. Older preview fields are ignored by the page;
 * - answers travel through the shared S07 autosave PATCH
 *   (draft_data["cssrs"]["answers"]) — see features/encounters/api.ts.
 * - Skip is answers {"__skipped": true} (evaluates to not_assessed). There
 *   is no ack/bypass, no new migration, no composite risk score, no
 *   treatment gate.
 */

import { ApiError } from "../../identity/api";

export type CssrsAnswerValue = "yes" | "no" | "unknown";

export interface CssrsItem {
  id: string;
  prompt: string;
  value_type: string;
  required: boolean;
}

export interface CssrsDefinition {
  id: string;
  version: string;
  title?: string;
  description?: string;
  items: CssrsItem[];
}

export interface CssrsScores {
  ideation_severity_recent: number | null;
}

export interface CssrsFindings {
  flags?: string[];
  notes?: string[];
  skipped?: boolean;
  [key: string]: unknown;
}

export interface CssrsEvaluation {
  status: "unanswered" | "partial" | "complete" | "not_assessed";
  missing_item_ids: string[];
  item_errors: Record<string, string[]>;
  scores: CssrsScores;
  findings: CssrsFindings;
  definition_version: string;
}

export interface CssrsPreview {
  answers: Record<string, unknown>;
  evaluation: CssrsEvaluation;
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
export async function getCssrsDefinition(): Promise<CssrsDefinition> {
  const result = await request<{
    type: string;
    version: string;
    definition: CssrsDefinition;
  }>("/content/assessments/cssrs");
  return result.definition;
}

/** Author-only live preview: answers + server evaluation (same T2). */
export async function getCssrs(encounterId: string): Promise<CssrsPreview> {
  return request<CssrsPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/cssrs`,
  );
}
