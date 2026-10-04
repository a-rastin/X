/* Diagnosis page-state HTTP client (S09, plan.md §§2.2, 5; FR-11, FR-16).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Reads need any active authenticated session; the two command
 * POSTs need an active physician session plus the per-session CSRF token.
 *
 * Server-authoritative contract (no client-side criterion duplication — the
 * browser never re-implements threshold logic, so the released definition
 * cannot drift from the S08 evaluator):
 * - item prompts come from GET /content/assessments/diagnosis (released v1);
 * - the verdict (status / findings.overall / can_proceed /
 *   requires_acknowledgment / proceed_via) comes from
 *   GET /encounters/{id}/diagnosis, which runs the same T2 evaluate().
 * - answers travel through the shared S07 autosave PATCH
 *   (draft_data["diagnosis"]["answers"]) — see features/encounters/api.ts.
 * - acknowledgment and bypass are empty-{} POSTs fenced by If-Match (current
 *   revision from GET) plus a fresh Idempotency-Key per attempt; there is no
 *   reason field on either command (extra fields are 422 by contract).
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../../identity/api";
import { formatIfMatch } from "../../encounters/api";

export type DiagnosisAnswer = "yes" | "no" | "unknown";

export interface DiagnosisItem {
  id: string;
  prompt: string;
  value_type: string;
  required: boolean;
}

export interface DiagnosisDefinition {
  id: string;
  version: string;
  items: DiagnosisItem[];
}

export interface DiagnosisEvaluation {
  status: "unanswered" | "partial" | "complete" | "not_assessed" | "bypassed";
  missing_item_ids: string[];
  item_errors: Record<string, string[]>;
  scores: Record<string, unknown>;
  findings: {
    criteria?: Record<string, string>;
    overall?: "criteria_satisfied" | "below_threshold" | "indeterminate";
    [key: string]: unknown;
  };
  definition_version: string;
}

export interface DiagnosisRecord {
  actor_id: string;
  status: string;
  revision: number;
  definition_version: string;
  acknowledged_at?: string;
  bypassed_at?: string;
  answers_hash?: string;
}

export interface DiagnosisPreview {
  answers: Record<string, string>;
  evaluation: DiagnosisEvaluation;
  definition_version: string;
  revision: number;
  acknowledgment: DiagnosisRecord | null;
  acknowledgment_valid: boolean;
  bypass: DiagnosisRecord | null;
  bypass_valid: boolean;
  can_proceed: boolean;
  requires_acknowledgment: boolean;
  proceed_via: "bypass" | "criteria_satisfied" | "acknowledged_below_threshold" | null;
  server_timestamp?: string;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  ifMatch?: number;
  idempotencyKey?: string;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  if (options.ifMatch !== undefined) {
    headers["If-Match"] = formatIfMatch(options.ifMatch);
  }
  if (options.idempotencyKey !== undefined) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }
  const response = await fetch(`/api/v1${path}`, {
    method: options.method ?? "GET",
    credentials: "include",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
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
export async function getDiagnosisDefinition(): Promise<DiagnosisDefinition> {
  const result = await request<{
    type: string;
    version: string;
    definition: DiagnosisDefinition;
  }>("/content/assessments/diagnosis");
  return result.definition;
}

/** Author-only live preview: answers + server evaluation + gate. */
export async function getDiagnosis(encounterId: string): Promise<DiagnosisPreview> {
  return request<DiagnosisPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/diagnosis`,
  );
}

/** Attributed warning acknowledgment for a completed below-threshold result.
 * Empty body by contract — no reason field exists. Fresh key per attempt. */
export async function acknowledgeDiagnosis(
  encounterId: string,
  revision: number,
): Promise<DiagnosisPreview> {
  return request<DiagnosisPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/diagnosis/acknowledgment`,
    {
      method: "POST",
      ifMatch: revision,
      idempotencyKey: newIdempotencyKey(),
      body: {},
    },
  );
}

/** Bypass without any reason field. Fresh key per attempt. Clears answers
 * server-side; the record survives resume via the same draft_data body. */
export async function bypassDiagnosis(
  encounterId: string,
  revision: number,
): Promise<DiagnosisPreview> {
  return request<DiagnosisPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/diagnosis/bypass`,
    {
      method: "POST",
      ifMatch: revision,
      idempotencyKey: newIdempotencyKey(),
      body: {},
    },
  );
}
