/* Versioned DDI evidence panel (S20, plan.md §§6.3, 9; FR-14-15).
 *
 * Reads only: GET /encounters/{id}/ddi-report (see ./api.ts). The parent
 * passes `refreshKey`, which changes after every acknowledged medications
 * save and every autosave revision, so changed medications show the pending
 * state until the current fingerprint matches — a stale report is never
 * presented as current (pending/error carry report:null by contract).
 *
 * Display rules (S19 shape, verbatim):
 * - severity is shown exactly as reported (highest_known_severity plus an
 *   explicit unknown-severity note); conflicts[] are listed, never merged;
 * - every evidence assertion shows raw_text + management (when present) +
 *   source location (source_path + span_start–span_end) + direction;
 * - catalog drugs/pairs without coverage show "coverage unavailable",
 *   including zero-pair reports (fewer than two medications) via
 *   coverage_unavailable_medications[] + limitations[];
 * - the covered pair status reads "No listed interaction in this dataset"
 *   (the reviewed explicit-coverage wording); the words "safe" and
 *   "no interaction" never appear.
 * - Source and user text render as plain text via React default escaping
 *   (no dangerouslySetInnerHTML), so literal markup shows literally.
 *
 * Accessibility + theming: native headings/lists (keyboard free), the report
 * status is an aria-live region, errors use role=alert with Retry and take
 * focus, styling reuses semantic tokens only (.xi-warning-panel for pending /
 * coverage-unavailable, .xi-history-panel for the current report, both
 * themes). No animation (global reduced-motion rule covers).
 *
 * E2E selector contract (for dev-test e2e/ddi.spec.ts):
 * - panel `#ddi-report-panel`
 * - pending state `#ddi-pending` (reason text inside)
 * - error state `#ddi-error` (role=alert) + retry `#ddi-retry`
 * - current report `#ddi-report` with `#ddi-report-status` (aria-live)
 * - pairs list `#ddi-report-pairs`, per-pair `#ddi-report-pair-{index}`
 * - uncovered catalog drugs `#ddi-report-uncovered`
 * - release limitations `#ddi-report-limitations`
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../identity/api";
import { getDdiReport, type DdiPair, type DdiReportPayload } from "./api";

interface DdiReportPanelProps {
  encounterId: string;
  /** Changes after every acknowledged save/revision — triggers refetch. */
  refreshKey: string;
  onSessionExpired: () => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; payload: DdiReportPayload };

function pairStatusText(status: DdiPair["status"]): string {
  if (status === "interaction_found") {
    return "Interaction found";
  }
  if (status === "covered_no_listed_interaction") {
    return "No listed interaction in this dataset";
  }
  return "Coverage unavailable";
}

function pendingText(reason: string | null): string {
  if (reason === "reconciliation_required") {
    return "Reconciliation required — review the copied medications and reconcile them before the report becomes current.";
  }
  if (reason === "stale_fingerprint") {
    return "Medication list changed — save the medications to refresh the report. The previous report is not shown as current.";
  }
  if (reason === "dataset_not_pinned") {
    return "No saved medication list yet — save the medications to generate the report.";
  }
  return "Report pending — save the medications to generate the report.";
}

function errorText(reason: string | null): string {
  if (reason === "dataset_unavailable") {
    return "DDI dataset unavailable — the saved list is kept. Retry when the dataset is available.";
  }
  return "Report failed to load — the saved list is kept. Check the connection and retry.";
}

function spanText(start: unknown, end: unknown): string {
  if (typeof start === "number" && typeof end === "number") {
    return `span ${start}–${end}`;
  }
  return "span unknown";
}

export function DdiReportPanel({
  encounterId,
  refreshKey,
  onSessionExpired,
}: DdiReportPanelProps) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const errorRef = useRef<HTMLParagraphElement>(null);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const payload = await getDdiReport(encounterId);
      setLoad({ kind: "ready", payload });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setLoad({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "DDI report failed to load. Check the connection and retry.",
      });
    }
  }, [encounterId, onSessionExpired]);

  useEffect(() => {
    setLoad({ kind: "loading" });
    void refresh();
  }, [refresh, refreshKey]);

  useEffect(() => {
    if (load.kind === "error") {
      errorRef.current?.focus();
    }
  }, [load.kind]);

  return (
    <div id="ddi-report-panel" aria-label="DDI evidence report">
      <h4 className="xi-section-title" style={{ fontSize: 16 }}>
        DDI evidence report
      </h4>
      {load.kind === "loading" && <p>Loading DDI report…</p>}
      {load.kind === "error" && (
        <div>
          <p
            className="xi-form-error"
            id="ddi-error"
            role="alert"
            tabIndex={-1}
            ref={errorRef}
          >
            {load.message}
          </p>
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              id="ddi-retry"
              onClick={() => {
                setLoad({ kind: "loading" });
                void refresh();
              }}
            >
              Retry report
            </button>
          </div>
        </div>
      )}
      {load.kind === "ready" && load.payload.report_status !== "current" && (
        <div>
          {load.payload.report_status === "pending" ? (
            <p className="xi-warning-panel" id="ddi-pending">
              Report pending: {pendingText(load.payload.reason)}
            </p>
          ) : (
            <div>
              <p
                className="xi-form-error"
                id="ddi-error"
                role="alert"
                tabIndex={-1}
                ref={errorRef}
              >
                {errorText(load.payload.reason)}
              </p>
              <div className="xi-row-actions" style={{ marginTop: 8 }}>
                <button
                  className="xi-btn xi-btn-secondary"
                  type="button"
                  id="ddi-retry"
                  onClick={() => {
                    setLoad({ kind: "loading" });
                    void refresh();
                  }}
                >
                  Retry report
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {load.kind === "ready" &&
        load.payload.report_status === "current" &&
        load.payload.report !== null && (
          <div className="xi-history-panel" id="ddi-report">
            {/* Status announces via a role-less live region (not role=status):
              the wizard's save status keeps the page's single status role per
              the S07 e2e contract. */}
            <p className="xi-status" id="ddi-report-status" aria-live="polite">
              Current report · dataset {load.payload.report.dataset_version} ·
              catalog {load.payload.report.catalog_version} ·{" "}
              {load.payload.report.pairs.length} pair
              {load.payload.report.pairs.length === 1 ? "" : "s"}
            </p>
            <p className="xi-hint" style={{ marginBottom: 8 }}>
              Fingerprint{" "}
              <code>{load.payload.report.medication_fingerprint}</code> ·
              generated {load.payload.report.generated_at}
            </p>

            {load.payload.report.resolved_medications.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <h5 style={{ margin: "0 0 4px", fontSize: 14 }}>
                  Checked medications
                </h5>
                <ul style={{ marginTop: 4 }}>
                  {load.payload.report.resolved_medications.map((med) => (
                    <li key={med.catalog_drug_id}>
                      {med.canonical_name} ({med.catalog_drug_id})
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {load.payload.report.coverage_unavailable_medications.length > 0 && (
              <div className="xi-warning-panel" id="ddi-report-uncovered">
                <h5 style={{ margin: "0 0 4px", fontSize: 14 }}>
                  Coverage unavailable
                </h5>
                <ul style={{ marginTop: 4, marginBottom: 0 }}>
                  {load.payload.report.coverage_unavailable_medications.map(
                    (med) => (
                      <li key={med.catalog_drug_id}>
                        {med.canonical_name} ({med.catalog_drug_id}) —
                        coverage unavailable in this dataset
                      </li>
                    ),
                  )}
                </ul>
              </div>
            )}

            {load.payload.report.pairs.length === 0 ? (
              <p className="xi-hint">
                No pairs to check — fewer than two medications in the saved
                list.
              </p>
            ) : (
              <ul id="ddi-report-pairs" style={{ paddingLeft: 20 }}>
                {load.payload.report.pairs.map((pair, index) => (
                  // ponytail: index key is fine — pairs arrive in canonical
                  // sorted order from the checker; upgrade if reorderable.
                  <li key={`${pair.drug_a}|${pair.drug_b}`} id={`ddi-report-pair-${index}`}>
                    <p style={{ marginBottom: 2 }}>
                      <strong>
                        {pair.drug_a} + {pair.drug_b}
                      </strong>{" "}
                      — {pairStatusText(pair.status)}
                      {pair.highest_known_severity !== null &&
                        ` · highest known severity: ${pair.highest_known_severity}`}
                      {pair.has_unknown_severity &&
                        " · includes unknown severity"}
                    </p>
                    {pair.conflicts.length > 0 && (
                      <p className="xi-hint" style={{ margin: "4px 0" }}>
                        Conflicting severities reported:{" "}
                        {pair.conflicts.join(", ")}
                      </p>
                    )}
                    {pair.evidence.length > 0 && (
                      <ul style={{ marginTop: 4 }}>
                        {pair.evidence.map((item, itemIndex) => (
                          <li
                            // ponytail: index key is fine — evidence arrives in
                            // source_path/span order; upgrade if reorderable.
                            key={`${item.source_path}:${String(item.span_start)}:${itemIndex}`}
                          >
                            <p style={{ marginBottom: 2 }}>
                              Severity: {item.source_severity ?? "unknown"} ·
                              direction: {item.direction ?? "unknown"}
                            </p>
                            {item.raw_text !== null &&
                              item.raw_text !== "" && (
                                <p style={{ margin: "4px 0" }}>
                                  {item.raw_text}
                                </p>
                              )}
                            {item.management !== null &&
                              item.management !== "" && (
                                <p style={{ margin: "4px 0" }}>
                                  Management: {item.management}
                                </p>
                              )}
                            <p className="xi-hint" style={{ marginTop: 0 }}>
                              Source: {item.source_path ?? "unknown"} (
                              {spanText(item.span_start, item.span_end)})
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            )}

            {load.payload.report.limitations.length > 0 && (
              <div style={{ marginTop: 8 }} id="ddi-report-limitations">
                <h5 style={{ margin: "0 0 4px", fontSize: 14 }}>
                  Release limitations
                </h5>
                <ul style={{ marginTop: 4 }}>
                  {load.payload.report.limitations.map((entry, index) => (
                    // ponytail: index key is fine — limitations are an ordered
                    // release list; upgrade if reorderable.
                    <li key={index}>{String(entry)}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
    </div>
  );
}
