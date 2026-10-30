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
      "PANSS skipped — not assessed. No scores are reported for a skipped assessment.";
  } else if (evaluation?.status === "complete" && scores?.total !== null) {
    verdict = `Complete — Positive ${scores?.positive}, Negative ${scores?.negative}, General ${scores?.general}, Total ${scores?.total}.`;
  } else if (evaluation?.status === "complete") {
    verdict =
      "Complete. The total is currently unavailable.";
  } else if (evaluation?.status === "partial") {
    const missing = evaluation.missing_item_ids.length;
    verdict = `Incomplete — ${missing} required item${missing === 1 ? "" : "s"} still need${missing === 1 ? "s" : ""} an answer. The total will appear when all items are answered.`;
  } else {
    verdict =
      "No answers yet. Answer all 30 items to see the assessment results.";
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
          <h4 id={`panss-group-${items[0].id}`} tabIndex={-1}>{title}</h4>
          <span>{selectedCount(items)} / {items.length} answered</span>
        </div>
        {items.map((item) => (
          <fieldset key={item.id} className="xi-assessment-item">
            <legend className="xi-assessment-sr-only">{item.prompt}</legend>
            <div className="xi-assessment-question" aria-hidden="true">
              <span className="xi-panss-item-code">{item.id}</span>
              <span>{item.prompt.replace(new RegExp(`^${item.id}\\.\\s*`), "")}</span>
            </div>
            <div
              className="xi-assessment-options"
              role="radiogroup"
              aria-label={item.prompt}
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
              <p className="xi-assessment-answer-state">{`Selected: ${answers[item.id]}`}</p>
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
            PANSS (step {stepNumber})
          </h3>
          <p className="xi-panss-subtitle">Positive and Negative Syndrome Scale</p>
          <p className="xi-hint">Rate symptoms over the previous 7 days using the interview and available collateral information.</p>
        </div>
        {definition !== null && (
          <div className="xi-panss-progress">
            <div><span>Items selected</span><strong>{selectedTotal} <span>/ {definition.items.length}</span></strong></div>
            <progress value={selectedTotal} max={definition.items.length} aria-label="PANSS items selected" />
            <span>Assessment window: previous 7 days</span>
          </div>
        )}
      </header>

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
      {definition === null && definitionError === null && (
        <p>Loading PANSS form…</p>
      )}

      <div className="xi-assessment-workspace">
      <aside className="xi-panss-outline">
        <nav aria-label="PANSS subscales">
          <h4>Subscales</h4>
          {subscales.filter(group => group.items.length > 0).map(group => (
            <a key={group.code} href={`#panss-group-${group.items[0].id}`} onClick={event => {
              event.preventDefault();
              document.getElementById(`panss-group-${group.items[0].id}`)?.focus();
            }}>
              <span className="xi-panss-nav-code">{group.code}</span>
              <span className="xi-panss-nav-meta">
                <strong>{group.title}</strong>
                <span>{group.range}</span>
                <span>{selectedCount(group.items)} / {group.items.length} answered</span>
              </span>
              {selectedCount(group.items) === group.items.length && <span className="xi-panss-nav-complete" aria-label="All items selected">✓</span>}
            </a>
          ))}
          <a className="xi-panss-review-link" href="#panss-review" onClick={event => {
            event.preventDefault();
            document.getElementById("panss-review")?.focus();
          }}>Go to assessment review <span aria-hidden="true">↗</span></a>
        </nav>
        <div className="xi-panss-navigation-hint">
          <strong>Keyboard navigation</strong>
          <p>Use Tab to reach a rating, then arrow keys to choose a value.</p>
        </div>
      </aside>
      <div className="xi-assessment-worksheet">
        <div className="xi-panss-rating-guide">
          <span>Choose one rating per item</span>
          <p className="xi-assessment-window">1 = absent <span aria-hidden="true">—</span> <span>7 = extreme</span></p>
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
          <h4 id="panss-review" tabIndex={-1}>Assessment review</h4>
          <span>Saved assessment results</span>
        </div>
        {previewLoading && preview === null && <p>Loading PANSS preview…</p>}
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
              id="panss-preview-status"
              aria-live="polite"
            >
              {verdict}
            </p>
            {(answersPending || previewError !== null) && <p className="xi-hint" aria-live="polite">
              {answersPending && "Answers have changed. Results reflect the last saved assessment. "}
              {previewError !== null && "Results could not refresh. Showing the last available assessment."}
            </p>}
            <details className="xi-assessment-provenance">
              <summary>Assessment details</summary>
              <p className="xi-hint">Definition {preview.definition_version}; saved revision {preview.revision}.</p>
            </details>

            {itemErrorIds.length > 0 && (
              <p className="xi-form-error" role="alert">
                Review these answers:{" "}
                {itemErrorIds
                  .map((id) => `${id}: ${(itemErrors[id] ?? []).join(" ")}`)
                  .join("; ")}
              </p>
            )}

            {evaluation?.status === "partial" && definition !== null && (
              <details id="panss-still-needed" className="xi-assessment-outstanding">
                <summary>Still needed: {evaluation.missing_item_ids.length} answers</summary>
                <ul>
                  {evaluation.missing_item_ids.map((id) => (
                    <li key={id}>
                      <a href={`#panss-heading`} onClick={event => {
                        event.preventDefault();
                        document.querySelector<HTMLInputElement>(`input[name="panss-${id}"]`)?.focus();
                      }}>{definition.items.find(item => item.id === id)?.prompt ?? id}</a>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {scores !== null && (
              <table
                className="xi-table xi-panss-score-table"
                id="panss-scores"
                aria-label="PANSS subscale scores"
              >
                <thead>
                  <tr>
                    <th scope="col">Scale</th>
                    <th scope="col">Score</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Positive (P1–P7)</td>
                    <td>{formatScore(scores.positive)}</td>
                  </tr>
                  <tr>
                    <td>Negative (N1–N7)</td>
                    <td>{formatScore(scores.negative)}</td>
                  </tr>
                  <tr>
                    <td>General (G1–G16)</td>
                    <td>{formatScore(scores.general)}</td>
                  </tr>
                  <tr className="xi-panss-total-row">
                    <td>Total (30 items)</td>
                    <td>{formatScore(scores.total)}</td>
                  </tr>
                </tbody>
              </table>
            )}
            {evaluation?.status !== "complete" &&
              evaluation?.status !== "not_assessed" && (
                <p className="xi-hint">
                  The total appears when all 30 items have valid answers. A dash means a score is not available.
                </p>
              )}

            {totalBand !== undefined && (
              <div
                className="xi-panss-panel"
                aria-label="PANSS total band (informational)"
              >
                <p style={{ margin: 0 }}>
                  Total band {totalBand.range}: {totalBand.label}.
                  Informational only — not a treatment gate.
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
              Resume PANSS (clear skip)
            </button>
          </div>
          <p className="xi-hint">Not assessed. Resume to start answering.</p>
        </div>
      )}
      </aside>
      </div>
    </section>
  );
}
