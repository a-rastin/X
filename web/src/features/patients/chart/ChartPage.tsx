/* Shared patient chart + follow-up entry (S14, plan.md §§2.2-2.3; FR-20-23).
 *
 * Any physician reads the same chart: demographics (escaped React text
 * nodes), signed chronology (honest empty state — signing is later scope,
 * so no fake rows), an occupancy-only open-draft badge (no author, no
 * content, no revision) and an honest unavailable-proposal note (generation
 * is not implemented — never a fake recommendation). "Start follow-up"
 * creates a plain follow-up draft (no baseline) and navigates to the wizard;
 * an occupied slot is the generic 409 with no leak. Loading/error states
 * carry Retry; 401 expires the session, 403/404 render fixed denial and
 * not-found copy rather than server text.
 *
 * E2E selector contract (for e2e/followup.spec.ts):
 * - heading (#chart-heading), demographics (#chart-demographics),
 *   chronology (#chart-chronology, empty #chart-chronology-empty),
 *   draft badge (#chart-draft-badge), proposal (#chart-proposal-status),
 *   error (#chart-error) + retry (#chart-retry)
 * - start follow-up (#followup-start), conflict (#followup-error, role=alert)
 * - back to directory (#chart-back), author resume (#chart-open-draft)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../identity/api";
import { useAuth } from "../../identity/auth";
import { createEncounter, encounterHash } from "../../encounters/api";
import {
  getChart,
  recallOpenDraft,
  rememberOpenDraft,
  type ChartPayload,
} from "./api";

type ChartState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; chart: ChartPayload };

const DENIED_MESSAGE = "Only physicians can view the shared chart.";
const NOT_FOUND_MESSAGE = "Patient not found. It may have been removed.";
const OCCUPIED_MESSAGE =
  "An open draft already exists for this patient. Ask its author to resume it.";

export function ChartPage({ patientId }: { patientId: string }) {
  const { user, sessionExpired } = useAuth();
  const [state, setState] = useState<ChartState>({ kind: "loading" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const createErrorRef = useRef<HTMLParagraphElement>(null);

  const load = useCallback(async () => {
    if (user?.role !== "physician") {
      // Client-side complement to the server's 403: never grant authority,
      // just avoid fetching clinical routes as the wrong role.
      setState({ kind: "error", message: DENIED_MESSAGE });
      return;
    }
    setState({ kind: "loading" });
    try {
      const chart = await getChart(patientId);
      setState({ kind: "ready", chart });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setState({ kind: "error", message: DENIED_MESSAGE });
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        setState({ kind: "error", message: NOT_FOUND_MESSAGE });
        return;
      }
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Patient chart failed to load. Check the connection and retry.",
      });
    }
  }, [patientId, sessionExpired, user?.role]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (state.kind === "error") {
      errorRef.current?.focus();
    }
  }, [state.kind]);

  useEffect(() => {
    if (createError !== null) {
      createErrorRef.current?.focus();
    }
  }, [createError]);

  async function startFollowUp(): Promise<void> {
    if (creating) {
      return;
    }
    setCreateError(null);
    setCreating(true);
    try {
      const result = await createEncounter(patientId, "follow_up");
      rememberOpenDraft(patientId, result.encounter.id);
      window.location.hash = encounterHash(result.encounter.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        // Generic slot conflict: no author, no clinical content.
        setCreateError(OCCUPIED_MESSAGE);
        return;
      }
      setCreateError(
        err instanceof ApiError
          ? err.message
          : "Could not start a follow-up draft. Check the connection and retry.",
      );
    } finally {
      setCreating(false);
    }
  }

  if (state.kind === "loading") {
    return (
      <div>
        <h2 className="xi-page-title" id="chart-heading">
          Patient chart
        </h2>
        <p>Loading chart…</p>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div>
        <h2 className="xi-page-title" id="chart-heading">
          Patient chart
        </h2>
        <p
          id="chart-error"
          className="xi-form-error"
          role="alert"
          tabIndex={-1}
          ref={errorRef}
        >
          {state.message}
        </p>
        <div className="xi-row-actions">
          <button
            id="chart-retry"
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => void load()}
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

  const chart = state.chart;
  const storedDraftId = recallOpenDraft(patientId);

  return (
    <div>
      <h2 className="xi-page-title" id="chart-heading">
        Patient chart
      </h2>

      <section className="xi-card" aria-labelledby="chart-demographics-heading">
        <h3 className="xi-section-title" id="chart-demographics-heading">
          Demographics
        </h3>
        <div id="chart-demographics">
          <dl>
            <div>
              <dt>Patient ID</dt>
              <dd>{chart.patient.identifier}</dd>
            </div>
            <div>
              <dt>Given name</dt>
              <dd>{chart.patient.given_name}</dd>
            </div>
            <div>
              <dt>Family name</dt>
              <dd>{chart.patient.family_name}</dd>
            </div>
            <div>
              <dt>Sex</dt>
              <dd>{chart.patient.sex}</dd>
            </div>
            <div>
              <dt>Age</dt>
              <dd>{chart.patient.age}</dd>
            </div>
            <div>
              <dt>Clinical status</dt>
              <dd>
                {chart.patient.clinical_status === "first_time"
                  ? "First-time"
                  : "Established"}
              </dd>
            </div>
            {chart.patient.phone !== null && (
              <div>
                <dt>Phone</dt>
                <dd>{chart.patient.phone}</dd>
              </div>
            )}
          </dl>
        </div>
      </section>

      <section className="xi-card" aria-labelledby="chart-chronology-heading">
        <h3 className="xi-section-title" id="chart-chronology-heading">
          Signed chronology
        </h3>
        <div id="chart-chronology">
          {chart.chronology.length === 0 ? (
            <p id="chart-chronology-empty">No signed encounters yet.</p>
          ) : (
            <ul>
              {chart.chronology.map((ref) => (
                <li key={ref.id}>
                  {ref.kind} — updated {ref.updated_at}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="xi-card" aria-labelledby="chart-draft-heading">
        <h3 className="xi-section-title" id="chart-draft-heading">
          Open draft
        </h3>
        <p id="chart-draft-badge" role="status">
          {chart.open_draft.exists ? "Open draft exists" : "No open draft"}
        </p>
        {chart.open_draft.exists && (
          <p className="xi-hint">
            Occupancy only: draft content is visible to its author alone.
          </p>
        )}
      </section>

      <section className="xi-card" aria-labelledby="chart-proposal-heading">
        <h3 className="xi-section-title" id="chart-proposal-heading">
          Proposal
        </h3>
        <p id="chart-proposal-status">
          No proposal yet — generation not implemented.
        </p>
      </section>

      <section className="xi-card" aria-labelledby="chart-followup-heading">
        <h3 className="xi-section-title" id="chart-followup-heading">
          Follow-up
        </h3>
        <div className="xi-row-actions">
          <button
            id="followup-start"
            className="xi-btn xi-btn-primary"
            type="button"
            disabled={creating}
            onClick={() => void startFollowUp()}
          >
            {creating ? "Starting…" : "Start follow-up"}
          </button>
          {chart.open_draft.exists && storedDraftId !== null && (
            <a
              id="chart-open-draft"
              className="xi-btn xi-btn-secondary"
              href={encounterHash(storedDraftId)}
            >
              Open draft
            </a>
          )}
        </div>
        {createError !== null && (
          <p
            id="followup-error"
            className="xi-form-error"
            role="alert"
            tabIndex={-1}
            ref={createErrorRef}
          >
            {createError}
          </p>
        )}
      </section>

      <p>
        <a id="chart-back" className="xi-btn xi-btn-secondary" href="#/patients">
          Back to directory
        </a>
      </p>
    </div>
  );
}
