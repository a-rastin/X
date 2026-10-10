/* Staged restore validation HTTP client (S55 §§1-4, seam T10 over T1;
 * plan.md §10.3 steps 1-3, staging only; FR-41, NFR-02/04).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Both routes are admin-only: 401 unauthenticated, 403
 * otherwise (physicians included). The route guard in app/pages.tsx only
 * chooses what to render — authority stays server-side.
 *
 * - `POST /restores/validate` is the single mutation: per-session CSRF
 *   token plus the raw zip bytes as the request body
 *   (`application/zip`). Synchronous bounded staging — 201 carries the
 *   terminal job (`succeeded` or `failed`) with the impact report plus
 *   the immutable confirmation digest S56 will require exactly. Only
 *   `restore_jobs` + audit rows are written server-side; clinical tables
 *   are never touched, so failed staging leaves live state unchanged.
 * - `GET /restores/{id}` (progress/impact read) carries no
 *   CSRF/Idempotency headers, like the audit/backups reads. `expired`
 *   is computed on read so a replacing upload or TTL expiry invalidates
 *   prior confirmations without a background job.
 * - No commit route exists server-side until S56, so none exists here
 *   either: this client never offers a premature Commit.
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

/** Impact report mirrors `operations/restore.build_impact_report`
 * (backend owns it): backup date/schema/content/checksums/impact,
 * key-reentry consequence, staging digest, and `commit_available: false`.
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
}

/** Public staging shape mirrors `operations/restore.safe_job` (backend
 * owns it): status + report/digest, no filesystem paths, no Commit. */
export interface RestoreJob {
  id: string;
  status: string;
  created_by: RestoreActor;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  /** Computed on read: a replacing upload or TTL expiry invalidates this
   * confirmation, so S56 must reject it. */
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
}

async function failOr<T>(response: Response, onOk: () => Promise<T>): Promise<T> {
  if (!response.ok) {
    let code = "REQUEST_FAILED";
    let message = `Request failed (${response.status}).`;
    try {
      const body = (await response.json()) as {
        code?: string;
        message?: string;
      };
      code = body.code ?? code;
      message = body.message ?? message;
    } catch {
      /* non-JSON error body: keep the generic message above */
    }
    throw new ApiError(response.status, code, message);
  }
  return onOk();
}

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
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

/** Staging progress/impact read (admin-only, no CSRF, like backups reads). */
export async function getRestore(restoreId: string): Promise<RestoreJob> {
  const response = await fetch(`/api/v1/restores/${encodeURIComponent(restoreId)}`, {
    method: "GET",
    credentials: "include",
  });
  return readJson<RestoreJob>(response);
}
