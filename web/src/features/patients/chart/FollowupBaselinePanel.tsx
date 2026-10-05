/* Follow-up baseline panel (S14, plan.md §2.2; FR-20-23).
 *
 * Author-only read of `GET /encounters/{id}/followup-baseline`: copied
 * history/medications with `copied_baseline` provenance + `pending`
 * reconciliation, with PANSS/C-SSRS starting unanswered — prior scores show
 * as historical only (never as answers). Reuses the S12 history/effects
 * preview contract for wording (provenance/reconciliation labels) and the
 * S13 separation rule (this panel never edits answers, notes stay separate).
 * No autosave here: read-only display, ETag revision shown for reference.
 *
 * Accessibility + theming: native headings/lists (keyboard free), loading
 * and error states from the real endpoint with Retry, status keeps
 * role=status, errors use role=alert, styling uses semantic tokens only
 * (both themes, .xi-history-panel for the reconciliation block).
 *
 * E2E selector contract (for the test stage's e2e/followup.spec.ts):
 * - panel `data-testid="followup-baseline"` (`#followup-baseline`)
 * - reconciliation `#followup-baseline-status` (role=status)
 * - prior scores `data-testid="followup-prior-scores"` (`#followup-prior-scores`)
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../identity/api";
import {
  getFollowupBaseline,
  type FollowupBaselineResponse,
} from "./api";

type LoadState =
  | { kind: "loading" }
  | { kind: "denied"; message: string }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: FollowupBaselineResponse };

function scoreLabel(key: string): string {
  if (key === "panss_total") {
    return "PANSS total";
  }
  if (key === "cssrs_severity") {
    return "C-SSRS severity";
  }
  return key;
}

export function FollowupBaselinePanel({
  encounterId,
  onSessionExpired,
}: {
  encounterId: string;
  onSessionExpired: () => void;
}) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });

  const reload = useCallback(async () => {
    setLoad({ kind: "loading" });
    try {
      const data = await getFollowupBaseline(encounterId);
      setLoad({ kind: "ready", data });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setLoad({
          kind: "denied",
          message: "Only the draft author can view this baseline.",
        });
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        setLoad({
          kind: "error",
          message: "Baseline not found. The draft may have been discarded.",
        });
        return;
      }
      setLoad({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Baseline failed to load. Check the connection and retry.",
      });
    }
  }, [encounterId, onSessionExpired]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (load.kind === "loading") {
    return (
      <section className="xi-card" aria-labelledby="followup-baseline-heading">
        <h3 className="xi-section-title" id="followup-baseline-heading">
          Follow-up baseline
        </h3>
        <p>Loading baseline…</p>
      </section>
    );
  }

  if (load.kind === "denied" || load.kind === "error") {
    return (
      <section className="xi-card" aria-labelledby="followup-baseline-heading">
        <h3 className="xi-section-title" id="followup-baseline-heading">
          Follow-up baseline
        </h3>
        <p className="xi-form-error" role="alert">
          {load.message}
        </p>
        {load.kind === "error" && (
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void reload()}
            >
              Retry
            </button>
          </div>
        )}
      </section>
    );
  }

  const { baseline, reconciliation, prior_scores_display: priorScores } =
    load.data;
  const priorKeys = Object.keys(priorScores);
  const historyIds =
    baseline !== null ? Object.keys(baseline.history_values) : [];
  const medications = baseline !== null ? baseline.medications : [];

  let reconciliationHint: string;
  if (reconciliation.status === "pending") {
    reconciliationHint =
      "Reconciliation required — review the copied history below in the history section before review.";
  } else if (reconciliation.status === "reconciled") {
    reconciliationHint = "Reconciled — the copied history was reviewed.";
  } else {
    reconciliationHint =
      "Reconciliation not required — this draft started without a copied baseline.";
  }

  return (
    <section
      className="xi-card"
      aria-labelledby="followup-baseline-heading"
      data-testid="followup-baseline"
      id="followup-baseline"
    >
      <h3 className="xi-section-title" id="followup-baseline-heading">
        Follow-up baseline
      </h3>
      <p className="xi-hint">
        Copied history and medications carry{" "}
        <code>copied_baseline</code> provenance. PANSS and C-SSRS always start
        unanswered — prior scores below are historical only, never filled in
        as answers.
      </p>

      {baseline === null ? (
        <p className="xi-hint" role="status">
          No copied baseline — history starts empty. Answer each history field
          explicitly in the history section below.
        </p>
      ) : (
        <div>
          <p className="xi-hint">
            Baseline{" "}
            {baseline.baseline_encounter_id !== null
              ? `from signed encounter ${baseline.baseline_encounter_id}`
              : "snapshot"}{" "}
            · {historyIds.length} copied histor
            {historyIds.length === 1 ? "y field" : "y fields"} ·{" "}
            {medications.length} copied medication
            {medications.length === 1 ? "" : "s"}
            {baseline.provenance_note !== null &&
              baseline.provenance_note !== "" &&
              ` · note: ${baseline.provenance_note}`}
          </p>
          {historyIds.length > 0 && (
            <ul aria-label="Copied history fields">
              {historyIds.map((id) => (
                <li key={id}>
                  <code>{id}</code>: {String(baseline.history_values[id])}{" "}
                  <span className="xi-hint">
                    · provenance copied_baseline
                  </span>
                </li>
              ))}
            </ul>
          )}
          {medications.length > 0 && (
            <ul aria-label="Copied medications">
              {medications.map((entry, index) => (
                // ponytail: index key is fine — list is a read-only snapshot
                // with no reordering, upgrade to stable ids if editable.
                <li key={`${entry.catalog_drug_id}-${index}`}>
                  {entry.catalog_drug_id}{" "}
                  <span className="xi-hint">
                    · provenance copied_baseline
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="xi-history-panel" aria-label="Baseline reconciliation">
        <h4 className="xi-section-title" style={{ fontSize: 15 }}>
          Reconciliation
        </h4>
        <p
          className="xi-hint"
          id="followup-baseline-status"
          role="status"
          style={{ marginBottom: 0 }}
        >
          Status: {reconciliation.status}
          {reconciliation.baseline_encounter_id !== null &&
          reconciliation.baseline_encounter_id !== undefined &&
          reconciliation.baseline_encounter_id !== ""
            ? ` · baseline ${reconciliation.baseline_encounter_id}`
            : ""}{" "}
          — {reconciliationHint}
        </p>
      </div>

      <div style={{ marginTop: 12 }}>
        <h4 className="xi-section-title" style={{ fontSize: 15 }}>
          Prior scores (historical only)
        </h4>
        {priorKeys.length === 0 ? (
          <p className="xi-hint" role="status">
            No prior scores.
          </p>
        ) : (
          <ul
            data-testid="followup-prior-scores"
            id="followup-prior-scores"
            aria-label="Prior scores, historical only"
          >
            {priorKeys.map((key) => (
              <li key={key}>
                {scoreLabel(key)}: {String(priorScores[key]?.value)} ·{" "}
                <span className="xi-hint">
                  historical — not a current answer
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="xi-hint">
          New PANSS and C-SSRS answers start empty in their sections below;
          these prior values are never filled in as answers.
        </p>
      </div>
    </section>
  );
}
