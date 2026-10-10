/* Admin printable longitudinal patient report (S53 §§3-4, seam T9;
 * plan.md §10.1, FR-40; ui-context "Complete CPT probability review").
 *
 * Admin-only route `#/patients/:id/report` (guard in app/pages.tsx
 * complements the server 403, never replaces it — physicians get
 * "Administrator access required." and no report button is rendered for
 * them anywhere, per the provisional physician-print policy in plan
 * §§1.3, 2.1). This page fetches ONLY the signed-records endpoint
 * `GET /patients/{id}/report`; it never fetches or renders private drafts
 * (no draft endpoint is called here).
 *
 * Rendering/safety choice: the backend HTML is pre-escaped server-side
 * (every value through `html.escape`; markup stays inert) and is embedded
 * via `<iframe srcDoc>` — never via `dangerouslySetInnerHTML` on the
 * fetched string. `sandbox="allow-same-origin"` blocks scripts, forms,
 * and popups while letting the parent auto-size the frame to the content
 * height (so the whole report reads and prints without an inner scroll
 * box). The embedded document is static HTML+CSS with no scripts.
 *
 * Print: the frame auto-height plus the backend's inline print rules
 * (encounter page-break-before, table rows avoid breaks) keep complete
 * CPT tables legible; outer print CSS (theme.css) hides app chrome and
 * forces ink-on-white so both editor themes print readably. No PDF
 * library, no LLM prose — sections come from frozen signed snapshots.
 *
 * Stable wiring for the coming test slice (mirrors #audit-* conventions):
 * - heading `data-testid="report-heading"` (`#report-heading`)
 * - research label `data-testid="report-research-label"`
 *   (`#report-research-label`, also printed)
 * - frame `data-testid="report-frame"` (`#report-frame`)
 * - print `data-testid="report-print"` (`#report-print`)
 * - status `data-testid="report-status"` (`#report-status`, role=status;
 *   errors use role=alert)
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchPatientReportHtml } from "./api";
import { useAuth } from "../../identity/auth";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string; forbidden: boolean; missing: boolean }
  | { kind: "ready"; html: string };

/** Grow the frame to its content height (screen + print) once the
 * sandboxed same-origin document is available. */
function sizeFrameToContent(frame: HTMLIFrameElement | null): void {
  if (frame === null) {
    return;
  }
  try {
    const doc = frame.contentDocument;
    if (doc === null) {
      return;
    }
    const height = Math.max(
      doc.documentElement.scrollHeight,
      doc.body?.scrollHeight ?? 0,
    );
    if (height > 0) {
      frame.style.height = `${height + 24}px`;
    }
  } catch {
    /* opaque document: keep the fallback min-height; content still reads
     * via the frame's own scroll region. */
  }
}

export function ReportPage({ patientId }: { patientId: string }) {
  const { sessionExpired } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  const reload = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const html = await fetchPatientReportHtml(patientId);
      setState({ kind: "ready", html });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Report failed to load. Check the connection and retry.",
        forbidden: err instanceof ApiError && err.status === 403,
        missing: err instanceof ApiError && err.status === 404,
      });
    }
  }, [patientId, sessionExpired]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div>
      <h2 className="xi-page-title" data-testid="report-heading" id="report-heading">
        Longitudinal patient report
      </h2>
      <div
        className="xi-notice"
        role="status"
        data-testid="report-research-label"
        id="report-research-label"
      >
        <p style={{ margin: 0 }}>
          This application is a research prototype and must not be used as the
          sole basis for treating patients. Signed records only — private
          drafts are excluded.
        </p>
      </div>

      {state.kind === "loading" && (
        <p role="status" data-testid="report-status" id="report-status">
          Loading printable report…
        </p>
      )}
      {state.kind === "error" && (
        <div>
          <p className="xi-form-error" role="alert" data-testid="report-status">
            {state.forbidden
              ? "Administrator access required."
              : state.missing
                ? "Patient not found."
                : state.message}
          </p>
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void reload()}
            >
              Retry
            </button>
            <a className="xi-btn xi-btn-secondary" href={`#/patients/${patientId}/chart`}>
              Back to chart
            </a>
          </div>
        </div>
      )}
      {state.kind === "ready" &&
        (state.html.trim() === "" ? (
          <p role="status" data-testid="report-status">
            No signed encounters yet — nothing printable for this patient.
          </p>
        ) : (
          <section className="xi-card" aria-label="Printable signed report">
            <div className="xi-row-actions xi-no-print">
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                data-testid="report-print"
                id="report-print"
                onClick={() => window.print()}
              >
                Print report
              </button>
              <a
                className="xi-btn xi-btn-secondary"
                href={`#/patients/${patientId}/chart`}
              >
                Back to chart
              </a>
            </div>
            <p className="xi-hint xi-no-print" role="status" data-testid="report-status">
              Signed-only printable report loaded. Complete original and
              accepted CPT tables, versions, adjustment indicators, and
              attribution render inside the frame below.
            </p>
            <iframe
              className="xi-report-frame"
              data-testid="report-frame"
              id="report-frame"
              title={`Printable signed report for patient ${patientId}`}
              sandbox="allow-same-origin"
              srcDoc={state.html}
              onLoad={(event) => sizeFrameToContent(event.currentTarget)}
            />
          </section>
        ))}
    </div>
  );
}
