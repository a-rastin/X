/* Admin backup HTTP client (S54 §§1-4, seams T1/T9; plan.md §10.2, FR-41–42).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). All three routes are admin-only: 401 unauthenticated, 403
 * otherwise (physicians included). The route guard in app/pages.tsx only
 * chooses what to render — authority stays server-side.
 *
 * - `POST /backups` is the single mutation: per-session CSRF token plus a
 *   fresh Idempotency-Key per submit (same key replays the original job
 *   without a new export; a fresh key mints a fresh archive, so a retry
 *   never overwrites a good prior backup). Bodyless — the key alone scopes
 *   replay per (operation, actor). Returns 201 with the terminal job
 *   (`succeeded` or `failed`); `GET /backups/{id}` is the stable polling
 *   interface the page uses while a job is non-terminal.
 * - `GET /backups/{id}` (progress + manifest read) and
 *   `GET /backups/{id}/download` (bounded zip) are reads: no
 *   CSRF/Idempotency headers, like the audit/exports reads.
 * - No secret handling anywhere here: cookies travel via credentials:include.
 *   The manifest carries hashes/inventory/notes only — no clinical bodies,
 *   no session tokens, no encryption key.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../../identity/api";

export { ApiError };

export interface BackupActor {
  id: string | null;
  username: string;
}

export interface BackupInventoryEntry {
  rows: number;
  sha256: string;
  missing?: boolean;
}

export interface BackupManifest {
  schema_version: string;
  backup_id: string;
  created_at: string;
  created_by: { id: string; username: string };
  app_schema_version: string;
  inventory: Record<string, BackupInventoryEntry>;
  artifacts: Record<string, string>;
  deployment_generation: number | null;
  exclusions: string;
  key_reentry_note: string;
  retention: string;
  runtime: Record<string, unknown>;
  warnings: string[];
}

/** Public job shape mirrors `operations/backup.safe_job` (backend owns it):
 * status + manifest (hashes/inventory), never a filesystem path. */
export interface BackupJob {
  id: string;
  status: string;
  created_by: BackupActor;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  archive_sha256: string | null;
  archive_bytes: number | null;
  error_code: string | null;
  error_message: string | null;
  manifest: BackupManifest | null;
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

/** Create one consistent full backup (admin-only mutation, 201). Fresh
 * Idempotency-Key per submit: a retry mints a new archive and never
 * overwrites a good prior backup (server mints a fresh UUID/path per
 * attempt; same key would replay instead). */
export async function createBackup(): Promise<BackupJob> {
  const headers: Record<string, string> = {};
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  headers["Idempotency-Key"] = newIdempotencyKey();
  const response = await fetch("/api/v1/backups", {
    method: "POST",
    credentials: "include",
    headers,
  });
  return readJson<BackupJob>(response);
}

/** Backup progress/manifest read (admin-only, no CSRF, like audit reads). */
export async function getBackup(backupId: string): Promise<BackupJob> {
  const response = await fetch(`/api/v1/backups/${encodeURIComponent(backupId)}`, {
    method: "GET",
    credentials: "include",
  });
  return readJson<BackupJob>(response);
}

/** Fetch a succeeded backup's zip as a Blob (authenticated same-origin
 * download). Failed jobs have no complete archive (server 409); purged
 * files are 404. */
export async function downloadBackupBlob(backupId: string): Promise<Blob> {
  const response = await fetch(
    `/api/v1/backups/${encodeURIComponent(backupId)}/download`,
    {
      method: "GET",
      credentials: "include",
    },
  );
  return failOr(response, async () => {
    const buffer = await response.arrayBuffer();
    const contentType =
      response.headers.get("content-type") ?? "application/zip";
    return new Blob([buffer], { type: contentType });
  });
}
