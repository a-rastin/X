/* Editable secondary plan + proposal comparison (S50 §1, plan.md §9.2;
 * FR-15-16, NFR-03).
 *
 * Mounted inside ProposalReviewPanel's tracking branch (the encounter-draft
 * wizard step), beside the immutable proposal — never inside the chart. The
 * proposal prop is the already-fetched batch proposal rendered verbatim
 * (no second fetch path); the plan is the separately revisioned
 * GET/PATCH .../secondary-plan (own If-Match fence on the plan revision,
 * never the encounter revision, never in the analysis fingerprint — plan
 * edits leave probability acceptance intact by server construction).
 *
 * §1: the successful proposal appears unchanged beside the editable plan;
 * saves persist through reload (only 2xx is durable); the comparison shows
 * proposal vs saved plan side by side with an explicit edited/empty status
 * (text + glyph, never color alone). Blocked signing never clears plan
 * text — this panel renders in both blocked and ready branches and owns its
 * draft independently of SignPanel.
 *
 * Save states saving/saved/failed mirror the S07 autosave contract:
 * debounced autosave (~1s) + explicit Save, 412 keeps edits with
 * Reload/Retry reconcile (never silent overwrite). Empty text is an
 * explicit clear (signing then blocks with PLAN_NOT_READY, S50 §2).
 *
 * Accessibility + theming: native textarea/label/buttons, status
 * role=status, errors role=alert, semantic tokens only (both themes), ink
 * text throughout, print keeps the saved plan readable (see theme.css).
 *
 * E2E selector contract (dev-test owns e2e/signing.spec.ts):
 * - panel `data-testid="secondary-plan-panel"`
 * - editor `data-testid="secondary-plan-textarea"`
 * - save `data-testid="secondary-plan-save"`
 * - save status `data-testid="secondary-plan-status"` (role=status)
 * - plan revision `data-testid="secondary-plan-revision"`
 * - comparison proposal `data-testid="plan-compare-proposal"`
 * - comparison plan `data-testid="plan-compare-plan"`
 * - comparison status `data-testid="plan-compare-status"` (role=status)
 */

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { ApiError, conflictHint } from "../identity/api";
import {
  MAX_SECONDARY_PLAN_CHARS,
  getSecondaryPlan,
  saveSecondaryPlan,
  CLOZAPINE_INCLUDED_LABELS,
  type ClozapineIncluded,
} from "./api";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready" };

export function SecondaryPlanPanel({
  encounterId,
  proposalText,
  onSessionExpired,
  flushRef,
}: {
  encounterId: string;
  /** Verbatim successful-proposal text, or null while incomplete. */
  proposalText: string | null;
  onSessionExpired: () => void;
  /** Sign waits for this panel's separately revisioned text and structured choice. */
  flushRef: RefObject<(() => Promise<void>) | null>;
}) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [revision, setRevision] = useState(1);
  const [savedText, setSavedText] = useState("");
  const [draft, setDraft] = useState("");
  const [inclusion, setInclusion] = useState<ClozapineIncluded>("unknown");
  const [savedInclusion, setSavedInclusion] = useState<ClozapineIncluded>("unknown");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [serverText, setServerText] = useState<string | null>(null);
  const [serverInclusion, setServerInclusion] = useState<ClozapineIncluded>("unknown");
  const [savedAt, setSavedAt] = useState<string | null>(null);

  const draftRef = useRef("");
  const inclusionRef = useRef<ClozapineIncluded>("unknown");
  const savedRef = useRef({ text: "", inclusion: "unknown" as ClozapineIncluded });
  const savingRef = useRef<Promise<boolean> | null>(null);
  const conflictRef = useRef(false);
  const revisionRef = useRef(1);
  const timerRef = useRef<number | null>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  draftRef.current = draft;
  inclusionRef.current = inclusion;
  revisionRef.current = revision;
  conflictRef.current = conflict;

  const failText = useCallback((err: unknown, fallback: string): string => {
    if (err instanceof ApiError) {
      return `${err.message} (${err.code})`;
    }
    return fallback;
  }, []);

  useEffect(() => {
    if (saveError !== null) {
      errorRef.current?.focus();
    }
  }, [saveError]);

  // Mount-per-encounter: committed plan from GET; reloads resume it without
  // a new write. Draft edits stay in memory until a 2xx acknowledges them.
  useEffect(() => {
    let cancelled = false;
    setLoad({ kind: "loading" });
    setSaveError(null);
    setConflict(false);
    void (async () => {
      try {
        const plan = await getSecondaryPlan(encounterId);
        if (cancelled) {
          return;
        }
        setRevision(plan.revision);
        setSavedText(plan.text);
        setDraft(plan.text);
        const value = plan.clozapine_included ?? "unknown";
        setInclusion(value);
        setSavedInclusion(value);
        savedRef.current = { text: plan.text, inclusion: value };
        draftRef.current = plan.text;
        inclusionRef.current = value;
        revisionRef.current = plan.revision;
        setDirty(false);
        setServerText(null);
        setSavedAt(plan.server_timestamp ?? null);
        setLoad({ kind: "ready" });
      } catch (err) {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          onSessionExpired();
          return;
        }
        // Signed drafts 404 here by server construction (draft-only read);
        // the signed view lives on the chart, not in this wizard step.
        setLoad({
          kind: "error",
          message:
            err instanceof ApiError && err.status === 404
              ? "Secondary plan is unavailable — this encounter is no longer an editable draft. The frozen plan lives on the signed chart."
              : failText(err, "Secondary plan failed to load. Check the connection and retry."),
        });
      }
    })();
    return () => {
      cancelled = true;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [encounterId, failText, onSessionExpired]);

  const saveNow: () => Promise<boolean> = useCallback(async (): Promise<boolean> => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    // A sign click can arrive during autosave; wait for that exact write
    // before deciding whether newer edits need a second save.
    if (savingRef.current !== null) return savingRef.current;
    const text = draftRef.current;
    const choice = inclusionRef.current;
    if (text.length > MAX_SECONDARY_PLAN_CHARS) {
      setSaveError(
        `Secondary plan must be at most ${MAX_SECONDARY_PLAN_CHARS} characters — shorten it before saving. Nothing was sent.`,
      );
      return false;
    }
    setSaveError(null);
    setSaving(true);
    const attempt = (async () => {
      try {
        const saved = await saveSecondaryPlan(encounterId, text, revisionRef.current, { clozapineIncluded: choice });
        const savedChoice = saved.clozapine_included ?? "unknown";
        revisionRef.current = saved.revision;
        savedRef.current = { text: saved.text, inclusion: savedChoice };
        setRevision(saved.revision);
        setSavedText(saved.text);
        setSavedInclusion(savedChoice);
        const newerEdits = draftRef.current !== saved.text || inclusionRef.current !== savedChoice;
        setDirty(newerEdits);
        if (newerEdits) {
          timerRef.current = window.setTimeout(() => {
            timerRef.current = null;
            void saveNow();
          }, 1000);
        }
        setConflict(false);
        conflictRef.current = false;
        setServerText(null);
        setSavedAt(saved.server_timestamp ?? null);
        return true;
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          onSessionExpired();
          return false;
        }
        if (err instanceof ApiError && err.status === 412) {
          // Stale plan revision: keep every keystroke, fetch server truth for
          // the reconcile UI, never overwrite the editor.
          setConflict(true);
          conflictRef.current = true;
          try {
            const truth = await getSecondaryPlan(encounterId);
            setServerText(truth.text);
            setServerInclusion(truth.clozapine_included ?? "unknown");
            setRevision(truth.revision);
            revisionRef.current = truth.revision;
          } catch {
            // Truth fetch failed: the conflict message already keeps edits safe.
          }
          return false;
        }
        if (err instanceof ApiError && err.status === 409) {
          // S51 race messaging (archived patient, signed encounter): edits are
          // kept, the canned hint names the outcome, no draft content leaks.
          const hint = conflictHint(err.code);
          setSaveError(
            hint !== null
              ? `${hint} Your edits are kept above.`
              : failText(err, "Secondary plan failed to save. Check the connection and retry."),
          );
          return false;
        }
        setSaveError(failText(err, "Secondary plan failed to save. Check the connection and retry."));
        return false;
      } finally {
        setSaving(false);
      }
    })();
    savingRef.current = attempt;
    try { return await attempt; }
    finally { savingRef.current = null; }
  }, [encounterId, failText, onSessionExpired]);

  useEffect(() => {
    const flush = async () => {
      if (load.kind !== "ready") throw new Error("Wait for the saved secondary plan to load before signing.");
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      if (savingRef.current !== null && !await savingRef.current) {
        throw new Error("Save the secondary plan successfully before signing. Your edits are kept.");
      }
      if (conflictRef.current) throw new Error("Resolve the secondary plan save conflict before signing. Your edits are kept.");
      while (draftRef.current !== savedRef.current.text || inclusionRef.current !== savedRef.current.inclusion) {
        if (!await saveNow()) throw new Error("Save the secondary plan successfully before signing. Your edits are kept.");
      }
    };
    flushRef.current = flush;
    return () => { flushRef.current = null; };
  }, [flushRef, load.kind, saveNow]);

  const schedule = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
    }
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      if (!conflict) {
        void saveNow();
      }
    }, 1000);
  }, [conflict, saveNow]);

  const onChange = useCallback(
    (value: string) => {
      setDraft(value);
      draftRef.current = value;
      setDirty(value !== savedText || inclusionRef.current !== savedInclusion);
      setSaveError(null);
      schedule();
    },
    [savedText, savedInclusion, schedule],
  );

  // ponytail: explicit reload adopts server truth only on user action —
  // never silently, matching the S07 conflict contract.
  const adoptServer = useCallback(() => {
    if (serverText !== null) {
      setDraft(serverText);
      setSavedText(serverText);
      draftRef.current = serverText;
      inclusionRef.current = serverInclusion;
      savedRef.current = { text: serverText, inclusion: serverInclusion };
      setInclusion(serverInclusion);
      setSavedInclusion(serverInclusion);
      setDirty(false);
      setConflict(false);
      conflictRef.current = false;
      setSaveError(null);
      setServerText(null);
    }
  }, [serverText, serverInclusion]);

  const retryKept = useCallback(() => {
    setConflict(false);
    conflictRef.current = false;
    setServerText(null);
    void saveNow();
  }, [saveNow]);

  if (load.kind === "loading") {
    return (
      <section
        className="xi-card"
        aria-labelledby="secondary-plan-heading"
        data-testid="secondary-plan-panel"
      >
        <h4 className="xi-section-title" id="secondary-plan-heading" style={{ fontSize: 16 }}>
          Secondary plan (S50)
        </h4>
        <p role="status">Loading saved secondary plan…</p>
      </section>
    );
  }

  if (load.kind === "error") {
    return (
      <section
        className="xi-card"
        aria-labelledby="secondary-plan-heading"
        data-testid="secondary-plan-panel"
      >
        <h4 className="xi-section-title" id="secondary-plan-heading" style={{ fontSize: 16 }}>
          Secondary plan (S50)
        </h4>
        <p className="xi-form-error" role="alert">
          {load.message}
        </p>
      </section>
    );
  }

  const edited = savedText !== "";
  const statusText = saving
    ? "Saving…"
    : dirty
      ? "Unsaved changes…"
      : conflict
        ? "Conflict — your edits are kept below."
        : `Saved (plan revision ${revision})${savedAt !== null ? ` · ${savedAt}` : ""}`;

  return (
    <section
      className="xi-card"
      aria-labelledby="secondary-plan-heading"
      style={{ marginBottom: 0 }}
      data-testid="secondary-plan-panel"
    >
      <h4 className="xi-section-title" id="secondary-plan-heading" style={{ fontSize: 16 }}>
        Secondary plan (S50)
      </h4>
      <p className="xi-hint" style={{ marginTop: 0 }}>
        The physician-edited secondary plan lands in S50 — edit and save it below. The successful
        proposal above stays unchanged system output; only acknowledged saves are durable.
      </p>

      <div className="xi-field">
        <label className="xi-label" htmlFor="secondary-plan-textarea">
          Physician secondary plan
        </label>
        <textarea
          className="xi-input"
          id="secondary-plan-textarea"
          data-testid="secondary-plan-textarea"
          rows={6}
          autoComplete="off"
          value={draft}
          onChange={(event) => onChange(event.target.value)}
          aria-describedby="secondary-plan-status"
          maxLength={MAX_SECONDARY_PLAN_CHARS + 1}
        />
      </div>
      <fieldset className="xi-field" aria-describedby="secondary-plan-clozapine-hint">
        <legend className="xi-label">Clozapine included in final treatment plan</legend>
        <div className="xi-row-actions">
          {(Object.entries(CLOZAPINE_INCLUDED_LABELS) as [ClozapineIncluded, string][]).map(([value, label]) => (
            <label key={value} htmlFor={`secondary-plan-clozapine-${value}`}>
              <input type="radio" id={`secondary-plan-clozapine-${value}`} name="secondary-plan-clozapine"
                data-testid={`secondary-plan-clozapine-${value}`} value={value} checked={inclusion === value}
                onChange={() => {
                  inclusionRef.current = value;
                  setInclusion(value);
                  setDirty(draftRef.current !== savedText || value !== savedInclusion);
                  setSaveError(null);
                  schedule();
                }} /> {label}
            </label>
          ))}
        </div>
        <p className="xi-hint" id="secondary-plan-clozapine-hint">
          This choice is saved with the plan and frozen when signed. A signed Yes enables the established-case questions at follow-up.
        </p>
      </fieldset>
      <p className="xi-hint" data-testid="secondary-plan-revision" style={{ marginTop: 0 }}>
        Plan revision {revision} · {draft.length} of {MAX_SECONDARY_PLAN_CHARS} characters
      </p>
      <p className="xi-hint" role="status" id="secondary-plan-status" data-testid="secondary-plan-status">
        {statusText}
      </p>
      {saveError !== null && (
        <p className="xi-form-error" role="alert" tabIndex={-1} ref={errorRef}>
          {saveError}
        </p>
      )}
      <div className="xi-row-actions">
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          data-testid="secondary-plan-save"
          disabled={saving || (!dirty && !conflict)}
          onClick={() => void saveNow()}
        >
          {saving ? "Saving…" : "Save secondary plan"}
        </button>
      </div>

      {conflict && (
        <div className="xi-notice" role="alert" style={{ marginTop: 12, marginBottom: 0 }}>
          <p style={{ marginTop: 0 }}>
            <strong>Another tab saved first — your edits are kept.</strong> The saved plan revision
            moved. Nothing was overwritten.
          </p>
          {serverText !== null && (
            <p className="xi-hint">
              Saved plan (revision {revision}): {serverText === "" ? "— empty —" : serverText}.
              Clozapine included: {CLOZAPINE_INCLUDED_LABELS[serverInclusion]}.
            </p>
          )}
          <div className="xi-row-actions">
            <button className="xi-btn xi-btn-secondary" type="button" onClick={adoptServer} disabled={serverText === null}>
              Use saved version
            </button>
            <button className="xi-btn xi-btn-primary" type="button" onClick={retryKept}>
              Keep my edits and retry
            </button>
          </div>
        </div>
      )}

      <h5 className="xi-proposal-subhead">Comparison — proposal vs secondary plan</h5>
      <div className="xi-cpt-compare">
        <div data-testid="plan-compare-proposal" id="plan-compare-proposal">
          <p style={{ marginBottom: 4 }}>
            <strong>Proposal (system output, unchanged)</strong>
          </p>
          {proposalText === null ? (
            <p className="xi-hint" role="status">
              Proposal incomplete — the comparison appears once the proposal is complete.
            </p>
          ) : (
            <p className="xi-plan-text" style={{ marginTop: 0 }}>
              {proposalText}
            </p>
          )}
        </div>
        <div data-testid="plan-compare-plan" id="plan-compare-plan">
          <p style={{ marginBottom: 4 }}>
            <strong>Secondary plan (physician-edited)</strong>
          </p>
          {!edited ? (
            <p className="xi-hint">No secondary plan saved yet.</p>
          ) : (
            <p className="xi-plan-text" style={{ marginTop: 0 }}>
              {savedText}
            </p>
          )}
        </div>
      </div>
      <p className="xi-hint" role="status" data-testid="plan-compare-status" id="plan-compare-status">
        {!edited ? (
          <span>
            <span aria-hidden="true">○</span> No physician edits yet — the proposal above is the
            whole record so far.
          </span>
        ) : (
          <span>
            <span aria-hidden="true">●</span> Physician edits present — saved plan revision{" "}
            {revision} ({savedText.length} characters). The proposal above stays unchanged.
          </span>
        )}
      </p>
    </section>
  );
}
