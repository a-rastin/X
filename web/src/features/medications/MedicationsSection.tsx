/* Medications section with strict drug-only save + reconciliation (S20,
 * plan.md §§2.2, 6.3, 9; FR-14-16, FR-20).
 *
 * Wizard integration (not separate persistence): the selected list lives in
 * the shared S07 autosave body as `draft_data["medications"].entries` and
 * edits go through `setDraftValue` (same debounced PATCH + revision fence as
 * every other page), so resume after restart, the 412 keeps-edits path, and
 * the navigation warnings (beforeunload + in-app hash guard in
 * EncounterPage) all come free — there is exactly one persistence
 * mechanism. The S07 PATCH is structural only; the validating step is the
 * explicit strict POST below.
 *
 * Strict save ("Save medications"): flushes pending autosave edits, takes
 * the fresh revision from GET .../medications, then POSTs the drug-only
 * list ([{catalog_drug_id}] — forged dose/unit/route/frequency/status keys
 * are never sent and the server 422s them anyway) with If-Match + a fresh
 * Idempotency-Key + CSRF. Only a 2xx is durable ("Saved"); the posted
 * section (server-stamped provenance + settled reconciliation + pinned
 * report reference) merges back via `applySavedSlice` without touching
 * other keys. A 412 keeps every edit (they already live in autosave) and
 * offers Reload (adopt the server list) / Retry (fresh revision, new
 * attempt). Transport failures offer Retry of the same attempt (key reuse);
 * 409/422 are terminal with field details.
 *
 * Follow-up: copied entries carry copied_baseline provenance with a pending
 * reconciliation gate (see GET .../medications). The pending notice shows
 * an explicit "Reconcile medications" action that POSTs the current list
 * with {reconciliation: reconciled}; an ordinary save never clears the gate
 * (backend keeps the prior state when reconciliation is omitted). Previous
 * signed lists/reports are never touched — this POST only replaces the
 * draft section. The DDI panel below reports pending/reconciliation_required
 * until the gate clears.
 *
 * Catalog search is GET /drugs (demo catalog only, no free-text additions —
 * unknown labels are server 422, never stored). DDI evidence itself renders
 * in DdiReportPanel, refreshed after every acknowledged save/revision.
 *
 * Accessibility + theming: native label/input/list/button (keyboard free),
 * save status is an aria-live region, failures use role=alert with focus,
 * styling uses semantic tokens only (.xi-warning-panel for pending /
 * coverage notices, both themes). Source/user text renders escaped
 * (no dangerouslySetInnerHTML). No animation (global reduced-motion rule).
 *
 * E2E selector contract (for dev-test e2e/ddi.spec.ts):
 * - section heading `#medications-heading` ("Medications (step 7)")
 * - search input `#meds-search`, results `#meds-search-results`,
 *   per-row add `#meds-add-{index}` (aria-label "Add {name}")
 * - catalog versions `#meds-catalog-info`
 * - selected list `#meds-selected-list`, empty `#meds-selected-empty`,
 *   per-row remove `#meds-remove-{index}` (aria-label "Remove {id}")
 * - save button `#meds-save`, status `#meds-save-status` (aria-live),
 *   error `#meds-save-error` (role=alert)
 * - conflict panel `#meds-conflict` (role=alert) + `#meds-conflict-reload`
 *   + `#meds-conflict-retry`
 * - reconciliation notice `#meds-reconcile-status` + action
 *   `#meds-reconcile-action`
 * - DDI panel selectors live in DdiReportPanel (`#ddi-report-panel`,
 *   `#ddi-pending`, `#ddi-error`, `#ddi-retry`, `#ddi-report`,
 *   `#ddi-report-status`, `#ddi-report-pairs`, `#ddi-report-pair-{index}`,
 *   `#ddi-report-uncovered`, `#ddi-report-limitations`)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, newIdempotencyKey } from "../identity/api";
import type { DraftData } from "../encounters/api";
import type { useAutosave } from "../encounters/useAutosave";
import {
  getMedications,
  saveMedications,
  searchDrugs,
  type CatalogDrug,
  type DrugSearchResult,
  type MedicationEntry,
  type MedicationsPreview,
} from "./api";
import { DdiReportPanel } from "./DdiReportPanel";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface MedicationsSectionProps {
  encounterId: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
}

interface MedicationsSlice {
  entries: MedicationEntry[];
  provenance: Record<string, Record<string, unknown>>;
  reconciliation: MedicationsPreview["reconciliation"];
  dataset_version: unknown;
  catalog_version: unknown;
  medication_fingerprint: unknown;
  generated_at: unknown;
}

function sliceOf(data: DraftData): MedicationsSlice {
  const raw = data["medications"];
  if (typeof raw !== "object" || raw === null) {
    return {
      entries: [],
      provenance: {},
      reconciliation: null,
      dataset_version: null,
      catalog_version: null,
      medication_fingerprint: null,
      generated_at: null,
    };
  }
  const section = raw as Record<string, unknown>;
  const entries = section["entries"];
  const provenance = section["provenance"];
  return {
    entries: Array.isArray(entries) ? [...entries] : [],
    provenance:
      typeof provenance === "object" && provenance !== null
        ? { ...(provenance as Record<string, Record<string, unknown>>) }
        : {},
    reconciliation:
      (section["reconciliation"] as MedicationsSlice["reconciliation"]) ??
      null,
    dataset_version: section["dataset_version"] ?? null,
    catalog_version: section["catalog_version"] ?? null,
    medication_fingerprint: section["medication_fingerprint"] ?? null,
    generated_at: section["generated_at"] ?? null,
  };
}

/** Drug-only ids from the editor slice (non-string rows are dropped). */
function entryIds(entries: MedicationEntry[]): string[] {
  const ids: string[] = [];
  for (const entry of entries) {
    const id =
      typeof entry === "object" && entry !== null
        ? entry["catalog_drug_id"]
        : null;
    if (typeof id === "string" && id !== "" && !ids.includes(id)) {
      ids.push(id);
    }
  }
  return ids;
}

function sameIdSet(a: string[], b: string[]): boolean {
  if (a.length !== b.length) {
    return false;
  }
  const sortedA = [...a].sort();
  const sortedB = [...b].sort();
  return sortedA.every((id, index) => sortedB[index] === id);
}

/** Full section object as the strict save returns it (for applySavedSlice). */
function sectionOfPreview(preview: MedicationsPreview): Record<string, unknown> {
  return {
    entries: preview.entries,
    provenance: preview.provenance,
    reconciliation: preview.reconciliation,
    dataset_version: preview.dataset_version,
    catalog_version: preview.catalog_version,
    medication_fingerprint: preview.medication_fingerprint,
    generated_at: preview.generated_at,
  };
}

function provenanceSource(entry: unknown): string | null {
  if (typeof entry !== "object" || entry === null) {
    return null;
  }
  const source = (entry as Record<string, unknown>)["source"];
  return typeof source === "string" ? source : null;
}

type MedsSaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; revision: number; serverTimestamp: string | null }
  | { kind: "failed"; message: string; retryable: boolean }
  | { kind: "conflict"; message: string };

const SEARCH_DEBOUNCE_MS = 300;

export function MedicationsSection({
  encounterId,
  autosave,
  onSessionExpired,
}: MedicationsSectionProps) {
  const [serverState, setServerState] = useState<MedicationsPreview | null>(
    null,
  );
  const [serverLoading, setServerLoading] = useState(true);
  const [serverError, setServerError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<DrugSearchResult | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [saveState, setSaveState] = useState<MedsSaveState>({ kind: "idle" });
  const [busy, setBusy] = useState<"save" | "reconcile" | null>(null);
  const [postTick, setPostTick] = useState(0);
  const attemptKeyRef = useRef<string | null>(null);
  const alertRef = useRef<HTMLParagraphElement>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refreshServer = useCallback(async (): Promise<MedicationsPreview | null> => {
    try {
      const fresh = await getMedications(encounterId);
      if (!mountedRef.current) {
        return null;
      }
      setServerState(fresh);
      setServerError(null);
      return fresh;
    } catch (err) {
      if (!mountedRef.current) {
        return null;
      }
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return null;
      }
      setServerError(
        err instanceof ApiError
          ? err.message
          : "Medications failed to load. Check the connection and retry.",
      );
      return null;
    } finally {
      if (mountedRef.current) {
        setServerLoading(false);
      }
    }
  }, [encounterId, onSessionExpired]);

  // Server preview follows every acknowledged autosave (another tab or a
  // note POST may have moved the revision) and the initial mount.
  const savedRevision =
    autosave.saveState.kind === "saved" ? autosave.saveState.revision : 0;
  useEffect(() => {
    void refreshServer();
  }, [refreshServer, savedRevision]);

  // Demo-catalog search, debounced; empty query lists the first page.
  useEffect(() => {
    setSearchLoading(true);
    const timer = window.setTimeout(() => {
      searchDrugs(query, 25, 0)
        .then((result) => {
          if (mountedRef.current) {
            setSearch(result);
            setSearchError(null);
          }
        })
        .catch((err: unknown) => {
          if (!mountedRef.current) {
            return;
          }
          if (err instanceof ApiError && err.status === 401) {
            onSessionExpired();
            return;
          }
          setSearchError(
            err instanceof ApiError
              ? err.message
              : "Catalog search failed. Check the connection and retry.",
          );
        })
        .finally(() => {
          if (mountedRef.current) {
            setSearchLoading(false);
          }
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, onSessionExpired]);

  useEffect(() => {
    if (saveState.kind === "failed" || saveState.kind === "conflict") {
      alertRef.current?.focus();
    }
  }, [saveState.kind]);

  const slice = sliceOf(autosave.localData);
  const localIds = entryIds(slice.entries);
  const serverIds =
    serverState !== null ? entryIds(serverState.entries) : null;
  const medsDirty =
    serverIds === null ? localIds.length > 0 : !sameIdSet(localIds, serverIds);
  const autosaveSaving = autosave.saveState.kind === "saving";

  function writeEntries(ids: string[]): void {
    autosave.setDraftValue(
      "medications",
      { ...slice, entries: ids.map((id) => ({ catalog_drug_id: id })) },
    );
  }

  function addDrug(id: string): void {
    if (busy !== null || localIds.includes(id)) {
      return;
    }
    writeEntries([...localIds, id]);
  }

  function removeDrug(index: number): void {
    if (busy !== null) {
      return;
    }
    writeEntries(localIds.filter((_, i) => i !== index));
  }

  function failed(message: string, retryable: boolean): void {
    if (mountedRef.current) {
      attemptKeyRef.current = null;
      setSaveState({ kind: "failed", message, retryable });
    }
  }

  /** Strict drug-only save; `reconcile=true` settles a pending gate. */
  async function submitList(
    ids: string[],
    revision: number,
    key: string,
    reconcile: boolean,
  ): Promise<void> {
    const result = await saveMedications(
      encounterId,
      ids.map((id) => ({ catalog_drug_id: id })),
      revision,
      {
        idempotencyKey: key,
        ...(reconcile
          ? {
              reconciliation: {
                status: "reconciled" as const,
                baseline_encounter_id:
                  serverState?.reconciliation?.baseline_encounter_id ?? null,
              },
            }
          : {}),
      },
    );
    if (!mountedRef.current) {
      return;
    }
    attemptKeyRef.current = null;
    // Adopt the server-stamped section (provenance + pins + reconciliation)
    // without touching other draft keys — one fence, no phantom PATCH.
    autosave.applySavedSlice(
      "medications",
      sectionOfPreview(result),
      result.revision,
      result.server_timestamp ?? null,
    );
    setServerState(result);
    setServerError(null);
    setPostTick((tick) => tick + 1);
    setSaveState({
      kind: "saved",
      revision: result.revision,
      serverTimestamp: result.server_timestamp ?? null,
    });
  }

  async function handleSave(reconcile: boolean): Promise<void> {
    if (busy !== null) {
      return;
    }
    setBusy(reconcile ? "reconcile" : "save");
    setSaveState({ kind: "saving" });
    // Fresh attempt, fresh key; conflict retries below mint their own.
    const key = newIdempotencyKey();
    attemptKeyRef.current = key;
    try {
      // Flush pending draft edits so the fence sees the latest base, then
      // read the fresh revision for If-Match (a note POST from another
      // control may have moved it too).
      await autosave.flush();
      const fresh = await refreshServer();
      if (fresh === null) {
        failed("Could not read the current revision. Retry the save.", true);
        return;
      }
      await submitList(entryIds(sliceOf(autosave.localData).entries), fresh.revision, key, reconcile);
    } catch (err) {
      if (!mountedRef.current) {
        return;
      }
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 412) {
        // Stale fence: every edit already lives in autosave, nothing is
        // lost. Refresh the server list for the reconcile UI; Retry posts
        // the kept edits onto the fresh revision as a new attempt.
        setSaveState({ kind: "conflict", message: err.message });
        await refreshServer();
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        failed(err.message, false);
        return;
      }
      if (err instanceof ApiError && err.status === 422) {
        const details = Object.values(err.fieldErrors).flat().join(" ");
        failed(details !== "" ? details : err.message, false);
        return;
      }
      failed(
        err instanceof ApiError
          ? err.message
          : "Medication save failed. Check the connection and retry.",
        true,
      );
    } finally {
      if (mountedRef.current) {
        setBusy(null);
      }
    }
  }

  async function handleRetry(): Promise<void> {
    if (
      saveState.kind !== "failed" &&
      saveState.kind !== "conflict"
    ) {
      return;
    }
    const isConflict = saveState.kind === "conflict";
    setBusy("save");
    setSaveState({ kind: "saving" });
    try {
      // Transport-failure retry of the same attempt reuses the key so a lost
      // response replays instead of double-saving; a 412 conflict is a new
      // attempt on the fresh revision with a fresh key (same list kept).
      let key = attemptKeyRef.current;
      if (isConflict || key === null) {
        key = newIdempotencyKey();
        attemptKeyRef.current = key;
      }
      await autosave.flush();
      const fresh = await refreshServer();
      if (fresh === null) {
        failed("Could not read the current revision. Retry the save.", true);
        return;
      }
      await submitList(entryIds(sliceOf(autosave.localData).entries), fresh.revision, key, false);
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
        await refreshServer();
        return;
      }
      if (err instanceof ApiError && (err.status === 409 || err.status === 422)) {
        const details =
          err instanceof ApiError
            ? Object.values(err.fieldErrors).flat().join(" ")
            : "";
        failed(details !== "" ? details : err.message, false);
        return;
      }
      failed(
        err instanceof ApiError
          ? err.message
          : "Medication save failed. Check the connection and retry.",
        true,
      );
    } finally {
      if (mountedRef.current) {
        setBusy(null);
      }
    }
  }

  function handleReloadServer(): void {
    if (serverState === null) {
      void refreshServer();
      return;
    }
    // Adopt the server list into the editor (explicit overwrite); the shared
    // autosave persists it through the normal debounced PATCH.
    autosave.setDraftValue("medications", {
      ...slice,
      ...sectionOfPreview(serverState),
    });
    setSaveState({ kind: "idle" });
  }

  const reconcileStatus = serverState?.reconciliation?.status ?? null;
  const showReconcile = reconcileStatus === "pending";

  let statusText: string;
  let statusClass = "xi-hint";
  if (saveState.kind === "saving") {
    statusText = busy === "reconcile" ? "Reconciling…" : "Saving…";
  } else if (saveState.kind === "saved") {
    statusText = `Saved (revision ${saveState.revision})`;
    statusClass = "xi-status";
    if (saveState.serverTimestamp !== null) {
      statusText += ` · ${saveState.serverTimestamp}`;
    }
  } else if (saveState.kind === "failed") {
    statusText = `Save failed: ${saveState.message}`;
    statusClass = "xi-form-error";
  } else if (saveState.kind === "conflict") {
    statusText = `Conflict: ${saveState.message} Your list is kept.`;
    statusClass = "xi-form-error";
  } else if (medsDirty) {
    statusText = "Unsaved medication changes…";
  } else if (serverState !== null) {
    statusText = `Saved list matches revision ${serverState.revision}.`;
    statusClass = "xi-status";
  } else {
    statusText = "";
  }

  const saveDisabled =
    busy !== null || autosaveSaving || (serverIds !== null && !medsDirty);

  return (
    <section className="xi-card" aria-labelledby="medications-heading">
      <h3 className="xi-section-title" id="medications-heading">
        Medications (step 7)
      </h3>
      <p className="xi-hint">
        Demo-catalog medications only — no free-text additions and no
        dose, unit, route, frequency, or status fields. Selecting drugs edits
        the shared draft (autosaves like every other page);{" "}
        <strong>Save medications</strong> validates the drug-only list and pins
        the versioned DDI report below. Only a saved list counts as durable.
      </p>

      <div className="xi-field">
        <label className="xi-label" htmlFor="meds-search">
          Search demo catalog
        </label>
        <input
          className="xi-input"
          id="meds-search"
          type="search"
          autoComplete="off"
          placeholder="Type to filter catalog drugs"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-describedby="meds-catalog-info"
        />
        {search !== null && (
          <p className="xi-hint" id="meds-catalog-info">
            Catalog {search.catalog_version} · dataset {search.dataset_version}{" "}
            · {search.total} drug{search.total === 1 ? "" : "s"}
          </p>
        )}
      </div>

      {searchError !== null && (
        <p className="xi-form-error" role="alert">
          {searchError}
        </p>
      )}
      {searchLoading && search === null && <p>Loading catalog…</p>}
      {search !== null && (
        <ul id="meds-search-results" aria-label="Catalog search results">
          {search.items.map((drug: CatalogDrug, index: number) => {
            const selected = localIds.includes(drug.catalog_drug_id);
            return (
              <li key={drug.catalog_drug_id}>
                {drug.canonical_name} ({drug.catalog_drug_id}) ·{" "}
                {drug.concept_type}{" "}
                <button
                  className="xi-btn xi-btn-secondary"
                  type="button"
                  id={`meds-add-${index}`}
                  aria-label={`Add ${drug.canonical_name}`}
                  disabled={selected || busy !== null}
                  onClick={() => addDrug(drug.catalog_drug_id)}
                >
                  {selected ? "Added" : "Add"}
                </button>
              </li>
            );
          })}
          {search.items.length === 0 && (
            <li className="xi-hint">No catalog drugs match.</li>
          )}
        </ul>
      )}

      <h4 className="xi-section-title" style={{ fontSize: 15 }}>
        Selected medications
      </h4>
      {localIds.length === 0 ? (
        <p className="xi-hint" id="meds-selected-empty">
          No medications selected. Search the demo catalog above to add
          drugs — saving an empty list records an intentionally empty list.
        </p>
      ) : (
        <ul id="meds-selected-list" aria-label="Selected medications">
          {localIds.map((id, index) => {
            const source = provenanceSource(serverState?.provenance[id]);
            return (
              <li key={id}>
                {id}
                {source !== null && (
                  <span className="xi-hint"> · provenance {source}</span>
                )}{" "}
                <button
                  className="xi-btn xi-btn-secondary"
                  type="button"
                  id={`meds-remove-${index}`}
                  aria-label={`Remove ${id}`}
                  disabled={busy !== null}
                  onClick={() => removeDrug(index)}
                >
                  Remove
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {serverLoading && serverState === null && serverError === null && (
        <p>Loading saved medications…</p>
      )}
      {serverError !== null && serverState === null && (
        <div>
          <p className="xi-form-error" role="alert">
            {serverError}
          </p>
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => {
                setServerLoading(true);
                setServerError(null);
                void refreshServer();
              }}
            >
              Retry
            </button>
          </div>
        </div>
      )}

      <div className="xi-row-actions" style={{ marginTop: 12 }}>
        <button
          className="xi-btn xi-btn-primary"
          type="button"
          id="meds-save"
          disabled={saveDisabled}
          onClick={() => void handleSave(false)}
        >
          {busy === "save" ? "Saving…" : "Save medications"}
        </button>
        {(saveState.kind === "failed" || saveState.kind === "conflict") && (
          <>
            {saveState.kind === "failed" && saveState.retryable && (
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                onClick={() => void handleRetry()}
              >
                Retry save
              </button>
            )}
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void refreshServer()}
            >
              Reload server list
            </button>
          </>
        )}
      </div>
      {statusText !== "" ? (
        <p className={statusClass} id="meds-save-status" aria-live="polite">
          {statusText}
        </p>
      ) : (
        <p className="xi-hint" id="meds-save-status" aria-live="polite">
          No medication activity yet.
        </p>
      )}
      {(saveState.kind === "failed" || saveState.kind === "conflict") && (
        <p
          className="xi-form-error"
          id="meds-save-error"
          role="alert"
          tabIndex={-1}
          ref={alertRef}
        >
          {saveState.kind === "failed"
            ? `Medication save failed — your list is kept. ${saveState.message}`
            : `Another tab saved first — your list is kept. ${saveState.message}`}
        </p>
      )}

      {saveState.kind === "conflict" && (
        <div
          className="xi-notice"
          role="alert"
          id="meds-conflict"
          aria-labelledby="meds-conflict-heading"
          style={{ marginTop: 12 }}
        >
          <h5
            id="meds-conflict-heading"
            style={{ margin: "0 0 8px", fontSize: 15 }}
          >
            Another tab saved first — your list is kept
          </h5>
          {serverState !== null && (
            <p className="xi-hint" style={{ marginBottom: 8 }}>
              Server revision {serverState.revision}:{" "}
              {entryIds(serverState.entries).join(", ") || "(empty list)"}
            </p>
          )}
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              id="meds-conflict-reload"
              onClick={handleReloadServer}
            >
              Use server list
            </button>
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              id="meds-conflict-retry"
              onClick={() => void handleRetry()}
            >
              Keep my list and retry
            </button>
          </div>
        </div>
      )}

      {showReconcile && (
        <div className="xi-warning-panel" style={{ marginTop: 12 }}>
          <p id="meds-reconcile-status" style={{ marginTop: 0 }}>
            Copied medications need reconciliation — review the copied list
            above
            {serverState?.reconciliation?.baseline_encounter_id != null &&
            serverState?.reconciliation?.baseline_encounter_id !== ""
              ? ` (baseline ${serverState?.reconciliation?.baseline_encounter_id})`
              : ""}{" "}
            and reconcile explicitly. The DDI report stays pending until then;
            previous signed lists and reports are unchanged.
          </p>
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              id="meds-reconcile-action"
              disabled={busy !== null || autosaveSaving}
              onClick={() => void handleSave(true)}
            >
              {busy === "reconcile"
                ? "Reconciling…"
                : `Reconcile ${localIds.length} medication${localIds.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <DdiReportPanel
          encounterId={encounterId}
          refreshKey={`${savedRevision}:${postTick}`}
          onSessionExpired={onSessionExpired}
        />
      </div>
    </section>
  );
}
