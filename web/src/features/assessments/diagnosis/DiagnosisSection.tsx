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
 * - `can_proceed` / `requires_acknowledgment` / `proceed_via` decide the
 *   warning and acknowledgment UI. Partial work has no completed threshold
 *   result and is never mislabeled below-threshold; indeterminate (unknowns,
 *   no criterion failed) neither proceeds nor requests acknowledgment.
 * - Acknowledgment is an empty-{} POST tied to the assessed revision +
 *   answers hash; any later relevant edit invalidates it server-side, which
 *   the refreshed preview then shows (the stale record is kept, not hidden).
 * - Bypass is a separate empty-{} POST with no reason field, visually
 *   distinct from completion; the record survives resume via draft_data.
 *
 * Accessibility: native fieldset/legend + radios (keyboard free), error
 * summary takes focus on action failure, preview verdict is role=status,
 * loading/empty/error states come from the real endpoints, styling uses
 * semantic tokens only (both themes), no motion to reduce.
 *
 * E2E selector contract (mirrors e2e/autosave.spec.ts style):
 * - section heading "Diagnosis (step 2)"
 * - radios `#diag-{itemId}-yes|no|unknown`
 * - verdict `#diagnosis-preview-status` (role=status)
 * - criteria table `#diagnosis-criteria`
 * - warning panel text "Completed below threshold", button
 *   "Acknowledge below-threshold result"
 * - bypass panel heading "Bypass instead of completing", button
 *   "Bypass diagnosis", bypassed state text "Diagnosis bypassed"
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../identity/api";
import {
  getEncounter,
  type DraftData,
} from "../../encounters/api";
import type { useAutosave } from "../../encounters/useAutosave";
import {
  acknowledgeDiagnosis,
  bypassDiagnosis,
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

export function DiagnosisSection({
  encounterId,
  autosave,
  onSessionExpired,
}: DiagnosisSectionProps) {
  const [definition, setDefinition] = useState<DiagnosisDefinition | null>(null);
  const [definitionError, setDefinitionError] = useState<string | null>(null);
  const [preview, setPreview] = useState<DiagnosisPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"acknowledge" | "bypass" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const actionErrorRef = useRef<HTMLParagraphElement>(null);

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
    try {
      const fresh = await getDiagnosis(encounterId);
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
          : "Diagnosis preview failed to load. Check the connection and retry.",
      );
    } finally {
      setPreviewLoading(false);
    }
  }, [encounterId, onSessionExpired]);

  // Live preview follows every acknowledged save: a working-note save bumps
  // the revision too, and the acknowledgment POST needs the current one.
  const savedRevision =
    autosave.saveState.kind === "saved" ? autosave.saveState.revision : 0;
  useEffect(() => {
    void refreshPreview();
  }, [refreshPreview, savedRevision]);

  const section = sectionOf(autosave.localData);
  const answers = section.answers;
  const answersPending =
    preview !== null && !sameAnswers(answers, preview.answers);

  function setAnswer(itemId: string, value: DiagnosisAnswer): void {
    if (answers[itemId] === value || busy !== null) {
      return;
    }
    setActionError(null);
    setNotice(null);
    autosave.setDraftValue("diagnosis", {
      answers: { ...answers, [itemId]: value },
      // Keep the stale acknowledgment/bypass records: the server derives
      // validity from the answers hash, so a relevant edit visibly
      // invalidates instead of silently disappearing.
      acknowledgment: section.acknowledgment,
      bypass: section.bypass,
    });
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

  async function handleBypass(): Promise<void> {
    if (busy !== null) {
      return;
    }
    setActionError(null);
    setNotice(null);
    setBusy("bypass");
    try {
      const current = await currentRevision();
      const result = await bypassDiagnosis(encounterId, current.revision);
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
    verdict = "Diagnosis bypassed. Bypass is not a completed assessment.";
  } else if (evaluation?.status === "complete" && overall === "criteria_satisfied") {
    verdict = `Every required criterion satisfied (server evaluation ${preview.definition_version}). This step can proceed.`;
  } else if (
    evaluation?.status === "complete" &&
    overall === "below_threshold" &&
    preview.requires_acknowledgment
  ) {
    verdict =
      "Completed below threshold. Continuing needs your attributed acknowledgment of this exact assessment.";
  } else if (
    evaluation?.status === "complete" &&
    overall === "below_threshold" &&
    preview.acknowledgment_valid
  ) {
    verdict =
      "Completed below threshold and acknowledged. This step can proceed via the acknowledgment below.";
  } else if (evaluation?.status === "complete") {
    verdict =
      "Indeterminate: every required item is answered but some criteria are still unknown and none failed. This is incomplete — it cannot proceed and needs no acknowledgment.";
  } else if (evaluation?.status === "partial") {
    const missing = evaluation.missing_item_ids.length;
    verdict = `Incomplete — ${missing} required item${missing === 1 ? "" : "s"} still need${missing === 1 ? "s" : ""} an answer. Saved as incomplete; this is not a below-threshold result and needs no acknowledgment.`;
  } else {
    verdict =
      "No answers yet. Diagnosis is unanswered — not a below-threshold result.";
  }

  return (
    <section className="xi-card" aria-labelledby="diagnosis-heading">
      <h3 className="xi-section-title" id="diagnosis-heading">
        Diagnosis (step 2)
      </h3>
      <p className="xi-hint">
        Answers autosave through the same draft as the rest of this encounter
        (about one second after changing an answer). The threshold preview
        below is computed by the server from the released criteria — the
        browser never decides the result itself.
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
        <p>Loading diagnosis form…</p>
      )}

      {definition !== null && (
        <div>
          {definition.items.map((item) => (
            <fieldset key={item.id} className="xi-field">
              <legend className="xi-label">{item.prompt}</legend>
              <div className="xi-radio-group">
                {ANSWER_OPTIONS.map((option) => (
                  <label
                    key={option.value}
                    htmlFor={`diag-${item.id}-${option.value}`}
                  >
                    <input
                      id={`diag-${item.id}-${option.value}`}
                      type="radio"
                      name={`diagnosis-${item.id}`}
                      value={option.value}
                      checked={answers[item.id] === option.value}
                      onChange={() => setAnswer(item.id, option.value)}
                      aria-describedby={
                        itemErrors[item.id] !== undefined
                          ? `diag-${item.id}-error`
                          : undefined
                      }
                    />
                    {option.label}
                  </label>
                ))}
              </div>
              {itemErrors[item.id] !== undefined && (
                <p className="xi-field-error" id={`diag-${item.id}-error`}>
                  {itemErrors[item.id].join(" ")}
                </p>
              )}
            </fieldset>
          ))}
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <h4 className="xi-section-title" style={{ fontSize: 16 }}>
          Server threshold preview
        </h4>
        {previewLoading && preview === null && <p>Loading diagnosis preview…</p>}
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
              id="diagnosis-preview-status"
              aria-live="polite"
            >
              {verdict}
            </p>
            <p className="xi-hint">
              Server evaluation · definition {preview.definition_version} ·
              saved revision {preview.revision}
              {answersPending &&
                " · unsaved answer edits pending — preview reflects the last saved revision"}
              {previewError !== null && " · preview refresh failed; showing last loaded state"}
            </p>

            {itemErrorIds.length > 0 && (
              <p className="xi-form-error" role="alert">
                The saved answers contain server-reported problems:{" "}
                {itemErrorIds
                  .map((id) => `${id}: ${(itemErrors[id] ?? []).join(" ")}`)
                  .join("; ")}
              </p>
            )}

            {evaluation?.status === "partial" && definition !== null && (
              <div>
                <p className="xi-hint">Still needed:</p>
                <ul>
                  {evaluation.missing_item_ids.map((id) => (
                    <li key={id}>
                      {definition.items.find((item) => item.id === id)?.prompt ?? id}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {criteria !== null && evaluation?.status === "complete" && (
              <table className="xi-table" id="diagnosis-criteria" aria-label="Criterion states">
                <thead>
                  <tr>
                    <th scope="col">Criterion</th>
                    <th scope="col">State</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(criteria).map(([criterion, state]) => (
                    <tr key={criterion}>
                      <td>{criterion}</td>
                      <td>{state}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {preview.requires_acknowledgment && (
              <div className="xi-warning-panel" aria-labelledby="diagnosis-warning-heading">
                <h5 id="diagnosis-warning-heading" style={{ margin: "0 0 8px" }}>
                  Warning: completed below threshold
                </h5>
                <p style={{ marginTop: 0 }}>
                  At least one required criterion is not met (server
                  evaluation {preview.definition_version}, saved revision{" "}
                  {preview.revision}). Continuing past this step records your
                  attributed acknowledgment of this exact assessment. Any later
                  change to these answers invalidates the acknowledgment.
                </p>
                <button
                  className="xi-btn xi-btn-primary"
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void handleAcknowledge()}
                >
                  {busy === "acknowledge"
                    ? "Acknowledging…"
                    : "Acknowledge below-threshold result"}
                </button>
              </div>
            )}

            {preview.acknowledgment_valid && preview.acknowledgment !== null && (
              <p className="xi-hint">
                Acknowledged by author {preview.acknowledgment.actor_id} at{" "}
                {preview.acknowledgment.acknowledged_at} (assessed revision{" "}
                {preview.acknowledgment.revision}, definition{" "}
                {preview.acknowledgment.definition_version}).
              </p>
            )}

            <p className="xi-hint">
              Can proceed to the next step:{" "}
              {preview.can_proceed
                ? `Yes — ${preview.proceed_via}`
                : "No"}
            </p>
          </div>
        )}
      </div>

      <div className="xi-bypass-panel" aria-labelledby="diagnosis-bypass-heading">
        <h4 id="diagnosis-bypass-heading" style={{ margin: "0 0 8px" }}>
          Bypass instead of completing
        </h4>
        {preview?.bypass_valid === true && preview.bypass !== null ? (
          <p style={{ marginTop: 0 }}>
            Diagnosis bypassed — recorded for author {preview.bypass.actor_id}{" "}
            at {preview.bypass.bypassed_at} (revision {preview.bypass.revision}).
            Bypass is distinct from completion: it is not a completed
            assessment and carries no reason.
          </p>
        ) : (
          <div>
            <p style={{ marginTop: 0 }}>
              Bypass records who bypassed and when, without asking for a
              reason. It clears these answers on the server and survives
              resume. Bypass is visibly different from completing the
              checklist.
            </p>
            {preview !== null &&
              preview.bypass !== null &&
              !preview.bypass_valid && (
              <p style={{ marginTop: 0 }}>
                A previous bypass no longer applies because answers were added
                afterwards.
              </p>
            )}
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={busy !== null || definition === null}
              onClick={() => void handleBypass()}
            >
              {busy === "bypass" ? "Bypassing…" : "Bypass diagnosis"}
            </button>
          </div>
        )}
      </div>

      {notice !== null && (
        <p className="xi-status" role="status">
          {notice}
        </p>
      )}
      {actionError !== null && (
        <p
          className="xi-form-error"
          role="alert"
          tabIndex={-1}
          ref={actionErrorRef}
        >
          {actionError}
        </p>
      )}
    </section>
  );
}
