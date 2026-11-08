import { clinicalText } from "../../../shared/messages";
import { useLanguage } from "../../../shared/i18n";
/* Diagnosis checklist with server-computed threshold preview (S09,
 * plan.md §§2.2, 5; FR-11, FR-16).
 *
 * Wizard integration (not separate persistence): answers live in the shared
 * S07 autosave body as `draft_data["diagnosis"] = {answers, acknowledgment,
 * bypass}` and save through the same debounced PATCH + revision fence. This
 * component only edits that slice of `localData`; the verdict always comes
 * from GET /encounters/{id}/diagnosis, which runs the same T2 `evaluate()`
 * as the backend — the browser never re-implements criterion logic, so the
 * threshold display cannot drift from the released definition.
 *
 * Server-authoritative gates (read from the preview, never derived here):
 * - `requires_acknowledgment` decides the warning and acknowledgment UI;
 *   acknowledgment validity gates proposal generation. Partial work has no
 *   completed threshold result and is never mislabeled below-threshold;
 *   indeterminate (unknowns, no criterion failed) requests no acknowledgment.
 * - Acknowledgment is an empty-{} POST tied to the assessed revision +
 *   answers hash; any later relevant edit invalidates it server-side, which
 *   the refreshed preview then shows (the stale record is kept, not hidden).
 * - Bypass records remain readable as status only (no new bypass action);
 *   the record survives resume via draft_data.
 *
 * Accessibility: native fieldset/legend + radios (keyboard free), error
 * summary takes focus on action failure, preview verdict is role=status,
 * loading/empty/error states come from the real endpoints, styling uses
 * semantic tokens only (both themes), no motion to reduce.
 *
 * E2E selector contract (mirrors e2e/autosave.spec.ts style):
 * - section heading `#diagnosis-heading`: "Diagnosis (step 2)" on
 *   registration, "Diagnosis (step 1)" on follow-up (wizard index)
 * - radios `#diag-{itemId}-yes|no|unknown`
 * - verdict `#diagnosis-preview-status` (role=status)
 * - criteria table `#diagnosis-criteria`
 * - warning panel text "Completed below threshold", button
 *   "Acknowledge below-threshold result"
 * - bypassed state text "Diagnosis bypassed" (status only, no action)
 */

import "./diagnosis.css";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../identity/api";
import {
  getEncounter,
  type DraftData,
} from "../../encounters/api";
import type { useAutosave } from "../../encounters/useAutosave";
import {
  acknowledgeDiagnosis,
  getDiagnosis,
  getDiagnosisDefinition,
  type DiagnosisAnswer,
  type DiagnosisDefinition,
  type DiagnosisPreview,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface DiagnosisSectionProps {
  encounterId: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
  /** 1-based wizard index (registration 2, follow-up 1). */
  stepNumber: number;
  /** The exact server response the parent navigation check decided on: the
   * visible summary adopts it, so the verdict shown is never older or newer
   * than the decision. Null until the first guarded check. */
  checkedPreview?: DiagnosisPreview | null;
  /** While the parent checks or confirms, edits that could invalidate the
   * checked result are disabled alongside the pager. */
  locked?: boolean;
  onInitializationReady?: (ready: boolean) => void;
}

interface SectionData {
  answers: Record<string, string>;
  acknowledgment: unknown;
  bypass: unknown;
}

function sectionOf(data: DraftData): SectionData {
  const raw = data["diagnosis"];
  if (typeof raw !== "object" || raw === null) {
    return { answers: {}, acknowledgment: null, bypass: null };
  }
  const section = raw as Record<string, unknown>;
  const answers = section["answers"];
  return {
    answers:
      typeof answers === "object" && answers !== null
        ? { ...(answers as Record<string, string>) }
        : {},
    acknowledgment: section["acknowledgment"] ?? null,
    bypass: section["bypass"] ?? null,
  };
}

function hasLegacyState(value: SectionData): boolean {
  return value.bypass !== null || "__bypass" in value.answers || "__skipped" in value.answers;
}

function withDefaults(section: SectionData, definition: DiagnosisDefinition): Record<string, string> {
  const answers = { ...section.answers };
  if (!hasLegacyState(section)) {
    for (const item of definition.items) {
      if (!Object.prototype.hasOwnProperty.call(answers, item.id)) answers[item.id] = "unknown";
    }
  }
  return answers;
}

function sameAnswers(a: Record<string, string>, b: Record<string, string>): boolean {
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  return (
    keysA.length === keysB.length && keysA.every((key) => b[key] === a[key])
  );
}

const ANSWER_OPTIONS: { value: DiagnosisAnswer; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "unknown", label: "Unknown" },
];

// Presentation labels only; prompts and evaluation remain server-authoritative.
const CRITERION_TITLES: Record<string, string> = {
  A: "Characteristic symptoms", B: "Functioning", C: "Duration",
  D: "Mood disorder exclusion", E: "Substance & medical exclusion",
  F: "Developmental history",
};

export function DiagnosisSection({
  encounterId,
  autosave,
  onSessionExpired,
  stepNumber,
  checkedPreview = null,
  locked = false,
  onInitializationReady,
}: DiagnosisSectionProps) {
  const { text: uiText, t, errorText, text, language } = useLanguage();
  function translatedPrompt(item: { id: string; prompt: string }): string {
    return clinicalText(language, definition?.id ?? "diagnosis", definition?.version ?? "unavailable", item.id, item.prompt);
  }

  const [definition, setDefinition] = useState<DiagnosisDefinition | null>(null);
  const [definitionError, setDefinitionError] = useState<string | null>(null);
  const [preview, setPreview] = useState<DiagnosisPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"acknowledge" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const actionErrorRef = useRef<HTMLParagraphElement>(null);
  /** Guards the live preview against out-of-order responses: only the
   * latest started request may replace the summary, so a slow background
   * refresh can never overwrite the parent's checked result. */
  const previewSeqRef = useRef(0);

  // Action failures announce via role=alert and take keyboard focus.
  useEffect(() => {
    if (actionError !== null) {
      actionErrorRef.current?.focus();
    }
  }, [actionError]);

  // Released definition: prompts + required flags for rendering the form.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await getDiagnosisDefinition();
        if (!cancelled) {
          setDefinition(loaded);
          setDefinitionError(null);
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          onSessionExpired();
          return;
        }
        setDefinitionError(
          err instanceof ApiError
            ? err.message
            : "Diagnosis form failed to load. Check the connection and retry.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [onSessionExpired]);

  const refreshPreview = useCallback(async (): Promise<void> => {
    const seq = (previewSeqRef.current += 1);
    try {
      const fresh = await getDiagnosis(encounterId);
      if (seq !== previewSeqRef.current) {
        return;
      }
      setPreview(fresh);
      setPreviewError(null);
    } catch (err) {
      if (seq !== previewSeqRef.current) {
        return;
      }
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setPreviewError(
        err instanceof ApiError
          ? err.message
          : "Diagnosis preview failed to load. Check the connection and retry.",
      );
    } finally {
      if (seq === previewSeqRef.current) {
        setPreviewLoading(false);
      }
    }
  }, [encounterId, onSessionExpired]);

  // Live preview follows every acknowledged save: a working-note save bumps
  // the revision too, and the acknowledgment POST needs the current one.
  const savedRevision =
    autosave.saveState.kind === "saved" ? autosave.saveState.revision : 0;
  useEffect(() => {
    void refreshPreview();
  }, [refreshPreview, savedRevision]);

  // Parent navigation check: adopt its exact server response so the summary
  // shows the decided state, and invalidate older in-flight refreshes.
  useEffect(() => {
    if (checkedPreview !== null) {
      previewSeqRef.current += 1;
      setPreview(checkedPreview);
      setPreviewError(null);
      setPreviewLoading(false);
    }
  }, [checkedPreview]);

  const section = sectionOf(autosave.localData);
  const answers = section.answers;
  const latestDataRef = useRef(autosave.localData);
  latestDataRef.current = autosave.localData;
  const needsDefaults = definition !== null && !hasLegacyState(section) &&
    definition.items.some((item) => !Object.prototype.hasOwnProperty.call(answers, item.id));
  const answersPending =
    preview !== null && !sameAnswers(answers, preview.answers);

  // Keep Continue fenced until defaults have entered the shared autosave
  // state. In particular, an asynchronous definition must not let a check
  // run against the old empty snapshot.
  useEffect(() => {
    onInitializationReady?.(definition !== null && !needsDefaults);
    return () => onInitializationReady?.(false);
  }, [definition, needsDefaults, onInitializationReady]);

  useEffect(() => {
    if (definition === null || locked || busy !== null ||
        autosave.saveState.kind === "failed" || autosave.saveState.kind === "conflict") {
      return;
    }
    const current = sectionOf(latestDataRef.current);
    if (hasLegacyState(current)) return;
    const merged = withDefaults(current, definition);
    if (sameAnswers(merged, current.answers)) return;
    const raw = latestDataRef.current["diagnosis"];
    const next = {
      ...(typeof raw === "object" && raw !== null ? raw : {}),
      answers: merged,
    };
    // The ref also prevents effect replay or an immediate edit from losing
    // the just-queued defaults before React renders the autosave update.
    latestDataRef.current = { ...latestDataRef.current, diagnosis: next };
    autosave.setDraftValue("diagnosis", next);
  }, [definition, needsDefaults, locked, busy, autosave.saveState.kind, autosave.setDraftValue]);

  function setAnswer(itemId: string, value: DiagnosisAnswer): void {
    if (busy !== null || locked) return;
    const current = sectionOf(latestDataRef.current);
    if (current.answers[itemId] === value) return;
    setActionError(null);
    setNotice(null);
    const merged = definition === null ? current.answers : withDefaults(current, definition);
    const raw = latestDataRef.current["diagnosis"];
    const next = {
      ...(typeof raw === "object" && raw !== null ? raw : {}),
      answers: { ...merged, [itemId]: value },
    };
    latestDataRef.current = { ...latestDataRef.current, diagnosis: next };
    autosave.setDraftValue("diagnosis", next);
  }

  /** Flush pending autosave edits so a command fences the current revision. */
  async function currentRevision(): Promise<DiagnosisPreview> {
    await autosave.flush();
    const current = await getDiagnosis(encounterId);
    setPreview(current);
    return current;
  }

  async function syncEditorToServer(): Promise<void> {
    const truth = await getEncounter(encounterId);
    autosave.applyServerSnapshot(
      truth.draft_data,
      truth.revision,
      truth.server_timestamp ?? null,
    );
  }

  function describeFailure(err: unknown): string {
    return err instanceof ApiError
      ? err.message
      : "Request failed. Check the connection and retry.";
  }

  async function handleAcknowledge(): Promise<void> {
    if (busy !== null) {
      return;
    }
    setActionError(null);
    setNotice(null);
    setBusy("acknowledge");
    try {
      const current = await currentRevision();
      if (!current.requires_acknowledgment) {
        setNotice(
          "Acknowledgment no longer applies — the preview above shows the current server state.",
        );
        return;
      }
      const result = await acknowledgeDiagnosis(encounterId, current.revision);
      await syncEditorToServer();
      setPreview(result);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 412) {
        await autosave.reloadTruth();
        await refreshPreview();
        setActionError(
          "The draft changed in another tab. Loaded the latest state — review and retry.",
        );
        return;
      }
      if (err instanceof ApiError && (err.status === 409 || err.status === 422)) {
        await refreshPreview();
        setActionError(err.message);
        return;
      }
      setActionError(describeFailure(err));
    } finally {
      setBusy(null);
    }
  }

  const evaluation = preview?.evaluation ?? null;
  const overall = evaluation?.findings.overall;
  const criteria = evaluation?.findings.criteria ?? null;
  const itemErrors = evaluation?.item_errors ?? {};
  const itemErrorIds = Object.keys(itemErrors);

  let verdict: string;
  if (preview === null) {
    verdict = "";
  } else if (preview.bypass_valid) {
    verdict = t("diagnosisBypassed");
  } else if (evaluation?.status === "complete" && overall === "criteria_satisfied") {
    verdict = t("diagnosisCriteriaSatisfied", { version: preview.definition_version });
  } else if (
    evaluation?.status === "complete" &&
    overall === "below_threshold" &&
    preview.requires_acknowledgment
  ) {
    verdict =
      t("diagnosisAckNeeded");
  } else if (
    evaluation?.status === "complete" &&
    overall === "below_threshold" &&
    preview.acknowledgment_valid
  ) {
    verdict =
      t("diagnosisAcknowledged");
  } else if (evaluation?.status === "complete") {
    verdict =
      t("diagnosisIndeterminate");
  } else if (evaluation?.status === "partial") {
    const missing = evaluation.missing_item_ids.length;
    verdict = t(missing === 1 ? "diagnosisMissingOne" : "diagnosisMissingMany", { missing });
  } else {
    verdict =
      t("diagnosisUnanswered");
  }

  return (
    <section className="xi-card xi-diagnosis" aria-labelledby="diagnosis-heading">
      <h3 className="xi-section-title" id="diagnosis-heading">
        
        {t("diagnosisStep")} {stepNumber})
      </h3>

      {definitionError !== null && (
        <p className="xi-form-error" role="alert">
          {errorText(definitionError)}{" "}
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => window.location.reload()}
          >
            {t("retry")}</button>
        </p>
      )}
      {definition === null && definitionError === null && (
        <p>{t("loadingDiagnosisForm")}</p>
      )}

      <div className="xi-diagnosis-workspace">
      {definition !== null && (
        <div className="xi-diagnosis-checklist">
          <div className="xi-diagnosis-overview">
            <div>
              <h4>{t("schizophreniaCriteria")}</h4>
              <p>{t("responseCountCaution")}</p>
            </div>
            <span className="xi-diagnosis-count">
              {definition.items.filter((item) => ANSWER_OPTIONS.some((option) => option.value === answers[item.id])).length}
              {" / "}{definition.items.length}  {t("responses")}
            </span>
          </div>
          {Array.from(new Set(definition.items.map((item) => item.id.split("_")[0].toUpperCase()))).map((criterion) => {
            const items = definition.items.filter((item) => item.id.split("_")[0].toUpperCase() === criterion);
            const answered = items.filter((item) => ANSWER_OPTIONS.some((option) => option.value === answers[item.id])).length;
            return (
              <section className="xi-diagnosis-criterion" key={criterion} aria-labelledby={`diag-group-${criterion}`}>
                <header className="xi-diagnosis-criterion-header">
                  <span className="xi-diagnosis-letter" aria-hidden="true">{criterion}</span>
                  <h4 id={`diag-group-${criterion}`} tabIndex={-1}>
                    <span className="xi-sr-only">{t("criterion")} {criterion}: </span>
                    {text(CRITERION_TITLES[criterion] ?? t("additionalCriteria"))}
                  </h4>
                  <span className="xi-diagnosis-count">{t("answeredCount", { answered, total: items.length })}</span>
                </header>
                <div className="xi-diagnosis-items">
                  {items.map((item) => (
                    <fieldset key={item.id} className="xi-diagnosis-item" disabled={busy !== null || locked}>
                      <legend className="xi-sr-only">{translatedPrompt(item)}</legend>
                      <div className="xi-diagnosis-question" aria-hidden="true">{translatedPrompt(item)}</div>
                      <div className="xi-diagnosis-answer-row">
                        <span className="xi-diagnosis-item-hint">
                          {item.required
                            ? (ANSWER_OPTIONS.some((option) => option.value === answers[item.id]) ? "" : t("awaitingResponse"))
                            : t("requiredApplicable")}
                        </span>
                        <div className="xi-diagnosis-options">
                          {ANSWER_OPTIONS.map((option) => (
                            <label key={option.value} htmlFor={`diag-${item.id}-${option.value}`}>
                              <input
                                id={`diag-${item.id}-${option.value}`}
                                type="radio"
                                name={`diagnosis-${item.id}`}
                                value={option.value}
                                checked={answers[item.id] === option.value}
                                onChange={() => setAnswer(item.id, option.value)}
                                aria-invalid={itemErrors[item.id] !== undefined || undefined}
                                aria-describedby={itemErrors[item.id] !== undefined ? `diag-${item.id}-error` : undefined}
                              />
                              <span>{text(option.label)}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                      {itemErrors[item.id] !== undefined && (
                        <p className="xi-field-error" id={`diag-${item.id}-error`}>
                          {itemErrors[item.id].join(" ")}
                        </p>
                      )}
                    </fieldset>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <aside className="xi-diagnosis-review" aria-label={t("diagnosisReview")}>
      <div className="xi-diagnosis-result">
        <h4 className="xi-section-title">
          {t("assessmentSummary")}
        </h4>
        {previewLoading && preview === null && <p>{t("loadingDiagnosisPreview")}</p>}
        {previewError !== null && preview === null && (
          <div>
            <p className="xi-form-error" role="alert">
              {errorText(previewError)}
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
                {t("retry")}</button>
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
              id="diagnosis-preview-status"
              aria-live="polite"
            >
              {uiText(verdict)}
            </p>
            <p className="xi-hint">
              
              {t("serverEvaluationDefinition")} {preview.definition_version}  {t("savedRevision278")} {preview.revision}
              {answersPending &&
                t("unsavedAnswerEditsPendingPreviewReflectsThe")}
              {previewError !== null && t("previewRefreshFailedShowingLastLoadedState")}
            </p>

            {itemErrorIds.length > 0 && (
              <p className="xi-form-error" role="alert">
                {t("serverAnswerProblems")}{" "}
                {itemErrorIds
                  .map((id) => `${id}: ${(itemErrors[id] ?? []).join(" ")}`)
                  .join("; ")}
              </p>
            )}

            {evaluation?.status === "partial" && definition !== null && (
              <details className="xi-diagnosis-outstanding">
                <summary>{t("stillNeeded")} {evaluation.missing_item_ids.length}  {t("answers")}</summary>
                <ul className="xi-diagnosis-missing">
                  {evaluation.missing_item_ids.map((id) => (
                    <li key={id}>
                      <a href={`#diag-${id}-yes`} onClick={(event) => {
                        event.preventDefault();
                        document.getElementById(`diag-${id}-yes`)?.focus();
                      }}>{translatedPrompt(definition.items.find((item) => item.id === id) ?? { id, prompt: id })}</a>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {criteria !== null && evaluation?.status === "complete" && (
              <table className="xi-table" id="diagnosis-criteria" aria-label={t("criterionStates")}>
                <thead>
                  <tr>
                    <th scope="col">{t("criterion")}</th>
                    <th scope="col">{t("state")}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(criteria).map(([criterion, state]) => (
                    <tr key={criterion}>
                      <td><strong>{criterion}</strong>{" · "}{text(CRITERION_TITLES[criterion] ?? t("criterion"))}</td>
                      <td><span className={`xi-diagnosis-state xi-diagnosis-state--${state}`}>
                        {state === "met" ? t("met") : state === "not_met" ? t("notMet") : state === "unknown" ? t("unknown") : state}
                      </span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {preview.requires_acknowledgment && (
              <div className="xi-warning-panel" aria-labelledby="diagnosis-warning-heading">
                <h5 id="diagnosis-warning-heading" style={{ margin: "0 0 8px" }}>
                  {t("belowThresholdWarning")}
                </h5>
                <p style={{ marginTop: 0 }}>
                  
                  {t("atLeastOneRequiredCriterionIsNot")} {preview.definition_version}{t("savedRevision280")}{" "}
                  {preview.revision}{t("acknowledgmentIsASeparateRecordedActionRequired")}
                </p>
                <button
                  className="xi-btn xi-btn-primary"
                  type="button"
                  disabled={busy !== null || locked}
                  onClick={() => void handleAcknowledge()}
                >
                  {busy === "acknowledge"
                    ? t("acknowledging")
                    : t("acknowledgeBelowThresholdResult")}
                </button>
              </div>
            )}

            {preview.acknowledgment_valid && preview.acknowledgment !== null && (
              <p className="xi-hint">
                
                {t("acknowledgedByAuthor")} {preview.acknowledgment.actor_id}  {t("at")}{" "}
                {preview.acknowledgment.acknowledged_at}  {t("assessedRevision")}{" "}
                {preview.acknowledgment.revision}{t("definition285")}{" "}
                {preview.acknowledgment.definition_version}).
              </p>
            )}

          </div>
        )}
      </div>

      {preview?.bypass_valid === true && preview.bypass !== null && (
        <p className="xi-hint">
          
          {t("diagnosisBypassedRecordedForAuthor")} {preview.bypass.actor_id}{" "}
          
          {t("at")} {preview.bypass.bypassed_at}  {t("revision287")} {preview.bypass.revision}{t("bypassIsDistinctFromCompletionItIs")}
        </p>
      )}
      {preview !== null &&
        preview.bypass !== null &&
        !preview.bypass_valid && (
        <p className="xi-hint">
          {t("previousBypassInvalid")}
        </p>
      )}

      </aside>
      </div>

      {notice !== null && (
        <p className="xi-status" role="status">
          {text(notice)}
        </p>
      )}
      {actionError !== null && (
        <p
          className="xi-form-error"
          role="alert"
          tabIndex={-1}
          ref={actionErrorRef}
        >
          {errorText(actionError)}
        </p>
      )}
    </section>
  );
}
