/* Admin consistent full backups (S54 §§1-4, seam T9; plan.md §10.2, FR-41–42).
 *
 * Admin-only navigation/route guard lives in app/pages.tsx (complement to
 * the server 401/403, never a replacement). This page calls the real
 * `POST /backups`, `GET /backups/{id}`, and `GET /backups/{id}/download`
 * endpoints only — authenticated same-origin fetch (session cookie via
 * credentials:include); the zip turns into an attachment download via the
 * shared exports blob helper. No restore surface exists here: staging and
 * commit belong to S55/S56, so this page never renders a Commit button.
 *
 * Behavior:
 * - One trigger button creates a backup (bounded synchronous build; 201
 *   carries the terminal job). Non-terminal jobs (`pending`/`running`,
 *   reserved for a future background build) poll `GET /backups/{id}`
 *   roughly every 2s with hidden-tab backoff to 15s, like proposal-review;
 *   polling stops at terminal states and after a bounded attempt budget.
 * - A failed job shows its error with a retry-new action (fresh
 *   Idempotency-Key, fresh server UUID/path) that never overwrites a good
 *   prior backup: the last succeeded job keeps its own download link.
 * - The manifest renders as inventory (tables/counts/hashes), artifact
 *   hashes, runtime/policy versions, warnings, and the key-reentry note —
 *   hashes and counts only, never clinical bodies or secrets (the server
 *   already excludes sessions, usable tokens, and the encryption key).
 *
 * Stable wiring for the coming test slice (mirrors #exports-* conventions):
 * - heading `data-testid="backups-heading"` (`#backups-heading`)
 * - create `data-testid="backups-create"` (`#backups-create`)
 * - status `data-testid="backups-status"` (`#backups-status`, role=status;
 *   errors use role=alert)
 * - download `data-testid="backups-download"` (`#backups-download`)
 * - manifest `data-testid="backups-manifest"` (`#backups-manifest`)
 */

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  createBackup,
  downloadBackupBlob,
  getBackup,
  type BackupJob,
} from "./api";
import { saveBlob } from "../exports/api";
import { useAuth } from "../../identity/auth";

const POLL_MS = 2000;
const HIDDEN_POLL_MS = 15000;
// ponytail: 60 foreground ticks (~2 min) then stop; the operator re-checks.
const MAX_POLLS = 60;

function isTerminal(status: string): boolean {
  return status === "succeeded" || status === "failed";
}

function shortHash(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 16)}…` : hash;
}

export function BackupsPage() {
  const { sessionExpired } = useAuth();
  const [job, setJob] = useState<BackupJob | null>(null);
  const [lastGood, setLastGood] = useState<BackupJob | null>(null);
  const [creating, setCreating] = useState(false);
  const [polling, setPolling] = useState(false);
  const [pollsUsed, setPollsUsed] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const jobIdRef = useRef<string | null>(null);

  function failAs(err: unknown, fallback: string): string {
    if (err instanceof ApiError && err.status === 403) {
      return "Administrator access required.";
    }
    return err instanceof ApiError ? err.message : fallback;
  }

  async function create(): Promise<void> {
    if (creating || polling) {
      return;
    }
    setCreating(true);
    setStatus("Creating backup…");
    setError(null);
    try {
      const created = await createBackup();
      setJob(created);
      jobIdRef.current = created.id;
      if (created.status === "succeeded") {
        setLastGood(created);
        setStatus(
          `Backup ready (${created.archive_bytes ?? "?"} bytes).`,
        );
      } else if (created.status === "failed") {
        setError(
          created.error_message ?? "Backup failed. Retry creates a new archive.",
        );
        setStatus(null);
      } else {
        // Async-compatible shape: keep polling until terminal (or budget).
        setPollsUsed(0);
        setPolling(true);
        setStatus("Backup in progress…");
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError(failAs(err, "Backup failed to start. Check the connection and retry."));
      setStatus(null);
    } finally {
      setCreating(false);
    }
  }

  // Bounded progress polling with hidden-tab backoff (proposal-review shape):
  // re-GET while non-terminal, stop at terminal states or the attempt budget.
  useEffect(() => {
    if (!polling || jobIdRef.current === null) {
      return;
    }
    let cancelled = false;
    let timer: number | null = null;
    let attempts = 0;
    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const tick = async () => {
      clearTimer();
      const target = jobIdRef.current;
      if (cancelled || target === null) {
        return;
      }
      attempts += 1;
      try {
        const current = await getBackup(target);
        if (cancelled || jobIdRef.current !== target) {
          return;
        }
        setJob(current);
        setPollsUsed(attempts);
        if (isTerminal(current.status)) {
          setPolling(false);
          if (current.status === "succeeded") {
            setLastGood(current);
            setStatus(`Backup ready (${current.archive_bytes ?? "?"} bytes).`);
          } else {
            setError(
              current.error_message ?? "Backup failed. Retry creates a new archive.",
            );
            setStatus(null);
          }
          return;
        }
      } catch (err) {
        if (cancelled || jobIdRef.current !== target) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          setPolling(false);
          sessionExpired();
          return;
        }
        // ponytail: one transient read failure must not kill the poll loop;
        // the budget below still bounds it.
        setStatus("Backup in progress (refresh retrying)…");
      }
      if (attempts >= MAX_POLLS) {
        setPolling(false);
        setStatus(
          "Backup is still running after the progress budget. Re-check status later.",
        );
        return;
      }
      timer = window.setTimeout(
        () => {
          void tick();
        },
        typeof document !== "undefined" && document.hidden ? HIDDEN_POLL_MS : POLL_MS,
      );
    };
    const onVisibility = () => {
      if (typeof document === "undefined" || document.hidden) {
        return;
      }
      void tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void tick();
    return () => {
      cancelled = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // sessionExpired is stable from the auth context; polling keys on the
    // job identity only (fresh job object per tick must not restart the loop).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [polling]);

  async function download(target: BackupJob): Promise<void> {
    if (downloading) {
      return;
    }
    setDownloading(true);
    setError(null);
    try {
      const blob = await downloadBackupBlob(target.id);
      saveBlob(blob, `x-insight-backup-${target.id}.zip`);
      setStatus(`Downloaded x-insight-backup-${target.id}.zip (${blob.size} bytes).`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError(failAs(err, "Backup download failed. Check the connection and retry."));
    } finally {
      setDownloading(false);
    }
  }

  const succeeded = job?.status === "succeeded" ? job : null;
  const downloadable = succeeded ?? lastGood;
  const manifest = job?.manifest ?? null;
  const inventoryEntries =
    manifest !== null
      ? Object.entries(manifest.inventory).sort(([a], [b]) => a.localeCompare(b))
      : [];
  const artifactEntries =
    manifest !== null
      ? Object.entries(manifest.artifacts).sort(([a], [b]) => a.localeCompare(b))
      : [];
  const networkCount = artifactEntries.filter(([name]) =>
    name.startsWith("networks/"),
  ).length;
  const busy = creating || polling || downloading;

  return (
    <div>
      <h2 className="xi-page-title" data-testid="backups-heading" id="backups-heading">
        Full backups
      </h2>
      <p className="xi-hint">
        Administrator full backups: one consistent database snapshot plus
        network XML, baselines, CPT revisions/results, acceptances, signed
        snapshots, audit, and pinned runtime metadata. Usable sessions and the
        deployment encryption key are never in the archive — migrating without
        the original key requires key re-entry.
      </p>

      <section className="xi-card" aria-labelledby="backups-actions-heading">
        <h3 className="xi-section-title" id="backups-actions-heading">
          Create backup
        </h3>
        <div className="xi-row-actions xi-no-print">
          <button
            className="xi-btn xi-btn-primary"
            type="button"
            data-testid="backups-create"
            id="backups-create"
            disabled={busy}
            onClick={() => void create()}
          >
            {creating
              ? "Creating…"
              : polling
                ? `In progress… (${pollsUsed}/${MAX_POLLS})`
                : job?.status === "failed"
                  ? "Retry new backup"
                  : job !== null
                    ? "Create new backup"
                    : "Create backup"}
          </button>
          {downloadable !== null && (
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              data-testid="backups-download"
              id="backups-download"
              disabled={busy}
              onClick={() => void download(downloadable)}
            >
              {downloading ? "Preparing…" : "Download backup zip"}
            </button>
          )}
        </div>
        {status !== null && (
          <p
            className="xi-status"
            role="status"
            data-testid="backups-status"
            id="backups-status"
          >
            {status}
          </p>
        )}
        {error !== null && (
          <p className="xi-form-error" role="alert" data-testid="backups-status">
            {error}
          </p>
        )}
        {job !== null && (
          <dl className="xi-hint" style={{ marginBottom: 0 }}>
            <dt style={{ display: "inline", fontWeight: 600 }}>Backup id: </dt>
            <dd style={{ display: "inline", margin: 0 }}>{job.id}</dd>
            {" · "}
            <dt style={{ display: "inline", fontWeight: 600 }}>Status: </dt>
            <dd style={{ display: "inline", margin: 0 }}>{job.status}</dd>
            {job.archive_sha256 !== null && (
              <>
                {" · "}
                <dt style={{ display: "inline", fontWeight: 600 }}>SHA-256: </dt>
                <dd
                  style={{ display: "inline", margin: 0 }}
                  title={job.archive_sha256}
                >
                  {shortHash(job.archive_sha256)}
                </dd>
              </>
            )}
          </dl>
        )}
      </section>

      {manifest !== null && (
        <section
          className="xi-card"
          aria-labelledby="backups-manifest-heading"
          data-testid="backups-manifest"
          id="backups-manifest"
        >
          <h3 className="xi-section-title" id="backups-manifest-heading">
            Backup manifest
          </h3>
          <p className="xi-hint">
            Manifest <code>{manifest.schema_version}</code>, app schema{" "}
            <code>{manifest.app_schema_version}</code>, created{" "}
            {manifest.created_at} by {manifest.created_by.username}.{" "}
            {networkCount} network XML export{networkCount === 1 ? "" : "s"};{" "}
            {manifest.warnings.length} warning{manifest.warnings.length === 1 ? "" : "s"}.
          </p>
          <table className="xi-table" aria-label="Backup table inventory">
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col">Rows</th>
                <th scope="col">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {inventoryEntries.map(([name, info]) => (
                <tr key={name}>
                  <td>
                    {name}
                    {info.missing === true ? " (unreadable — exported empty)" : ""}
                  </td>
                  <td>{info.rows}</td>
                  <td title={info.sha256}>{shortHash(info.sha256)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Archive files
          </h4>
          <ul className="xi-hint" style={{ marginBottom: 8 }}>
            {artifactEntries.map(([name, hash]) => (
              <li key={name}>
                <code>{name}</code> — <span title={hash}>{shortHash(hash)}</span>
              </li>
            ))}
          </ul>
          {manifest.warnings.length > 0 && (
            <>
              <h4 className="xi-section-title" style={{ marginTop: 16 }}>
                Warnings
              </h4>
              <ul className="xi-hint" style={{ marginBottom: 8 }}>
                {manifest.warnings.map((warning, index) => (
                  <li key={`warning-${index}`}>{warning}</li>
                ))}
              </ul>
            </>
          )}
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Key re-entry note
          </h4>
          <p className="xi-hint" style={{ marginBottom: 8 }}>
            {manifest.key_reentry_note}
          </p>
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            {manifest.exclusions} {manifest.retention}
          </p>
        </section>
      )}

      <section className="xi-card" aria-labelledby="backups-notes-heading">
        <h3 className="xi-section-title" id="backups-notes-heading">
          Recovery notes
        </h3>
        <p className="xi-hint" style={{ marginBottom: 0 }}>
          A retry always mints a new archive and never overwrites a good prior
          backup; interrupted exports leave a failed job with no downloadable
          archive. Restore staging and commit arrive in a later session — this
          page creates and inspects backups only.
        </p>
      </section>
    </div>
  );
}
