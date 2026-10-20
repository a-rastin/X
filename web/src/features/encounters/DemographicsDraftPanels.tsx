/* Registration draft working note (plan.md §2.2).

   This belongs on the registration wizard's Register patient step, not on
   later encounter-draft steps. The save-status line stays available on
   every step: assessment pages share one autosave fence and one
   `#draft-save-status`. */

import { useEffect, useRef } from "react";
import { useAutosave, type SaveState } from "./useAutosave";

/** Shown once on `#register` after a successful create. Not sessionStorage:
 * a StrictMode initializer would consume that on the discarded first call. */
export interface RegistrationNotice {
  draftId: string;
  identifier: string;
  serverTimestamp: string;
  revision: number;
}

let pendingRegistrationNotice: RegistrationNotice | null = null;

export function stageRegistrationNotice(notice: RegistrationNotice): void {
  pendingRegistrationNotice = notice;
}

export function registrationNoticeFor(draftId: string): RegistrationNotice | null {
  return pendingRegistrationNotice?.draftId === draftId
    ? pendingRegistrationNotice
    : null;
}

type AutosaveApi = ReturnType<typeof useAutosave>;

function statusOf(saveState: SaveState): { text: string; className: string } {
  if (saveState.kind === "saving") {
    return { text: "Saving…", className: "xi-hint" };
  }
  if (saveState.kind === "dirty") {
    return { text: "Unsaved changes…", className: "xi-hint" };
  }
  if (saveState.kind === "saved") {
    let text = `Saved (revision ${saveState.revision})`;
    if (saveState.serverTimestamp !== null) {
      text += ` · ${saveState.serverTimestamp}`;
    }
    return { text, className: "xi-status" };
  }
  if (saveState.kind === "failed") {
    return { text: `Save failed: ${saveState.message}`, className: "xi-form-error" };
  }
  return {
    text: `Conflict: ${saveState.message} Your edits are kept.`,
    className: "xi-form-error",
  };
}

/** Wizard save status + 412 reconcile. One `#draft-save-status` per page. */
export function DraftSaveStatus({ autosave }: { autosave: AutosaveApi }) {
  const saveState = autosave.saveState;
  const { text, className } = statusOf(saveState);
  const conflictHeadingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (saveState.kind === "conflict") {
      conflictHeadingRef.current?.focus();
    }
  }, [saveState.kind]);

  return (
    <>
      <p className={className} role="status" id="draft-save-status">
        {text}
      </p>
      {(saveState.kind === "failed" || saveState.kind === "conflict") && (
        <div className="xi-row-actions">
          {saveState.kind === "failed" && saveState.retryable && (
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void autosave.retry()}
            >
              Retry save
            </button>
          )}
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => void autosave.reloadTruth()}
          >
            Reload server version
          </button>
        </div>
      )}
      {saveState.kind === "conflict" && (
        <div
          className="xi-notice"
          role="alert"
          aria-labelledby="draft-conflict-heading"
          style={{ marginTop: 12 }}
        >
          <h4
            className="xi-section-title"
            id="draft-conflict-heading"
            tabIndex={-1}
            ref={conflictHeadingRef}
            style={{ fontSize: 16 }}
          >
            Another tab saved first — your edits are kept
          </h4>
          <p style={{ marginBottom: 8 }}>
            The server revision moved. Nothing was overwritten. Compare and
            choose how to reconcile.
          </p>
          {autosave.serverTruth !== null && (
            <p className="xi-hint" style={{ marginBottom: 8 }}>
              Server revision {autosave.serverTruth.revision}:{" "}
              {JSON.stringify(autosave.serverTruth.draftData)}
            </p>
          )}
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => autosave.adoptServerVersion()}
            >
              Use server version
            </button>
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              onClick={() => void autosave.retryOnFreshRevision()}
            >
              Keep my edits and retry
            </button>
          </div>
        </div>
      )}
    </>
  );
}

function workingNoteOf(data: Record<string, unknown>): string {
  const value = data["working_note"];
  return typeof value === "string" ? value : "";
}

/** Working note. Register patient only. */
export function DemographicsDraftPanels({
  autosave,
}: {
  autosave: AutosaveApi;
}) {
  const note = workingNoteOf(autosave.localData);
  return (
    <section className="xi-card" aria-labelledby="draft-editor-heading">
      <h3 className="xi-section-title" id="draft-editor-heading">
        Working note (draft only)
      </h3>
      <p className="xi-hint">
        Autosaves about one second after typing and when leaving the page.
        Only server-acknowledged saves count as Saved.
      </p>
      <div className="xi-field">
        <label className="xi-label" htmlFor="draft-working-note">
          Draft working note
        </label>
        <textarea
          className="xi-input"
          id="draft-working-note"
          rows={4}
          autoComplete="off"
          value={note}
          onChange={(event) =>
            autosave.setDraftValue("working_note", event.target.value)
          }
          aria-describedby="draft-save-status"
        />
      </div>
      <DraftSaveStatus autosave={autosave} />
    </section>
  );
}
