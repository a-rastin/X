/* Admin audit inspection HTTP client (S52 §4, seams T1/T9; plan.md §10.1).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). `GET /audit-events` is admin-only: 401 unauthenticated, 403
 * otherwise (physician included). The route guard in app/pages.tsx only
 * chooses what to render — authority stays server-side. Reads carry no
 * CSRF/Idempotency headers (GET, like the identity/directory reads).
 *
 * Response shape mirrors `operations/audit.safe_event` (backend slice owns
 * it): `{items, total, limit, offset}` with stable server ordering
 * (`occurred_at`, `id`) and bounded pagination (default 25, max 100).
 * Filters are passed through verbatim: actor/operation/since/until plus
 * target correlation keys (patient/encounter/question-run/batch/question).
 * Times travel as ISO-8601 UTC (`Z`); `since`/`until` inputs are converted
 * from local `datetime-local` values at the form layer.
 */

import { ApiError } from "../../identity/api";

export { ApiError };

export interface AuditEvent {
  id: string;
  /** UTC ISO-8601 with `Z` suffix (server `serialize_utc`). */
  occurred_at: string;
  actor: string | null;
  actor_id: string | null;
  actor_display: string | null;
  operation: string;
  request_id: string | null;
  details: Record<string, unknown>;
  payload_hash: string;
}

export interface AuditList {
  items: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface AuditQuery {
  actor?: string;
  operation?: string;
  since?: string;
  until?: string;
  patientId?: string;
  encounterId?: string;
  questionRunId?: string;
  batchId?: string;
  questionKey?: string;
  limit?: number;
  offset?: number;
}

export const AUDIT_PAGE_SIZE = 25;

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    credentials: "include",
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

/** Admin audit read (plan §10.1): blank filter strings are omitted so an
 * untouched filter form lists the whole trail. */
export async function listAuditEvents(query: AuditQuery): Promise<AuditList> {
  const params = new URLSearchParams();
  const textEntries: Array<[string, string | undefined]> = [
    ["actor", query.actor],
    ["operation", query.operation],
    ["since", query.since],
    ["until", query.until],
    ["patient_id", query.patientId],
    ["encounter_id", query.encounterId],
    ["question_run_id", query.questionRunId],
    ["batch_id", query.batchId],
    ["question_key", query.questionKey],
  ];
  for (const [key, value] of textEntries) {
    if (value !== undefined && value.trim() !== "") {
      params.set(key, value.trim());
    }
  }
  params.set("limit", String(query.limit ?? AUDIT_PAGE_SIZE));
  params.set("offset", String(query.offset ?? 0));
  return request<AuditList>(`/audit-events?${params.toString()}`);
}
