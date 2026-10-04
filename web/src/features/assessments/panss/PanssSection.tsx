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
 * zeros. A fresh form is 30 unanswered items with null scores. Skip writes
 * `{"__skipped": true}` (evaluates to not_assessed). The total appears only
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
 * - section heading "PANSS (step 3)"
 * - radios `#panss-{P1..G16}-{1..7}` (name `panss-{itemId}`)
 * - verdict `#panss-preview-status` (aria-live)
 * - scores `#panss-scores`
 * - skip button "Skip PANSS (not assessed)"
 * - still-needed list `#panss-still-needed`
 */

import { useCallback, useEffect, useState } from "react";
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

  function handleSkip(): void {
    autosave.setDraftValue("panss", { answers: { __skipped: true } });
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
    verdict = `Complete — Positive ${scores?.positive}, Negative ${scores?.negative}, General ${scores?.general}, Total ${scores?.total} (server evaluation ${preview.definition_version}).`;
  } else if (evaluation?.status === "complete") {
    verdict =
      "Complete but the total is unavailable — the server suppressed it. This needs no treatment decision.";
  } else if (evaluation?.status === "partial") {
    const missing = evaluation.missing_item_ids.length;
    verdict = `Incomplete — ${missing} required item${missing === 1 ? "" : "s"} still need${missing === 1 ? "s" : ""} an answer. Saved as incomplete; the total is suppressed to null (never zero).`;
  } else {
    verdict =
      "No answers yet. PANSS is unanswered — 30 items with nothing selected and no scores.";
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

  function renderSubscale(
    title: string,
    items: PanssDefinition["items"],
  ): React.ReactNode {
    if (items.length === 0) {
      return null;
    }
    return (
      <div>
        <h4 className="xi-section-title" style={{ fontSize: 16 }}>
          {title}
        </h4>
        {items.map((item) => (
          <fieldset key={item.id} className="xi-field">
            <legend className="xi-label">{item.prompt}</legend>
            <div
              className="xi-radio-group"
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
                  {value}
                </label>
              ))}
            </div>
            {itemErrors[item.id] !== undefined && (
              <p className="xi-field-error" id={`panss-${item.id}-error`}>
                {itemErrors[item.id].join(" ")}
              </p>
            )}
          </fieldset>
        ))}
      </div>
    );
  }

  return (
    <section className="xi-card" aria-labelledby="panss-heading">
      <h3 className="xi-section-title" id="panss-heading">
        PANSS (step 3)
      </h3>
      <p className="xi-hint">
        Rate each of the 30 items from 1 (absent) to 7 (extreme) for the
        previous 7 days. Nothing is pre-filled — every answer is an explicit
        selection. Answers autosave through the same draft as the rest of
        this encounter (about one second after changing an answer). The
        scores below are computed by the server from the released definition
        — the browser never adds them up itself.
      </p>
      <p className="xi-hint">
        Assessment window: previous 7 days — rate symptoms present during the
        previous 7 days, using the interview and available collateral
        information.
      </p>
      <p className="xi-hint">
        Prior encounter score: shown as historical when available. This form
        never fetches other encounters and never invents prior numbers.
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
      {definition === null && definitionError === null && (
        <p>Loading PANSS form…</p>
      )}

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

      <div className="xi-row-actions" style={{ marginTop: 8 }}>
        {skipped ? (
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={handleResume}
          >
            Resume PANSS (clear skip)
          </button>
        ) : (
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            disabled={definition === null}
            onClick={handleSkip}
          >
            Skip PANSS (not assessed)
          </button>
        )}
      </div>
      {skipped && (
        <p className="xi-hint" style={{ marginTop: 8 }}>
          Skipped — the saved answers are {"{__skipped: true}"} and the server
          reports not_assessed. Choose an answer above to replace the skip, or
          resume to clear it.
        </p>
      )}

      <div style={{ marginTop: 16 }}>
        <h4 className="xi-section-title" style={{ fontSize: 16 }}>
          Server score preview
        </h4>
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
            <p className="xi-hint">
              Server evaluation · definition {preview.definition_version} ·
              saved revision {preview.revision}
              {answersPending &&
                " · unsaved answer edits pending — preview reflects the last saved revision"}
              {previewError !== null &&
                " · preview refresh failed; showing last loaded state"}
            </p>

            {itemErrorIds.length > 0 && (
              <p className="xi-form-error" role="alert">
                The saved answers contain server-reported problems and were
                rejected (never zero-filled):{" "}
                {itemErrorIds
                  .map((id) => `${id}: ${(itemErrors[id] ?? []).join(" ")}`)
                  .join("; ")}
              </p>
            )}

            {evaluation?.status === "partial" && definition !== null && (
              <div id="panss-still-needed">
                <p className="xi-hint">Still needed:</p>
                <ul>
                  {evaluation.missing_item_ids.map((id) => (
                    <li key={id}>
                      {definition.items.find((item) => item.id === id)?.prompt ??
                        id}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {scores !== null && (
              <table
                className="xi-table"
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
                  <tr>
                    <td>Total (30 items)</td>
                    <td>{formatScore(scores.total)}</td>
                  </tr>
                </tbody>
              </table>
            )}
            {evaluation?.status !== "complete" &&
              evaluation?.status !== "not_assessed" && (
                <p className="xi-hint">
                  The total appears only when all 30 items are validly
                  answered — one missing item suppresses it to null.
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
    </section>
  );
}
