/* Reusable wizard page-note control (S13, plan.md §§2.3, 4.1-4.3; FR-16, FR-22).
 *
 * One control mounts per wizard page with its backend ALLOWED_PAGES key
 * (demographics after the initial draft exists, plus diagnosis/panss/cssrs/
 * history/effects and any future step reusing this component). Each control
 * keeps its own per-page list + add form against POST/GET
 * /encounters/{id}/notes with the current encounter revision as If-Match, a
 * fresh Idempotency-Key per submit, X-CSRF-Token, and credentials include
 * (see ./api.ts, mirroring features/encounters/api.ts and
 * features/identity/api.ts patterns).
 *
 * Revision fence: the note POST bumps the encounter revision (S07 autosave
 * coherence). Before submitting, pending autosave edits flush so the fence
 * sees the latest base; after a 201 the autosave base advances via
 * applyExternalRevision without touching the draft editor. A 412 keeps the
 * typed text and offers Reload/Retry on the fresh revision (never silent
 * overwrite); any failure never shows Saved — only a 2xx is durable.
 *
 * Separation: notes render in their own .xi-notes-panel section with
 * page/author/time from server values (author_display + created_at), never
 * inside the history analysis_visible list. Text renders as plain text via
 * React default escaping (no dangerouslySetInnerHTML), so literal markup
 * like <b>hello</b> shows literally.
 *
 * No snapshot machinery here. S40/S41/S59 are the mandatory end-to-end
 * note-noninterference checks: each must prove page notes never leak into
 * analysis-visible history, prompts, CPTs, DDI, or proposal selection (a
 * page note is never relabeled algorithm-visible history), and that a
 * note-only change does not invalidate probability acceptance. Those
 * sessions implement the checks; this control only defines the UI channel
 * they assert against (backend exclusion: exclude_notes_from_analysis).
 *
 * Accessibility + theming: native label/textarea/button (keyboard free),
 * the list is an aria-live polite region, the per-page status keeps
 * role=status, conflict/error uses role=alert with focus moved to the
 * conflict heading, styling uses semantic tokens only (both themes, see
 * .xi-notes-panel in shared/theme.css). Reduced motion is respected via
 * the existing global prefers-reduced-motion rule (no animation here).
 *
 * E2E selector contract (stable for dev-test, see e2e/notes.spec.ts):
 * - panel `.xi-notes-panel` (per page)
 * - heading `#notes-{page}-heading`, e.g. `#notes-diagnosis-heading`
 * - list `#notes-{page}-list` (aria-live polite), e.g. `#notes-history-list`
 * - empty state `#notes-{page}-empty`
 * - input `#notes-{page}-input` (labelled "Page note for {page}")
 * - add button `#notes-{page}-add`
 * - status `#notes-{page}-status` (role=status)
 * - conflict panel `#notes-{page}-conflict` (role=alert)
 * - reload button `#notes-{page}-reload`, retry button `#notes-{page}-retry`
 * Demographics uses the same shape: #notes-demographics-list/input/add/status.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, newIdempotencyKey } from "../identity/api";
import { getEncounter } from "../encounters/api";
import type { useAutosave } from "../encounters/useAutosave";
import { createNote, listNotes, type PageNote } from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface NotesSectionProps {
  encounterId: string;
  /** Backend ALLOWED_PAGES key (demographics, diagnosis, panss, cssrs,
   * history, medications, effects, proposal, secondary_plan). */
  page: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
}

type NoteSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; revision: number; serverTimestamp: string }
  | { kind: "failed"; message: string; retryable: boolean }
  | { kind: "conflict"; message: string };

export function NotesSection({
  encounterId,
  page,
  autosave,
  onSessionExpired,
}: NotesSectionProps) {
  const [notes, setNotes] = useState<PageNote[]>([]);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [saveState, setSaveState] = useState<NoteSaveState>({ kind: "idle" });
  const [fieldError, setFieldError] = useState<string | null>(null);
  const attemptKeyRef = useRef<string | null>(null);
  const conflictHeadingRef = useRef<HTMLHeadingElement>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refreshList = useCallback(async (): Promise<number | null> => {
    try {
      const result = await listNotes(encounterId, page);
      if (!mountedRef.current) {
        return null;
      }
      setNotes(result.items);
      setTotal(result.total);
      setListError(null);
      return result.revision;
    } catch (err) {
      if (!mountedRef.current) {
        return null;
      }
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return null;
      }
      setListError(
        err instanceof ApiError
          ? err.message
          : "Page notes failed to load. Check the connection and retry.",
      );
      return null;
    } finally {
      if (mountedRef.current) {
        setListLoading(false);
      }
    }
  }, [encounterId, page, onSessionExpired]);

  // Live list follows every acknowledged autosave (a note POST from another
  // control bumps the revision too) and the initial mount.
  const savedRevision =
    autosave.saveState.kind === "saved" ? autosave.saveState.revision : 0;
  useEffect(() => {
    void refreshList();
  }, [refreshList, savedRevision]);

  useEffect(() => {
    if (saveState.kind === "conflict" || saveState.kind === "failed") {
      conflictHeadingRef.current?.focus();
    }
  }, [saveState.kind]);

  /** Current encounter revision for the If-Match fence: prefer the notes
   * list revision (same counter as GET /encounters), falling back to the
   * autosave base when the list has not loaded yet. */
  async function currentRevision(): Promise<number> {
    await autosave.flush();
    const listed = await listNotes(encounterId, page);
    if (mountedRef.current) {
      setNotes(listed.items);
      setTotal(listed.total);
      setListError(null);
      setListLoading(false);
    }
    return listed.revision;
  }

  async function submitAttempt(revision: number, key: string): Promise<void> {
    const result = await createNote(encounterId, page, text, revision, {
      idempotencyKey: key,
    });
    if (!mountedRef.current) {
      return;
    }
    attemptKeyRef.current = null;
    // Advance the shared autosave fence without touching the draft editor:
    // the note changed the revision, not draft_data.
    autosave.applyExternalRevision(result.revision, result.server_timestamp);
    const listed = await listNotes(encounterId, page);
    if (mountedRef.current) {
      setNotes(listed.items);
      setTotal(listed.total);
      setText("");
      setFieldError(null);
      setSaveState({
        kind: "saved",
        revision: result.revision,
        serverTimestamp: result.server_timestamp,
      });
    }
  }

  async function handleAdd(): Promise<void> {
    if (text === "") {
      setFieldError("Note text must be non-empty.");
      return;
    }
    if (saveState.kind === "saving") {
      return;
    }
    setFieldError(null);
    setSaveState({ kind: "saving" });
    const key = newIdempotencyKey();
    attemptKeyRef.current = key;
    try {
      const revision = await currentRevision();
      await submitAttempt(revision, key);
    } catch (err) {
      if (!mountedRef.current) {
        return;
      }
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 412) {
        // Stale fence: keep the typed text, refresh the list for the
        // reconcile UI. Retry uses the fresh revision as a new attempt.
        setSaveState({ kind: "conflict", message: err.message });
        try {
          const fresh = await listNotes(encounterId, page);
          if (mountedRef.current) {
            setNotes(fresh.items);
            setTotal(fresh.total);
            setListError(null);
          }
        } catch {
          // The conflict message already keeps the text safe.
        }
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        attemptKeyRef.current = null;
        setSaveState({ kind: "failed", message: err.message, retryable: false });
        return;
      }
      if (err instanceof ApiError && err.status === 422) {
        attemptKeyRef.current = null;
        const details = Object.values(err.fieldErrors).flat().join(" ");
        setFieldError(details !== "" ? details : err.message);
        setSaveState({ kind: "failed", message: err.message, retryable: false });
        return;
      }
      const message =
        err instanceof ApiError
          ? err.message
          : "Note save failed. Check the connection and retry.";
      setSaveState({ kind: "failed", message, retryable: true });
    }
  }

  async function handleRetry(): Promise<void> {
    if (saveState.kind !== "failed" && saveState.kind !== "conflict") {
      return;
    }
    const isConflict = saveState.kind === "conflict";
    setSaveState({ kind: "saving" });
    try {
      // Transport-failure retry of the same attempt reuses the key so a lost
      // response replays instead of double-creating; a 412 conflict is a new
      // attempt on the fresh revision with a fresh key (same text kept).
      let key = attemptKeyRef.current;
      if (isConflict || key === null) {
        key = newIdempotencyKey();
        attemptKeyRef.current = key;
      }
      const revision = await currentRevision();
      await submitAttempt(revision, key);
    } catch (err) {
      if (!mountedRef.current) {
        return;
      }
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 412) {
        setSaveState({ kind: "conflict", message: err.message });
        try {
          const fresh = await listNotes(encounterId, page);
          if (mountedRef.current) {
            setNotes(fresh.items);
            setTotal(fresh.total);
          }
        } catch {
          // Keep the typed text safe regardless.
        }
        return;
      }
      if (err instanceof ApiError && (err.status === 409 || err.status === 422)) {
        attemptKeyRef.current = null;
        const details =
          err instanceof ApiError
            ? Object.values(err.fieldErrors).flat().join(" ")
            : "";
        if (details !== "") {
          setFieldError(details);
        }
        setSaveState({ kind: "failed", message: err.message, retryable: false });
        return;
      }
      const message =
        err instanceof ApiError
          ? err.message
          : "Note save failed. Check the connection and retry.";
      setSaveState({ kind: "failed", message, retryable: true });
    }
  }

  async function handleReload(): Promise<void> {
    setListLoading(true);
    const revision = await refreshList();
    if (revision !== null) {
      // Adopt the fresh server revision as the next fence without clearing
      // the typed text: reload never overwrites the editor.
      try {
        const truth = await getEncounter(encounterId);
        if (mountedRef.current) {
          autosave.applyExternalRevision(
            truth.revision,
            truth.server_timestamp ?? null,
          );
        }
      } catch {
        // List refresh already succeeded; fence sync is best effort.
      }
    }
    if (mountedRef.current) {
      setListLoading(false);
    }
  }

  const inputId = `notes-${page}-input`;
  const listId = `notes-${page}-list`;
  const addId = `notes-${page}-add`;
  const statusId = `notes-${page}-status`;
  const headingId = `notes-${page}-heading`;
  const emptyId = `notes-${page}-empty`;
  const conflictId = `notes-${page}-conflict`;
  const reloadId = `notes-${page}-reload`;
  const retryId = `notes-${page}-retry`;

  let statusText: string;
  let statusClass = "xi-hint";
  if (saveState.kind === "saving") {
    statusText = "Saving note…";
  } else if (saveState.kind === "saved") {
    statusText = `Note saved (revision ${saveState.revision}) · ${saveState.serverTimestamp}`;
    statusClass = "xi-status";
  } else if (saveState.kind === "failed") {
    statusText = `Note save failed: ${saveState.message}`;
    statusClass = "xi-form-error";
  } else if (saveState.kind === "conflict") {
    statusText = `Note conflict: ${saveState.message} Your text is kept.`;
    statusClass = "xi-form-error";
  } else {
    statusText =
      total === 0 && !listLoading && listError === null
        ? "No page notes yet."
        : "";
  }

  const addDisabled = text === "" || saveState.kind === "saving";

  return (
    <section className="xi-card" aria-labelledby={headingId}>
      <h4 className="xi-section-title" id={headingId}>
        Page notes — {page}
      </h4>
      <p className="xi-hint">
        Attributed notes for this page only. They are stored separately from
        the draft and never appear in history analysis.
      </p>
      <div className="xi-notes-panel" aria-label={`Page notes for ${page}`}>
        {listLoading && notes.length === 0 && listError === null && (
          <p>Loading page notes…</p>
        )}
        {listError !== null && notes.length === 0 && (
          <div>
            <p className="xi-form-error" role="alert">
              {listError}
            </p>
            <div className="xi-row-actions" style={{ marginTop: 8 }}>
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                onClick={() => {
                  setListLoading(true);
                  setListError(null);
                  void refreshList();
                }}
              >
                Retry
              </button>
            </div>
          </div>
        )}
        {/* Stable per-page list anchor for e2e (always rendered, aria-live
          polite for list updates). Empty state is separate below. */}
        <ul id={listId} aria-live="polite" style={{ paddingLeft: 20 }}>
          {notes.map((note) => (
            <li key={note.id}>
              <p style={{ marginBottom: 2 }}>{note.text}</p>
              <p className="xi-hint" style={{ marginTop: 0 }}>
                {note.author_display} · {note.created_at} · {note.page}
              </p>
            </li>
          ))}
        </ul>
        {notes.length === 0 && listError === null && !listLoading && (
          <p className="xi-hint" id={emptyId}>
            No notes for this page yet.
          </p>
        )}
        {notes.length > 0 && listError === null && (
          <p className="xi-hint">
            {total} note{total === 1 ? "" : "s"} for this page.
          </p>
        )}
      </div>

      <div className="xi-field" style={{ marginTop: 12 }}>
        <label className="xi-label" htmlFor={inputId}>
          Page note for {page}
        </label>
        <textarea
          className="xi-input"
          id={inputId}
          rows={3}
          autoComplete="off"
          maxLength={2000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          aria-describedby={statusId}
        />
        <p className="xi-hint">At most 2000 characters. Empty notes are not saved.</p>
      </div>
      {fieldError !== null && (
        <p className="xi-form-error" role="alert">
          {fieldError}
        </p>
      )}
      <div className="xi-row-actions">
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          id={addId}
          disabled={addDisabled}
          onClick={() => void handleAdd()}
        >
          {saveState.kind === "saving" ? "Saving…" : "Add page note"}
        </button>
        {(saveState.kind === "failed" || saveState.kind === "conflict") && (
          <>
            {saveState.kind === "failed" && saveState.retryable && (
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                id={retryId}
                onClick={() => void handleRetry()}
              >
                Retry note save
              </button>
            )}
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              id={reloadId}
              onClick={() => void handleReload()}
            >
              Reload notes
            </button>
          </>
        )}
        {saveState.kind !== "failed" && saveState.kind !== "conflict" && (
          <>
            <span id={retryId} hidden />
            <span id={reloadId} hidden />
          </>
        )}
        {saveState.kind === "conflict" && <span id={retryId} hidden />}
      </div>
      {statusText !== "" ? (
        <p className={statusClass} role="status" id={statusId}>
          {statusText}
        </p>
      ) : (
        <p className="xi-hint" id={statusId} aria-live="polite">
          No note activity yet.
        </p>
      )}

      {saveState.kind === "conflict" && (
        <div
          className="xi-notice"
          role="alert"
          id={conflictId}
          aria-labelledby={`${conflictId}-heading`}
          style={{ marginTop: 12 }}
        >
          <h5
            id={`${conflictId}-heading`}
            tabIndex={-1}
            ref={conflictHeadingRef}
            style={{ margin: "0 0 8px", fontSize: 15 }}
          >
            Another tab saved first — your note text is kept
          </h5>
          <p style={{ marginBottom: 8 }}>
            The draft revision moved. Nothing was overwritten. Reload the list
            and retry with the current revision.
          </p>
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void handleReload()}
            >
              Reload notes
            </button>
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              onClick={() => void handleRetry()}
            >
              Keep my text and retry
            </button>
          </div>
        </div>
      )}
      {saveState.kind === "failed" && (
        <p className="xi-form-error" role="alert" id={conflictId} hidden={false}>
          Note save failed — your text is kept. Reload or retry.
        </p>
      )}
      {(saveState.kind === "idle" ||
        saveState.kind === "saving" ||
        saveState.kind === "saved") && (
        <span id={conflictId} hidden />
      )}
    </section>
  );
}
