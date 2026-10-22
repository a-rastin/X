/* Structured history in the shared draft autosave body (S12).
 *
 * Core fields retain their ids with conditional exposure and treatment controls. Reviewed
 * workflow fields come from GET /encounters/{id}/history with exact versioned
 * source-path ids, definitions and permitted responses. Namespace separation
 * prevents similarly named questions from sharing different assessments.
 * Writes preserve all server-allowlisted history ids, including copied prior
 * workflow context, and discard undeclared keys. Core and declared workflow
 * responses default to Unknown; hidden dependent answers are cleared.
 *
 * Server previews evaluate saved responses. Unknown and not assessed remain
 * distinct from No. Required proposal context must be clarified before its
 * question can execute; no history score or clinical threshold is invented.
 * The strict history command stamps provenance; autosave preserves it.
 * Working notes and phone updates never enter question projections.
 *
 * Accessibility: native fieldset/legend + radios (keyboard free), preview
 * verdict is an aria-live region, per-field errors use role=alert with
 * aria-describedby, user text (phone, provenance, baseline id) renders as
 * escaped text only, styling uses semantic tokens only (both themes).
 *
 * E2E selector contract (for the test agent's e2e/history.spec.ts):
 * - section heading `#history-heading`: "History (step 5)" on registration,
 *   "History (step 4)" on follow-up (wizard index, not a fixed label).
 *   Adverse effects is follow-up only and is not this section.
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

import { useCallback, useEffect, useRef, useState } from "react";
import "../assessments/diagnosis/diagnosis.css";
import "./history.css";
import { ApiError } from "../identity/api";
import type { DraftData } from "../encounters/api";
import type { useAutosave } from "../encounters/useAutosave";
import { focusHistoryControl, ProposalReadinessSummary } from "../reasoning/ProposalReadinessSummary";
import {
  getHistory,
  type HistoryPreview,
  type HistoryReconciliation,
  type WorkflowHistoryField,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface HistorySectionProps {
  encounterId: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
  /** 1-based wizard index (registration 5, follow-up 4). */
  stepNumber: number;
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
    label: "History of lifetime antipsychotic exposure",
    values: ["yes", "no", "unknown"],
    period: "lifetime",
    category: "exposure",
  },
  {
    id: "h_movement_persistence_4w",
    label: "Involuntary movements persist over at least 4 weeks",
    values: ["yes", "no", "unknown"],
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
      "other",
      "unknown",
    ],
    period: "current encounter",
    category: "exposure",
  },
  {
    id: "h_trial_adequacy_prior",
    label: "Adequacy of prior treatment trial",
    values: ["adequate", "inadequate", "unknown"],
    period: "lifetime",
    category: "trial adequacy",
  },
  {
    id: "h_prior_response",
    label: "Response to prior treatment",
    values: ["response", "no_response", "unknown"],
    period: "lifetime",
    category: "prior response",
  },
  {
    id: "h_alternative_cause_considered",
    label: "Alternative explanations ruled out",
    values: ["yes", "no", "unknown"],
    period: "current encounter",
    category: "exposure",
  },
  {
    id: "h_falls_or_limitation",
    label: "Falls or functional limitation from parkinsonian signs",
    values: ["yes", "no", "unknown"],
    period: "current encounter",
    category: "monitoring",
  },
];

// Presentation groups follow the applicable core questions.
const HISTORY_GROUPS = [
  { id: "exposure", title: "Medication exposure", fields: [
    "h_exposure_dopamine_blocker", "h_onset_timing",
  ] },
  { id: "treatment", title: "Prior treatment", fields: [
    "h_trial_adequacy_prior", "h_prior_response",
  ] },
  { id: "movement", title: "Movement & monitoring", fields: [
    "h_movement_persistence_4w", "h_alternative_cause_considered", "h_falls_or_limitation",
  ] },
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
  response: "Some level of response",
  other: "Other",
  no_response: "No response",
};

function valueLabel(value: string): string {
  return VALUE_LABELS[value] ?? value.replace(/([a-z])([A-Z])/g, "$1 $2");
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

function applicableFields(values: Record<string, unknown>): HistoryField[] {
  return HISTORY_FIELDS.filter((field) => {
    if (["h_onset_timing", "h_trial_adequacy_prior", "h_prior_response"].includes(field.id)) {
      if (values.h_exposure_dopamine_blocker !== "yes") return false;
      if (field.id === "h_prior_response" && values.h_trial_adequacy_prior !== "inadequate") return false;
    }
    if (["h_alternative_cause_considered", "h_falls_or_limitation"].includes(field.id)) {
      return values.h_movement_persistence_4w === "yes";
    }
    return true;
  });
}

/** Unknown is the explicit default for active core and declared workflow questions.
 * Hidden children cannot retain a previous assessment after a parent changes. */
function normalizedValues(values: Record<string, unknown>, workflowFields: WorkflowHistoryField[] = []): Record<string, unknown> {
  const next = { ...values };
  for (const id of FIELD_IDS) {
    if (next[id] === undefined || next[id] === "not_assessed") next[id] = "unknown";
  }
  const applicableIds = applicableFields(next).map((field) => field.id);
  for (const id of FIELD_IDS) {
    if (!applicableIds.includes(id)) delete next[id];
  }
  for (const field of workflowFields) {
    if (!workflowFieldApplies(field, next)) delete next[field.id];
    else if (field.default_value && (next[field.id] === undefined || next[field.id] === "not_assessed")) {
      next[field.id] = field.default_value;
    }
  }
  return next;
}

function workflowFieldApplies(field: WorkflowHistoryField, values: Record<string, unknown>): boolean {
  return !field.show_if || values[field.show_if.field_id] === field.show_if.value;
}

/** Writes preserve server-allowlisted context. Undeclared keys are discarded;
 * invalid active values stay visible through server-reported item errors. */
function sanitizedValues(values: Record<string, unknown>, ids = FIELD_IDS, workflowFields: WorkflowHistoryField[] = []): Record<string, unknown> {
  const normalized = normalizedValues(values, workflowFields);
  const next: Record<string, unknown> = {};
  for (const id of ids) {
    if (normalized[id] !== undefined) {
      next[id] = normalized[id];
    }
  }
  return next;
}

function sameSlice(a: HistorySlice, b: HistorySlice, ids = FIELD_IDS, workflowFields: WorkflowHistoryField[] = []): boolean {
  const valuesA = sanitizedValues(a.values, ids, workflowFields);
  const valuesB = sanitizedValues(b.values, ids, workflowFields);
  const keysA = Object.keys(valuesA);
  const keysB = Object.keys(valuesB);
  if (keysA.length !== keysB.length) {
    return false;
  }
  if (!keysA.every((key) => valuesB[key] === valuesA[key])) {
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
  stepNumber,
}: HistorySectionProps) {
  const [preview, setPreview] = useState<HistoryPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewRequest = useRef(0);

  const refreshPreview = useCallback(async (): Promise<void> => {
    const request = ++previewRequest.current;
    try {
      const fresh = await getHistory(encounterId);
      if (request !== previewRequest.current) return;
      setPreview(fresh);
      setPreviewError(null);
    } catch (err) {
      if (request !== previewRequest.current) return;
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
      if (request === previewRequest.current) setPreviewLoading(false);
    }
  }, [encounterId, onSessionExpired]);

  // Live preview follows every acknowledged save.
  const savedRevision =
    autosave.saveState.kind === "saved" ? autosave.saveState.revision : 0;
  useEffect(() => {
    void refreshPreview();
    return () => { previewRequest.current += 1; };
  }, [refreshPreview, savedRevision]);

  useEffect(() => {
    if (preview === null) return;
    const focusLinkedField = () => {
      const fragment = window.location.hash.split("#")[2] ?? "";
      const field = new URLSearchParams(fragment.split("?")[1] ?? "").get("field");
      if (field?.startsWith("candidate/history/")) focusHistoryControl(field);
    };
    // The wizard focuses its page after a step transition; focus the requested
    // native control once the loaded History controls have mounted.
    const frame = requestAnimationFrame(focusLinkedField);
    window.addEventListener("hashchange", focusLinkedField);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("hashchange", focusLinkedField);
    };
  }, [preview !== null]);

  const workflowInventory = preview?.workflow_fields ?? [];
  const rawSlice = sliceOf(autosave.localData);
  const normalized = normalizedValues(rawSlice.values, workflowInventory);
  const slice = {
    ...rawSlice,
    values: normalized,
    provenance: Object.fromEntries(Object.entries(rawSlice.provenance).filter(([id]) =>
      normalized[id] !== undefined && rawSlice.values[id] === normalized[id],
    )),
  };
  const visibleFields = applicableFields(slice.values);
  const visibleGroups = HISTORY_GROUPS.map((group) => ({
    ...group, fields: visibleFields.filter((field) => group.fields.includes(field.id)),
  })).filter((group) => group.fields.length > 0);
  const allowedIds = preview?.allowed_field_ids ?? FIELD_IDS;
  const workflowFields = workflowInventory.filter((field) => workflowFieldApplies(field, slice.values));
  const questionKeys = [...new Set(workflowFields.map((field) => field.question_key))];
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
    previewSlice !== null && !sameSlice(slice, previewSlice, allowedIds, workflowInventory);

  function writeSlice(next: HistorySlice): void {
    if (preview === null) return;
    const values = sanitizedValues(next.values, allowedIds, workflowInventory);
    autosave.setDraftValue("history", {
      values,
      provenance: Object.fromEntries(Object.entries(next.provenance).filter(([id]) =>
        allowedIds.includes(id) && values[id] !== undefined && next.values[id] === values[id],
      )),
      reconciliation: next.reconciliation,
      phone_update: next.phone_update,
    });
  }

  function setValue(fieldId: string, value: string): void {
    if (slice.values[fieldId] === value) {
      return;
    }
    const values = { ...slice.values };
    if (value === "") delete values[fieldId];
    else values[fieldId] = value;
    const provenance = { ...slice.provenance };
    delete provenance[fieldId];
    writeSlice({ ...slice, values, provenance });
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
  const savedFields = applicableFields(normalizedValues(preview?.values ?? {}));
  const savedFieldIds = savedFields.map((field) => field.id);
  const missingFieldIds = (evaluation?.missing_item_ids ?? []).filter((id) => savedFieldIds.includes(id));
  const itemErrorIds = Object.keys(itemErrors).filter((id) => savedFieldIds.includes(id) || workflowFields.some((field) => field.id === id));

  let verdict: string;
  if (preview === null) {
    verdict = "";
  } else if (evaluation?.status === "complete") {
    verdict = `Complete — all ${savedFields.length} applicable history fields answered.`;
  } else if (evaluation?.status === "partial") {
    const missing = missingFieldIds.length;
    verdict = `Incomplete — ${missing} of ${savedFields.length} applicable fields still need${missing === 1 ? "s" : ""} an answer.`;
  } else {
    verdict =
      `No answers yet. ${savedFields.length} applicable history fields need review.`;
  }

  const previewProvenance = Object.fromEntries(Object.entries(preview?.provenance ?? {}).filter(([id]) =>
    slice.values[id] !== undefined && slice.values[id] === preview?.values[id],
  ));

  const hasResponse = (field: HistoryField): boolean =>
    field.values.some((value) => value === slice.values[field.id]);
  const responseCount = visibleFields.filter(hasResponse).length;

  return (
    <section className="xi-card xi-diagnosis xi-history" aria-labelledby="history-heading">
      <h3 className="xi-section-title" id="history-heading">
        History (step {stepNumber})
      </h3>
      <p className="xi-hint">
        Review medication exposure, prior treatment and movement history.
        Select Unknown when you cannot determine an answer. Other proposal context
        may also offer Not assessed when it has not been reviewed. Responses save automatically.
      </p>
      <div className="xi-diagnosis-workspace">
        <div className="xi-diagnosis-checklist">
          <div className="xi-diagnosis-overview">
            <div>
              <h4>Past medical history</h4>
              <p>Record a response for each question. Core history responses default to Unknown.</p>
            </div>
            <span className="xi-diagnosis-count">{responseCount} / {visibleFields.length} responses</span>
          </div>
          <nav className="xi-diagnosis-nav xi-history-nav" aria-label="Jump to history section">
            {visibleGroups.map((group) => (
              <a key={group.id} href={`#history-group-${group.id}`} onClick={(event) => {
                event.preventDefault();
                document.getElementById(`history-group-${group.id}`)?.focus();
              }}>{group.title}</a>
            ))}
          </nav>
          {visibleGroups.map((group) => {
            const fields = group.fields;
            return (
              <section className="xi-diagnosis-criterion" key={group.id} aria-labelledby={`history-group-${group.id}`}>
                <header className="xi-diagnosis-criterion-header">
                  <h4 id={`history-group-${group.id}`} tabIndex={-1}>{group.title}</h4>
                  <span className="xi-diagnosis-count">{fields.filter(hasResponse).length} / {fields.length} answered</span>
                </header>
                {fields.map((field) => {
                  const errorId = itemErrors[field.id] !== undefined ? `hist-${field.id}-error` : undefined;
                  return (
                    <fieldset key={field.id} className="xi-diagnosis-item xi-history-item" disabled={preview === null}>
                      <legend className="xi-sr-only">{field.label}</legend>
                      <div className="xi-diagnosis-question">
                        <div aria-hidden="true">{field.label}</div>
                        <p className="xi-history-period">{field.period}</p>
                      </div>
                      <div className="xi-diagnosis-answer-row">
                        <span className="xi-diagnosis-item-hint">{hasResponse(field) ? "Answered" : "Awaiting response"}</span>
                        <div className={`xi-diagnosis-options${field.id === "h_onset_timing" ? " xi-history-options-long" : group.id === "treatment" ? " xi-history-options-paired" : ""}`}>
                          {field.values.map((value) => (
                            <label key={value} htmlFor={`hist-${field.id}-${value}`}>
                              <input id={`hist-${field.id}-${value}`} type="radio"
                                name={`hist-${field.id}`} value={value}
                                checked={slice.values[field.id] === value}
                                onChange={() => setValue(field.id, value)}
                                aria-invalid={errorId !== undefined || undefined}
                                aria-describedby={errorId}
                              />
                              <span>{valueLabel(value)}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                      {errorId !== undefined && (
                        <p className="xi-field-error" id={errorId} role="alert">{(itemErrors[field.id] ?? []).join(" ")}</p>
                      )}
                      <details className="xi-history-provenance">
                        <summary>Response source{previewProvenance[field.id]?.source === "copied_baseline" ? ": copied from baseline" : ""}</summary>
                        <p className="xi-hint" id={`hist-${field.id}-provenance`}>{provenanceText(previewProvenance[field.id])}</p>
                      </details>
                    </fieldset>
                  );
                })}
              </section>
            );
          })}

          {questionKeys.map((key) => {
            const fields = workflowFields.filter((field) => field.question_key === key);
            const answered = fields.filter((field) => field.permitted.includes(String(slice.values[field.id] ?? ""))).length;
            const readiness = preview?.proposal_readiness?.questions.find((question) => question.question_key === key);
            return (
              <section className="xi-diagnosis-criterion" key={key} aria-labelledby={`history-workflow-${key}`}>
                <header className="xi-diagnosis-criterion-header">
                  <h4 id={`history-workflow-${key}`} tabIndex={-1}>{readiness?.question_label ?? key.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase())}</h4>
                  <span className="xi-diagnosis-count">{answered} / {fields.length} answered</span>
                </header>
                <p className="xi-hint">Review this context for proposal generation. Unknown or unassessed required context pauses the affected question.</p>
                {readiness && <p className="xi-hint">Saved required inputs: {readiness.execution_ready_count} / {readiness.required_count} execution-ready.{answersPending && " Changes pending."}</p>}
                {fields.map((field) => (
                  <div className="xi-diagnosis-item xi-history-item" key={field.id}>
                    <label htmlFor={`hist-${field.id}`}>{field.definition}</label>
                    <p className="xi-history-period">{field.time_window.replaceAll("_", " ")}</p>
                    <select id={`hist-${field.id}`} className="xi-input"
                      disabled={preview === null}
                      aria-describedby={preview?.proposal_readiness?.blockers.some((blocker) => blocker.source_path === field.id) ? `hist-${field.id}-readiness` : undefined}
                      value={typeof slice.values[field.id] === "string" ? String(slice.values[field.id]) : ""}
                      onChange={(event) => setValue(field.id, event.target.value)}>
                      {!field.default_value && <option value="">Select a response</option>}
                      {field.permitted.map((value) => <option key={value} value={value}>{valueLabel(value)}</option>)}
                    </select>
                    {preview?.proposal_readiness?.blockers.filter((blocker) => blocker.source_path === field.id).map((blocker) => (
                      <p className="xi-hint" key={blocker.question_key} id={`hist-${field.id}-readiness`}>{blocker.reason}{answersPending && " Based on saved responses."}</p>
                    ))}
                    {itemErrors[field.id] && <p className="xi-field-error" role="alert">{itemErrors[field.id].join(" ")}</p>}
                    {field.operationalization && <details className="xi-history-provenance"><summary>Assessment guidance</summary><p className="xi-hint">{field.operationalization}</p></details>}
                  </div>
                ))}
              </section>
            );
          })}

          <div className="xi-history-followup" aria-label="History reconciliation">
            <h4 className="xi-section-title" style={{ fontSize: 15 }}>
              Reconciliation (follow-up)
            </h4>
            <p className="xi-hint" style={{ marginTop: 0 }}>
              For follow-up encounters, review the copied history and mark it
              reconciled. For registration, select Not required.
            </p>
            <div className="xi-field">
              <label className="xi-label" htmlFor="history-reconciliation-status">
                Reconciliation status
              </label>
              <select
                className="xi-select"
                id="history-reconciliation-status"
                disabled={preview === null}
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
                Baseline encounter ID (optional)
              </label>
              <input
                className="xi-input"
                id="history-reconciliation-baseline"
                disabled={preview === null}
                type="text"
                autoComplete="off"
                value={slice.reconciliation?.baseline_encounter_id ?? ""}
                onChange={(event) => setBaselineId(event.target.value)}
              />
            </div>
          </div>

          <div className="xi-field" style={{ marginTop: 12 }}>
            <label className="xi-label" htmlFor="history-phone-update">
              Phone update (optional)
            </label>
            <input
              className="xi-input"
              id="history-phone-update"
              disabled={preview === null}
              type="text"
              autoComplete="off"
              value={slice.phone_update ?? ""}
              onChange={(event) => setPhoneUpdate(event.target.value)}
            />
            <p className="xi-hint">
              Contact information only; not used in clinical analysis.
            </p>
          </div>

        </div>
        <aside className="xi-diagnosis-review" aria-label="History review">
          <div className="xi-diagnosis-result">
            <h4 className="xi-section-title">
              Core history summary
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
                  className="xi-status xi-diagnosis-verdict"
                  id="history-preview-status"
                  aria-live="polite"
                >
                  {verdict}
                </p>
                <p className="xi-hint">
                  Based on saved responses.
                  {" "}Core completion is separate from proposal readiness below.
                  {answersPending &&
                    " Changes pending — summary reflects the last saved responses."}
                  {previewError !== null &&
                    " Summary refresh failed; showing the last loaded responses."}
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
                    <ul className="xi-diagnosis-missing">
                      {missingFieldIds.map((id) => {
                        const field = HISTORY_FIELDS.find((candidate) => candidate.id === id);
                        return <li key={id}><a href={`#hist-${id}-${field?.values[0]}`} onClick={(event) => {
                          event.preventDefault();
                          document.getElementById(`hist-${id}-${field?.values[0]}`)?.focus();
                        }}>{field?.label ?? id}</a></li>;
                      })}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="xi-diagnosis-result">
            {preview?.proposal_readiness && previewError === null ? (
              <>
                {(answersPending || autosave.hasUnsaved || preview.revision !== autosave.baseRevision) && <p className="xi-hint">Changes pending — proposal readiness reflects the last loaded saved responses.</p>}
                <ProposalReadinessSummary readiness={preview.proposal_readiness} encounterId={encounterId} local />
              </>
            ) : <p className="xi-hint">{previewLoading ? "Loading proposal readiness…" : "Proposal readiness could not be verified."}{previewError && ` ${previewError}`}</p>}
          </div>
          <p className="xi-hint xi-history-summary-note">Unknown and Not assessed are recorded separately from No. Response counts do not indicate clinical findings.</p>
        </aside>
      </div>
    </section>
  );
}
