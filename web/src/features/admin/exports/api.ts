/* Admin export/report HTTP client (S53 §§1-2, seams T1/T9; plan.md §10.1).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Both CSV endpoints and the signed-patient report are
 * admin-only: 401 unauthenticated, 403 otherwise (physicians included,
 * per the provisional physician-print policy in plan §§1.3, 2.1 — the
 * frontend never offers a physician report button). Route guards in
 * app/pages.tsx only choose what to render — authority stays server-side.
 * Reads carry no CSRF/Idempotency headers (GET, like the audit reads).
 * No secret handling anywhere here: cookies travel via credentials:include.
 *
 * - `GET /exports/patients.csv` / `GET /exports/physicians.csv` return
 *   `text/csv` (UTF-8, stable headers, QUOTE_MINIMAL, formula-neutralized
 *   text; the Patient ID column is exact 10-digit text bytes — CSV carries
 *   no types, so the Exports page tells operators to import it as Text and
 *   never promises formula-typed columns).
 * - `GET /patients/{id}/report` returns escaped signed-only HTML
 *   (chronology, frozen clinical content, secondary plans, signatures,
 *   addenda, per-question original + accepted CPTs/results with versions
 *   and adjustment indicators). Private drafts are never fetched here —
 *   no draft endpoint is called by this module.
 */

import { ApiError } from "../../identity/api";

export { ApiError };

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

/** Fetch one CSV export as a Blob (authenticated same-origin download). */
async function fetchCsvBlob(path: string): Promise<Blob> {
  const response = await fetch(`/api/v1${path}`, {
    method: "GET",
    credentials: "include",
  });
  return failOr(response, async () => blobOf(response));
}

async function blobOf(response: Response): Promise<Blob> {
  const buffer = await response.arrayBuffer();
  const contentType =
    response.headers.get("content-type") ?? "text/csv; charset=utf-8";
  return new Blob([buffer], { type: contentType });
}

export async function fetchPatientsCsv(): Promise<Blob> {
  return fetchCsvBlob("/exports/patients.csv");
}

export async function fetchPhysiciansCsv(): Promise<Blob> {
  return fetchCsvBlob("/exports/physicians.csv");
}

/** Trigger a same-origin authenticated CSV download (blob object URL). */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // ponytail: revoke on next tick so the download starts first.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Fetch the escaped signed-patient report HTML as text (never parsed as
 * JSON, never innerHTML'd by callers — ReportPage embeds it via a sandboxed
 * iframe srcDoc, documented there). */
export async function fetchPatientReportHtml(patientId: string): Promise<string> {
  const response = await fetch(
    `/api/v1/patients/${encodeURIComponent(patientId)}/report`,
    {
      method: "GET",
      credentials: "include",
      headers: { Accept: "text/html" },
    },
  );
  return failOr(response, () => response.text());
}
