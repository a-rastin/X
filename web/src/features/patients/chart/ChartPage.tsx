/* Shared patient chart (S14, plan.md §§2.2-2.3, 9; FR-20-23).
 *
 * Route: `#/patients/:id/chart` (see app/router.ts — patient-scoped shared
 * state, mirroring `GET /patients/{id}/chart`). Readable by any active
 * authenticated user (physician or administrator, like the S06 directory
 * reads); the server stays authoritative. The chart carries demographics +
 * signed chronology + slot badge + honestly-unavailable proposal — never
 * draft clinical content, no author oracle, no revision (transitive privacy:
 * only `open_draft.exists`, so stranger-draft content never leaks through
 * this surface, the directory, or reports).
 *
 * Follow-up entry reuses the S07 single-slot POST
 * (`POST /patients/{id}/encounters {kind: follow_up}` with a fresh
 * Idempotency-Key + CSRF + credentials:include): free slot creates and
 * navigates to `#/encounters/:id`; an occupied slot is the generic 409
 * conflict with no author/content; different patients stay independent
 * (per-patient slot). No baseline snapshot is sent from this surface yet —
 * signing (S49) owns signed baselines, so the fresh follow-up starts with
 * the backend's empty history shell until a signed baseline exists to copy.
 *
 * Honest unavailable: until reasoning exists the proposal reports
 * `generation_not_implemented` — shown verbatim, never a fake successful
 * proposal. History/medications/effects detail lives in the author-only
 * encounter wizard (S12/S13 separation: notes never mix into history);
 * the chart links there without exposing content.
 *
 * Accessibility + theming: native headings/lists/links (keyboard free),
 * loading/empty/error states from the real endpoint with Retry, badge keeps
 * role=status, errors use role=alert, styling uses semantic tokens only
 * (both themes; proposal uses .xi-notice, never color alone).
 *
 * E2E selector contract (for the test stage's e2e/followup.spec.ts):
 * - heading `data-testid="chart-heading"` (`#chart-heading`)
 * - draft badge `data-testid="chart-draft-badge"` (`#chart-draft-badge`, role=status)
 * - proposal `data-testid="chart-proposal-unavailable"` (`#chart-proposal-unavailable`)
 * - create button `data-testid="chart-create-followup"` (`#chart-create-followup`)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../identity/api";
import { useAuth } from "../../identity/auth";
import {
  createEncounter,
  encounterHash,
} from "../../encounters/api";
import { chartHash } from "../../../app/router";
import { getChart, type ChartResponse } from "./api";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string; status: number | null }
  | { kind: "ready"; chart: ChartResponse };

function statusLabel(status: string): string {
  return status === "first_time" ? "First-time" : "Established";
}

export function ChartPage({ patientId }: { patientId: string }) {
  const { user, sessionExpired } = useAuth();
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const createErrorRef = useRef<HTMLParagraphElement>(null);

  const reload = useCallback(async () => {
    setLoad({ kind: "loading" });
    try {
      const chart = await getChart(patientId);
      setLoad({ kind: "ready", chart });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        setLoad({
          kind: "error",
          message: "Patient not found.",
          status: 404,
        });
        return;
      }
      setLoad({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Chart failed to load. Check the connection and retry.",
        status: err instanceof ApiError ? err.status : null,
      });
    }
  }, [patientId, sessionExpired]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (createError !== null) {
      createErrorRef.current?.focus();
    }
  }, [createError]);

  async function startFollowUp(): Promise<void> {
    if (createBusy) {
      return;
    }
    setCreateError(null);
    setCreateBusy(true);
    try {
      // Fresh key per attempt inside createEncounter; no baseline snapshot
      // until signing (S49) provides a signed baseline to copy.
      const result = await createEncounter(patientId, "follow_up");
      window.location.hash = encounterHash(result.encounter.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        // Generic slot conflict: no author, no clinical content.
        setCreateError(
          "An open draft already exists for this patient. Ask its author to resume it.",
        );
        return;
      }
      setCreateError(
        err instanceof ApiError
          ? err.message
          : "Could not start a follow-up draft. Check the connection and retry.",
      );
    } finally {
      setCreateBusy(false);
    }
  }

  const canCreateFollowup = user?.role === "physician";

  if (load.kind === "loading") {
    return (
      <div>
        <h2
          className="xi-page-title"
          data-testid="chart-heading"
          id="chart-heading"
        >
          Patient chart
        </h2>
        <p>Loading chart…</p>
      </div>
    );
  }

  if (load.kind === "error") {
    return (
      <div>
        <h2
          className="xi-page-title"
          data-testid="chart-heading"
          id="chart-heading"
        >
          Patient chart
        </h2>
        <p className="xi-form-error" role="alert">
          {load.message}
        </p>
        <div className="xi-row-actions">
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => void reload()}
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

  const { patient, signed_encounters, open_draft, chronology, proposal } =
    load.chart;
  const chartLink = chartHash(patient.id);

  return (
    <div>
      <h2
        className="xi-page-title"
        data-testid="chart-heading"
        id="chart-heading"
      >
        Patient chart
      </h2>

      <section className="xi-card" aria-labelledby="chart-demographics-heading">
        <h3 className="xi-section-title" id="chart-demographics-heading">
          Demographics
        </h3>
        <dl>
          <div>
            <dt className="xi-hint">Name</dt>
            <dd>
              {patient.given_name} {patient.family_name}
            </dd>
          </div>
          <div>
            <dt className="xi-hint">Patient ID</dt>
            <dd>
              <code>{patient.identifier}</code>
            </dd>
          </div>
          <div>
            <dt className="xi-hint">Sex · Age · Status</dt>
            <dd>
              {patient.sex} · {patient.age} ·{" "}
              {statusLabel(patient.clinical_status)}
            </dd>
          </div>
          <div>
            <dt className="xi-hint">Phone</dt>
            <dd>{patient.phone ?? "—"}</dd>
          </div>
        </dl>
        <p className="xi-hint">
          Shared demographics (revision {patient.revision}) ·{" "}
          <a href={chartLink}>Permalink {chartLink}</a>
        </p>
        <p
          className="xi-badge"
          data-testid="chart-draft-badge"
          id="chart-draft-badge"
          role="status"
        >
          {open_draft.exists
            ? "Open draft exists — ask its author to resume it"
            : "No open draft"}
        </p>
      </section>

      <section className="xi-card" aria-labelledby="chart-chronology-heading">
        <h3 className="xi-section-title" id="chart-chronology-heading">
          Signed chronology ({chronology.length})
        </h3>
        {signed_encounters.length === 0 ? (
          <p className="xi-hint" role="status">
            No signed encounters yet. Signing lands in S49; this list stays
            empty until then — no draft content appears here.
          </p>
        ) : (
          <table className="xi-table" aria-label="Signed encounters">
            <thead>
              <tr>
                <th scope="col">Kind</th>
                <th scope="col">Signed</th>
                <th scope="col">Encounter</th>
              </tr>
            </thead>
            <tbody>
              {chronology.map((row) => (
                <tr key={row.id}>
                  <td>{row.kind}</td>
                  <td>{row.updated_at}</td>
                  <td>
                    <code>{row.id}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="xi-hint">
          History, medications, and adverse effects for signed encounters will
          appear here once signing exists. Draft history and effects stay
          author-only inside the encounter wizard.
        </p>
      </section>

      <section className="xi-card" aria-labelledby="chart-proposal-heading">
        <h3 className="xi-section-title" id="chart-proposal-heading">
          Proposal
        </h3>
        <div
          className="xi-notice"
          data-testid="chart-proposal-unavailable"
          id="chart-proposal-unavailable"
          role="status"
        >
          <p style={{ margin: 0 }}>
            Proposal {proposal.status}
            {proposal.reason !== undefined && proposal.reason !== ""
              ? ` — ${proposal.reason}`
              : ""}
            . Reasoning is not implemented yet; no proposal is shown and no
            successful result is claimed.
          </p>
        </div>
        <p className="xi-hint">
          Review navigation: the proposal review opens from the author&apos;s
          encounter draft once generation exists. Until then this honest
          unavailable state is the whole proposal surface.
        </p>
      </section>

      <section className="xi-card" aria-labelledby="chart-followup-heading">
        <h3 className="xi-section-title" id="chart-followup-heading">
          Follow-up
        </h3>
        {createError !== null && (
          <p
            className="xi-form-error"
            role="alert"
            tabIndex={-1}
            ref={createErrorRef}
          >
            {createError}
          </p>
        )}
        {canCreateFollowup ? (
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              data-testid="chart-create-followup"
              id="chart-create-followup"
              disabled={createBusy}
              onClick={() => void startFollowUp()}
            >
              {createBusy ? "Starting…" : "Start follow-up draft"}
            </button>
            <a className="xi-btn xi-btn-secondary" href="#/patients">
              Back to directory
            </a>
          </div>
        ) : (
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            Read-only: only physicians start follow-up drafts.{" "}
            <a className="xi-btn xi-btn-secondary" href="#/patients">
              Back to directory
            </a>
          </p>
        )}
        {open_draft.exists && (
          <p className="xi-hint">
            A draft already occupies this patient&apos;s single slot — a new
            follow-up waits until its author resumes or discards it. Other
            patients keep independent slots.
          </p>
        )}
      </section>
    </div>
  );
}
