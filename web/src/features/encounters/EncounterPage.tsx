/* Draft wizard page with autosave (S07, plan.md §§2.3, 9; FR-16, NFR-04).
 *
 * Route: `#/encounters/:id` (see api.ts for why the encounter id is in the
 * URL). Physician-only guard complements the server 403; the server stays
 * authoritative. No placeholder medical recommendations: the S07 field is an
 * explicit working note stored as `draft_data.working_note`, S09 adds the
 * diagnosis checklist (`draft_data.diagnosis`), S10 adds PANSS
 * (`draft_data.panss`), S11 adds C-SSRS (`draft_data.cssrs`), and S12 adds
 * structured history (`draft_data.history`) plus the four adverse effects
 * (`draft_data.effects`) — later assessment pages extend `draft_data` keys
 * through the same autosave path (full object on each save).
 *
 * States: loading (GET), ready (editor + saving/saved/failed), conflict
 * (412 keeps edits + Reload/Retry reconcile UI, never silent overwrite),
 * error (403 author-only, 404 discarded/missing, transport with Retry).
 * Discard needs explicit confirmation + current revision, releases the slot
 * without deleting the patient. Navigation warns while edits remain
 * (beforeunload + in-app hash guard); flush persists on page transition.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../identity/api";
import { useAuth } from "../identity/auth";
import { getPatient, type Patient } from "../patients/api";
import { registerUnsavedGuard } from "../../app/navigationGuard";
import {
  discardEncounter,
  encounterHash,
  getEncounter,
  type EncounterReference,
} from "./api";
import { DiagnosisSection } from "../assessments/diagnosis/DiagnosisSection";
import { PanssSection } from "../assessments/panss/PanssSection";
import { CssrsSection } from "../assessments/cssrs/CssrsSection";
import { HistorySection } from "../history/HistorySection";
import { EffectsSection } from "../history/EffectsSection";
import { NotesSection } from "../notes/NotesSection";
import { FollowupBaselinePanel } from "../patients/chart/FollowupBaselinePanel";
import { useAutosave } from "./useAutosave";

type LoadState =
  | { kind: "loading" }
  | { kind: "denied"; message: string }
  | { kind: "error"; message: string; status: number | null }
  | {
      kind: "ready";
      encounter: EncounterReference;
      initialData: Record<string, unknown>;
      initialRevision: number;
      serverTimestamp: string | null;
    }
  | { kind: "discarded"; revision: number };

export function parseEncounterId(hash: string): string | null {
  const prefix = "#/encounters/";
  if (!hash.startsWith(prefix)) {
    return null;
  }
  const id = hash.slice(prefix.length).split(/[?#]/)[0];
  return id !== "" ? id : null;
}

function workingNoteOf(data: Record<string, unknown>): string {
  const value = data["working_note"];
  return typeof value === "string" ? value : "";
}

export function EncounterPage({ encounterId }: { encounterId: string }) {
  const { user, sessionExpired } = useAuth();
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [patient, setPatient] = useState<Patient | null>(null);

  const loadEncounter = useCallback(async () => {
    setLoad({ kind: "loading" });
    try {
      const result = await getEncounter(encounterId);
      setLoad({
        kind: "ready",
        encounter: result.encounter,
        initialData: result.draft_data,
        initialRevision: result.revision,
        serverTimestamp: result.server_timestamp ?? null,
      });
      // Patient demographics for context (shared read, never clinical).
      try {
        const fetched = await getPatient(result.encounter.patient_id);
        setPatient(fetched);
      } catch {
        setPatient(null);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setLoad({
          kind: "denied",
          message: "Only the draft author can access this draft.",
        });
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        setLoad({
          kind: "error",
          message: "Encounter not found. It may have been discarded.",
          status: 404,
        });
        return;
      }
      setLoad({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Draft failed to load. Check the connection and retry.",
        status: err instanceof ApiError ? err.status : null,
      });
    }
  }, [encounterId, sessionExpired]);

  useEffect(() => {
    void loadEncounter();
  }, [loadEncounter]);

  if (user?.role !== "physician") {
    return (
      <div>
        <h2 className="xi-page-title">Encounter draft</h2>
        <p className="xi-form-error" role="alert">
          Physician access required. Only the draft author can view or edit
          draft content.
        </p>
      </div>
    );
  }

  if (load.kind === "loading") {
    return (
      <div>
        <h2 className="xi-page-title">Encounter draft</h2>
        <p>Loading draft…</p>
      </div>
    );
  }

  if (load.kind === "denied") {
    return (
      <div>
        <h2 className="xi-page-title">Encounter draft</h2>
        <p className="xi-form-error" role="alert">
          {load.message}
        </p>
        <p className="xi-hint">
          Draft content is visible only to its author. The patient directory
          remains shared.
        </p>
        <p>
          <a className="xi-btn xi-btn-secondary" href="#/patients">
            Back to directory
          </a>
        </p>
      </div>
    );
  }

  if (load.kind === "error") {
    return (
      <div>
        <h2 className="xi-page-title">Encounter draft</h2>
        <p className="xi-form-error" role="alert">
          {load.message}
        </p>
        <div className="xi-row-actions">
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => void loadEncounter()}
          >
            Retry
          </button>
          <a className="xi-btn xi-btn-secondary" href="#/patients">
            Back to directory
          </a>
        </div>
      </div>
    );
  }

  if (load.kind === "discarded") {
    return (
      <div>
        <h2 className="xi-page-title">Encounter draft</h2>
        <p className="xi-status" role="status">
          Draft discarded (revision {load.revision}). The slot is free and the
          patient record was kept.
        </p>
        <p>
          <a className="xi-btn xi-btn-secondary" href="#/patients">
            Back to directory
          </a>
        </p>
      </div>
    );
  }

  return (
    <EncounterWizard
      key={encounterId}
      encounterId={encounterId}
      encounter={load.encounter}
      initialData={load.initialData}
      initialRevision={load.initialRevision}
      initialServerTimestamp={load.serverTimestamp}
      patient={patient}
      onDiscarded={(revision) => setLoad({ kind: "discarded", revision })}
    />
  );
}

function EncounterWizard({
  encounterId,
  encounter,
  initialData,
  initialRevision,
  initialServerTimestamp,
  patient,
  onDiscarded,
}: {
  encounterId: string;
  encounter: EncounterReference;
  initialData: Record<string, unknown>;
  initialRevision: number;
  initialServerTimestamp: string | null;
  patient: Patient | null;
  onDiscarded: (revision: number) => void;
}) {
  const { sessionExpired } = useAuth();
  const autosave = useAutosave({
    encounterId,
    initialData,
    initialRevision,
    initialServerTimestamp,
    onSessionExpired: sessionExpired,
  });
  const [discardOpen, setDiscardOpen] = useState(false);
  const [discardConfirm, setDiscardConfirm] = useState(false);
  const [discardBusy, setDiscardBusy] = useState(false);
  const [discardError, setDiscardError] = useState<string | null>(null);
  const conflictHeadingRef = useRef<HTMLHeadingElement>(null);
  const discardDialogRef = useRef<HTMLDivElement>(null);

  const note = workingNoteOf(autosave.localData);

  // Navigation guard: warn while local edits remain (in-app + beforeunload).
  useEffect(() => {
    registerUnsavedGuard(() => autosave.hasUnsaved);
    return () => registerUnsavedGuard(null);
  }, [autosave.hasUnsaved]);

  useEffect(() => {
    if (!autosave.hasUnsaved) {
      return;
    }
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [autosave.hasUnsaved]);

  // In-app hash-route guard for manual hash edits and back/forward: confirm
  // before leaving the draft with unsaved edits, restoring the draft hash
  // when the user stays.
  useEffect(() => {
    const ownHash = encounterHash(encounterId);
    const onHashChange = () => {
      if (window.location.hash.startsWith("#/encounters/")) {
        return;
      }
      if (!autosave.hasUnsaved) {
        void autosave.flush();
        return;
      }
      const stay = !window.confirm(
        "You have unsaved draft changes. Leave without saving?",
      );
      if (stay) {
        window.location.hash = ownHash;
      } else {
        void autosave.flush();
      }
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [autosave, encounterId]);

  useEffect(() => {
    if (autosave.saveState.kind === "conflict") {
      conflictHeadingRef.current?.focus();
    }
  }, [autosave.saveState.kind]);

  useEffect(() => {
    if (discardOpen) {
      discardDialogRef.current?.querySelector("button")?.focus();
    }
  }, [discardOpen]);

  async function handleDiscard(): Promise<void> {
    if (!discardConfirm || discardBusy) {
      return;
    }
    setDiscardError(null);
    setDiscardBusy(true);
    try {
      // Flush first so the discard revision fence sees the latest base;
      // discard itself requires the current revision explicitly.
      await autosave.flush();
      const result = await discardEncounter(encounterId, autosave.baseRevision);
      registerUnsavedGuard(null);
      onDiscarded(result.revision);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 412) {
        setDiscardError(
          "The draft changed. Reload the draft and retry discard with the current revision.",
        );
        return;
      }
      setDiscardError(
        err instanceof ApiError
          ? err.message
          : "Discard failed. Check the connection and retry.",
      );
    } finally {
      setDiscardBusy(false);
    }
  }

  const saveState = autosave.saveState;
  let statusText: string;
  let statusClass = "xi-hint";
  if (saveState.kind === "saving") {
    statusText = "Saving…";
  } else if (saveState.kind === "dirty") {
    statusText = "Unsaved changes…";
  } else if (saveState.kind === "saved") {
    statusText = `Saved (revision ${saveState.revision})`;
    statusClass = "xi-status";
    if (saveState.serverTimestamp !== null) {
      statusText += ` · ${saveState.serverTimestamp}`;
    }
  } else if (saveState.kind === "failed") {
    statusText = `Save failed: ${saveState.message}`;
    statusClass = "xi-form-error";
  } else {
    statusText = `Conflict: ${saveState.message} Your edits are kept.`;
    statusClass = "xi-form-error";
  }

  return (
    <div>
      <h2 className="xi-page-title">Encounter draft</h2>
      <section className="xi-card" aria-labelledby="draft-context-heading">
        <h3 className="xi-section-title" id="draft-context-heading">
          Draft context
        </h3>
        <p className="xi-hint" style={{ marginTop: 0 }}>
          {encounter.kind === "registration" ? "Registration" : "Follow-up"} draft
          {" · "}revision {autosave.baseRevision}
          {patient !== null &&
            ` · ${patient.given_name} ${patient.family_name} (${patient.identifier})`}
        </p>
        <p className="xi-hint">
          Later assessment pages extend this same draft through autosave — one
          persisted object, one revision fence, no separate mechanisms.
        </p>
      </section>

      {/* S14 follow-up baseline: author-only copied history/medications with
        copied_baseline provenance + pending reconciliation; prior scores show
        as historical only (never as answers). PANSS/C-SSRS sections below
        still start unanswered. Registration drafts skip this panel. */}
      {encounter.kind === "follow_up" && (
        <FollowupBaselinePanel
          encounterId={encounterId}
          onSessionExpired={sessionExpired}
        />
      )}

      {/* S13 page notes for demographics: the demographics form itself stays
        local until valid, but once this draft exists (encounterId present)
        the author can attach attributed demographics notes here. Reusable
        NotesSection per wizard page; page key matches backend ALLOWED_PAGES.
        No snapshot machinery: S40/S41/S59 carry the mandatory end-to-end
        note-noninterference checks. */}
      <NotesSection
        encounterId={encounterId}
        page="demographics"
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />

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
        <p className={statusClass} role="status" id="draft-save-status">
          {statusText}
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
      </section>

      <DiagnosisSection
        encounterId={encounterId}
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />
      <NotesSection
        encounterId={encounterId}
        page="diagnosis"
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />

      <PanssSection
        encounterId={encounterId}
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />
      <NotesSection
        encounterId={encounterId}
        page="panss"
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />

      <CssrsSection
        encounterId={encounterId}
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />
      <NotesSection
        encounterId={encounterId}
        page="cssrs"
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />

      <HistorySection
        encounterId={encounterId}
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />
      {/* Page notes render SEPARATELY from history: never inside the history
        analysis_visible list. This distinct panel carries page/author/time
        from server values; S40/S41/S59 assert the noninterference end to end. */}
      <NotesSection
        encounterId={encounterId}
        page="history"
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />

      <EffectsSection
        encounterId={encounterId}
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />
      <NotesSection
        encounterId={encounterId}
        page="effects"
        autosave={autosave}
        onSessionExpired={sessionExpired}
      />

      <section className="xi-card" aria-labelledby="draft-danger-heading">
        <h3 className="xi-section-title" id="draft-danger-heading">
          Discard draft
        </h3>
        <p className="xi-hint">
          Discarding releases the patient slot without deleting the patient.
          It needs explicit confirmation and the current revision (
          {autosave.baseRevision}).
        </p>
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          onClick={() => {
            setDiscardConfirm(false);
            setDiscardError(null);
            setDiscardOpen(true);
          }}
        >
          Discard draft
        </button>
      </section>

      <p>
        <a
          className="xi-btn xi-btn-secondary"
          href="#/patients"
          onClick={(event) => {
            // Page-transition flush: persist pending edits before leaving.
            void autosave.flush();
            if (autosave.hasUnsaved) {
              if (
                !window.confirm(
                  "You have unsaved draft changes. Leave without saving?",
                )
              ) {
                event.preventDefault();
              }
            }
          }}
        >
          Back to directory
        </a>
      </p>

      {discardOpen && (
        <div className="xi-modal-backdrop">
          <div
            className="xi-modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="discard-title"
            aria-describedby="discard-text"
            ref={discardDialogRef}
          >
            <h2 className="xi-section-title" id="discard-title">
              Discard this draft?
            </h2>
            <p id="discard-text">
              This releases the patient slot (revision {autosave.baseRevision})
              without deleting the patient. This cannot be undone.
            </p>
            <div className="xi-field">
              <label className="xi-label" htmlFor="discard-confirm">
                <input
                  id="discard-confirm"
                  type="checkbox"
                  checked={discardConfirm}
                  onChange={(event) =>
                    setDiscardConfirm(event.target.checked)
                  }
                />{" "}
                I understand this discards the draft but keeps the patient.
              </label>
            </div>
            {discardError !== null && (
              <p className="xi-form-error" role="alert">
                {discardError}
              </p>
            )}
            <div className="xi-row-actions">
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                onClick={() => setDiscardOpen(false)}
              >
                Cancel
              </button>
              <button
                className="xi-btn xi-btn-primary"
                type="button"
                disabled={!discardConfirm || discardBusy}
                onClick={() => void handleDiscard()}
              >
                {discardBusy ? "Discarding…" : "Confirm discard"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
