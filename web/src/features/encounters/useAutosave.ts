/* Debounced draft autosave (S07, plan.md §2.3; FR-16, NFR-04).
 *
 * One observable contract: short debounce, flush on page transition, and
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
 * hash-route guard in the app).
 *
 * Command POSTs that bump the encounter revision outside this PATCH path
 * (diagnosis acknowledgment/bypass) resynchronize through
 * `applyServerSnapshot` instead of another PATCH — one saved base, one
 * revision fence, no separate persistence mechanism. S20
 * medications strict saves change only the medications slice outside PATCH
 * and resynchronize through `applySavedSlice` (same fence, other keys kept).
 */

import {reconcileAutosave} from "./reconcileAutosave";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, newIdempotencyKey } from "../identity/api";
import { getEncounter, patchEncounter, type DraftData } from "./api";

export const AUTOSAVE_DEBOUNCE_MS = 250;

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
  const attemptRef = useRef<{ key: string; data: DraftData; revision: number; seq: number } | null>(null);
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

  const updateSaveState = useCallback((next: SaveState) => {
    stateRef.current = next;
    setSaveState(next);
  }, []);

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
      const previous = opts.retrySameAttempt ? attemptRef.current : null;
      const data = previous?.data ?? localRef.current;
      const revision = previous?.revision ?? baseRef.current;
      const seqAtStart = previous?.seq ?? editSeqRef.current;
      const attempted: SaveAttemptResult = {
        outcome: "failed",
        revision,
        data,
        seqAtStart,
      };
      // A fresh attempt gets a fresh key; a retry of the same attempt
      // (transport failure, same body + same base revision) reuses it so a
      // lost response replays instead of double-bumping.
      const key = previous?.key ?? newIdempotencyKey();
      attemptRef.current = { key, data, revision, seq: seqAtStart };
      if (mountedRef.current) {
        updateSaveState({ kind: "saving" });
      }
      try {
        const result = await patchEncounter(encounterId, data, revision, {
          idempotencyKey: key,
        });
        if (!mountedRef.current) {
          return { outcome: "saved", revision: result.revision, data: result.draft_data, seqAtStart };
        }
        const acknowledged = reconcileAutosave(data, localRef.current, result.draft_data) as DraftData;
        localRef.current = acknowledged;
        setLocalData(acknowledged);
        attemptRef.current = null;
        baseRef.current = result.revision;
        setBaseRevision(result.revision);
        setServerTimestamp(result.server_timestamp ?? null);
        setServerTruth(null);
        savedSeqRef.current = seqAtStart;
        if (editSeqRef.current === seqAtStart) {
          updateSaveState({ kind: "saved", revision: result.revision, serverTimestamp: result.server_timestamp ?? null });
        } else {
          updateSaveState({ kind: "dirty" });
          // The current request has to settle before the next revision-fenced PATCH.
          window.setTimeout(() => {
            if (stateRef.current.kind === "dirty") void saveNow();
          }, 0);
        }
        return { outcome: "saved", revision: result.revision, data: result.draft_data, seqAtStart };
      } catch (err) {
        if (!mountedRef.current) {
          return attempted;
        }
        if (err instanceof ApiError && err.status === 401) {
          sessionGoneRef.current = true;
          updateSaveState({ kind: "failed", message: "Session expired. Sign in in another tab as the same physician, then retry save.", retryable: true });
          return { ...attempted, outcome: "session" };
        }
        if (err instanceof ApiError && err.status === 412) {
          // Stale tab: keep every keystroke, fetch server truth for the
          // reconcile UI, never overwrite the editor. The fresh revision is
          // adopted as the next save base so an explicit "Keep my edits and
          // retry" succeeds as a new attempt (never a silent overwrite: the
          // editor still shows the kept local edits until the user acts).
          attemptRef.current = null;
          updateSaveState({ kind: "conflict", message: err.message });
          try {
            const truth = await getEncounter(encounterId);
            if (mountedRef.current) {
              setServerTruth({
                draftData: truth.draft_data,
                revision: truth.revision,
              });
              baseRef.current = truth.revision;
              setBaseRevision(truth.revision);
            }
          } catch {
            // Truth fetch failed: the conflict message already keeps edits
            // safe; Retry/Reload remain available.
          }
          return { ...attempted, outcome: "conflict" };
        }
        if (err instanceof ApiError && err.status === 409) {
          attemptRef.current = null;
          updateSaveState({
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
        updateSaveState({ kind: "failed", message, retryable: true });
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
    [encounterId, updateSaveState],
  );

  const schedule = useCallback(() => {
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      // Only save when there is something unsaved; a conflict waits for an
      // explicit Retry/Reload instead of auto-overwriting.
      if (stateRef.current.kind !== "dirty") return;
      void saveNow();
    }, AUTOSAVE_DEBOUNCE_MS);
  }, [clearTimer, saveNow]);

  /** Merge one top-level key into the full draft object (later pages add
   * their own keys through this same setter). */
  const setDraftValue = useCallback(
    (key: string, value: unknown) => {
      editSeqRef.current += 1;
      localRef.current = { ...localRef.current, [key]: value };
      setLocalData(localRef.current);
      setServerTruth((previous) => previous);
      if (stateRef.current.kind !== "conflict" && stateRef.current.kind !== "failed") updateSaveState({ kind: "dirty" });
      schedule();
    },
    [schedule, updateSaveState],
  );

  const setDraftData = useCallback(
    (next: DraftData) => {
      editSeqRef.current += 1;
      localRef.current = next;
      setLocalData(next);
      if (stateRef.current.kind !== "conflict" && stateRef.current.kind !== "failed") updateSaveState({ kind: "dirty" });
      schedule();
    },
    [schedule, updateSaveState],
  );

  /** Pre-navigation save barrier:
   * waits for any in-flight write plus pending edits and reports an
   * explicit outcome. A success carries the saved base revision and the
   * draft snapshot it covers, so the caller can verify a later server read
   * against the exact saved answers. Attempts report their own outcome and
   * snapshot directly: the barrier never re-reads render-synced refs right
   * after an await. Edits made during a save are persisted in a follow-up
   * pass (bounded: continuous concurrent editing reports failure rather
   * than looping forever). */
  const ensureSaved = useCallback(async (): Promise<EnsureSavedResult> => {
    clearTimer();
    for (let attempt = 0; attempt < 5; attempt += 1) {
      if (sessionGoneRef.current) {
        return { ok: false, reason: "session" };
      }
      if (stateRef.current.kind === "conflict") {
        return { ok: false, reason: "conflict" };
      }
      if (stateRef.current.kind === "failed") return { ok: false, reason: "failed" };
      if (
        stateRef.current.kind === "saved" &&
        editSeqRef.current === savedSeqRef.current
      ) {
        return { ok: true, revision: baseRef.current, data: localRef.current };
      }
      const result = await saveNow();
      if (result.outcome === "session") {
        return { ok: false, reason: "session" };
      }
      if (result.outcome === "conflict") {
        return { ok: false, reason: "conflict" };
      }
      if (result.outcome === "failed") {
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

  const flush = useCallback(async (): Promise<void> => {
    const result = await ensureSaved();
    if (!result.ok) throw new Error("Save the draft successfully before continuing. Your edits are kept.");
  }, [ensureSaved]);

  const retry = useCallback(async (): Promise<void> => {
    clearTimer();
    sessionGoneRef.current = false;
    const result = await saveNow({ retrySameAttempt: true });
    if (result.outcome === "saved" && editSeqRef.current !== savedSeqRef.current) {
      await ensureSaved();
    }
  }, [clearTimer, ensureSaved, saveNow]);

  /** Reload server truth without losing the editor (reconcile step). */
  const reloadTruth = useCallback(async (): Promise<void> => {
    try {
      const truth = await getEncounter(encounterId);
      if (!mountedRef.current) {
        return;
      }
      setServerTruth({ draftData: truth.draft_data, revision: truth.revision });
      baseRef.current = truth.revision;
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
        localRef.current = truth.draftData;
        baseRef.current = truth.revision;
        setLocalData(truth.draftData);
        setBaseRevision(truth.revision);
        updateSaveState({
          kind: "saved",
          revision: truth.revision,
          serverTimestamp,
        });
      }
      return null;
    });
  }, [serverTimestamp, updateSaveState]);

  /** Retry local edits onto the fresh base revision (explicit overwrite of
   * server truth with the kept edits — never silent). */
  const retryOnFreshRevision = useCallback(async (): Promise<void> => {
    clearTimer();
    attemptRef.current = null;
    await saveNow();
  }, [clearTimer, saveNow]);

  /** Adopt an explicit server snapshot after a command POST bumped the
   * revision outside this PATCH path (diagnosis acknowledgment/bypass).
   * The fetched server truth becomes the new saved base directly — no extra
   * PATCH, so the revision fence stays exact for the next autosave. */
  const applyServerSnapshot = useCallback(
    (draftData: DraftData, revision: number, timestamp: string | null) => {
      clearTimer();
      attemptRef.current = null;
      savedSeqRef.current = editSeqRef.current;
      localRef.current = draftData;
      baseRef.current = revision;
      setLocalData(draftData);
      setBaseRevision(revision);
      setServerTimestamp(timestamp);
      setServerTruth(null);
      updateSaveState({ kind: "saved", revision, serverTimestamp: timestamp });
    },
    [clearTimer, updateSaveState],
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
      // Merge the server-stamped slice without marking other local edits saved.
      localRef.current = { ...localRef.current, [key]: value };
      baseRef.current = revision;
      setLocalData(localRef.current);
      setBaseRevision(revision);
      setServerTimestamp(timestamp);
      if (stateRef.current.kind === "saved") updateSaveState({ kind: "saved", revision, serverTimestamp: timestamp });
    },
    [updateSaveState],
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
