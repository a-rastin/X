/* Staged restore validation + commit HTTP client (S55 §§1-4 staging, S56
 * commit/rollback/reopen; seams T10 over T1 for staging, T9 for the page;
 * plan.md §10.3 steps 1-6; FR-41–42, NFR-03/04).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). All routes are admin-only: 401 unauthenticated, 403
 * otherwise (physicians included). The route guard in app/pages.tsx only
 * chooses what to render — authority stays server-side.
 *
 * - `POST /restores/validate` is the staging mutation: per-session CSRF
 *   token plus the raw zip bytes as the request body
 *   (`application/zip`). Synchronous bounded staging — 201 carries the
 *   terminal job (`succeeded` or `failed`) with the impact report plus
 *   the immutable confirmation digest commit requires exactly. Only
 *   `restore_jobs` + audit rows are written server-side; clinical tables
 *   are never touched, so failed staging leaves live state unchanged.
 * - `GET /restores/{id}` (staging + commit progress/impact read) carries
 *   no CSRF/Idempotency headers, like the audit/backups reads. `expired`
 *   is computed on read so a replacing upload or TTL expiry invalidates
 *   prior confirmations without a background job; commit polling fields
 *   (`commit_status`/`commit_phase`/`pre_restore_backup_id`/
 *   `commit_history`/`maintenance_active`) ride the same read.
 * - `POST /restores/{id}/commit` (S56) takes
 *   `{"confirmation_digest": "<64hex>"}` with CSRF: the exact validated
 *   staged digest only (stale/mismatched 409, malformed 422). Success is
 *   200 with the terminal job (`commit_status: succeeded`,
 *   `maintenance_active: false`); a phase failure rolls back to the
 *   pre-switch snapshot, keeps maintenance, and returns 500 with the
 *   rolled-back job (`commit_status: failed`/`rolled_back`,
 *   `maintenance_active: true`) — still a job payload, so this client
 *   returns it instead of throwing.
 * - `POST /restores/{id}/reopen` (S56) verifies health then clears the
 *   filesystem maintenance lock (200 reopened / 503 still held with the
 *   polled job nested under `job`, which this client attaches to the
 *   thrown error so the page can render both).
 */

import { ApiError, CSRF_HEADER, getCsrfToken } from "../../identity/api";

export { ApiError };

export interface RestoreActor {
  id: string | null;
  username: string;
}

export interface RestoreInventoryEntry {
  rows: number;
  sha256: string;
  missing?: boolean;
}

/** One durable commit-phase record mirrors the backend `commit_history`
 * entry (backend owns it): phase/at/ok/detail, oldest-to-newest. */
export interface CommitHistoryEntry {
  phase: string;
  at: string | null;
  ok: boolean;
  detail: string;
}

/** Pending-job reconciliation summary (backend owns it): eligible durable
 * jobs requeued under the fenced generation, ineligible cancelled, terminal
 * rows preserved. Failed/unsolved CPTs stay explicit local-retry work. */
export interface ReconciledSummary {
  requeued: number;
  cancelled: number;
  preserved_terminal: number;
}

/** Rollback record (backend owns it): whether the prior database was
 * restored, when, and why the commit failed. */
export interface RollbackInfo {
  restored: boolean;
  at: string | null;
  reason: string;
}

/** Impact report mirrors `operations/restore.build_impact_report`
 * (backend owns it): backup date/schema/content/checksums/impact,
 * key-reentry consequence, staging digest, and `commit_available: false`.
 * S56 commit details below are optional extras the same report carries
 * once commit runs — the page reads the top-level `RestoreJob` polling
 * fields first and these only for session/grant/reconcile/rollback notes.
 */
export interface RestoreReport {
  backup_id: string;
  backup_created_at: string;
  manifest_schema: string;
  app_schema_version: string;
  archive_sha256: string;
  archive_bytes: number;
  artifacts: Record<string, string>;
  inventory: Record<string, RestoreInventoryEntry>;
  staged_counts: Record<string, number>;
  live_counts: Record<string, number>;
  replacement_impact: Record<string, { live: number; staged: number }>;
  deployment_generation: { live: number | null; staged: number | null };
  key_consequence: string;
  confirmation_digest: string;
  commit_available: boolean;
  commit_note: string;
  sessions_revoked?: number | null;
  grants_revoked?: number | null;
  reconciled?: ReconciledSummary | null;
  rollback?: RollbackInfo | null;
  committed_at?: string | null;
}

/** Public staging + commit shape mirrors `operations/restore.safe_job`
 * (backend owns it): status + report/digest plus S56 commit polling, no
 * filesystem paths, no secrets. */
export interface RestoreJob {
  id: string;
  status: string;
  created_by: RestoreActor;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  /** Computed on read: a replacing upload or TTL expiry invalidates this
   * confirmation, so commit rejects it. */
  expired: boolean;
  backup_id: string | null;
  backup_created_at: string | null;
  app_schema_version: string | null;
  archive_sha256: string | null;
  archive_bytes: number | null;
  confirmation_digest: string | null;
  report: RestoreReport | null;
  manifest: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  /** S56 commit polling (backend owns it): `idle` before any commit,
   * `in_progress` while phases run, `succeeded` on verified reopen,
   * `failed`/`rolled_back` when the prior database was restored and
   * maintenance is held until verified healthy. */
  commit_status: string;
  commit_phase: string | null;
  commit_digest: string | null;
  pre_restore_backup_id: string | null;
  commit_error_code: string | null;
  commit_error_message: string | null;
  commit_history: CommitHistoryEntry[];
  generation_before: number | null;
  generation_after: number | null;
  /** Filesystem maintenance lock, independent of either database: HTTP
   * writes fail 503 MAINTENANCE behind it except login + restores routes. */
  maintenance_active: boolean;
}

/** Thrown action error that still carries the latest polled job (reopen
 * 503 nests it under `job`): the page renders the job plus the message. */
export type RestoreActionError = ApiError & { job?: RestoreJob };

function isRestoreJob(value: unknown): value is RestoreJob {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as { id?: unknown; commit_status?: unknown };
  return typeof candidate.id === "string" && candidate.id.length > 0;
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function toApiError(
  response: Response,
  payload: unknown,
  fallback: string,
): ApiError {
  let code = "REQUEST_FAILED";
  let message = `${fallback} (${response.status}).`;
  let fieldErrors: Record<string, string[]> = {};
  if (typeof payload === "object" && payload !== null) {
    const body = payload as {
      code?: unknown;
      message?: unknown;
      field_errors?: unknown;
    };
    if (typeof body.code === "string" && body.code !== "") {
      code = body.code;
    }
    if (typeof body.message === "string" && body.message !== "") {
      message = body.message;
    }
    if (typeof body.field_errors === "object" && body.field_errors !== null) {
      fieldErrors = body.field_errors as Record<string, string[]>;
    }
  }
  return new ApiError(response.status, code, message, fieldErrors);
}

async function failOr<T>(response: Response, onOk: () => Promise<T>): Promise<T> {
  if (!response.ok) {
    throw toApiError(response, await parseBody(response), "Request failed");
  }
  return onOk();
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await parseBody(response);
  return failOr(response, async () => (payload ?? {}) as T);
}

/** Stage one uploaded backup zip for validation (admin-only mutation,
 * 201 with the terminal job). A new upload expires prior succeeded
 * digests server-side, so replacing the file invalidates the old
 * confirmation — the page simply renders the newest job. */
export async function validateRestore(archive: Blob): Promise<RestoreJob> {
  const headers: Record<string, string> = { "Content-Type": "application/zip" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  const response = await fetch("/api/v1/restores/validate", {
    method: "POST",
    credentials: "include",
    headers,
    body: archive,
  });
  return readJson<RestoreJob>(response);
}

/** Staging + commit progress/impact read (admin-only, no CSRF, like
 * backups reads). Carries the S56 commit polling fields so the page can
 * render phases/history/maintenance without duplicating backend logic. */
export async function getRestore(restoreId: string): Promise<RestoreJob> {
  const response = await fetch(`/api/v1/restores/${encodeURIComponent(restoreId)}`, {
    method: "GET",
    credentials: "include",
  });
  return readJson<RestoreJob>(response);
}

/** Commit one staged restore behind maintenance (admin-only mutation with
 * CSRF). The digest must exactly match the validated staged artifact;
 * stale/mismatched confirmations fail 409 with no mutation, malformed
 * digests fail 422. Success is 200 with the terminal job; a phase failure
 * returns 500 with the rolled-back job (prior database restored,
 * maintenance held) — returned, not thrown, so the caller renders the
 * rollback from the same payload the server durably stored. */
export async function commitRestore(
  restoreId: string,
  confirmationDigest: string,
): Promise<RestoreJob> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  const response = await fetch(
    `/api/v1/restores/${encodeURIComponent(restoreId)}/commit`,
    {
      method: "POST",
      credentials: "include",
      headers,
      body: JSON.stringify({ confirmation_digest: confirmationDigest }),
    },
  );
  const payload = await parseBody(response);
  if (response.ok) {
    return (payload ?? {}) as RestoreJob;
  }
  // ponytail: 500 here is a terminal domain result (rolled back + held),
  // not a transport failure — return the job so the page shows it directly.
  if (response.status === 500 && isRestoreJob(payload)) {
    return payload;
  }
  const err = toApiError(response, payload, "Restore commit failed") as RestoreActionError;
  if (
    typeof payload === "object" &&
    payload !== null &&
    isRestoreJob((payload as { job?: unknown }).job)
  ) {
    err.job = (payload as { job: RestoreJob }).job;
  }
  throw err;
}

/** Verify health (login/read/replay) then clear maintenance (admin-only
 * mutation with CSRF). Success is 200 with the reopened job; while the
 * deployment is unhealthy the server keeps the lock (503) with the polled
 * job nested under `job` — thrown with the job attached. */
export async function reopenRestore(restoreId: string): Promise<RestoreJob> {
  const headers: Record<string, string> = {};
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  const response = await fetch(
    `/api/v1/restores/${encodeURIComponent(restoreId)}/reopen`,
    {
      method: "POST",
      credentials: "include",
      headers,
    },
  );
  const payload = await parseBody(response);
  if (response.ok) {
    return (payload ?? {}) as RestoreJob;
  }
  const err = toApiError(
    response,
    payload,
    "Reopen health check failed",
  ) as RestoreActionError;
  if (
    typeof payload === "object" &&
    payload !== null &&
    isRestoreJob((payload as { job?: unknown }).job)
  ) {
    err.job = (payload as { job: RestoreJob }).job;
  }
  throw err;
}
