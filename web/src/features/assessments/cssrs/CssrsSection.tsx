/* C-SSRS 29-item form with server-computed preview (S11,
 * plan.md §§2.2, 5; FR-13, FR-20).
 *
 * Wizard integration (not separate persistence): answers live in the shared
 * S07 autosave body as `draft_data["cssrs"] = {answers}` and save through
 * the same debounced PATCH + revision fence. This component only edits that
 * slice of `localData`; every severity/flag comes from
 * GET /encounters/{id}/cssrs, which runs the same T2 `evaluate()` as the
 * backend — the browser never computes severity or flags, so the display
 * cannot drift from the S08 released definition (cssrs.v1, no owner
 * review, no_composite_score).
 *
 * Explicit selection only: nothing is pre-filled, no defaults, no hidden
 * zeros. A fresh form is 29 unanswered items with null severities. Skip
 * writes `{"__skipped": true}` (evaluates to not_assessed). Complete
 * explicit negatives give the S08-defined no-ideation result (severity 0 +
 * no_positive_items flag). A yes to a higher ideation level never fills in
 * lower levels — each level stays explicitly answered, and partial work
 * reports missing_item_ids (never a guessed negative).
 *
 * Periods stay distinct: recent ideation = past_month, recent behavior =
 * past_3_months, lifetime = lifetime. Intensity, behavior, and lethality
 * render as separate panels from the server findings — no composite risk
 * score anywhere. Alert logic (high_risk_alert vs clinical_review) is read
 * from the evaluation flags, never derived here.
 *
 * No ack/bypass here (unlike diagnosis): there are no command POSTs, so no
 * `applyServerSnapshot` resync is needed — the preview simply refreshes
 * after each acknowledged autosave (same pattern as PANSS). The 412 path
 * stays inside the shared useAutosave hook (keeps edits + reload/reconcile
 * UI); a failed save never shows Saved (the wizard save status owns that).
 * Switching pages or autosaving never erases responses and never turns a
 * skip into zero; reload/resume preserves state via the saved draft_data.
 *
 * Accessibility: native fieldset/legend + radios (keyboard free), preview
 * verdict is an aria-live region, urgent/review panels are persistent text
 * (never auto-dismissed, never color-alone) with headings, item errors use
 * role=alert + aria-describedby, styling uses semantic tokens only (both
 * themes), reduced motion is handled by the global theme media query.
 *
 * E2E selector contract (for the test agent's e2e/cssrs.spec.ts):
 * - section heading "C-SSRS (step 4)" (#cssrs-heading)
 * - yes/no radios `#cssrs-{itemId}-yes|no` (name `cssrs-{itemId}`)
 * - numeric radios `#cssrs-{itemId}-{n}` (name `cssrs-{itemId}`), e.g.
 *   `#cssrs-css_frequency-2`, `#cssrs-css_leth_actual_recent-0`
 * - verdict `#cssrs-preview-status` (aria-live polite)
 * - scores `#cssrs-scores`
 * - flags container `#cssrs-flags`; urgent panel `#cssrs-high-risk-alert`
 *   (role=alert); review panel `#cssrs-clinical-review`
 * - dimension panels `#cssrs-intensity`, `#cssrs-behavior`,
 *   `#cssrs-lethality` (separate, never combined)
 * - skip button "Skip C-SSRS (not assessed)"
 * - resume button "Resume C-SSRS (clear skip)"
 * - still-needed list `#cssrs-still-needed`
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ApiError } from "../../identity/api";
import type { DraftData } from "../../encounters/api";
import type { useAutosave } from "../../encounters/useAutosave";
import {
  getCssrs,
  getCssrsDefinition,
  type CssrsDefinition,
  type CssrsItem,
  type CssrsPreview,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface CssrsSectionProps {
  encounterId: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
}

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
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  return (
    keysA.length === keysB.length && keysA.every((key) => b[key] === a[key])
  );
}

const YES_NO_OPTIONS: { value: "yes" | "no"; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
];

function numericOptions(valueType: string): number[] {
  switch (valueType) {
    case "frequency_1_5":
    case "duration_1_5":
      return [1, 2, 3, 4, 5];
    case "control_0_5":
    case "deterrent_0_5":
    case "reason_0_5":
    case "lethality_0_5":
      return [0, 1, 2, 3, 4, 5];
    case "potential_0_2":
      return [0, 1, 2];
    default:
      return [];
  }
}

function isYesNo(item: CssrsItem): boolean {
  return item.value_type === "yes_no";
}

const INTENSITY_IDS = [
  "css_frequency",
  "css_duration",
  "css_controllability",
  "css_deterrents",
  "css_reasons",
];

const LETHALITY_RECENT_IDS = [
  "css_leth_actual_recent",
  "css_leth_potential_recent",
];

const LETHALITY_LIFETIME_IDS = [
  "css_leth_actual_lifetime",
  "css_leth_potential_lifetime",
];

interface ItemGroup {
  key: string;
  title: string;
  hint: string;
  items: CssrsItem[];
}

function groupItems(definition: CssrsDefinition): ItemGroup[] {
  const byId = new Map(definition.items.map((item) => [item.id, item]));
  const pick = (ids: string[]): CssrsItem[] =>
    ids.flatMap((id) => {
      const item = byId.get(id);
      return item !== undefined ? [item] : [];
    });
  const recentIdeation = definition.items.filter((item) =>
    /^css_i[1-5]_recent$/.test(item.id),
  );
  const lifetimeIdeation = definition.items.filter((item) =>
    /^css_i[1-5]_lifetime$/.test(item.id),
  );
  const recentBehavior = definition.items.filter((item) =>
    /^css_beh_.+_recent$/.test(item.id),
  );
  const lifetimeBehavior = definition.items.filter((item) =>
    /^css_beh_.+_lifetime$/.test(item.id),
  );
  return [
    {
      key: "recent-ideation",
      title: "Recent ideation — past month",
      hint: "Current ideation levels 1–5 for the past month. Each level is asked explicitly; a higher-level yes never fills in lower levels.",
      items: recentIdeation,
    },
    {
      key: "lifetime-ideation",
      title: "Lifetime ideation — lifetime history",
      hint: "Historical ideation levels 1–5 over the lifetime. Kept separate from recent answers; history never overwrites the current window.",
      items: lifetimeIdeation,
    },
    {
      key: "intensity",
      title: "Intensity of the most severe recent ideation",
      hint: "Five intensity dimensions for the most severe recent ideation. Required if and only if any recent ideation (levels 1–5) is endorsed. Recorded separately — never combined into a score.",
      items: pick(INTENSITY_IDS),
    },
    {
      key: "recent-behavior",
      title: "Recent behavior — past 3 months",
      hint: "Suicidal and self-injury behavior in the past 3 months, including nonsuicidal self-injury documented separately.",
      items: recentBehavior,
    },
    {
      key: "lifetime-behavior",
      title: "Lifetime behavior — lifetime history",
      hint: "Historical behavior over the lifetime. Kept separate from recent behavior; history never overwrites the current window.",
      items: lifetimeBehavior,
    },
    {
      key: "lethality",
      title: "Lethality codings",
      hint: "Actual and potential lethality per window, using the authorized form codings. Required if and only if an actual attempt occurred in that window (potential additionally requires actual damage 0).",
      items: [...pick(LETHALITY_RECENT_IDS), ...pick(LETHALITY_LIFETIME_IDS)],
    },
  ];
}

export function CssrsSection({
  encounterId,
  autosave,
  onSessionExpired,
}: CssrsSectionProps) {
  const [definition, setDefinition] = useState<CssrsDefinition | null>(null);
  const [definitionError, setDefinitionError] = useState<string | null>(null);
  const [preview, setPreview] = useState<CssrsPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Released definition: prompts + value types for rendering the form.
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

  function setAnswer(itemId: string, value: string | number): void {
    if (answers[itemId] === value) {
      return;
    }
    // Any explicit answer replaces a prior Skip: Skip must not accompany
    // other answers (server rejects the combination).
    const { __skipped: _removed, ...rest } = answers;
    void _removed;
    autosave.setDraftValue("cssrs", {
      answers: { ...rest, [itemId]: value },
    });
  }

  function handleSkip(): void {
    autosave.setDraftValue("cssrs", { answers: { __skipped: true } });
  }

  function handleResume(): void {
    autosave.setDraftValue("cssrs", { answers: {} });
  }

  const evaluation = preview?.evaluation ?? null;
  const scores = evaluation?.scores ?? null;
  const findings = evaluation?.findings ?? null;
  const flags = findings?.flags ?? [];
  const itemErrors = evaluation?.item_errors ?? {};
  const itemErrorIds = Object.keys(itemErrors);
  const hasHighRisk = flags.includes("high_risk_alert");
  const hasReview = flags.includes("clinical_review");
  const hasNoPositives = flags.includes("no_positive_items");

  let verdict: string;
  if (preview === null) {
    verdict = "";
  } else if (evaluation?.status === "not_assessed") {
    verdict =
      "C-SSRS skipped — not assessed. No severity is reported for a skipped assessment.";
  } else if (evaluation?.status === "complete" && hasNoPositives) {
    verdict = `Complete — no ideation or behavior endorsed (no-ideation result, severity 0 recent / 0 lifetime / 0 max, server evaluation ${preview.definition_version}). A negative screen does not prove absence of risk.`;
  } else if (evaluation?.status === "complete" && scores !== null) {
    verdict = `Complete — Recent severity ${scores.ideation_severity_recent}, Lifetime severity ${scores.ideation_severity_lifetime}, Max ${scores.ideation_severity_max} (server evaluation ${preview.definition_version}). Intensity, behavior, and lethality below stay separate — no composite score.`;
  } else if (evaluation?.status === "complete") {
    verdict =
      "Complete but the severity is unavailable — the server suppressed it. This needs no treatment decision.";
  } else if (evaluation?.status === "partial") {
    const missing = evaluation.missing_item_ids.length;
    verdict = `Incomplete — ${missing} required item${missing === 1 ? "" : "s"} still need${missing === 1 ? "s" : ""} an answer. Saved as incomplete; severity is suppressed to null (never zero, never a guessed negative).`;
  } else {
    verdict =
      "No answers yet. C-SSRS is unanswered — 29 items with nothing selected and no assessed result (null, never zero).";
  }

  function formatScore(value: number | null | undefined): string {
    return typeof value === "number" ? String(value) : "—";
  }

  function formatFinding(value: unknown): string {
    if (value === null || value === undefined) {
      return "—";
    }
    return String(value);
  }

  function renderItem(item: CssrsItem): ReactNode {
    const errorId =
      itemErrors[item.id] !== undefined
        ? `cssrs-${item.id}-error`
        : undefined;
    if (isYesNo(item)) {
      return (
        <fieldset key={item.id} className="xi-field">
          <legend className="xi-label">{item.prompt}</legend>
          <div className="xi-radio-group" aria-label={item.prompt}>
            {YES_NO_OPTIONS.map((option) => (
              <label
                key={option.value}
                htmlFor={`cssrs-${item.id}-${option.value}`}
              >
                <input
                  id={`cssrs-${item.id}-${option.value}`}
                  type="radio"
                  name={`cssrs-${item.id}`}
                  value={option.value}
                  checked={answers[item.id] === option.value}
                  onChange={() => setAnswer(item.id, option.value)}
                  disabled={skipped}
                  aria-describedby={errorId}
                />
                {option.label}
              </label>
            ))}
          </div>
          {errorId !== undefined && (
            <p className="xi-field-error" id={errorId} role="alert">
              {(itemErrors[item.id] ?? []).join(" ")}
            </p>
          )}
        </fieldset>
      );
    }
    const options = numericOptions(item.value_type);
    return (
      <fieldset key={item.id} className="xi-field">
        <legend className="xi-label">{item.prompt}</legend>
        <div className="xi-radio-group" aria-label={item.prompt}>
          {options.map((value) => (
            <label key={value} htmlFor={`cssrs-${item.id}-${value}`}>
              <input
                id={`cssrs-${item.id}-${value}`}
                type="radio"
                name={`cssrs-${item.id}`}
                value={value}
                checked={answers[item.id] === value}
                onChange={() => setAnswer(item.id, value)}
                disabled={skipped}
                aria-describedby={errorId}
              />
              {value}
            </label>
          ))}
        </div>
        {errorId !== undefined && (
          <p className="xi-field-error" id={errorId} role="alert">
            {(itemErrors[item.id] ?? []).join(" ")}
          </p>
        )}
      </fieldset>
    );
  }

  function renderGroup(group: ItemGroup): ReactNode {
    if (group.items.length === 0) {
      return null;
    }
    return (
      <div key={group.key}>
        <h4 className="xi-section-title" style={{ fontSize: 16 }}>
          {group.title}
        </h4>
        <p className="xi-hint">{group.hint}</p>
        {group.items.map(renderItem)}
      </div>
    );
  }

  const groups = definition !== null ? groupItems(definition) : [];
  const intensity = findings?.intensity ?? null;
  const behavior = findings?.behavior ?? null;
  const lethality = findings?.lethality ?? null;

  return (
    <section className="xi-card" aria-labelledby="cssrs-heading">
      <h3 className="xi-section-title" id="cssrs-heading">
        C-SSRS (step 4)
      </h3>
      <p className="xi-hint">
        Clinician-administered assessment of suicidal ideation and behavior.
        Nothing is pre-filled — every answer is an explicit selection. Answers
        autosave through the same draft as the rest of this encounter (about
        one second after changing an answer). Ideation severity, intensity,
        behavior, and lethality below are computed by the server from the
        released definition — the browser never combines them into a
        composite score.
      </p>
      <p className="xi-hint">
        Periods: recent ideation covers the past month, recent behavior covers
        the past 3 months, and lifetime sections cover lifetime history.
        Recent and lifetime answers are kept separate.
      </p>
      <p className="xi-hint">
        Item wording below is an experimental paraphrase of the source table;
        the authorized form governs exact administration wording and probes.
        For each positive response the clinician documents timing,
        current-versus-historical status, the own description, and the
        determination, outside this structured payload.
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
        <p>Loading C-SSRS form…</p>
      )}

      {definition !== null && <div>{groups.map(renderGroup)}</div>}

      <div className="xi-row-actions" style={{ marginTop: 8 }}>
        {skipped ? (
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={handleResume}
          >
            Resume C-SSRS (clear skip)
          </button>
        ) : (
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            disabled={definition === null}
            onClick={handleSkip}
          >
            Skip C-SSRS (not assessed)
          </button>
        )}
      </div>
      {skipped && (
        <p className="xi-hint" style={{ marginTop: 8 }}>
          Skipped — the saved answers are {"{__skipped: true}"} and the server
          reports not_assessed. Choose an answer above to replace the skip, or
          resume to clear it. A skip never becomes zero.
        </p>
      )}

      <div style={{ marginTop: 16 }}>
        <h4 className="xi-section-title" style={{ fontSize: 16 }}>
          Server severity preview
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
              <div id="cssrs-still-needed">
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
                  <tr>
                    <td>Lifetime ideation severity (lifetime)</td>
                    <td>{formatScore(scores.ideation_severity_lifetime)}</td>
                  </tr>
                  <tr>
                    <td>Maximum endorsed severity</td>
                    <td>{formatScore(scores.ideation_severity_max)}</td>
                  </tr>
                </tbody>
              </table>
            )}
            {evaluation?.status !== "complete" &&
              evaluation?.status !== "not_assessed" && (
                <p className="xi-hint">
                  Severity appears only when every required item in the active
                  branches is validly answered — one missing item suppresses
                  it to null (never zero, never a guessed negative).
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
                      The server evaluation reports high_risk_alert (recent
                      ideation level 4–5 or recent suicidal behavior in the
                      past-3-months window). This message persists until the
                      server state changes. Immediate emergency concern
                      (current intent or plan, attempt in progress, inability
                      to stay safe) is clinician-determined and is never a
                      calculated flag.
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
                      The server evaluation reports clinical_review (ideation
                      or historical behavior endorsed without recent high-risk
                      findings). This message persists until the server state
                      changes. Nonsuicidal self-injury contributes to review
                      but never triggers a high-risk alert by itself.
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
                      alongside the urgent finding above. Both messages persist
                      until the server state changes.
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

            {evaluation?.status === "complete" && (
              <div>
                <div
                  className="xi-cssrs-panel"
                  id="cssrs-intensity"
                  aria-label="C-SSRS intensity (separate dimension)"
                >
                  <p style={{ margin: 0 }}>
                    Intensity (most severe recent ideation, separate — no
                    composite): frequency{" "}
                    {formatFinding(intensity?.frequency)}, duration{" "}
                    {formatFinding(intensity?.duration)}, controllability{" "}
                    {formatFinding(intensity?.controllability)}, deterrents{" "}
                    {formatFinding(intensity?.deterrents)}, reasons{" "}
                    {formatFinding(intensity?.reasons)}
                    {intensity === null &&
                      " — none (no recent ideation endorsed)."}
                  </p>
                </div>
                <div
                  className="xi-cssrs-panel"
                  id="cssrs-behavior"
                  aria-label="C-SSRS behavior (separate dimension)"
                >
                  <p style={{ margin: 0 }}>
                    Behavior by window (separate — no composite): recent{" "}
                    {behavior !== null
                      ? Object.entries(behavior.recent ?? {})
                          .map(([category, value]) => `${category}: ${formatFinding(value)}`)
                          .join(", ")
                      : "—"}
                    {" · "}lifetime{" "}
                    {behavior !== null
                      ? Object.entries(behavior.lifetime ?? {})
                          .map(([category, value]) => `${category}: ${formatFinding(value)}`)
                          .join(", ")
                      : "—"}
                  </p>
                </div>
                <div
                  className="xi-cssrs-panel"
                  id="cssrs-lethality"
                  aria-label="C-SSRS lethality (separate dimension)"
                >
                  <p style={{ margin: 0 }}>
                    Lethality by window (separate — no composite): recent
                    actual {formatFinding(lethality?.recent?.actual)},
                    potential {formatFinding(lethality?.recent?.potential)}
                    {" · "}lifetime actual{" "}
                    {formatFinding(lethality?.lifetime?.actual)}, potential{" "}
                    {formatFinding(lethality?.lifetime?.potential)}
                  </p>
                </div>
                <p className="xi-hint">
                  No composite risk score is defined or shown — severity,
                  intensity, behavior, and lethality stay separate.
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
