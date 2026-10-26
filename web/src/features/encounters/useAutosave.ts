/* Debounced draft autosave (S07, plan.md §2.3; FR-16, NFR-04).
 *
 * One observable contract: ~1s debounce, flush on page transition, and
 * saving/saved/failed states where only a 2xx response is "Saved". Later
 * assessment pages extend `draft_data` keys through this same path instead
 * of inventing separate persistence mechanisms — the hook always sends the
 * full object on each save.
 *
 * Stale-tab (412): the failed write changes nothing server-side, so local
 * edits are kept and the caller reconciles against GET server truth. The
 * hook fetches that truth for display but never overwrites the editor
 * silently — the conflict UI decides (Reload shows server truth, Retry
 * saves local edits onto the fresh revision as an explicit new attempt).
 *
 * Failed network saves never show Saved: transport/4xx/5xx leave the
 * revision untouched and surface as failed with Retry. Navigation warns
 * while local edits remain via `hasUnsaved` (beforeunload + in-app
 * hash-route guard in the page component).
 *
 * Command POSTs that bump the encounter revision outside this PATCH path
 * (diagnosis acknowledgment/bypass) resynchronize through
 * `applyServerSnapshot` instead of another PATCH — one saved base, one
 * revision fence, no separate persistence mechanism. S20
 * medications strict saves change only the medications slice outside PATCH
 * and resynchronize through `applySavedSlice` (same fence, other keys kept).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, newIdempotencyKey } from "../identity/api";
import { getEncounter, patchEncounter, type DraftData } from "./api";

export const AUTOSAVE_DEBOUNCE_MS = 1000;

export type SaveState =
  | { kind: "saved"; revision: number; serverTimestamp: string | null }
  | { kind: "saving" }
  | { kind: "dirty" }
  | { kind: "failed"; message: string; retryable: boolean }
  | { kind: "conflict"; message: string };

export interface ServerTruth {
  draftData: DraftData;
  revision: number;
}

/** Outcome of one save attempt. Success carries the persisted snapshot and
 * its new revision directly, so barriers never re-read render-synced refs
 * after an await (a re-render may not have flushed yet). */
export interface SaveAttemptResult {
  outcome: "saved" | "failed" | "conflict" | "session";
  /** New revision on "saved"; the attempted base otherwise. */
  revision: number;
  /** Persisted snapshot on "saved"; the attempted snapshot otherwise. */
  data: DraftData;
  /** Edit counter at capture: a higher counter after the attempt means the
   * user edited during the save and another pass is needed. */
  seqAtStart: number;
}

/** Result of the pre-navigation save barrier: an explicit outcome, never a
 * best-effort guess. Success carries the saved base revision and the draft
 * snapshot it covers so the caller can verify a later server read against
 * the exact answers that were saved. */
export type EnsureSavedResult =
  | { ok: true; revision: number; data: DraftData }
  | { ok: false; reason: "conflict" | "failed" | "session" };

interface UseAutosaveOptions {
  encounterId: string;
  initialData: DraftData;
  initialRevision: number;
  initialServerTimestamp?: string | null;
  onSessionExpired: () => void;
}

export function useAutosave(options: UseAutosaveOptions) {
  const { encounterId, initialRevision, initialServerTimestamp, onSessionExpired } =
    options;
  const [localData, setLocalData] = useState<DraftData>(options.initialData);
  const [baseRevision, setBaseRevision] = useState<number>(initialRevision);
  const [serverTimestamp, setServerTimestamp] = useState<string | null>(
    initialServerTimestamp ?? null,
  );
  const [saveState, setSaveState] = useState<SaveState>({
    kind: "saved",
    revision: initialRevision,
    serverTimestamp: initialServerTimestamp ?? null,
  });
  const [serverTruth, setServerTruth] = useState<ServerTruth | null>(null);

  const localRef = useRef<DraftData>(options.initialData);
  const baseRef = useRef<number>(initialRevision);
  const stateRef = useRef<SaveState>(saveState);
  const timerRef = useRef<number | null>(null);
  const attemptKeyRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  /** Latest in-flight PATCH, so the save barrier can wait for it instead of
   * equating a resolved promise with saved current data. */
  const inflightRef = useRef<Promise<SaveAttemptResult> | null>(null);
  /** Set when a save fails with 401: the session is gone, so the barrier
   * must report failure instead of spinning on a state that stays "saving". */
  const sessionGoneRef = useRef(false);
  /** Synchronous edit counter: incremented on every local edit before React
   * re-renders, so the barrier can tell edits-during-save apart without
   * trusting render-synced refs right after an await. */
  const editSeqRef = useRef(0);
  /** Edit counter value covered by the last acknowledged save. */
  const savedSeqRef = useRef(0);

  // Keep refs in sync without re-subscribing timers.
  localRef.current = localData;
  baseRef.current = baseRevision;
  stateRef.current = saveState;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const isDirty = useCallback((): boolean => {
    const kind = stateRef.current.kind;
    return kind === "dirty" || kind === "saving" || kind === "failed" || kind === "conflict";
  }, []);

  /** Public unsaved flag for navigation guards: any local edits not yet
   * acknowledged by a 2xx remain warn-worthy. */
  const hasUnsaved = isDirty();

  const saveNow = useCallback(
    async (opts: { retrySameAttempt?: boolean } = {}): Promise<SaveAttemptResult> => {
      // Coalesce concurrent saves: a second caller waits for the in-flight
      // write instead of racing it with the same If-Match base.
      if (inflightRef.current !== null) {
        return inflightRef.current;
      }
      const run = (async (): Promise<SaveAttemptResult> => {
      const data = localRef.current;
      const revision = baseRef.current;
      const seqAtStart = editSeqRef.current;
      const attempted: SaveAttemptResult = {
        outcome: "failed",
        revision,
        data,
        seqAtStart,
      };
      // A fresh attempt gets a fresh key; a retry of the same attempt
      // (transport failure, same body + same base revision) reuses it so a
      // lost response replays instead of double-bumping.
      const key =
        opts.retrySameAttempt && attemptKeyRef.current !== null
          ? attemptKeyRef.current
          : newIdempotencyKey();
      attemptKeyRef.current = key;
      if (mountedRef.current) {
        setSaveState({ kind: "saving" });
      }
      try {
        const result = await patchEncounter(encounterId, data, revision, {
          idempotencyKey: key,
        });
        if (!mountedRef.current) {
          return { outcome: "saved", revision: result.revision, data, seqAtStart };
        }
        attemptKeyRef.current = null;
        setBaseRevision(result.revision);
        setServerTimestamp(result.server_timestamp ?? null);
        setServerTruth(null);
        setSaveState({
          kind: "saved",
          revision: result.revision,
          serverTimestamp: result.server_timestamp ?? null,
        });
        // Normal debounce/flush saves also cover the snapshot when no edits
        // landed during the write; otherwise the barrier needs another pass.
        // Revision-fenced acknowledgment stays exact: only the covered
        // snapshot is marked, never concurrent edits.
        if (editSeqRef.current === seqAtStart) {
          savedSeqRef.current = seqAtStart;
        }
        return { outcome: "saved", revision: result.revision, data, seqAtStart };
      } catch (err) {
        if (!mountedRef.current) {
          return attempted;
        }
        if (err instanceof ApiError && err.status === 401) {
          sessionGoneRef.current = true;
          onSessionExpired();
          return { ...attempted, outcome: "session" };
        }
        if (err instanceof ApiError && err.status === 412) {
          // Stale tab: keep every keystroke, fetch server truth for the
          // reconcile UI, never overwrite the editor. The fresh revision is
          // adopted as the next save base so an explicit "Keep my edits and
          // retry" succeeds as a new attempt (never a silent overwrite: the
          // editor still shows the kept local edits until the user acts).
          setSaveState({ kind: "conflict", message: err.message });
          try {
            const truth = await getEncounter(encounterId);
            if (mountedRef.current) {
              setServerTruth({
                draftData: truth.draft_data,
                revision: truth.revision,
              });
              setBaseRevision(truth.revision);
            }
          } catch {
            // Truth fetch failed: the conflict message already keeps edits
            // safe; Retry/Reload remain available.
          }
          return { ...attempted, outcome: "conflict" };
        }
        if (err instanceof ApiError && err.status === 409) {
          attemptKeyRef.current = null;
          setSaveState({
            kind: "failed",
            message: err.message,
            retryable: false,
          });
          return attempted;
        }
        const message =
          err instanceof ApiError
            ? err.message
            : "Save failed. Check the connection and retry.";
        setSaveState({ kind: "failed", message, retryable: true });
        return attempted;
      }
      })();
      inflightRef.current = run;
      try {
        return await run;
      } finally {
        if (inflightRef.current === run) {
          inflightRef.current = null;
        }
      }
    },
    [encounterId, onSessionExpired],
  );

  const schedule = useCallback(() => {
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      // Only save when there is something unsaved; a conflict waits for an
      // explicit Retry/Reload instead of auto-overwriting.
      if (stateRef.current.kind === "conflict") {
        return;
      }
      void saveNow();
    }, AUTOSAVE_DEBOUNCE_MS);
  }, [clearTimer, saveNow]);

  /** Merge one top-level key into the full draft object (later pages add
   * their own keys through this same setter). */
  const setDraftValue = useCallback(
    (key: string, value: unknown) => {
      editSeqRef.current += 1;
      setLocalData((previous) => ({ ...previous, [key]: value }));
      attemptKeyRef.current = null;
      setServerTruth((previous) => previous);
      setSaveState((previous) =>
        previous.kind === "conflict" ? previous : { kind: "dirty" },
      );
      schedule();
    },
    [schedule],
  );

  const setDraftData = useCallback(
    (next: DraftData) => {
      editSeqRef.current += 1;
      setLocalData(next);
      attemptKeyRef.current = null;
      setSaveState((previous) =>
        previous.kind === "conflict" ? previous : { kind: "dirty" },
      );
      schedule();
    },
    [schedule],
  );

  /** Page-transition flush: persist pending edits immediately (best effort;
   * navigation still warns via the guard when edits remain). */
  const flush = useCallback(async (): Promise<void> => {
    clearTimer();
    if (
      stateRef.current.kind === "dirty" ||
      stateRef.current.kind === "failed"
    ) {
      await saveNow();
      return;
    }
    return;
  }, [clearTimer, saveNow]);

  /** Explicit retry after failure: same attempt reuses the key. */
  const retry = useCallback(async (): Promise<void> => {
    clearTimer();
    await saveNow({ retrySameAttempt: true });
  }, [clearTimer, saveNow]);

  /** Pre-navigation save barrier (registration Diagnosis Continue only):
   * waits for any in-flight write plus pending edits and reports an
   * explicit outcome. A success carries the saved base revision and the
   * draft snapshot it covers, so the caller can verify a later server read
   * against the exact saved answers. Attempts report their own outcome and
   * snapshot directly: the barrier never re-reads render-synced refs right
   * after an await. Edits made during a save are persisted in a follow-up
   * pass (bounded: continuous concurrent editing reports failure rather
   * than looping forever). Existing `flush()` behavior for other callers
   * is unchanged. */
  const ensureSaved = useCallback(async (): Promise<EnsureSavedResult> => {
    clearTimer();
    for (let attempt = 0; attempt < 5; attempt += 1) {
      if (sessionGoneRef.current) {
        return { ok: false, reason: "session" };
      }
      if (stateRef.current.kind === "conflict") {
        return { ok: false, reason: "conflict" };
      }
      if (
        stateRef.current.kind === "saved" &&
        editSeqRef.current === savedSeqRef.current
      ) {
        return { ok: true, revision: baseRef.current, data: localRef.current };
      }
      const editsBefore = editSeqRef.current;
      const result = await saveNow();
      if (result.outcome === "session") {
        return { ok: false, reason: "session" };
      }
      if (result.outcome === "conflict") {
        return { ok: false, reason: "conflict" };
      }
      if (result.outcome === "failed") {
        // New edits arrived during the failed write: retry once bounded,
        // otherwise report so the caller shows its retry action.
        if (editSeqRef.current !== editsBefore) {
          continue;
        }
        return { ok: false, reason: "failed" };
      }
      if (editSeqRef.current !== result.seqAtStart) {
        continue;
      }
      savedSeqRef.current = result.seqAtStart;
      return { ok: true, revision: result.revision, data: result.data };
    }
    if (sessionGoneRef.current) {
      return { ok: false, reason: "session" };
    }
    if (stateRef.current.kind === "conflict") {
      return { ok: false, reason: "conflict" };
    }
    return { ok: false, reason: "failed" };
  }, [clearTimer, saveNow]);

  /** Reload server truth without losing the editor (reconcile step). */
  const reloadTruth = useCallback(async (): Promise<void> => {
    try {
      const truth = await getEncounter(encounterId);
      if (!mountedRef.current) {
        return;
      }
      setServerTruth({ draftData: truth.draft_data, revision: truth.revision });
      setBaseRevision(truth.revision);
      // Keep the editor untouched: the user reconciles explicitly. The
      // conflict stays until they retry (new attempt on the fresh base) or
      // adopt the server version.
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
      }
    }
  }, [encounterId, onSessionExpired]);

  /** Adopt the server version into the editor (explicit overwrite). */
  const adoptServerVersion = useCallback(() => {
    setServerTruth((truth) => {
      if (truth !== null) {
        savedSeqRef.current = editSeqRef.current;
        setLocalData(truth.draftData);
        setBaseRevision(truth.revision);
        setSaveState({
          kind: "saved",
          revision: truth.revision,
          serverTimestamp,
        });
      }
      return null;
    });
  }, [serverTimestamp]);

  /** Retry local edits onto the fresh base revision (explicit overwrite of
   * server truth with the kept edits — never silent). */
  const retryOnFreshRevision = useCallback(async (): Promise<void> => {
    clearTimer();
    attemptKeyRef.current = null;
    await saveNow();
  }, [clearTimer, saveNow]);

  /** Adopt an explicit server snapshot after a command POST bumped the
   * revision outside this PATCH path (diagnosis acknowledgment/bypass).
   * The fetched server truth becomes the new saved base directly — no extra
   * PATCH, so the revision fence stays exact for the next autosave. */
  const applyServerSnapshot = useCallback(
    (draftData: DraftData, revision: number, timestamp: string | null) => {
      clearTimer();
      attemptKeyRef.current = null;
      savedSeqRef.current = editSeqRef.current;
      setLocalData(draftData);
      setBaseRevision(revision);
      setServerTimestamp(timestamp);
      setServerTruth(null);
      setSaveState({ kind: "saved", revision, serverTimestamp: timestamp });
    },
    [clearTimer],
  );

  /** Adopt one server-stamped slice after a command POST changed only that
   * key outside this PATCH path (S20 medications strict save stamps
   * provenance + pins + reconciliation server-side). Merges the server
   * section into the editor without touching other keys: the fence advances,
   * and a currently-saved status advances with it — dirty/failed/conflict
   * states keep their kind so their own reconcile path still runs and any
   * pending debounce still saves onto the fresh base. */
  const applySavedSlice = useCallback(
    (
      key: string,
      value: unknown,
      revision: number,
      timestamp: string | null,
    ) => {
      // Server-stamped merge, not a local edit: the saved counter follows
      // so the barrier treats the merged snapshot as covered when saved.
      savedSeqRef.current = editSeqRef.current;
      setLocalData((previous) => ({ ...previous, [key]: value }));
      setBaseRevision(revision);
      setServerTimestamp(timestamp);
      setSaveState((previous) =>
        previous.kind === "saved"
          ? { kind: "saved", revision, serverTimestamp: timestamp }
          : previous,
      );
    },
    [],
  );

  // Flush pending edits when the page unmounts (hash-route transition).
  useEffect(() => {
    return () => {
      clearTimer();
    };
  }, [clearTimer]);

  return {
    localData,
    setDraftValue,
    setDraftData,
    baseRevision,
    serverTimestamp,
    saveState,
    serverTruth,
    hasUnsaved,
    isDirty,
    flush,
    ensureSaved,
    retry,
    reloadTruth,
    adoptServerVersion,
    retryOnFreshRevision,
    applyServerSnapshot,
    applySavedSlice,
    saveNow,
  };
}
