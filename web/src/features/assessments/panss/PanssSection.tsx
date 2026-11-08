import { clinicalText } from "../../../shared/messages";
import { useLanguage } from "../../../shared/i18n";
/* PANSS 30-item form with server-computed preview (S10,
 * plan.md §§2.2, 5; FR-12, FR-20).
 *
 * Wizard integration (not separate persistence): answers live in the shared
 * S07 autosave body as `draft_data["panss"] = {answers}` and save through
 * the same debounced PATCH + revision fence. This component only edits that
 * slice of `localData`; every score comes from GET /encounters/{id}/panss,
 * which runs the same T2 `evaluate()` as the backend — the browser never
 * sums subscales or totals, so the display cannot drift from the S08
 * released definition (panss.v1, no owner review).
 *
 * Explicit selection only: nothing is pre-filled, no default 1s, no hidden
 * zeros. A fresh form is 30 unanswered items with null scores. A legacy
 * skip record is `{"__skipped": true}` (evaluates to not_assessed); it stays
 * readable with a Resume recovery action and no new Skip control. The total appears only
 * when all 30 items are valid; one missing item suppresses the total to
 * null. Total bands are informational only — never a treatment gate.
 *
 * No ack/bypass here (unlike diagnosis): there are no command POSTs, so no
 * `applyServerSnapshot` resync is needed — the preview simply refreshes
 * after each acknowledged autosave. The 412 path stays inside the shared
 * useAutosave hook (keeps edits + reload/reconcile UI); a failed save never
 * shows Saved (the wizard save status owns that).
 *
 * Prior encounter score is historical text only: this component never fetches
 * other encounters and never invents numbers.
 *
 * Accessibility: native fieldset/legend + radios (keyboard free), preview
 * verdict is an aria-live region, loading/empty/error states come from the
 * real endpoints, styling uses semantic tokens only (both themes).
 *
 * E2E selector contract (for e2e/panss.spec.ts):
 * - section heading `#panss-heading`: "PANSS (step 3)" on registration,
 *   "PANSS (step 2)" on follow-up (wizard index)
 * - radios `#panss-{P1..G16}-{1..7}` (name `panss-{itemId}`)
 * - verdict `#panss-preview-status` (aria-live)
 * - scores `#panss-scores`
 * - legacy skip shows "not assessed" with a "Resume PANSS (clear skip)"
 *   recovery action (no Skip button)
 * - still-needed list `#panss-still-needed`
 */

import { useCallback, useEffect, useState } from "react";
import "../assessment-worksheet.css";
import "./panss.css";
import { ApiError } from "../../identity/api";
import type { DraftData } from "../../encounters/api";
import type { useAutosave } from "../../encounters/useAutosave";
import {
  getPanss,
  getPanssDefinition,
  type PanssAnswer,
  type PanssDefinition,
  type PanssPreview,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface PanssSectionProps {
  encounterId: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
  /** 1-based wizard index (registration 3, follow-up 2). */
  stepNumber: number;
}

function answersOf(data: DraftData): Record<string, unknown> {
  const raw = data["panss"];
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
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  return (
    keysA.length === keysB.length && keysA.every((key) => b[key] === a[key])
  );
}

const SCALE_VALUES: PanssAnswer[] = [1, 2, 3, 4, 5, 6, 7];

function subscaleOf(itemId: string): "Positive" | "Negative" | "General" {
  if (itemId.startsWith("P")) {
    return "Positive";
  }
  if (itemId.startsWith("N")) {
    return "Negative";
  }
  return "General";
}

export function PanssSection({
  encounterId,
  autosave,
  onSessionExpired,
  stepNumber,
}: PanssSectionProps) {
  const { text: uiText, t, errorText, language, text } = useLanguage();
  function translatedPrompt(item: { id: string; prompt: string }): string {
    return clinicalText(language, definition?.id ?? "panss", definition?.version ?? "unavailable", item.id, item.prompt);
  }

  const [definition, setDefinition] = useState<PanssDefinition | null>(null);
  const [definitionError, setDefinitionError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PanssPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Released definition: prompts + required flags for rendering the form.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await getPanssDefinition();
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
            : "PANSS form failed to load. Check the connection and retry.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [onSessionExpired]);

  const refreshPreview = useCallback(async (): Promise<void> => {
    try {
      const fresh = await getPanss(encounterId);
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
          : "PANSS preview failed to load. Check the connection and retry.",
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

  const answers = answersOf(autosave.localData);
  const skipped = answers["__skipped"] === true;
  const answersPending =
    preview !== null && !sameAnswers(answers, preview.answers);

  function setAnswer(itemId: string, value: PanssAnswer): void {
    if (answers[itemId] === value) {
      return;
    }
    // Any explicit answer replaces a prior Skip: Skip must not accompany
    // other answers (server rejects the combination).
    const { __skipped: _removed, ...rest } = answers;
    void _removed;
    autosave.setDraftValue("panss", {
      answers: { ...rest, [itemId]: value },
    });
  }

  function handleResume(): void {
    autosave.setDraftValue("panss", { answers: {} });
  }

  const evaluation = preview?.evaluation ?? null;
  const scores = evaluation?.scores ?? null;
  const findings = evaluation?.findings ?? null;
  const totalBand = findings?.total_band;
  const itemErrors = evaluation?.item_errors ?? {};
  const itemErrorIds = Object.keys(itemErrors);

  let verdict: string;
  if (preview === null) {
    verdict = "";
  } else if (evaluation?.status === "not_assessed") {
    verdict =
      t("panssSkipped");
  } else if (evaluation?.status === "complete" && scores?.total !== null) {
    verdict = t("panssComplete", { positive: String(scores?.positive), negative: String(scores?.negative), general: String(scores?.general), total: String(scores?.total) });
  } else if (evaluation?.status === "complete") {
    verdict =
      t("panssTotalUnavailable");
  } else if (evaluation?.status === "partial") {
    const missing = evaluation.missing_item_ids.length;
    verdict = t(missing === 1 ? "panssMissingOne" : "panssMissingMany", { missing });
  } else {
    verdict =
      t("panssUnanswered");
  }

  function formatScore(value: number | null | undefined): string {
    return typeof value === "number" ? String(value) : "—";
  }

  const positiveItems =
    definition?.items.filter((item) => subscaleOf(item.id) === "Positive") ??
    [];
  const negativeItems =
    definition?.items.filter((item) => subscaleOf(item.id) === "Negative") ??
    [];
  const generalItems =
    definition?.items.filter((item) => subscaleOf(item.id) === "General") ??
    [];

  // Presentation counts describe local selections, never clinical scores or saved state.
  const subscales = [
    { code: "P", title: "Positive symptoms", range: "P1–P7", items: positiveItems },
    { code: "N", title: "Negative symptoms", range: "N1–N7", items: negativeItems },
    { code: "G", title: "General psychopathology", range: "G1–G16", items: generalItems },
  ];
  const selectedCount = (items: PanssDefinition["items"]) =>
    items.filter(item => SCALE_VALUES.includes(answers[item.id] as PanssAnswer)).length;
  const selectedTotal = selectedCount(definition?.items ?? []);

  function renderSubscale(
    title: string,
    items: PanssDefinition["items"],
  ): React.ReactNode {
    if (items.length === 0) {
      return null;
    }
    return (
      <section className="xi-assessment-group" aria-labelledby={`panss-group-${items[0].id}`}>
        <div className="xi-assessment-group-heading">
          <h4 id={`panss-group-${items[0].id}`} tabIndex={-1}>{text(title)}</h4>
          <span>{t("answeredCount", { answered: selectedCount(items), total: items.length })}</span>
        </div>
        {items.map((item) => (
          <fieldset key={item.id} className="xi-assessment-item">
            <legend className="xi-assessment-sr-only">{translatedPrompt(item)}</legend>
            <div className="xi-assessment-question" aria-hidden="true">
              <span className="xi-panss-item-code">{item.id}</span>
              <span>{translatedPrompt(item).replace(new RegExp(`^${item.id}\\.\\s*`), "")}</span>
            </div>
            <div
              className="xi-assessment-options"
              role="radiogroup"
              aria-label={translatedPrompt(item)}
            >
              {SCALE_VALUES.map((value) => (
                <label key={value} htmlFor={`panss-${item.id}-${value}`}>
                  <input
                    id={`panss-${item.id}-${value}`}
                    type="radio"
                    name={`panss-${item.id}`}
                    value={value}
                    checked={answers[item.id] === value}
                    onChange={() => setAnswer(item.id, value)}
                    disabled={skipped}
                    aria-describedby={
                      itemErrors[item.id] !== undefined
                        ? `panss-${item.id}-error`
                        : undefined
                    }
                  />
                  <span>{value}</span>
                </label>
              ))}
            </div>
            {answers[item.id] !== undefined && (
              <p className="xi-assessment-answer-state">{t("selectedRating", { value: String(answers[item.id]) })}</p>
            )}
            {itemErrors[item.id] !== undefined && (
              <p className="xi-field-error" id={`panss-${item.id}-error`}>
                {itemErrors[item.id].join(" ")}
              </p>
            )}
          </fieldset>
        ))}
      </section>
    );
  }

  return (
    <section className="xi-card xi-assessment xi-panss" aria-labelledby="panss-heading">
      <header className="xi-panss-header">
        <div>
          <h3 className="xi-section-title" id="panss-heading">
            
            {t("panssStep")} {stepNumber})
          </h3>
          <p className="xi-panss-subtitle">{t("panssFullName")}</p>
          <p className="xi-hint">{t("panssWindowGuide")}</p>
        </div>
        {definition !== null && (
          <div className="xi-panss-progress">
            <div><span>{t("itemsSelected")}</span><strong>{selectedTotal} <span>/ {definition.items.length}</span></strong></div>
            <progress value={selectedTotal} max={definition.items.length} aria-label={t("panssItemsSelected")} />
            <span>{t("panssWindow")}</span>
          </div>
        )}
      </header>

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
        <p>{t("loadingPanssForm")}</p>
      )}

      <div className="xi-assessment-workspace">
      <aside className="xi-panss-outline">
        <nav aria-label={t("panssSubscales")}>
          <h4>{t("subscales")}</h4>
          {subscales.filter(group => group.items.length > 0).map(group => (
            <a key={group.code} href={`#panss-group-${group.items[0].id}`} onClick={event => {
              event.preventDefault();
              document.getElementById(`panss-group-${group.items[0].id}`)?.focus();
            }}>
              <span className="xi-panss-nav-code">{group.code}</span>
              <span className="xi-panss-nav-meta">
                <strong>{text(group.title)}</strong>
                <span>{group.range}</span>
                <span>{selectedCount(group.items)} / {group.items.length}  {t("answered")}</span>
              </span>
              {selectedCount(group.items) === group.items.length && <span className="xi-panss-nav-complete" aria-label={t("allItemsSelected")}>✓</span>}
            </a>
          ))}
          <a className="xi-panss-review-link" href="#panss-review" onClick={event => {
            event.preventDefault();
            document.getElementById("panss-review")?.focus();
          }}>{t("goAssessmentReview")} <span aria-hidden="true">↗</span></a>
        </nav>
        <div className="xi-panss-navigation-hint">
          <strong>{t("keyboardNavigation")}</strong>
          <p>{t("ratingKeyboardGuide")}</p>
        </div>
      </aside>
      <div className="xi-assessment-worksheet">
        <div className="xi-panss-rating-guide">
          <span>{t("oneRatingPerItem")}</span>
          <p className="xi-assessment-window">{t("ratingAbsent")} <span aria-hidden="true">—</span> <span>{t("ratingExtreme")}</span></p>
        </div>
      {definition !== null && (
        <div>
          {renderSubscale("Positive symptoms (P1–P7)", positiveItems)}
          {renderSubscale("Negative symptoms (N1–N7)", negativeItems)}
          {renderSubscale(
            "General psychopathology (G1–G16)",
            generalItems,
          )}
        </div>
      )}

      </div>
      <aside className="xi-assessment-review" aria-labelledby="panss-review">
      <div className="xi-assessment-result">
        <div className="xi-panss-review-heading">
          <h4 id="panss-review" tabIndex={-1}>{t("assessmentReviewTitle")}</h4>
          <span>{t("savedAssessmentResults")}</span>
        </div>
        {previewLoading && preview === null && <p>{t("loadingPanssPreview")}</p>}
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
              id="panss-preview-status"
              aria-live="polite"
            >
              {uiText(verdict)}
            </p>
            {(answersPending || previewError !== null) && <p className="xi-hint" aria-live="polite">
              {answersPending && t("answersHaveChangedResultsReflectTheLast")}
              {previewError !== null && t("resultsCouldNotRefreshShowingTheLast")}
            </p>}
            <details className="xi-assessment-provenance">
              <summary>{t("assessmentDetails")}</summary>
              <p className="xi-hint">{t("definition")} {preview.definition_version}{t("savedRevision269")} {preview.revision}.</p>
            </details>

            {itemErrorIds.length > 0 && (
              <p className="xi-form-error" role="alert">
                {t("reviewAnswers")}{" "}
                {itemErrorIds
                  .map((id) => `${id}: ${(itemErrors[id] ?? []).join(" ")}`)
                  .join("; ")}
              </p>
            )}

            {evaluation?.status === "partial" && definition !== null && (
              <details id="panss-still-needed" className="xi-assessment-outstanding">
                <summary>{t("stillNeeded")} {evaluation.missing_item_ids.length}  {t("answers")}</summary>
                <ul>
                  {evaluation.missing_item_ids.map((id) => (
                    <li key={id}>
                      <a href={`#panss-heading`} onClick={event => {
                        event.preventDefault();
                        document.querySelector<HTMLInputElement>(`input[name="panss-${id}"]`)?.focus();
                      }}>{translatedPrompt(definition.items.find(item => item.id === id) ?? { id, prompt: id })}</a>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {scores !== null && (
              <table
                className="xi-table xi-panss-score-table"
                id="panss-scores"
                aria-label={t("panssSubscaleScores")}
              >
                <thead>
                  <tr>
                    <th scope="col">{t("scale")}</th>
                    <th scope="col">{t("score")}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>{t("positivePanss")}</td>
                    <td>{formatScore(scores.positive)}</td>
                  </tr>
                  <tr>
                    <td>{t("negativePanss")}</td>
                    <td>{formatScore(scores.negative)}</td>
                  </tr>
                  <tr>
                    <td>{t("generalPanss")}</td>
                    <td>{formatScore(scores.general)}</td>
                  </tr>
                  <tr className="xi-panss-total-row">
                    <td>{t("totalPanss")}</td>
                    <td>{formatScore(scores.total)}</td>
                  </tr>
                </tbody>
              </table>
            )}
            {evaluation?.status !== "complete" &&
              evaluation?.status !== "not_assessed" && (
                <p className="xi-hint">
                  {t("panssTotalGuide")}
                </p>
              )}

            {totalBand !== undefined && (
              <div
                className="xi-panss-panel"
                aria-label={t("panssTotalBand")}
              >
                <p style={{ margin: 0 }}>
                  
                  {t("totalBand")} {totalBand.range}: {text(totalBand.label)}{t("informationalOnlyNotATreatmentGate")}
                </p>
              </div>
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
              {t("resumePanss")}
            </button>
          </div>
          <p className="xi-hint">{t("notAssessedResume")}</p>
        </div>
      )}
      </aside>
      </div>
    </section>
  );
}
