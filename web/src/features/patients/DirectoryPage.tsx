/* Shared patient directory (S06, plan.md §§2.2-2.3, FR-23).
 *
 * Search by name/ID substring plus clinical-status filter with bounded
 * server pagination (default 25, max 100, stable server ordering). Results
 * are shared across physicians: no author scoping, client or server. Reads
 * render loading/empty/error states from the real endpoint with Retry; user
 * text renders as React text nodes (escaped by construction). Physicians get
 * a registration entry point; administrators get a read-only view.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../identity/api";
import { useAuth } from "../identity/auth";
import { createEncounter, encounterHash } from "../encounters/api";
import { chartHash } from "../../app/router";
import {
  DIRECTORY_PAGE_SIZE,
  listPatients,
  type ClinicalStatusFilter,
  type Patient,
} from "./api";

interface DirectoryData {
  items: Patient[];
  total: number;
  limit: number;
  offset: number;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "refreshing"; data: DirectoryData }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: DirectoryData };

function statusLabel(status: Patient["clinical_status"]): string {
  return status === "first_time" ? "First-time" : "Established";
}

export function PatientDirectory() {
  const { user, sessionExpired } = useAuth();
  const uid = useId();
  const searchId = `${uid}-search`;
  const statusId = `${uid}-status`;
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<ClinicalStatusFilter>("all");
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [draftBusy, setDraftBusy] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const draftErrorRef = useRef<HTMLParagraphElement>(null);

  // Debounce free-text search so each keystroke is not a request; the
  // status filter and pager apply immediately.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  const reload = useCallback(async () => {
    setState((previous) =>
      previous.kind === "ready" || previous.kind === "refreshing"
        ? { kind: "refreshing", data: previous.data }
        : { kind: "loading" },
    );
    try {
      const result = await listPatients({
        query: debouncedQuery,
        clinicalStatus: statusFilter,
        limit: DIRECTORY_PAGE_SIZE,
        offset,
      });
      setState({
        kind: "ready",
        data: {
          items: result.items,
          total: result.total,
          limit: result.limit,
          offset: result.offset,
        },
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Patient directory failed to load. Check the connection and retry.",
      });
    }
  }, [debouncedQuery, statusFilter, offset, sessionExpired]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const canRegister = user?.role === "physician";
  const data =
    state.kind === "ready" || state.kind === "refreshing" ? state.data : null;
  const from = data === null || data.total === 0 ? 0 : data.offset + 1;
  const to = data === null ? 0 : Math.min(data.offset + data.items.length, data.total);

  useEffect(() => {
    if (draftError !== null) {
      draftErrorRef.current?.focus();
    }
  }, [draftError]);

  async function startFollowUp(patientId: string): Promise<void> {
    if (draftBusy !== null) {
      return;
    }
    setDraftError(null);
    setDraftBusy(patientId);
    try {
      const result = await createEncounter(patientId, "follow_up");
      window.location.hash = encounterHash(result.encounter.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        // Generic slot conflict: no author, no clinical content.
        setDraftError(
          "An open draft already exists for this patient. Ask its author to resume it.",
        );
        return;
      }
      setDraftError(
        err instanceof ApiError
          ? err.message
          : "Could not start a follow-up draft. Check the connection and retry.",
      );
    } finally {
      setDraftBusy(null);
    }
  }

  return (
    <section className="xi-card" aria-labelledby={`${uid}-heading`}>
      <h3 className="xi-section-title" id={`${uid}-heading`}>
        Patient directory
        {data !== null && ` (${data.total})`}
      </h3>
      <form
        aria-label="Search patients"
        onSubmit={(event) => {
          // Search already applies live (debounced); submit only resets the
          // page so Enter from the input cannot strand the pager mid-list.
          event.preventDefault();
          setOffset(0);
          void reload();
        }}
      >
        <div className="xi-form-row">
          <div className="xi-field">
            <label className="xi-label" htmlFor={searchId}>
              Search by name or ID
            </label>
            <input
              className="xi-input"
              id={searchId}
              type="search"
              autoComplete="off"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setOffset(0);
              }}
            />
          </div>
          <div className="xi-field">
            <label className="xi-label" htmlFor={statusId}>
              Clinical status
            </label>
            <select
              className="xi-select"
              id={statusId}
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value as ClinicalStatusFilter);
                setOffset(0);
              }}
            >
              <option value="all">All statuses</option>
              <option value="first_time">First-time</option>
              <option value="established">Established</option>
            </select>
          </div>
        </div>
      </form>
      {state.kind === "loading" && <p>Loading patients…</p>}
      {state.kind === "error" && (
        <div>
          <p className="xi-form-error" role="alert">
            {state.message}
          </p>
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => void reload()}
          >
            Retry
          </button>
        </div>
      )}
      {draftError !== null && (
        <p
          className="xi-form-error"
          role="alert"
          tabIndex={-1}
          ref={draftErrorRef}
        >
          {draftError}
        </p>
      )}
      {data !== null &&
        (data.total === 0 ? (
          <p>No patients found.</p>
        ) : (
          <div>
            <table className="xi-table" aria-label="Patients">
              <thead>
                <tr>
                  <th scope="col">Patient ID</th>
                  <th scope="col">Given name</th>
                  <th scope="col">Family name</th>
                  <th scope="col">Sex</th>
                  <th scope="col">Age</th>
                  <th scope="col">Status</th>
                  <th scope="col">Chart</th>
                  {canRegister && <th scope="col">Draft</th>}
                </tr>
              </thead>
              <tbody>
                {data.items.map((patient) => (
                  <tr key={patient.id}>
                    <td>{patient.identifier}</td>
                    <td>{patient.given_name}</td>
                    <td>{patient.family_name}</td>
                    <td>{patient.sex}</td>
                    <td>{patient.age}</td>
                    <td>{statusLabel(patient.clinical_status)}</td>
                    <td>
                      <a
                        className="xi-btn xi-btn-secondary"
                        href={chartHash(patient.id)}
                        aria-label={`View chart for ${patient.identifier}`}
                      >
                        View chart
                      </a>
                    </td>
                    {canRegister && (
                      <td>
                        <button
                          className="xi-btn xi-btn-secondary"
                          type="button"
                          data-testid="directory-create-followup"
                          disabled={draftBusy !== null}
                          onClick={() => void startFollowUp(patient.id)}
                          aria-label={`Start follow-up draft for ${patient.identifier}`}
                        >
                          {draftBusy === patient.id
                            ? "Starting…"
                            : "Start follow-up"}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="xi-hint" role="status">
              Showing {from}–{to} of {data.total}.
            </p>
            <div className="xi-row-actions">
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                disabled={data.offset === 0}
                onClick={() =>
                  setOffset(Math.max(0, data.offset - data.limit))
                }
              >
                Previous
              </button>
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                disabled={data.offset + data.items.length >= data.total}
                onClick={() => setOffset(data.offset + data.limit)}
              >
                Next
              </button>
            </div>
          </div>
        ))}
      {canRegister ? (
        <p style={{ marginBottom: 0 }}>
          <a className="xi-btn xi-btn-primary" href="#/patients/new">
            Register new patient
          </a>
        </p>
      ) : (
        <p className="xi-hint" style={{ marginBottom: 0 }}>
          Read-only: administrators do not register patients.
        </p>
      )}
    </section>
  );
}

export function PatientsPage() {
  return (
    <div>
      <h2 className="xi-page-title">Patients</h2>
      <PatientDirectory />
    </div>
  );
}
