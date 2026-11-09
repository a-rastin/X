import { useLanguage } from "../../../shared/i18n";
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
  const { language, text: uiText, t } = useLanguage();
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
          
          {t("followUpBaseline")}
        </h3>
        <p>{t("loadingBaseline")}</p>
      </section>
    );
  }

  if (load.kind === "denied" || load.kind === "error") {
    return (
      <section className="xi-card" aria-labelledby="followup-baseline-heading">
        <h3 className="xi-section-title" id="followup-baseline-heading">
          
          {t("followUpBaseline")}
        </h3>
        <p className="xi-form-error" role="alert">
          {uiText(load.message)}
        </p>
        {load.kind === "error" && (
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void reload()}
            >
              {t("retry")}</button>
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
        
        {t("followUpBaseline")}
      </h3>
      <p className="xi-hint">
        
        {t("copiedHistoryAndMedicationsCarry")}{" "}
        <code>copied_baseline</code>  {t("provenancePanssAndCSsrsAlwaysStart")}
      </p>

      {baseline?.prior_plan && <section><h4>{t("nc.finalPlan")}</h4><pre style={{whiteSpace:"pre-wrap"}}>{baseline.prior_plan}</pre></section>}
      {baseline === null ? (
        <p className="xi-hint" role="status">
          
          {t("noCopiedBaselineReviewEachHistoryField")}
        </p>
      ) : (
        <div>
          <p className="xi-hint">
            {t("baseline")}{" "}
            {baseline.baseline_encounter_id !== null
              ? t("fromSignedEncounterValue1", { value1: baseline.baseline_encounter_id })
              : t("snapshot665")}{" "}
            · {historyIds.length}  {t("copiedHistor")}
            {historyIds.length === 1 ? t("yField") : t("yFields")} ·{" "}
            {medications.length}  {t("copiedMedication")}
            {(language === "fa" ? "" : medications.length === 1 ? "" : "s")}
            {baseline.provenance_note !== null &&
              baseline.provenance_note !== "" &&
              t("noteValue1", { value1: baseline.provenance_note })}
          </p>
          {historyIds.length > 0 && (
            <ul aria-label={t("copiedHistoryFields")}>
              {historyIds.map((id) => (
                <li key={id}>
                  <code>{id}</code>: {String(baseline.history_values[id])}{" "}
                  <span className="xi-hint">
                    
                    {t("provenanceCopiedBaseline")}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {medications.length > 0 && (
            <ul aria-label={t("copiedMedications")}>
              {medications.map((entry, index) => (
                // ponytail: index key is fine — list is a read-only snapshot
                // with no reordering, upgrade to stable ids if editable.
                <li key={`${entry.catalog_drug_id}-${index}`}>
                  {entry.catalog_drug_id}{" "}
                  <span className="xi-hint">
                    
                    {t("provenanceCopiedBaseline")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="xi-history-panel" aria-label={t("baselineReconciliation")}>
        <h4 className="xi-section-title" style={{ fontSize: 15 }}>
          
          {t("reconciliation")}
        </h4>
        <p
          className="xi-hint"
          id="followup-baseline-status"
          role="status"
          style={{ marginBottom: 0 }}
        >
          
          {t("status387")} {reconciliation.status}
          {reconciliation.baseline_encounter_id !== null &&
          reconciliation.baseline_encounter_id !== undefined &&
          reconciliation.baseline_encounter_id !== ""
            ? t("baselineValue1", { value1: reconciliation.baseline_encounter_id })
            : ""}{" "}
          — {uiText(reconciliationHint)}
        </p>
      </div>

      <div style={{ marginTop: 12 }}>
        <h4 className="xi-section-title" style={{ fontSize: 15 }}>
          
          {t("priorScoresHistoricalOnly")}
        </h4>
        {priorKeys.length === 0 ? (
          <p className="xi-hint" role="status">
            
            {t("noPriorScores")}
          </p>
        ) : (
          <ul
            data-testid="followup-prior-scores"
            id="followup-prior-scores"
            aria-label={t("priorScoresHistoricalOnly677")}
          >
            {priorKeys.map((key) => (
              <li key={key}>
                {uiText(scoreLabel(key))}: {String(priorScores[key]?.value)} ·{" "}
                <span className="xi-hint">
                  
                  {t("historicalNotACurrentAnswer")}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="xi-hint">
          
          {t("newPanssAndCSsrsAnswersStart")}
        </p>
      </div>
    </section>
  );
}
