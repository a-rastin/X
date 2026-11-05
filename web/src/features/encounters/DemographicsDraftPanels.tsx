import { useLanguage } from "../../shared/i18n";
import { message, presentError, type Language } from "../../shared/messages";
/* Registration notice and shared draft save status. */

import { useEffect, useRef } from "react";
import { useAutosave, type SaveState } from "./useAutosave";

/** Shown on the matching encounter after a successful create. Not sessionStorage:
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

function statusOf(saveState: SaveState, language: Language): { text: string; className: string } {
  if (saveState.kind === "saving") {
    return { text: message(language, "saving"), className: "xi-hint" };
  }
  if (saveState.kind === "dirty") {
    return { text: message(language, "unsavedChanges"), className: "xi-hint" };
  }
  if (saveState.kind === "saved") {
    let text = message(language, "savedRevision", { revision: saveState.revision });
    if (saveState.serverTimestamp !== null) {
      text += ` · ${saveState.serverTimestamp}`;
    }
    return { text, className: "xi-status" };
  }
  if (saveState.kind === "failed") {
    return { text: message(language, "saveFailed", { diagnostic: presentError(language, saveState.message) }), className: "xi-form-error" };
  }
  return {
    text: message(language, "saveConflict", { diagnostic: presentError(language, saveState.message) }),
    className: "xi-form-error",
  };
}

/** Wizard save status + 412 reconcile. One `#draft-save-status` per page. */
export function DraftSaveStatus({ autosave }: { autosave: AutosaveApi }) {
  const { t, language } = useLanguage();
  const saveState = autosave.saveState;
  const { text, className } = statusOf(saveState, language);
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
              {t("retrySave")}
            </button>
          )}
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => void autosave.reloadTruth()}
          >
            {t("reloadServer")}
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
            {t("concurrentSave")}
          </h4>
          <p style={{ marginBottom: 8 }}>
            {t("reconcileRevision")}
          </p>
          {autosave.serverTruth !== null && (
            <p className="xi-hint" style={{ marginBottom: 8 }}>
              
              {t("serverRevision")} {autosave.serverTruth.revision}:{" "}
              {JSON.stringify(autosave.serverTruth.draftData)}
            </p>
          )}
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => autosave.adoptServerVersion()}
            >
              {t("useServer")}
            </button>
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              onClick={() => void autosave.retryOnFreshRevision()}
            >
              {t("keepEdits")}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
