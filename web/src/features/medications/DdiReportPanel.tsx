/* Versioned DDI evidence panel (S20, plan.md §§6.3, 9; FR-14-15).
 *
 * Reads only: GET /encounters/{id}/ddi-report (see ./api.ts). The parent
 * passes `refreshKey`, which changes after every acknowledged medications
 * save and every autosave revision, so changed medications show the pending
 * state until the current fingerprint matches. Local selected IDs also
 * hide old findings before the autosave request completes. A stale report is never
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
import { DdiEvidenceList } from "./DdiEvidenceList";

interface DdiReportPanelProps {
  encounterId: string;
  /** Changes after every acknowledged save/revision — triggers refetch. */
  refreshKey: string;
  selectedIds: string[];
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

export function DdiReportPanel({
  encounterId,
  refreshKey,
  selectedIds,
  onSessionExpired,
}: DdiReportPanelProps) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [filter, setFilter] = useState("all");
  const errorRef = useRef<HTMLParagraphElement>(null);
  const requestRef = useRef(0);

  const refresh = useCallback(async (): Promise<void> => {
    const request = ++requestRef.current;
    try {
      const payload = await getDdiReport(encounterId);
      if (request !== requestRef.current) return;
      setLoad({ kind: "ready", payload });
    } catch (err) {
      if (request !== requestRef.current) return;
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
    return () => { requestRef.current += 1; };
  }, [refresh, refreshKey]);

  useEffect(() => {
    if (load.kind === "error") {
      errorRef.current?.focus();
    }
  }, [load.kind]);

  const report = load.kind === "ready" && load.payload.report_status === "current"
    ? load.payload.report : null;
  // Fence the displayed report against local edits too, before autosave's debounce.
  const selectionChanged = report !== null && (
    report.resolved_medications.length !== selectedIds.length ||
    report.resolved_medications.some((med) => !selectedIds.includes(med.catalog_drug_id))
  );
  const current = report !== null && !selectionChanged;
  const reportStateLabel = current ? "Current report" : load.kind === "loading" ? "Loading" :
    load.kind === "error" || (load.kind === "ready" && load.payload.report_status === "error") ? "Report unavailable" : "Review pending";
  const pairs = report?.pairs ?? [];
  const nameOf = (id: string) => report?.resolved_medications.find(
    (med) => med.catalog_drug_id === id || med.concept_id === id,
  )?.canonical_name ?? id;
  const filters = [
    { key: "all", label: "All pairs", count: pairs.length },
    ...["Major", "Moderate", "Minor", "Unknown"].map((severity) => ({
      key: severity.toLowerCase(), label: severity,
      count: pairs.filter((pair) => pair.status === "interaction_found" && (
        (pair.highest_known_severity ?? "Unknown").toLowerCase() === severity.toLowerCase()
      )).length,
    })),
    { key: "unavailable", label: "Coverage unavailable", count: pairs.filter((pair) => pair.status === "coverage_unavailable").length },
  ];
  const matchesFilter = (pair: DdiPair) => filter === "all" || (
    filter === "unavailable" ? pair.status === "coverage_unavailable" :
    pair.status === "interaction_found" && (pair.highest_known_severity ?? "Unknown").toLowerCase() === filter
  );
  const rank = (pair: DdiPair) => {
    if (pair.status === "coverage_unavailable") return 90;
    if (pair.status === "covered_no_listed_interaction") return 100;
    return ({ contraindicated: 0, serious: 1, major: 1, monitor_closely: 2, moderate: 2, minor: 3, unknown: 4 } as Record<string, number>)[(pair.highest_known_severity ?? "unknown").toLowerCase()] ?? 4;
  };
  const visiblePairs = pairs.map((pair, index) => ({ pair, index })).filter(({ pair }) => matchesFilter(pair))
    .sort((a, b) => rank(a.pair) - rank(b.pair) || a.index - b.index);

  const retryButton = (
    <button className="xi-btn xi-btn-secondary" type="button" id="ddi-retry"
      onClick={() => { setLoad({ kind: "loading" }); void refresh(); }}>
      Retry report
    </button>
  );

  return (
    <div id="ddi-report-panel" aria-label="DDI evidence report" className="xi-ddi-panel">
      <div className="xi-meds-section-heading">
        <h4>Interaction review</h4>
        <span className="xi-ddi-state">{reportStateLabel}</span>
      </div>
      <p className="xi-hint">Drug–drug interaction evidence for the saved medication list.</p>
      {load.kind === "loading" && <p className="xi-ddi-empty" aria-live="polite">Loading DDI report…</p>}
      {load.kind === "error" && (
        <div className="xi-ddi-empty">
          <p className="xi-form-error" id="ddi-error" role="alert" tabIndex={-1} ref={errorRef}>{load.message}</p>
          {retryButton}
        </div>
      )}
      {selectionChanged && <p className="xi-warning-panel" id="ddi-pending">Report pending: {pendingText("stale_fingerprint")}</p>}
      {load.kind === "ready" && load.payload.report_status !== "current" && (
        load.payload.report_status === "pending" ? (
          <div className="xi-ddi-empty">
            <span className="xi-ddi-empty-symbol" aria-hidden="true">↔</span>
            <h5>{load.payload.reason === "reconciliation_required" ? "Review copied medications" : "Save the list to review interactions"}</h5>
            <p id="ddi-pending">Report pending: {pendingText(load.payload.reason)}</p>
          </div>
        ) : (
          <div className="xi-ddi-empty">
            <p className="xi-form-error" id="ddi-error" role="alert" tabIndex={-1} ref={errorRef}>{errorText(load.payload.reason)}</p>
            {retryButton}
          </div>
        )
      )}
      {current && report && (
        <div id="ddi-report">
          <div className="xi-ddi-overview">
            <div><strong>{pairs.filter((pair) => pair.status === "interaction_found").length}</strong><span>Pairs with evidence</span></div>
            <div><strong>{pairs.filter((pair) => pair.status === "coverage_unavailable").length}</strong><span>Coverage unavailable</span></div>
            <div><strong>{report.resolved_medications.length}</strong><span>Medications checked</span></div>
          </div>
          <p className="xi-hint" id="ddi-report-status" aria-live="polite">
            Current report · dataset {report.dataset_version} · {pairs.length} pair{pairs.length === 1 ? "" : "s"} · {report.source_label ?? "Released interaction dataset"}
          </p>
          {report.detail_availability === "severity_only" && (
            <p className="xi-ddi-source-note">Mechanism and management details were not supplied; this source provides severity assertions only.</p>
          )}
          {report.coverage_unavailable_medications.length > 0 && (
            <div className="xi-warning-panel" id="ddi-report-uncovered">
              <h5>Coverage unavailable</h5>
              <ul>{report.coverage_unavailable_medications.map((med) => (
                <li key={med.catalog_drug_id}>{med.canonical_name} ({med.catalog_drug_id}) — coverage unavailable in this dataset</li>
              ))}</ul>
            </div>
          )}
          {pairs.length === 0 ? (
            <div className="xi-ddi-empty"><h5>No medication pairs</h5><p>No pairs to check — fewer than two medications in the saved list.</p></div>
          ) : (
            <>
              <div className="xi-ddi-results-heading"><h5>Pair findings</h5><span>Highest known severity first</span></div>
              <div className="xi-ddi-filters" role="group" aria-label="Filter interaction pairs">
                {filters.filter((item) => item.key === "all" || item.count > 0 || item.key === filter).map((item) => (
                  <button key={item.key} type="button" aria-pressed={filter === item.key} onClick={() => setFilter(item.key)}>
                    {item.label} <span>{item.count}</span>
                  </button>
                ))}
              </div>
              <p className="xi-ddi-result-count" aria-live="polite">Showing {visiblePairs.length} of {pairs.length} pairs</p>
              {visiblePairs.length === 0 && <p className="xi-ddi-empty">No pairs match this filter. Select All pairs to review the full list.</p>}
              <ul id="ddi-report-pairs" className="xi-ddi-pairs">
                {visiblePairs.map(({ pair, index }) => (
                  <li key={`${pair.drug_a}|${pair.drug_b}`} id={`ddi-report-pair-${index}`}
                    className={`xi-ddi-pair xi-ddi-pair-${pair.status === "interaction_found" ? (pair.highest_known_severity ?? "unknown").toLowerCase().replace(/[^a-z]/g, "") : "neutral"}`}>
                    <div className="xi-ddi-pair-heading">
                      <h6>{nameOf(pair.drug_a)} <span>+</span> {nameOf(pair.drug_b)}</h6>
                      <span className="xi-ddi-severity">{pair.status === "interaction_found" ? pair.highest_known_severity ?? "Unknown" : pair.status === "coverage_unavailable" ? "Coverage unavailable" : "No listed interaction"}</span>
                    </div>
                    <p className="xi-ddi-pair-status">{pairStatusText(pair.status)}
                      {pair.highest_known_severity !== null && ` · highest known severity: ${pair.highest_known_severity}`}
                      {pair.has_unknown_severity && " · includes unknown severity"}
                    </p>
                    {pair.status === "coverage_unavailable" && <p className="xi-hint">An absent record does not establish absence of an interaction.</p>}
                    {pair.conflicts.length > 0 && <p className="xi-hint">Conflicting severities reported: {pair.conflicts.join(", ")}</p>}
                    <DdiEvidenceList evidence={pair.evidence} />
                  </li>
                ))}
              </ul>
            </>
          )}
          {report.limitations.length > 0 && (
            <div className="xi-ddi-limitations" id="ddi-report-limitations">
              <h5>Release limitations</h5>
              <ul>{report.limitations.map((entry, index) => <li key={index}>{String(entry)}</li>)}</ul>
            </div>
          )}
          <details className="xi-ddi-metadata">
            <summary>Report provenance &amp; checked medications</summary>
            <dl>
              <div><dt>Dataset</dt><dd>{report.dataset_version}</dd></div>
              <div><dt>Catalog</dt><dd>{report.catalog_version}</dd></div>
              <div><dt>Generated</dt><dd>{report.generated_at}</dd></div>
              <div><dt>Fingerprint</dt><dd><code>{report.medication_fingerprint}</code></dd></div>
            </dl>
            <ul>{report.resolved_medications.map((med) => <li key={med.catalog_drug_id}>{med.canonical_name} ({med.catalog_drug_id})</li>)}</ul>
          </details>
        </div>
      )}
    </div>
  );
}
