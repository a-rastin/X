/* Structured history form with server-computed preview (S12,
 * plan.md §§2.2, 5; FR-14, FR-20).
 *
 * Wizard integration (not separate persistence): history lives in the shared
 * S07 autosave body as `draft_data["history"] = {values, provenance,
 * reconciliation, phone_update}` and saves through the same debounced PATCH +
 * revision fence. This component only edits that slice of `localData`; every
 * verdict comes from GET /encounters/{id}/history, which runs the same
 * `evaluate_history()` as the backend — the browser never computes
 * completeness, so the display cannot drift from history.v1 (draft v1,
 * awaiting owner review; not approved).
 *
 * Explicit selection only: nothing is pre-filled. A fresh form has 12
 * unanswered fields; unknown and not_assessed stay distinct from no (false).
 * The 12 typed fields below mirror content/history/history.v1.json exactly
 * (ids, labels, periods, permitted values); there is no GET /content/history
 * route, so this file is the rendering contract. Undeclared or FR-14-excluded
 * ids (dose, route, frequency, active/stopped, free text) are never rendered,
 * and writes carry only the 12 known ids — a forged key already in storage
 * is dropped on the next history save rather than echoed back. Invalid
 * values for known ids are kept verbatim so the server item_errors stay
 * visible next to the unselected field.
 *
 * Provenance is server-stamped by the strict POST .../history command; PATCH
 * bodies preserve it verbatim and this UI never invents it, so fields saved
 * through autosave show "not yet recorded" until a strict save stamps them.
 * Phone update is optional free text with no country validation (like S06);
 * it is analysis_visible false (an identifier, never a model input), unlike
 * the 12 typed values which are the only analysis-visible history source.
 * Page notes (S13) are a separate channel and never influence history.
 *
 * The 412 path stays inside the shared useAutosave hook (keeps edits +
 * reload/reconcile UI); a failed save never shows Saved (the wizard save
 * status owns that). No applyServerSnapshot resync is needed: there are no
 * command POSTs here, so the preview simply refreshes after each
 * acknowledged autosave (same pattern as PANSS/C-SSRS).
 *
 * Accessibility: native fieldset/legend + radios (keyboard free), preview
 * verdict is an aria-live region, per-field errors use role=alert with
 * aria-describedby, user text (phone, provenance, baseline id) renders as
 * escaped text only, styling uses semantic tokens only (both themes).
 *
 * E2E selector contract (for the test agent's e2e/history.spec.ts):
 * - section heading "History (step 5)" (#history-heading)
 * - radios `#hist-{fieldId}-{value}` (name `hist-{fieldId}`), e.g.
 *   `#hist-h_exposure_dopamine_blocker-yes`,
 *   `#hist-h_onset_timing-within_4w_oral_withdrawal`
 * - verdict `#history-preview-status` (aria-live polite)
 * - still-needed list `#history-still-needed`
 * - per-field errors `#hist-{fieldId}-error` (role=alert)
 * - per-field provenance `#hist-{fieldId}-provenance`
 * - reconciliation select `#history-reconciliation-status`, baseline input
 *   `#history-reconciliation-baseline`
 * - phone input `#history-phone-update`
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../identity/api";
import type { DraftData } from "../encounters/api";
import type { useAutosave } from "../encounters/useAutosave";
import {
  getHistory,
  type HistoryPreview,
  type HistoryReconciliation,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface HistorySectionProps {
  encounterId: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
}

/** Typed inventory mirroring content/history/history.v1.json (draft v1). */
interface HistoryField {
  id: string;
  label: string;
  values: string[];
  period: string;
  category: string;
}

const HISTORY_FIELDS: HistoryField[] = [
  {
    id: "h_exposure_dopamine_blocker",
    label: "Dopamine-blocker exposure (lifetime)",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "lifetime",
    category: "exposure",
  },
  {
    id: "h_exposure_duration_3m",
    label: "Cumulative antipsychotic exposure of at least 3 months",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "past 3 months",
    category: "duration",
  },
  {
    id: "h_exposure_1m_if_60plus",
    label: "Exposure of at least 1 month at age 60 or older",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "past 3 months",
    category: "duration",
  },
  {
    id: "h_movement_persistence_4w",
    label: "Involuntary movements persist over at least 4 weeks",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "past 4 weeks",
    category: "duration",
  },
  {
    id: "h_onset_timing",
    label: "Onset timing relative to exposure or withdrawal",
    values: [
      "during_exposure",
      "within_4w_oral_withdrawal",
      "within_8w_lai_withdrawal",
      "unknown",
      "not_assessed",
    ],
    period: "current encounter",
    category: "exposure",
  },
  {
    id: "h_trial_adequacy_prior",
    label: "Adequacy of prior treatment trial",
    values: ["adequate", "inadequate", "unknown", "not_assessed"],
    period: "lifetime",
    category: "trial adequacy",
  },
  {
    id: "h_prior_response",
    label: "Response to prior treatment",
    values: ["response", "no_response", "unknown", "not_assessed"],
    period: "lifetime",
    category: "prior response",
  },
  {
    id: "h_monitoring_baseline",
    label: "Baseline movement examination documented for monitoring",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "current encounter",
    category: "monitoring",
  },
  {
    id: "h_alternative_cause_considered",
    label: "Alternative explanations considered and documented",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "current encounter",
    category: "exposure",
  },
  {
    id: "h_functional_impact",
    label: "Functional impact of movements or restlessness",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "current encounter",
    category: "monitoring",
  },
  {
    id: "h_falls_or_limitation",
    label: "Falls or functional limitation from parkinsonian signs",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "current encounter",
    category: "monitoring",
  },
  {
    id: "h_medication_timeline_documented",
    label:
      "Medication timeline documented (agents and changes, no regimen details)",
    values: ["yes", "no", "unknown", "not_assessed"],
    period: "current encounter",
    category: "exposure",
  },
];

const FIELD_IDS = HISTORY_FIELDS.map((field) => field.id);

const VALUE_LABELS: Record<string, string> = {
  yes: "Yes",
  no: "No",
  unknown: "Unknown",
  not_assessed: "Not assessed",
  during_exposure: "During exposure",
  within_4w_oral_withdrawal: "Within 4 weeks after oral withdrawal",
  within_8w_lai_withdrawal: "Within 8 weeks after LAI withdrawal",
  adequate: "Adequate",
  inadequate: "Inadequate",
  response: "Response",
  no_response: "No response",
};

function valueLabel(value: string): string {
  return VALUE_LABELS[value] ?? value;
}

interface HistorySlice {
  values: Record<string, unknown>;
  provenance: Record<string, unknown>;
  reconciliation: HistoryReconciliation | null;
  phone_update: string | null;
}

function sliceOf(data: DraftData): HistorySlice {
  const raw = data["history"];
  if (typeof raw !== "object" || raw === null) {
    return { values: {}, provenance: {}, reconciliation: null, phone_update: null };
  }
  const section = raw as Record<string, unknown>;
  const values = section["values"];
  const provenance = section["provenance"];
  const reconciliation = section["reconciliation"];
  const phoneUpdate = section["phone_update"];
  return {
    values:
      typeof values === "object" && values !== null
        ? { ...(values as Record<string, unknown>) }
        : {},
    provenance:
      typeof provenance === "object" && provenance !== null
        ? { ...(provenance as Record<string, unknown>) }
        : {},
    reconciliation:
      typeof reconciliation === "object" && reconciliation !== null
        ? (reconciliation as HistoryReconciliation)
        : null,
    phone_update: typeof phoneUpdate === "string" ? phoneUpdate : null,
  };
}

/** Writes carry only the 12 known field ids: undeclared or FR-14-excluded
 * keys already in storage are dropped rather than echoed back. Invalid
 * values for known ids are kept verbatim so server item_errors stay visible. */
function sanitizedValues(values: Record<string, unknown>): Record<string, unknown> {
  const next: Record<string, unknown> = {};
  for (const id of FIELD_IDS) {
    if (values[id] !== undefined) {
      next[id] = values[id];
    }
  }
  return next;
}

function sameSlice(a: HistorySlice, b: HistorySlice): boolean {
  const keysA = Object.keys(sanitizedValues(a.values));
  const keysB = Object.keys(sanitizedValues(b.values));
  if (keysA.length !== keysB.length) {
    return false;
  }
  if (!keysA.every((key) => b.values[key] === a.values[key])) {
    return false;
  }
  const reconA = a.reconciliation;
  const reconB = b.reconciliation;
  if ((reconA === null) !== (reconB === null)) {
    return false;
  }
  if (
    reconA !== null &&
    reconB !== null &&
    (reconA.status !== reconB.status ||
      (reconA.baseline_encounter_id ?? null) !==
        (reconB.baseline_encounter_id ?? null))
  ) {
    return false;
  }
  return (a.phone_update ?? null) === (b.phone_update ?? null);
}

function provenanceText(entry: unknown): string {
  if (typeof entry !== "object" || entry === null) {
    return "Not yet recorded — saved as draft only.";
  }
  const record = entry as Record<string, unknown>;
  const source = typeof record["source"] === "string" ? record["source"] : null;
  if (source === null) {
    return "Not yet recorded — saved as draft only.";
  }
  const recordedAt =
    typeof record["recorded_at"] === "string" ? ` · ${record["recorded_at"]}` : "";
  const baseline =
    typeof record["baseline_encounter_id"] === "string"
      ? ` · baseline ${record["baseline_encounter_id"]}`
      : "";
  return `Provenance: ${source}${recordedAt}${baseline}`;
}

export function HistorySection({
  encounterId,
  autosave,
  onSessionExpired,
}: HistorySectionProps) {
  const [preview, setPreview] = useState<HistoryPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const refreshPreview = useCallback(async (): Promise<void> => {
    try {
      const fresh = await getHistory(encounterId);
      setPreview(fresh);
      setPreviewError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setPreviewError(
        err instanceof ApiError
          ? err.message
          : "History preview failed to load. Check the connection and retry.",
      );
    } finally {
      setPreviewLoading(false);
    }
  }, [encounterId, onSessionExpired]);

  // Live preview follows every acknowledged save.
  const savedRevision =
    autosave.saveState.kind === "saved" ? autosave.saveState.revision : 0;
  useEffect(() => {
    void refreshPreview();
  }, [refreshPreview, savedRevision]);

  const slice = sliceOf(autosave.localData);
  const previewSlice: HistorySlice | null =
    preview === null
      ? null
      : {
          values: preview.values,
          provenance: preview.provenance,
          reconciliation: preview.reconciliation,
          phone_update: preview.phone_update,
        };
  const answersPending =
    previewSlice !== null && !sameSlice(slice, previewSlice);

  function writeSlice(next: HistorySlice): void {
    autosave.setDraftValue("history", {
      values: sanitizedValues(next.values),
      provenance: next.provenance,
      reconciliation: next.reconciliation,
      phone_update: next.phone_update,
    });
  }

  function setValue(fieldId: string, value: string): void {
    if (slice.values[fieldId] === value) {
      return;
    }
    writeSlice({ ...slice, values: { ...slice.values, [fieldId]: value } });
  }

  function setReconciliationStatus(status: string): void {
    if (status === "") {
      writeSlice({ ...slice, reconciliation: null });
      return;
    }
    if (
      status === "not_required" ||
      status === "pending" ||
      status === "reconciled"
    ) {
      writeSlice({
        ...slice,
        reconciliation: {
          status,
          baseline_encounter_id:
            slice.reconciliation?.baseline_encounter_id ?? null,
        },
      });
    }
  }

  function setBaselineId(baselineId: string): void {
    const current = slice.reconciliation ?? {
      status: "pending" as const,
      baseline_encounter_id: null,
    };
    writeSlice({
      ...slice,
      reconciliation: {
        status: current.status,
        baseline_encounter_id: baselineId === "" ? null : baselineId,
      },
    });
  }

  function setPhoneUpdate(phone: string): void {
    const next = phone === "" ? null : phone;
    if ((slice.phone_update ?? null) === next) {
      return;
    }
    writeSlice({ ...slice, phone_update: next });
  }

  const evaluation = preview?.evaluation ?? null;
  const itemErrors = evaluation?.item_errors ?? {};
  const itemErrorIds = Object.keys(itemErrors);

  let verdict: string;
  if (preview === null) {
    verdict = "";
  } else if (evaluation?.status === "complete") {
    verdict = `Complete — all 12 history fields answered (server evaluation ${preview.definition_version}). Explicit no answers stay no; unknown and not assessed stay distinct from false.`;
  } else if (evaluation?.status === "partial") {
    const missing = evaluation.missing_item_ids.length;
    verdict = `Incomplete — ${missing} of 12 fields still need${missing === 1 ? "s" : ""} an answer. Saved as incomplete; history carries no scores, only completeness.`;
  } else {
    verdict =
      "No answers yet. History is unanswered — 12 fields with nothing selected.";
  }

  const previewProvenance = preview?.provenance ?? {};

  return (
    <section className="xi-card" aria-labelledby="history-heading">
      <h3 className="xi-section-title" id="history-heading">
        History (step 5)
      </h3>
      <p className="xi-hint">
        Structured history for this encounter. Nothing is pre-filled — every
        field is an explicit selection. Answers autosave through the same draft
        as the rest of this encounter (about one second after changing an
        answer). Completeness below is computed by the server — the browser
        never fills in missing fields.
      </p>
      <p className="xi-hint">
        These typed fields are the only analysis-visible history source
        (analysis_visible true). The optional phone update below is an
        identifier (analysis_visible false, never a model input), and page
        notes added elsewhere never influence history.
      </p>

      <div style={{ marginTop: 8 }}>
        {HISTORY_FIELDS.map((field) => {
          const errorId =
            itemErrors[field.id] !== undefined
              ? `hist-${field.id}-error`
              : undefined;
          return (
            <fieldset key={field.id} className="xi-field">
              <legend className="xi-label">
                {field.label}{" "}
                <span className="xi-hint">
                  · {field.period} · {field.category}
                </span>
              </legend>
              <div
                className="xi-radio-group"
                role="radiogroup"
                aria-label={field.label}
              >
                {field.values.map((value) => (
                  <label key={value} htmlFor={`hist-${field.id}-${value}`}>
                    <input
                      id={`hist-${field.id}-${value}`}
                      type="radio"
                      name={`hist-${field.id}`}
                      value={value}
                      checked={slice.values[field.id] === value}
                      onChange={() => setValue(field.id, value)}
                      aria-describedby={
                        errorId !== undefined
                          ? `${errorId} hist-${field.id}-provenance`
                          : `hist-${field.id}-provenance`
                      }
                    />
                    {valueLabel(value)}
                  </label>
                ))}
              </div>
              {errorId !== undefined && (
                <p className="xi-field-error" id={errorId} role="alert">
                  {(itemErrors[field.id] ?? []).join(" ")}
                </p>
              )}
              <p className="xi-hint" id={`hist-${field.id}-provenance`}>
                {provenanceText(previewProvenance[field.id])}
              </p>
            </fieldset>
          );
        })}
      </div>

      <div className="xi-history-panel" aria-label="History reconciliation">
        <h4 className="xi-section-title" style={{ fontSize: 15 }}>
          Reconciliation (follow-up)
        </h4>
        <p className="xi-hint" style={{ marginTop: 0 }}>
          Follow-up drafts copy baseline history with status pending; the
          author reconciles explicitly. Registration drafts are not required.
        </p>
        <div className="xi-field">
          <label className="xi-label" htmlFor="history-reconciliation-status">
            Reconciliation status
          </label>
          <select
            className="xi-select"
            id="history-reconciliation-status"
            value={slice.reconciliation?.status ?? ""}
            onChange={(event) => setReconciliationStatus(event.target.value)}
          >
            <option value="">Not set</option>
            <option value="not_required">Not required</option>
            <option value="pending">Pending</option>
            <option value="reconciled">Reconciled</option>
          </select>
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="history-reconciliation-baseline">
            Baseline encounter id (optional)
          </label>
          <input
            className="xi-input"
            id="history-reconciliation-baseline"
            type="text"
            autoComplete="off"
            value={slice.reconciliation?.baseline_encounter_id ?? ""}
            onChange={(event) => setBaselineId(event.target.value)}
          />
        </div>
      </div>

      <div className="xi-field" style={{ marginTop: 12 }}>
        <label className="xi-label" htmlFor="history-phone-update">
          Phone update (optional, free text — never a model input)
        </label>
        <input
          className="xi-input"
          id="history-phone-update"
          type="text"
          autoComplete="off"
          value={slice.phone_update ?? ""}
          onChange={(event) => setPhoneUpdate(event.target.value)}
        />
        <p className="xi-hint">
          Optional follow-up phone update. No country validation is applied.
        </p>
      </div>

      <div style={{ marginTop: 16 }}>
        <h4 className="xi-section-title" style={{ fontSize: 16 }}>
          Server completeness preview
        </h4>
        {previewLoading && preview === null && <p>Loading history preview…</p>}
        {previewError !== null && preview === null && (
          <div>
            <p className="xi-form-error" role="alert">
              {previewError}
            </p>
            <div className="xi-row-actions" style={{ marginTop: 8 }}>
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                onClick={() => {
                  setPreviewLoading(true);
                  setPreviewError(null);
                  void refreshPreview();
                }}
              >
                Retry
              </button>
            </div>
          </div>
        )}
        {preview !== null && (
          <div>
            {/* Verdict announces via a role-less live region (not
              role=status): the wizard's save status keeps the page's single
              status role per the S07 e2e contract. */}
            <p
              className="xi-status"
              id="history-preview-status"
              aria-live="polite"
            >
              {verdict}
            </p>
            <p className="xi-hint">
              Server evaluation · definition {preview.definition_version} ·
              saved revision {preview.revision}
              {answersPending &&
                " · unsaved edits pending — preview reflects the last saved revision"}
              {previewError !== null &&
                " · preview refresh failed; showing last loaded state"}
            </p>

            {itemErrorIds.length > 0 && (
              <p className="xi-form-error" role="alert">
                The saved values contain server-reported problems:{" "}
                {itemErrorIds
                  .map((id) => `${id}: ${(itemErrors[id] ?? []).join(" ")}`)
                  .join("; ")}
              </p>
            )}

            {evaluation?.status === "partial" && (
              <div id="history-still-needed">
                <p className="xi-hint">Still needed:</p>
                <ul>
                  {evaluation.missing_item_ids.map((id) => (
                    <li key={id}>
                      {HISTORY_FIELDS.find((field) => field.id === id)?.label ??
                        id}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
