/* Admin CSV exports (S53 §§1-2, seam T9; plan.md §10.1, FR-03/FR-40).
 *
 * Admin-only navigation/route guard lives in app/pages.tsx (complement to
 * the server 403, never a replacement). This page calls the real
 * `GET /exports/patients.csv` and `GET /exports/physicians.csv` endpoints
 * only — authenticated same-origin fetch (session cookie via
 * credentials:include) turned into an attachment download via blob URL;
 * no fake rows, no credentials in the payload (server sends safe columns
 * only). Physicians get no nav item here and the guard renders
 * "Administrator access required." on the direct route.
 *
 * Import note (plan §10.1): the Patient ID column is exact 10-digit text
 * bytes — CSV carries no type information, so operators must import that
 * column as Text in spreadsheets. Formula-like text is server-neutralized
 * with a leading quote (no `="..."` wrappers are used anywhere); quotes
 * and newlines rely on standard CSV quoting.
 *
 * Stable wiring for the coming test slice (mirrors #audit-* conventions):
 * - heading `data-testid="exports-heading"` (`#exports-heading`)
 * - actions `data-testid="exports-patients"` (`#exports-patients`),
 *   `data-testid="exports-physicians"` (`#exports-physicians`)
 * - status `data-testid="exports-status"` (`#exports-status`, role=status;
 *   errors use role=alert)
 */

import { useState } from "react";
import {
  ApiError,
  fetchPatientsCsv,
  fetchPhysiciansCsv,
  saveBlob,
} from "./api";
import { useAuth } from "../../identity/auth";

type ExportKey = "patients" | "physicians";

export function ExportsPage() {
  const { sessionExpired } = useAuth();
  const [busy, setBusy] = useState<ExportKey | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(key: ExportKey): Promise<void> {
    if (busy !== null) {
      return;
    }
    setBusy(key);
    setStatus(null);
    setError(null);
    try {
      const blob =
        key === "patients" ? await fetchPatientsCsv() : await fetchPhysiciansCsv();
      const filename = key === "patients" ? "patients.csv" : "physicians.csv";
      saveBlob(blob, filename);
      setStatus(`Downloaded ${filename} (${blob.size} bytes).`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError(
        err instanceof ApiError && err.status === 403
          ? "Administrator access required."
          : err instanceof ApiError
            ? err.message
            : "Export download failed. Check the connection and retry.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <h2 className="xi-page-title" data-testid="exports-heading" id="exports-heading">
        Data exports
      </h2>
      <p className="xi-hint">
        Administrator list exports. Downloads are authenticated attachments
        (private, no-store); physicians are denied by the server.
      </p>

      <section className="xi-card" aria-labelledby="exports-actions-heading">
        <h3 className="xi-section-title" id="exports-actions-heading">
          List downloads
        </h3>
        <div className="xi-row-actions">
          <button
            className="xi-btn xi-btn-primary"
            type="button"
            data-testid="exports-patients"
            id="exports-patients"
            aria-describedby="exports-import-note"
            disabled={busy !== null}
            onClick={() => void run("patients")}
          >
            {busy === "patients" ? "Preparing…" : "Download patients CSV"}
          </button>
          <button
            className="xi-btn xi-btn-primary"
            type="button"
            data-testid="exports-physicians"
            id="exports-physicians"
            aria-describedby="exports-import-note"
            disabled={busy !== null}
            onClick={() => void run("physicians")}
          >
            {busy === "physicians" ? "Preparing…" : "Download physicians CSV"}
          </button>
        </div>
        {status !== null && (
          <p
            className="xi-status"
            role="status"
            data-testid="exports-status"
            id="exports-status"
          >
            {status}
          </p>
        )}
        {error !== null && (
          <p className="xi-form-error" role="alert" data-testid="exports-status">
            {error}
          </p>
        )}
      </section>

      <section className="xi-card" aria-labelledby="exports-notes-heading">
        <h3 className="xi-section-title" id="exports-notes-heading">
          Spreadsheet notes
        </h3>
        <p className="xi-hint" id="exports-import-note" style={{ marginBottom: 8 }}>
          Import the Patient ID column as Text: the file carries exact
          10-digit identifiers (e.g. 0012345678) but CSV has no type
          information, so spreadsheets may otherwise drop leading zeros. No
          column promises formula typing.
        </p>
        <p className="xi-hint" style={{ marginBottom: 0 }}>
          Text starting with = + - or @ carries a leading quote so it stays
          inert when opened directly; quoted commas, quotes, and newlines use
          standard CSV quoting. Exports contain safe list columns only — no
          passwords, hashes, or session secrets.
        </p>
      </section>
    </div>
  );
}
