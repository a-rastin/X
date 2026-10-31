/* Operational status HTTP client (S58 §§3-4, seams T1/T9; plan.md §11, FR-43).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). `GET /ops/status` is an admin-only read: 401 unauthenticated,
 * 403 otherwise (physicians included). The route guard in app/pages.tsx only
 * chooses what to render — authority stays server-side. No CSRF needed for
 * this read, like the audit/exports reads.
 *
 * The payload carries counts and timestamps only — queue age/heartbeat,
 * provider attempt/auth/retry counters, inference limits, disk usage, last
 * backup success, per-group request gauges, and the alert list. No secrets,
 * record text, tokens, or clinical payloads travel here (the server already
 * excludes them; this client renders only the whitelisted fields below).
 */

import { ApiError } from "../../identity/api";

export { ApiError };

export interface OpsQueue {
  queued: number;
  leased: number;
  local_queued: number;
  local_leased: number;
  oldest_queued_age_seconds: number | null;
  oldest_heartbeat_age_seconds: number | null;
}

export interface OpsProvider {
  timeout_seconds: number;
  attempts_24h: number;
  failures_24h: number;
  auth_failures_24h: number;
  retries_24h: number;
}

export interface OpsInference {
  limits: Record<string, number>;
  local_failures_24h: number;
}

export interface OpsDisk {
  total_bytes: number | null;
  used_bytes: number | null;
  used_percent: number | null;
}

export interface OpsBackup {
  last_job_id: string | null;
  last_job_status: string | null;
  last_success_at: string | null;
}

export interface OpsRequestGroup {
  count: number;
  errors: number;
  avg_seconds: number | null;
}

export interface OpsAlert {
  name: string;
  message: string;
  [detail: string]: unknown;
}

/** Public status shape mirrors `operations/status.collect_status` (backend owns it). */
export interface OpsStatus {
  service: string;
  checked_at: string;
  queue: OpsQueue;
  provider: OpsProvider;
  inference: OpsInference;
  disk: OpsDisk;
  backup: OpsBackup;
  requests: Record<string, OpsRequestGroup>;
  alerts: OpsAlert[];
}

/** Operational status read (admin-only, no CSRF, like audit reads). */
export async function getOpsStatus(): Promise<OpsStatus> {
  const response = await fetch("/api/v1/ops/status", {
    method: "GET",
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
    const body = (payload ?? {}) as { code?: string; message?: string };
    throw new ApiError(
      response.status,
      body.code ?? "REQUEST_FAILED",
      body.message ?? `Request failed (${response.status}).`,
    );
  }
  return (payload ?? {}) as OpsStatus;
}
