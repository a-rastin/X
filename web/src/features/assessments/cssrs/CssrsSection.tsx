import { clinicalText } from "../../../shared/messages";
import { useLanguage } from "../../../shared/i18n";
/* C-SSRS recent-ideation worksheet with server-computed preview
 * (S11, plan.md §§2.2, 5; FR-13, FR-20).
 *
 * Wizard integration (not separate persistence): answers live in the shared
 * S07 autosave body as `draft_data["cssrs"] = {answers}` and save through
 * the same debounced PATCH + revision fence. This component only edits that
 * slice of `localData`. Severity and flags come from
 * GET /encounters/{id}/cssrs — the browser never computes them.
 *
 * One group, five items (css_i1_recent .. css_i5_recent). Each item is
 * yes / no on registration, with No as the default; follow-up also offers
 * Unknown and defaults to it. Registration normalizes saved Unknown to No.
 * An explicit yes or no is left as stored. A higher-level yes does not change
 * any other item.
 * A legacy skip record is `{"__skipped": true}` (evaluates to
 * not_assessed) and the five items are not shown as required while skipped.
 * Resume clears the skip: registration resets to No; follow-up retains explicit
 * answers and defaults missing ones to Unknown. There is no Skip control.
 *
 * The definition payload may still list other items or value_type yes_no.
 * Only these five prompts are rendered; a missing prompt keeps the wording
 * from the current released recent items. Older preview fields are not
 * rendered. No numeric scales.
 *
 * No ack/bypass here (unlike diagnosis): there are no command POSTs, so no
 * `applyServerSnapshot` resync is needed — the preview refreshes after each
 * acknowledged autosave (same pattern as PANSS). The 412 path stays inside
 * the shared useAutosave hook. A failed save never shows Saved. Switching
 * pages or autosaving never turns a skip into a yes or no; reload/resume
 * preserves state via the saved draft_data.
 *
 * Accessibility: native fieldset/legend + radios (keyboard free), preview
 * verdict is an aria-live region, urgent/review panels are persistent text
 * (never auto-dismissed, never color-alone) with headings, item errors use
 * role=alert + aria-describedby, styling uses the existing worksheet tokens.
 *
 * E2E selector contract (for the test agent's e2e/cssrs.spec.ts):
 * - section heading `#cssrs-heading`: "C-SSRS (step 4)" on registration,
 *   "C-SSRS (step 3)" on follow-up (wizard index)
 * - one group `#cssrs-group-recent-ideation`: "Recent ideation — past month"
 * - radios `#cssrs-{itemId}-yes`, `#cssrs-{itemId}-no` and, on
 *   follow-up, `#cssrs-{itemId}-unknown` (name `cssrs-{itemId}`) for css_i1_recent,
 *   css_i2_recent, css_i3_recent, css_i4_recent, css_i5_recent.
 *   Registration defaults to No; follow-up offers and defaults to Unknown.
 * - verdict `#cssrs-preview-status` (aria-live polite)
 * - scores `#cssrs-scores` (recent ideation severity only)
 * - flags container `#cssrs-flags`; urgent panel `#cssrs-high-risk-alert`
 *   (role=alert); review panel `#cssrs-clinical-review`
 * - legacy skip shows "not assessed" with a "Resume C-SSRS (clear skip)"
 *   recovery action (no Skip button)
 * - still-needed list `#cssrs-still-needed` (these five items only)
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import "../assessment-worksheet.css";
import { ApiError } from "../../identity/api";
import type { DraftData } from "../../encounters/api";
import type { useAutosave } from "../../encounters/useAutosave";
import {
  getCssrs,
  getCssrsDefinition,
  type CssrsAnswerValue,
  type CssrsDefinition,
  type CssrsPreview,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface CssrsSectionProps {
  encounterId: string;
  isRegistration: boolean;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
  /** 1-based wizard index (registration 4, follow-up 3). */
  stepNumber: number;
}

/** Prompts from the current released recent items, used when the definition omits one. */
const RECENT_IDEATION: { id: string; prompt: string }[] = [
  {
    id: "css_i1_recent",
    prompt:
      "Ideation 1 (Wish to be dead, recent): Have you wished you were dead, or wished you could go to sleep and not wake up?",
  },
  {
    id: "css_i2_recent",
    prompt:
      "Ideation 2 (Nonspecific active thoughts, recent): Have you had thoughts of killing yourself, without thinking about a method, intent, or plan?",
  },
  {
    id: "css_i3_recent",
    prompt:
      "Ideation 3 (Method, without intent, recent): Have you thought about how you might kill yourself, without intending to act on those thoughts?",
  },
  {
    id: "css_i4_recent",
    prompt:
      "Ideation 4 (Some intent, no specific plan, recent): Have you had suicidal thoughts and some intention of acting on them, but without a specific plan?",
  },
  {
    id: "css_i5_recent",
    prompt:
      "Ideation 5 (Specific plan and intent, recent): Have you worked out a method or plan for killing yourself, and do you intend to carry it out?",
  },
];

const ANSWER_OPTIONS: { value: CssrsAnswerValue; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "unknown", label: "Unknown" },
];

function answersOf(data: DraftData): Record<string, unknown> {
  const raw = data["cssrs"];
  if (typeof raw !== "object" || raw === null) {
    return {};
  }
  const section = raw as Record<string, unknown>;
  const answers = section["answers"];
  if (typeof answers !== "object" || answers === null) {
    return {};
  }
  return { ...(answers as Record<string, unknown>) };
}

function sameAnswers(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    if (a[key] !== b[key]) {
      return false;
    }
  }
  return true;
}

function explicitAnswer(value: unknown): CssrsAnswerValue | null {
  return value === "yes" || value === "no" || value === "unknown" ? value : null;
}

function isRecentId(id: string): boolean {
  return RECENT_IDEATION.some((item) => item.id === id);
}

/** Normalize the five answers for the encounter, or return null if unchanged. */
function normalizedAnswers(
  answers: Record<string, unknown>,
  isRegistration: boolean,
): Record<string, CssrsAnswerValue> | null {
  if (answers["__skipped"] === true) {
    return null;
  }
  const next: Record<string, CssrsAnswerValue> = {};
  let changed = false;
  for (const item of RECENT_IDEATION) {
    next[item.id] = answerForMode(answers[item.id], isRegistration);
    if (next[item.id] !== answers[item.id]) {
      changed = true;
    }
  }
  if (Object.keys(answers).some((key) => !isRecentId(key))) {
    changed = true;
  }
  return changed ? next : null;
}

function answeredItems(
  answers: Record<string, unknown>,
  itemId: string,
  value: CssrsAnswerValue,
  isRegistration: boolean,
): Record<string, CssrsAnswerValue> {
  const next: Record<string, CssrsAnswerValue> = {};
  for (const item of RECENT_IDEATION) {
    next[item.id] =
      item.id === itemId
        ? value
        : answerForMode(answers[item.id], isRegistration);
  }
  return next;
}

function answerForMode(value: unknown, isRegistration: boolean): CssrsAnswerValue {
  const answer = explicitAnswer(value);
  return answer === null || (isRegistration && answer === "unknown")
    ? (isRegistration ? "no" : "unknown")
    : answer;
}

function recentItems(
  definition: CssrsDefinition | null,
): { id: string; prompt: string }[] {
  const byId = new Map(
    (definition?.items ?? []).map((item) => [item.id, item.prompt]),
  );
  return RECENT_IDEATION.map((item) => {
    const prompt = byId.get(item.id);
    return {
      id: item.id,
      prompt: prompt !== undefined && prompt !== "" ? prompt : item.prompt,
    };
  });
}

export function CssrsSection({
  encounterId,
  isRegistration,
  autosave,
  onSessionExpired,
  stepNumber,
}: CssrsSectionProps) {
  const { text: uiText, t, text, errorText, language } = useLanguage();
  const [definition, setDefinition] = useState<CssrsDefinition | null>(null);
  const [definitionError, setDefinitionError] = useState<string | null>(null);
  const [preview, setPreview] = useState<CssrsPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await getCssrsDefinition();
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
            : "C-SSRS form failed to load. Check the connection and retry.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [onSessionExpired]);

  const refreshPreview = useCallback(async (): Promise<void> => {
    try {
      const fresh = await getCssrs(encounterId);
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
          : "C-SSRS preview failed to load. Check the connection and retry.",
      );
    } finally {
      setPreviewLoading(false);
    }
  }, [encounterId, onSessionExpired]);

  const savedRevision =
    autosave.saveState.kind === "saved" ? autosave.saveState.revision : 0;
  useEffect(() => {
    void refreshPreview();
  }, [refreshPreview, savedRevision]);

  const answers = answersOf(autosave.localData);
  const skipped = answers["__skipped"] === true;
  const answerKey = JSON.stringify(answers);
  const answersRef = useRef(answers);
  answersRef.current = answers;
  const { setDraftValue } = autosave;

  // Default answers are stored for the encounter kind; retired ids are dropped
  // on that same write. Skip is left untouched. The ref is the latest slice
  // so a queued write cannot replace a newer yes or no.
  useEffect(() => {
    const next = normalizedAnswers(answersRef.current, isRegistration);
    if (next === null) {
      return;
    }
    setDraftValue("cssrs", { answers: next });
  }, [answerKey, setDraftValue, isRegistration]);

  const answersPending =
    preview !== null && !sameAnswers(answers, preview.answers);

  function setAnswer(itemId: string, value: CssrsAnswerValue): void {
    if (!skipped && answers[itemId] === value && normalizedAnswers(answers, isRegistration) === null) {
      return;
    }
    setDraftValue("cssrs", { answers: answeredItems(answers, itemId, value, isRegistration) });
  }

  function handleResume(): void {
    const next: Record<string, CssrsAnswerValue> = {};
    for (const item of RECENT_IDEATION) {
      next[item.id] = isRegistration ? "no" : answerForMode(answers[item.id], false);
    }
    setDraftValue("cssrs", { answers: next });
  }

  const evaluation = preview?.evaluation ?? null;
  const scores = evaluation?.scores ?? null;
  const findings = evaluation?.findings ?? null;
  const flags = findings?.flags ?? [];
  const itemErrors = evaluation?.item_errors ?? {};
  const visibleErrorIds = Object.keys(itemErrors).filter(isRecentId);
  const visibleMissing = (evaluation?.missing_item_ids ?? []).filter(isRecentId);
  const hasHighRisk = flags.includes("high_risk_alert");
  const hasReview = flags.includes("clinical_review");
  const hasNoPositives = flags.includes("no_positive_items");
  const items = recentItems(definition);

  function translatedPrompt(item: { id: string; prompt: string }): string {
    return clinicalText(language, definition?.id ?? "cssrs", definition?.version ?? "v2", item.id, item.prompt);
  }



  let verdict: string;
  if (preview === null) {
    verdict = "";
  } else if (evaluation?.status === "not_assessed") {
    verdict =
      t("cssrsSkipped");
  } else if (evaluation?.status === "complete" && hasNoPositives) {
    verdict = t("cssrsNoPositives", { severity: formatScore(scores?.ideation_severity_recent) });
  } else if (evaluation?.status === "complete" && scores !== null) {
    verdict = t("cssrsComplete", { severity: formatScore(scores.ideation_severity_recent) });
  } else if (evaluation?.status === "complete") {
    verdict = t("cssrsUnavailable");
  } else if (evaluation?.status === "partial") {
    const missing = visibleMissing.length;
    verdict =
      missing === 0
        ? t("cssrsAnswersSaved")
        : t(missing === 1 ? "cssrsMissingOne" : "cssrsMissingMany", { missing });
  } else {
    verdict = t("cssrsUnanswered");
  }

  function renderItem(item: { id: string; prompt: string }): ReactNode {
    const errorId =
      itemErrors[item.id] !== undefined ? `cssrs-${item.id}-error` : undefined;
    const selected = answerForMode(answers[item.id], isRegistration);
    return (
      <fieldset key={item.id} className="xi-assessment-item">
        <legend className="xi-assessment-sr-only">{translatedPrompt(item)}</legend>
        <div className="xi-assessment-question" aria-hidden="true">
          {translatedPrompt(item)}
        </div>
        <div className="xi-assessment-options" aria-label={translatedPrompt(item)}>
          {ANSWER_OPTIONS.filter((option) => !isRegistration || option.value !== "unknown").map((option) => (
            <label key={option.value} htmlFor={`cssrs-${item.id}-${option.value}`}>
              <input
                id={`cssrs-${item.id}-${option.value}`}
                type="radio"
                name={`cssrs-${item.id}`}
                value={option.value}
                checked={selected === option.value}
                onChange={() => setAnswer(item.id, option.value)}
                disabled={skipped}
                aria-describedby={errorId}
              />
              <span>{text(option.label)}</span>
            </label>
          ))}
        </div>
        {errorId !== undefined && !skipped && (
          <p className="xi-field-error" id={errorId} role="alert">
            {(itemErrors[item.id] ?? []).join(" ")}
          </p>
        )}
      </fieldset>
    );
  }

  return (
    <section className="xi-card xi-assessment" aria-labelledby="cssrs-heading">
      <h3 className="xi-section-title" id="cssrs-heading">
        
        {t("cSsrsStep")} {stepNumber})
      </h3>
      <p className="xi-hint">
        {t("cssrsWindowGuide")}
      </p>
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

      <div className="xi-assessment-workspace">
        <div className="xi-assessment-worksheet">
          <div className="xi-assessment-group">
            <div className="xi-assessment-group-heading">
              <h4 id="cssrs-group-recent-ideation" tabIndex={-1}>
                {t("recentIdeationMonth")}
              </h4>
              <span>
                {skipped
                  ? t("notAssessed")
                  : `${items.length} / ${items.length} answered`}
              </span>
            </div>
            <p className="xi-hint">
              {t("cssrsExplicitLevels")}
            </p>
            {items.map(renderItem)}
          </div>
        </div>
        <aside className="xi-assessment-review" aria-labelledby="cssrs-review">
          <div className="xi-assessment-result">
            <h4 id="cssrs-review" tabIndex={-1}>
              {t("assessmentReviewTitle")}
            </h4>
            {previewLoading && preview === null && <p>{t("loadingCssrsPreview")}</p>}
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
                  className="xi-status"
                  id="cssrs-preview-status"
                  aria-live="polite"
                >
                  {uiText(verdict)}
                </p>
                {(answersPending || previewError !== null) && (
                  <p className="xi-hint" aria-live="polite">
                    {answersPending &&
                      t("answersHaveChangedResultsReflectTheLast")}
                    {previewError !== null &&
                      t("resultsCouldNotRefreshShowingTheLast")}
                  </p>
                )}
                <details className="xi-assessment-provenance">
                  <summary>{t("assessmentDetails")}</summary>
                  <p className="xi-hint">
                    {t("definition")} {preview.definition_version}{t("savedRevision269")}{" "}
                    {preview.revision}.
                  </p>
                </details>

                {visibleErrorIds.length > 0 && !skipped && (
                  <p className="xi-form-error" role="alert">
                    {t("reviewAnswers")}{" "}
                    {visibleErrorIds
                      .map((id) => `${id}: ${(itemErrors[id] ?? []).join(" ")}`)
                      .join("; ")}
                  </p>
                )}

                {(hasHighRisk || hasReview || hasNoPositives) && (
                  <div id="cssrs-flags" aria-live="polite">
                    {hasHighRisk && (
                      <div
                        className="xi-urgent-panel"
                        id="cssrs-high-risk-alert"
                        role="alert"
                        aria-labelledby="cssrs-urgent-heading"
                      >
                        <h5
                          id="cssrs-urgent-heading"
                          style={{ margin: "0 0 8px" }}
                        >
                          {t("urgentCssrsReview")}
                        </h5>
                        <p style={{ marginTop: 0, marginBottom: 0 }}>
                          {t("cssrsHighRiskGuide")}
                        </p>
                      </div>
                    )}
                    {hasReview && !hasHighRisk && (
                      <div
                        className="xi-warning-panel"
                        id="cssrs-clinical-review"
                        aria-labelledby="cssrs-review-heading"
                      >
                        <h5
                          id="cssrs-review-heading"
                          style={{ margin: "0 0 8px" }}
                        >
                          {t("clinicalReviewFlagged")}
                        </h5>
                        <p style={{ marginTop: 0, marginBottom: 0 }}>
                          {t("cssrsClinicalReviewGuide")}
                        </p>
                      </div>
                    )}
                    {hasReview && hasHighRisk && (
                      <div
                        className="xi-warning-panel"
                        id="cssrs-clinical-review"
                        aria-labelledby="cssrs-review-heading"
                      >
                        <h5
                          id="cssrs-review-heading"
                          style={{ margin: "0 0 8px" }}
                        >
                          {t("clinicalReviewAlsoFlagged")}
                        </h5>
                        <p style={{ marginTop: 0, marginBottom: 0 }}>
                          {t("cssrsBothFlagsGuide")}
                        </p>
                      </div>
                    )}
                    {hasNoPositives && (
                      <p className="xi-hint">
                        {t("cssrsNegativeGuide")}
                      </p>
                    )}
                  </div>
                )}

                {!skipped &&
                  evaluation?.status === "partial" &&
                  visibleMissing.length > 0 && (
                    <details
                      id="cssrs-still-needed"
                      className="xi-assessment-outstanding"
                    >
                      <summary>
                        {t("stillNeeded")} {visibleMissing.length}  {t("answers")}
                      </summary>
                      <ul>
                        {visibleMissing.map((id) => (
                          <li key={id}>
                            <a
                              href={`#cssrs-${id}-yes`}
                              onClick={(event) => {
                                event.preventDefault();
                                document
                                  .querySelector<HTMLInputElement>(
                                    `input[name="cssrs-${id}"]`,
                                  )
                                  ?.focus();
                              }}
                            >
                              {translatedPrompt(items.find((item) => item.id === id) ?? { id, prompt: id })}
                            </a>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}

                {scores !== null && evaluation?.status !== "not_assessed" && (
                  <table
                    className="xi-table"
                    id="cssrs-scores"
                    aria-label={t("cssrsSeverityScores")}
                  >
                    <thead>
                      <tr>
                        <th scope="col">{t("dimension")}</th>
                        <th scope="col">{t("severity")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>{t("recentIdeationSeverity")}</td>
                        <td>{formatScore(scores.ideation_severity_recent)}</td>
                      </tr>
                    </tbody>
                  </table>
                )}
                {evaluation?.status !== "complete" &&
                  evaluation?.status !== "not_assessed" && (
                    <p className="xi-hint">
                      {t("cssrsSeverityGuide")}
                    </p>
                  )}
              </div>
            )}
          </div>
          {skipped && (
            <div className="xi-assessment-skip">
              <div className="xi-row-actions" style={{ marginTop: 8 }}>
                <button
                  className="xi-btn xi-btn-secondary"
                  type="button"
                  onClick={handleResume}
                >
                  {t("resumeCssrs")}
                </button>
              </div>
              <p className="xi-hint">
                {t("notAssessedResume")}
              </p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}

function formatScore(value: number | null | undefined): string {
  return typeof value === "number" ? String(value) : "—";
}
