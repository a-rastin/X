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
 * yes / no / unknown. A missing saved answer selects Unknown and, when the
 * section is not skipped, is persisted as "unknown". An explicit yes or no
 * is left as stored. A higher-level yes does not change any other item.
 * A legacy skip record is `{"__skipped": true}` (evaluates to
 * not_assessed) and the five items are not shown as required while skipped.
 * Resume clears the skip and returns each item without an explicit yes or
 * no to Unknown. There is no Skip control.
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
 * - tri-state radios `#cssrs-{itemId}-yes`, `#cssrs-{itemId}-no`,
 *   `#cssrs-{itemId}-unknown` (name `cssrs-{itemId}`) for css_i1_recent,
 *   css_i2_recent, css_i3_recent, css_i4_recent, css_i5_recent.
 *   Unknown is selected when the saved answer is missing.
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

/** Five tri-state answers, or null when the stored slice already matches.
 * Missing keys become "unknown". Any other key is dropped. Yes and no stay. */
function normalizedAnswers(
  answers: Record<string, unknown>,
): Record<string, CssrsAnswerValue> | null {
  if (answers["__skipped"] === true) {
    return null;
  }
  const next: Record<string, CssrsAnswerValue> = {};
  let changed = false;
  for (const item of RECENT_IDEATION) {
    const explicit = explicitAnswer(answers[item.id]);
    if (explicit === null) {
      next[item.id] = "unknown";
      changed = true;
    } else {
      next[item.id] = explicit;
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
): Record<string, CssrsAnswerValue> {
  const next: Record<string, CssrsAnswerValue> = {};
  for (const item of RECENT_IDEATION) {
    next[item.id] =
      item.id === itemId
        ? value
        : (explicitAnswer(answers[item.id]) ?? "unknown");
  }
  return next;
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
  autosave,
  onSessionExpired,
  stepNumber,
}: CssrsSectionProps) {
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

  // Absent recent answers are stored as "unknown"; retired ids are dropped
  // on that same write. Skip is left untouched. The ref is the latest slice
  // so a queued write cannot replace a newer yes or no.
  useEffect(() => {
    const next = normalizedAnswers(answersRef.current);
    if (next === null) {
      return;
    }
    setDraftValue("cssrs", { answers: next });
  }, [answerKey, setDraftValue]);

  const answersPending =
    preview !== null && !sameAnswers(answers, preview.answers);

  function setAnswer(itemId: string, value: CssrsAnswerValue): void {
    if (!skipped && answers[itemId] === value && normalizedAnswers(answers) === null) {
      return;
    }
    setDraftValue("cssrs", { answers: answeredItems(answers, itemId, value) });
  }

  function handleResume(): void {
    const next: Record<string, CssrsAnswerValue> = {};
    for (const item of RECENT_IDEATION) {
      next[item.id] = explicitAnswer(answers[item.id]) ?? "unknown";
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

  let verdict: string;
  if (preview === null) {
    verdict = "";
  } else if (evaluation?.status === "not_assessed") {
    verdict =
      "C-SSRS skipped — not assessed. No severity is reported for a skipped assessment.";
  } else if (evaluation?.status === "complete" && hasNoPositives) {
    verdict = `Complete — no positive items (recent ideation severity ${formatScore(scores?.ideation_severity_recent)}). A negative screen does not prove absence of risk.`;
  } else if (evaluation?.status === "complete" && scores !== null) {
    verdict = `Complete — recent ideation severity ${formatScore(scores.ideation_severity_recent)}.`;
  } else if (evaluation?.status === "complete") {
    verdict = "Complete. Severity is currently unavailable.";
  } else if (evaluation?.status === "partial") {
    const missing = visibleMissing.length;
    verdict =
      missing === 0
        ? "Recent ideation answers are saved. Severity appears when the server evaluation is complete."
        : `Incomplete — ${missing} required item${missing === 1 ? "" : "s"} still need${missing === 1 ? "s" : ""} an answer. Severity will appear when all required items are answered.`;
  } else {
    verdict = "No answers yet. Select responses to begin the assessment.";
  }

  function renderItem(item: { id: string; prompt: string }): ReactNode {
    const errorId =
      itemErrors[item.id] !== undefined ? `cssrs-${item.id}-error` : undefined;
    const selected = explicitAnswer(answers[item.id]) ?? "unknown";
    return (
      <fieldset key={item.id} className="xi-assessment-item">
        <legend className="xi-assessment-sr-only">{item.prompt}</legend>
        <div className="xi-assessment-question" aria-hidden="true">
          {item.prompt}
        </div>
        <div className="xi-assessment-options" aria-label={item.prompt}>
          {ANSWER_OPTIONS.map((option) => (
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
              <span>{option.label}</span>
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
        C-SSRS (step {stepNumber})
      </h3>
      <p className="xi-hint">
        Assess recent suicidal ideation for the past month. Answers save
        automatically.
      </p>
      {definitionError !== null && (
        <p className="xi-form-error" role="alert">
          {definitionError}{" "}
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => window.location.reload()}
          >
            Retry
          </button>
        </p>
      )}

      <div className="xi-assessment-workspace">
        <div className="xi-assessment-worksheet">
          <div className="xi-assessment-group">
            <div className="xi-assessment-group-heading">
              <h4 id="cssrs-group-recent-ideation" tabIndex={-1}>
                Recent ideation — past month
              </h4>
              <span>
                {skipped
                  ? "Not assessed"
                  : `${items.length} / ${items.length} answered`}
              </span>
            </div>
            <p className="xi-hint">
              Current ideation levels 1–5 for the past month. Each level is
              asked explicitly; a higher-level yes never fills in lower levels.
            </p>
            {items.map(renderItem)}
          </div>
        </div>
        <aside className="xi-assessment-review" aria-labelledby="cssrs-review">
          <div className="xi-assessment-result">
            <h4 id="cssrs-review" tabIndex={-1}>
              Assessment review
            </h4>
            {previewLoading && preview === null && <p>Loading C-SSRS preview…</p>}
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
                  id="cssrs-preview-status"
                  aria-live="polite"
                >
                  {verdict}
                </p>
                {(answersPending || previewError !== null) && (
                  <p className="xi-hint" aria-live="polite">
                    {answersPending &&
                      "Answers have changed. Results reflect the last saved assessment. "}
                    {previewError !== null &&
                      "Results could not refresh. Showing the last available assessment."}
                  </p>
                )}
                <details className="xi-assessment-provenance">
                  <summary>Assessment details</summary>
                  <p className="xi-hint">
                    Definition {preview.definition_version}; saved revision{" "}
                    {preview.revision}.
                  </p>
                </details>

                {visibleErrorIds.length > 0 && !skipped && (
                  <p className="xi-form-error" role="alert">
                    Review these answers:{" "}
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
                          Urgent: server reports high-risk findings needing prompt
                          clinical review
                        </h5>
                        <p style={{ marginTop: 0, marginBottom: 0 }}>
                          The server evaluation reports high_risk_alert. This
                          message persists until the server state changes.
                          Immediate emergency concern (current intent or plan,
                          attempt in progress, inability to stay safe) is
                          clinician-determined and is never a calculated flag.
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
                          Clinical review flagged
                        </h5>
                        <p style={{ marginTop: 0, marginBottom: 0 }}>
                          The server evaluation reports clinical_review. This
                          message persists until the server state changes.
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
                          Clinical review also flagged
                        </h5>
                        <p style={{ marginTop: 0, marginBottom: 0 }}>
                          The server evaluation also reports clinical_review
                          alongside the urgent finding above. Both messages
                          persist until the server state changes.
                        </p>
                      </div>
                    )}
                    {hasNoPositives && (
                      <p className="xi-hint">
                        Server flags: no_positive_items — complete explicit
                        negatives (no-ideation result). A negative screen does not
                        prove absence of risk.
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
                        Still needed: {visibleMissing.length} answers
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
                              {items.find((item) => item.id === id)?.prompt ?? id}
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
                    aria-label="C-SSRS ideation severity scores"
                  >
                    <thead>
                      <tr>
                        <th scope="col">Dimension</th>
                        <th scope="col">Severity</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>Recent ideation severity (past month)</td>
                        <td>{formatScore(scores.ideation_severity_recent)}</td>
                      </tr>
                    </tbody>
                  </table>
                )}
                {evaluation?.status !== "complete" &&
                  evaluation?.status !== "not_assessed" && (
                    <p className="xi-hint">
                      Recent ideation severity appears when the server
                      evaluation is complete. A dash means a result is not
                      available.
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
                  Resume C-SSRS (clear skip)
                </button>
              </div>
              <p className="xi-hint">
                Not assessed. Resume to start answering.
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
