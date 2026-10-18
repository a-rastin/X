/* Admin operational status (S58 §§3-4, seam T9 over T1; plan.md §11, FR-43).
 *
 * Admin-only navigation/route guard lives in app/pages.tsx (complement to
 * the server 401/403, never a replacement). This page calls the real
 * `GET /ops/status` endpoint only — authenticated same-origin fetch
 * (session cookie via credentials:include). It renders the safe payload
 * fields (counts/timestamps/alert names) and nothing else: no secrets,
 * record text, tokens, or clinical payloads can reach this view because the
 * typed client below whitelists exactly those fields.
 *
 * Behavior:
 * - Load on mount, then re-read roughly every 30s while the tab stays
 *   visible (hidden tabs skip ticks; polling stops on unmount). A Refresh
 *   button re-reads on demand without clearing the last good snapshot.
 * - Loading shows once (until the first snapshot); later reads keep the
 *   rendered sections. Errors use role=alert; the checked-at line uses
 *   role=status. A 401 expires the session via the auth context.
 *
 * Stable wiring (mirrors #backups-* conventions):
 * - heading `data-testid="status-heading"` (`#status-heading`)
 * - refresh `data-testid="status-refresh"` (`#status-refresh`)
 * - updated `data-testid="status-updated"` (`#status-updated`, role=status)
 * - alerts `data-testid="status-alerts"` (`#status-alerts`)
 * - metrics `data-testid="status-metrics"` (`#status-metrics`)
 */

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  getOpsStatus,
  type OpsAlert,
  type OpsStatus,
} from "./api";
import { useAuth } from "../../identity/auth";

// ponytail: 30s visible-tab refresh; alerts move on minute-scale thresholds.
const REFRESH_MS = 30000;
const REQUEST_GROUPS = ["saves", "searches", "polls", "other"] as const;

function formatAge(seconds: number | null): string {
  if (seconds === null) {
    return "none waiting";
  }
  if (seconds < 60) {
    return `${seconds.toFixed(1)} s`;
  }
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)} min ${(seconds % 60).toFixed(0)} s`;
  }
  if (seconds < 86400) {
    return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
  }
  return `${Math.floor(seconds / 86400)} d ${Math.floor((seconds % 86400) / 3600)} h`;
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) {
    return "unavailable";
  }
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function formatLatency(avgSeconds: number | null): string {
  if (avgSeconds === null) {
    return "no requests yet";
  }
  return `${(avgSeconds * 1000).toFixed(1)} ms`;
}

function alertDetail(alert: OpsAlert): string | null {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(alert)) {
    if (key === "name" || key === "message") {
      continue;
    }
    if (typeof value === "number" || typeof value === "string") {
      parts.push(`${key}: ${value}`);
    }
  }
  return parts.length > 0 ? parts.join("; ") : null;
}

function MetricTable({
  label,
  rows,
}: {
  label: string;
  rows: Array<[string, string]>;
}) {
  return (
    <table className="xi-table" aria-label={label}>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">Value</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([metric, value]) => (
          <tr key={metric}>
            <td>{metric}</td>
            <td>{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function StatusPage() {
  const { sessionExpired } = useAuth();
  const [snapshot, setSnapshot] = useState<OpsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const expiredRef = useRef(false);

  async function load(initial: boolean): Promise<void> {
    if (initial) {
      setLoading(true);
    } else {
      setRefreshing(true);
    }
    setError(null);
    try {
      const current = await getOpsStatus();
      setSnapshot(current);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        expiredRef.current = true;
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setError("Administrator access required.");
      } else {
        setError(
          err instanceof ApiError
            ? err.message
            : "Status read failed. Check the connection and refresh.",
        );
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  // Visible-tab refresh: hidden ticks are skipped, polling stops on unmount.
  useEffect(() => {
    let cancelled = false;
    const tick = (initial: boolean) => {
      if (cancelled || expiredRef.current) {
        return;
      }
      if (!initial && typeof document !== "undefined" && document.hidden) {
        return;
      }
      void load(initial);
    };
    tick(true);
    const timer = window.setInterval(() => tick(false), REFRESH_MS);
    const onVisibility = () => {
      if (typeof document === "undefined" || document.hidden) {
        return;
      }
      tick(false);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // sessionExpired is stable from the auth context; the loop keys on mount
    // only and reads the live endpoint every tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const queue = snapshot?.queue;
  const provider = snapshot?.provider;
  const inference = snapshot?.inference;
  const disk = snapshot?.disk;
  const backup = snapshot?.backup;
  const requests = snapshot?.requests;
  const alerts = snapshot?.alerts ?? [];
  const limitEntries =
    inference !== undefined && inference !== null
      ? Object.entries(inference.limits).sort(([a], [b]) => a.localeCompare(b))
      : [];

  return (
    <div>
      <h2 className="xi-page-title" data-testid="status-heading" id="status-heading">
        Operational status
      </h2>
      <p className="xi-hint">
        Live service gauges for the operator: queue age and worker heartbeat,
        provider attempts and authentication failures, inference limits, backup
        staging disk use, last backup success, and request counts. Counts and
        timestamps only — never record content or secrets.
      </p>

      <div className="xi-row-actions xi-no-print">
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          data-testid="status-refresh"
          id="status-refresh"
          disabled={loading || refreshing}
          onClick={() => void load(false)}
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {loading && snapshot === null && <p>Loading operational status…</p>}
      {error !== null && (
        <p className="xi-form-error" role="alert">
          {error}
        </p>
      )}
      {snapshot !== null && (
        <p
          className="xi-status"
          role="status"
          data-testid="status-updated"
          id="status-updated"
        >
          Checked at {snapshot.checked_at}.
        </p>
      )}

      {snapshot !== null && (
        <section className="xi-card" aria-labelledby="status-alerts">
          <h3
            className="xi-section-title"
            data-testid="status-alerts"
            id="status-alerts"
          >
            Alerts
          </h3>
          {alerts.length === 0 ? (
            <p className="xi-hint" style={{ marginBottom: 0 }}>
              No active alerts.
            </p>
          ) : (
            <ul style={{ marginBottom: 0, paddingLeft: 20 }}>
              {alerts.map((alert) => (
                <li key={alert.name} className="xi-warning-panel">
                  <strong>{alert.name}</strong>: {alert.message}
                  {alertDetail(alert) !== null && (
                    <> ({alertDetail(alert)})</>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {snapshot !== null &&
        queue !== undefined &&
        provider !== undefined &&
        inference !== undefined &&
        disk !== undefined &&
        backup !== undefined &&
        requests !== undefined && (
          <section
            className="xi-card"
            aria-labelledby="status-metrics-heading"
            data-testid="status-metrics"
            id="status-metrics"
          >
            <h3 className="xi-section-title" id="status-metrics-heading">
              Metrics
            </h3>

            <h4 className="xi-section-title">Queue</h4>
            <MetricTable
              label="Queue"
              rows={[
                ["Queued generation jobs", String(queue.queued)],
                ["Leased generation jobs", String(queue.leased)],
                ["Queued local calculations", String(queue.local_queued)],
                ["Leased local calculations", String(queue.local_leased)],
                [
                  "Oldest queued age",
                  formatAge(queue.oldest_queued_age_seconds),
                ],
                [
                  "Oldest heartbeat age",
                  formatAge(queue.oldest_heartbeat_age_seconds),
                ],
              ]}
            />

            <h4 className="xi-section-title">
              Provider
            </h4>
            <MetricTable
              label="Provider"
              rows={[
                ["Provider attempts (24h)", String(provider.attempts_24h)],
                ["Provider failures (24h)", String(provider.failures_24h)],
                [
                  "Provider auth failures (24h)",
                  String(provider.auth_failures_24h),
                ],
                ["Provider retries (24h)", String(provider.retries_24h)],
              ]}
            />

            <h4 className="xi-section-title">
              Inference limits
            </h4>
            <MetricTable
              label="Inference limits"
              rows={[
                ...limitEntries.map(
                  ([name, value]): [string, string] => [name, String(value)],
                ),
                [
                  "Local calculation failures (24h)",
                  String(inference.local_failures_24h),
                ],
              ]}
            />

            <h4 className="xi-section-title">
              Disk
            </h4>
            <MetricTable
              label="Disk"
              rows={[
                [
                  "Backup staging disk used",
                  disk.used_percent === null
                    ? "unavailable"
                    : `${disk.used_percent.toFixed(1)}%`,
                ],
                ["Used bytes", formatBytes(disk.used_bytes)],
                ["Total bytes", formatBytes(disk.total_bytes)],
              ]}
            />

            <h4 className="xi-section-title">
              Backup
            </h4>
            <MetricTable
              label="Backup"
              rows={[
                ["Last backup status", backup.last_job_status ?? "never"],
                ["Last backup success", backup.last_success_at ?? "never"],
                ["Last backup id", backup.last_job_id ?? "none"],
              ]}
            />

            <h4 className="xi-section-title">
              Requests
            </h4>
            <table className="xi-table" aria-label="Requests">
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Requests</th>
                  <th scope="col">Errors</th>
                  <th scope="col">Avg latency</th>
                </tr>
              </thead>
              <tbody>
                {REQUEST_GROUPS.map((group) => {
                  const entry = requests[group] ?? {
                    count: 0,
                    errors: 0,
                    avg_seconds: null,
                  };
                  return (
                    <tr key={group}>
                      <td>{group}</td>
                      <td>{entry.count}</td>
                      <td>{entry.errors}</td>
                      <td>{formatLatency(entry.avg_seconds)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        )}
    </div>
  );
}
