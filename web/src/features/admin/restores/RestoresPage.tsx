/* Staged restore validation without live mutation (S55 §§1-4, seam T10
 * over T1; plan.md §10.3 steps 1-3, staging only; FR-41, NFR-02/04).
 *
 * Admin-only navigation/route guard lives in app/pages.tsx (complement to
 * the server 401/403, never a replacement). This page calls the real
 * `POST /restores/validate` (raw zip upload to isolated staging) and
 * `GET /restores/{id}` (progress/impact read) endpoints only —
 * authenticated same-origin fetch (session cookie via
 * credentials:include). The upload mutation carries the per-session CSRF
 * token; the progress read carries no CSRF/idempotency, like the
 * audit/backups reads.
 *
 * Behavior:
 * - One file input stages a backup zip (bounded synchronous validation;
 *   201 carries the terminal `succeeded`/`failed` job). A "Re-check
 *   status" read re-GETs the current job so TTL expiry or a replacing
 *   upload surfaces as `expired: true` (stale confirmations S56 must
 *   reject).
 * - A succeeded job shows its impact report (backup date/schema/content/
 *   checksums/live-vs-staged impact, key-reentry consequence) plus the
 *   immutable confirmation digest S56 will require exactly.
 * - A failed job shows its validation error; live patients/login/jobs are
 *   untouched server-side (only staging + audit rows are written).
 * - There is deliberately NO Commit button and no live-mutation path here:
 *   commit/switch/rollback belong to S56. The page says so next to the
 *   digest so an operator never mistakes staging for replacement.
 *
 * Stable wiring for the coming test slice (mirrors #backups-* conventions):
 * - heading `data-testid="restores-heading"` (`#restores-heading`)
 * - file input `data-testid="restores-upload"` (`#restores-upload`)
 * - status `data-testid="restores-status"` (`#restores-status`, role=status)
 * - digest `data-testid="restores-digest"` (`#restores-digest`)
 * - report `data-testid="restores-report"` (`#restores-report`)
 * - error `data-testid="restores-error"` (`#restores-error`, role=alert)
 */

import { useState } from "react";
import {
  ApiError,
  getRestore,
  validateRestore,
  type RestoreJob,
} from "./api";
import { useAuth } from "../../identity/auth";

function shortHash(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 16)}…` : hash;
}

export function RestoresPage() {
  const { sessionExpired } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<RestoreJob | null>(null);
  const [uploading, setUploading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function failAs(err: unknown, fallback: string): string {
    if (err instanceof ApiError && err.status === 403) {
      return "Administrator access required.";
    }
    return err instanceof ApiError ? err.message : fallback;
  }

  function applyJob(staged: RestoreJob): void {
    setJob(staged);
    if (staged.status === "succeeded") {
      setStatus(
        staged.expired
          ? "Staging expired or superseded — this confirmation is stale and must not be committed."
          : "Archive staged and validated. Live data unchanged — review the report and digest below.",
      );
      setError(null);
    } else if (staged.status === "failed") {
      setStatus(null);
      setError(
        staged.error_message ?? "Restore validation failed. Live data unchanged.",
      );
    } else {
      setStatus("Restore staging in progress…");
      setError(null);
    }
  }

  async function upload(): Promise<void> {
    if (uploading || refreshing || file === null) {
      return;
    }
    setUploading(true);
    setStatus("Validating archive in staging…");
    setError(null);
    try {
      const staged = await validateRestore(file);
      applyJob(staged);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError(failAs(err, "Restore upload failed. Check the connection and retry."));
      setStatus(null);
    } finally {
      setUploading(false);
    }
  }

  async function refresh(): Promise<void> {
    if (uploading || refreshing || job === null) {
      return;
    }
    setRefreshing(true);
    try {
      const current = await getRestore(job.id);
      applyJob(current);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError(failAs(err, "Restore status re-check failed. Check the connection and retry."));
    } finally {
      setRefreshing(false);
    }
  }

  const busy = uploading || refreshing;
  const report = job?.report ?? null;
  const inventoryEntries =
    report !== null
      ? Object.entries(report.inventory).sort(([a], [b]) => a.localeCompare(b))
      : [];
  const artifactEntries =
    report !== null
      ? Object.entries(report.artifacts).sort(([a], [b]) => a.localeCompare(b))
      : [];
  const impactEntries =
    report !== null
      ? Object.entries(report.replacement_impact).sort(([a], [b]) => a.localeCompare(b))
      : [];

  return (
    <div>
      <h2 className="xi-page-title" data-testid="restores-heading" id="restores-heading">
        Staged restores
      </h2>
      <p className="xi-hint">
        Administrator restore staging: an uploaded backup zip is validated
        into isolated staging only — live patients, logins, and jobs are
        never touched here. A succeeded staging reports backup date, schema,
        content, checksums, replacement impact, and the key-reentry
        consequence, plus the immutable confirmation digest a later commit
        step will require exactly.
      </p>

      <section className="xi-card" aria-labelledby="restores-upload-heading">
        <h3 className="xi-section-title" id="restores-upload-heading">
          Upload backup zip to staging
        </h3>
        <div className="xi-row-actions xi-no-print">
          <label className="xi-hint" htmlFor="restores-upload">
            Backup archive (.zip)
          </label>
          <input
            type="file"
            accept=".zip,application/zip,application/octet-stream"
            data-testid="restores-upload"
            id="restores-upload"
            disabled={busy}
            onChange={(event) => {
              const picked = event.target.files?.[0] ?? null;
              setFile(picked);
              setError(null);
              if (picked !== null) {
                setStatus(`Selected ${picked.name} (${picked.size} bytes).`);
              }
            }}
          />
          <button
            className="xi-btn xi-btn-primary"
            type="button"
            disabled={busy || file === null}
            onClick={() => void upload()}
          >
            {uploading ? "Validating…" : "Validate in staging"}
          </button>
          {job !== null && (
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={busy}
              onClick={() => void refresh()}
            >
              {refreshing ? "Re-checking…" : "Re-check status"}
            </button>
          )}
        </div>
        {status !== null && (
          <p
            className="xi-status"
            role="status"
            data-testid="restores-status"
            id="restores-status"
          >
            {status}
          </p>
        )}
        {error !== null && (
          <p
            className="xi-form-error"
            role="alert"
            data-testid="restores-error"
            id="restores-error"
          >
            {error}
          </p>
        )}
        {job !== null && (
          <dl className="xi-hint" style={{ marginBottom: 0 }}>
            <dt style={{ display: "inline", fontWeight: 600 }}>Restore id: </dt>
            <dd style={{ display: "inline", margin: 0 }}>{job.id}</dd>
            {" · "}
            <dt style={{ display: "inline", fontWeight: 600 }}>Status: </dt>
            <dd style={{ display: "inline", margin: 0 }}>{job.status}</dd>
            {job.expired && (
              <>
                {" · "}
                <dd style={{ display: "inline", margin: 0 }}>
                  Expired or superseded — confirmation stale.
                </dd>
              </>
            )}
          </dl>
        )}
      </section>

      {job?.confirmation_digest && (
        <section
          className="xi-card"
          aria-labelledby="restores-digest-heading"
          data-testid="restores-digest"
          id="restores-digest"
        >
          <h3 className="xi-section-title" id="restores-digest-heading">
            Confirmation digest
          </h3>
          <p className="xi-hint">
            Immutable staging digest — record it exactly; a later commit step
            must present this same value before expiry.
            {job.expired
              ? " This digest has expired or been superseded and must not be committed."
              : ""}
          </p>
          <p className="xi-hint" style={{ marginBottom: 8 }}>
            <code>{job.confirmation_digest}</code>
          </p>
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            Staging only: no restore-over-live, commit, switch, or rollback
            exists in this session — that belongs to S56. This page never
            renders a Commit control.
          </p>
        </section>
      )}

      {report !== null && (
        <section
          className="xi-card"
          aria-labelledby="restores-report-heading"
          data-testid="restores-report"
          id="restores-report"
        >
          <h3 className="xi-section-title" id="restores-report-heading">
            Validation report
          </h3>
          <p className="xi-hint">
            Backup <code>{report.backup_id}</code>, created{" "}
            {report.backup_created_at}; manifest{" "}
            <code>{report.manifest_schema}</code>, app schema{" "}
            <code>{report.app_schema_version}</code>; archive{" "}
            <span title={report.archive_sha256}>
              {shortHash(report.archive_sha256)}
            </span>{" "}
            ({report.archive_bytes} bytes).
          </p>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Staged content
          </h4>
          <table className="xi-table" aria-label="Staged table inventory">
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col">Staged rows</th>
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
            Archive checksums
          </h4>
          <ul className="xi-hint" style={{ marginBottom: 8 }}>
            {artifactEntries.map(([name, hash]) => (
              <li key={name}>
                <code>{name}</code> — <span title={hash}>{shortHash(hash)}</span>
              </li>
            ))}
          </ul>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Replacement impact (live vs staged — comparison only, no merge)
          </h4>
          <table className="xi-table" aria-label="Replacement impact">
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col">Live rows</th>
                <th scope="col">Staged rows</th>
              </tr>
            </thead>
            <tbody>
              {impactEntries.map(([name, counts]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{counts.live}</td>
                  <td>{counts.staged}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="xi-hint" style={{ marginTop: 8 }}>
            Deployment generation: live{" "}
            {report.deployment_generation.live ?? "unknown"}, staged{" "}
            {report.deployment_generation.staged ?? "unknown"}.
          </p>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Key-reentry consequence
          </h4>
          <p className="xi-hint" style={{ marginBottom: 8 }}>
            {report.key_consequence}
          </p>
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            {report.commit_note}
          </p>
        </section>
      )}
    </div>
  );
}
