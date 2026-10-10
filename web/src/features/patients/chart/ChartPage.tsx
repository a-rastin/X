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
 *
 * S50 signed views (dev-test owns e2e/signing.spec.ts; `<id>` is the full
 * encounter UUID — see features/plans/SignedView.tsx for the full contract):
 * - section `data-testid="signed-view-<id>"` (read-only original/final
 *   probabilities, frozen plan, signer/time, hash)
 * - addenda `data-testid="addendum-list-<id>"`,
 *   `addendum-textarea-<id>`, `addendum-submit-<id>`, `addendum-status-<id>`
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, conflictHint } from "../../identity/api";
import { useAuth } from "../../identity/auth";
import {
  createEncounter,
  encounterHash,
} from "../../encounters/api";
import { chartHash, reportHash } from "../../../app/router";
import { getChart, type ChartResponse } from "./api";
import {
  archivePatient,
  getPatient,
  isRelevantDemographicsEdit,
  patchPatientDemographics,
  unarchivePatient,
  type DemographicsPatch,
  type Patient,
} from "../api";
import { SignedEncounterView } from "../../plans/SignedView";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string; status: number | null }
  | { kind: "ready"; chart: ChartResponse };

function statusLabel(status: string): string {
  return status === "first_time" ? "First-time" : "Established";
}

/* Admin-only archive/unarchive (S51 §1, provisional policy — NOT
 * owner-confirmed). Explicit confirmation shows the patient identifier +
 * current revision and POSTs with that revision as If-Match (412 stale
 * reloads, 409 already-in-state reloads). Physicians never see these
 * buttons (server 403 complements this hiding). There is no delete button:
 * archive is read-only retention, never deletion. Retained private drafts
 * stay author-only with no contents shown here; the generic slot badge
 * below covers resumability without an author oracle. */
function ArchivePanel({
  patient,
  initialDone,
  onChanged,
  onSessionExpired,
}: {
  patient: Patient;
  initialDone: string | null;
  onChanged: (done: string | null) => void;
  onSessionExpired: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // ponytail: success toast lifted via onChanged/initialDone so it survives
  // the parent reload (which unmounts this panel before paint); per-call
  // error stays local (412/409 paths without reload keep it; with reload the
  // reloaded badge/truth is the stable proof).
  const [done, setDone] = useState<string | null>(initialDone);
  const errorRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (error !== null) {
      errorRef.current?.focus();
    }
  }, [error]);

  // Revision captured when the dialog opens — the confirm POSTs exactly it.
  const [confirmRevision, setConfirmRevision] = useState(patient.revision);
  function openConfirm(): void {
    setError(null);
    setDone(null);
    setConfirmRevision(patient.revision);
    setConfirming(true);
  }

  async function run(): Promise<void> {
    if (busy) {
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const result = patient.archived
        ? await unarchivePatient(patient.id, confirmRevision)
        : await archivePatient(patient.id, confirmRevision);
      setConfirming(false);
      const doneText = result.patient.archived
        ? `Patient ${patient.identifier} archived (revision ${result.revision}). Creates, edits, and sign-off are now blocked under the provisional policy.`
        : `Patient ${patient.identifier} unarchived (revision ${result.revision}). The retained draft slot is resumed — its author can continue.`;
      setDone(doneText);
      onChanged(doneText);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      if (err instanceof ApiError && (err.status === 409 || err.status === 412)) {
        // Already-in-state or stale: reload server truth, keep nothing local.
        setConfirming(false);
        setError(
          `${conflictHint(err.code) ?? err.message} (reloaded revision ${patient.revision}).`,
        );
        onChanged(null);
        return;
      }
      setError(
        err instanceof ApiError
          ? `${err.message} (${err.code}). Nothing was changed.`
          : "Archive request failed. Check the connection and retry — nothing was changed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="xi-card" aria-labelledby="chart-archive-heading" data-testid="archive-panel">
      <h3 className="xi-section-title" id="chart-archive-heading">
        Archive
      </h3>
      <p
        className="xi-badge"
        data-testid="chart-archived-badge"
        id="chart-archived-badge"
        role="status"
      >
        {patient.archived
          ? "■ Archived — read-only under the provisional policy (not owner-confirmed)"
          : "● Active"}
      </p>
      {patient.archived && (
        <div className="xi-notice" role="status">
          <p style={{ margin: 0 }}>
            Archived patients are read-only under the provisional archive policy (not
            owner-confirmed): creating, editing, or signing drafts is blocked, while shared reads
            stay available. Retained private drafts stay author-only with no contents shown here;
            the author can resume after unarchive.
          </p>
        </div>
      )}
      {error !== null && (
        <p className="xi-form-error" role="alert" tabIndex={-1} ref={errorRef} data-testid="archive-status">
          {error}
        </p>
      )}
      {done !== null && (
        <p className="xi-status" role="status" data-testid="archive-status">
          {done}
        </p>
      )}
      {!confirming ? (
        <div className="xi-row-actions">
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            data-testid={patient.archived ? "unarchive-button" : "archive-button"}
            disabled={busy}
            onClick={openConfirm}
          >
            {patient.archived ? "Unarchive patient" : "Archive patient"}
          </button>
        </div>
      ) : (
        <div data-testid="archive-confirm" role="group" aria-label="Confirm archive change">
          <p role="status">
            {patient.archived ? "Unarchive" : "Archive"} patient{" "}
            <code className="xi-mono">{patient.identifier}</code> at revision{" "}
            {confirmRevision}?{" "}
            {patient.archived
              ? "Writes resume; the retained draft slot stays with its author."
              : "Creates, edits, and sign-off block under the provisional policy (not owner-confirmed). This is reversible via Unarchive — nothing is deleted."}
          </p>
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              data-testid="archive-confirm-button"
              disabled={busy}
              onClick={() => void run()}
            >
              {busy
                ? "Working…"
                : `Confirm ${patient.archived ? "unarchive" : "archive"} ${patient.identifier} (rev ${confirmRevision})`}
            </button>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={busy}
              onClick={() => setConfirming(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/* Shared demographics edit (S51 §2: physician writes, admin reads).
 * If-Match on the patient revision with saving/saved/failed states and the
 * S07-style 412 reconcile flow (edits kept, server truth shown, explicit
 * Reload/Retry — never silent overwrite). Relevant edits (age/sex/status)
 * surface the ◍ out-of-date staleness banner reusing the proposal-freshness
 * wording; phone/name edits preserve acceptance (no stale banner). This
 * form reads/writes patient fields only — no draft content passes through
 * it, so it grants no draft access. */
function DemographicsForm({
  patient,
  initialNotice,
  onChanged,
  onSessionExpired,
}: {
  patient: Patient;
  initialNotice: { message: string; staleRelevant: boolean } | null;
  onChanged: (notice: { message: string; staleRelevant: boolean } | null) => void;
  onSessionExpired: () => void;
}) {
  const [givenName, setGivenName] = useState(patient.given_name);
  const [familyName, setFamilyName] = useState(patient.family_name);
  const [sex, setSex] = useState(patient.sex);
  const [age, setAge] = useState(String(patient.age));
  const [clinicalStatus, setClinicalStatus] = useState(patient.clinical_status);
  const [phone, setPhone] = useState(patient.phone ?? "");
  const [baseRevision, setBaseRevision] = useState(patient.revision);
  const [saving, setSaving] = useState(false);
  // ponytail: saved/stale lifted via onChanged/initialNotice so the ◍ banner
  // survives the parent reload (which unmounts this form before paint).
  const [saved, setSaved] = useState<string | null>(initialNotice?.message ?? null);
  const [error, setError] = useState<string | null>(null);
  const [conflictServer, setConflictServer] = useState<Patient | null>(null);
  const [staleRelevant, setStaleRelevant] = useState(initialNotice?.staleRelevant ?? false);
  const errorRef = useRef<HTMLParagraphElement>(null);

  // Adopt a freshly loaded patient (post-save reload or conflict truth).
  function adopt(next: Patient): void {
    setGivenName(next.given_name);
    setFamilyName(next.family_name);
    setSex(next.sex);
    setAge(String(next.age));
    setClinicalStatus(next.clinical_status);
    setPhone(next.phone ?? "");
    setBaseRevision(next.revision);
  }

  useEffect(() => {
    if (error !== null) {
      errorRef.current?.focus();
    }
  }, [error]);

  const dirty =
    givenName !== patient.given_name ||
    familyName !== patient.family_name ||
    sex !== patient.sex ||
    age !== String(patient.age) ||
    clinicalStatus !== patient.clinical_status ||
    phone !== (patient.phone ?? "");

  async function save(): Promise<void> {
    if (saving || !dirty) {
      return;
    }
    setError(null);
    setSaved(null);
    setConflictServer(null);
    setSaving(true);
    try {
      const patch: DemographicsPatch = {};
      if (givenName !== patient.given_name) {
        patch.given_name = givenName;
      }
      if (familyName !== patient.family_name) {
        patch.family_name = familyName;
      }
      if (sex !== patient.sex) {
        patch.sex = sex;
      }
      if (age !== String(patient.age)) {
        const parsed = Number(age);
        patch.age = Number.isInteger(parsed) ? parsed : (age as unknown as number);
      }
      if (clinicalStatus !== patient.clinical_status) {
        patch.clinical_status = clinicalStatus;
      }
      if (phone !== (patient.phone ?? "")) {
        patch.phone = phone.trim() === "" ? null : phone;
      }
      const relevant = isRelevantDemographicsEdit(patch);
      const result = await patchPatientDemographics(patient.id, patch, baseRevision);
      setBaseRevision(result.revision);
      const message =
        `Saved demographics (revision ${result.revision}).${relevant ? "" : " Phone/name edits preserve acceptance — no question is marked out of date."}`;
      setSaved(message);
      setStaleRelevant(relevant);
      onChanged({ message, staleRelevant: relevant });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 412) {
        // Stale patient revision: keep every edit, fetch server truth for
        // the reconcile UI, never overwrite the form.
        try {
          setConflictServer(await getPatient(patient.id));
        } catch {
          // Truth fetch failed: the conflict message already keeps edits safe.
        }
        setError(
          "The patient changed (revision mismatch). Your edits are kept — review the current values, Reload them, or Retry your save. Nothing was overwritten.",
        );
        return;
      }
      if (err instanceof ApiError && err.status === 409 && err.code === "PATIENT_ARCHIVED") {
        setError(`${conflictHint(err.code)} Your edits are kept.`);
        onChanged(null);
        return;
      }
      setError(
        err instanceof ApiError
          ? `${err.message} (${err.code}). Your edits are kept — nothing was saved.`
          : "Demographics save failed. Check the connection and retry — your edits are kept.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      aria-label="Edit demographics"
      data-testid="demographics-form"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
      noValidate
    >
      <div className="xi-form-row">
        <div className="xi-field">
          <label className="xi-label" htmlFor="demo-given-name">
            Given name
          </label>
          <input
            className="xi-input"
            id="demo-given-name"
            value={givenName}
            onChange={(event) => setGivenName(event.target.value)}
            autoComplete="off"
          />
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="demo-family-name">
            Family name
          </label>
          <input
            className="xi-input"
            id="demo-family-name"
            value={familyName}
            onChange={(event) => setFamilyName(event.target.value)}
            autoComplete="off"
          />
        </div>
      </div>
      <div className="xi-form-row">
        <div className="xi-field">
          <label className="xi-label" htmlFor="demo-sex">
            Sex
          </label>
          <select
            className="xi-select"
            id="demo-sex"
            value={sex}
            onChange={(event) => setSex(event.target.value as Patient["sex"])}
          >
            <option value="M">M</option>
            <option value="F">F</option>
          </select>
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="demo-age">
            Age
          </label>
          <input
            className="xi-input"
            id="demo-age"
            inputMode="numeric"
            value={age}
            onChange={(event) => setAge(event.target.value)}
            autoComplete="off"
          />
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="demo-status">
            Clinical status
          </label>
          <select
            className="xi-select"
            id="demo-status"
            value={clinicalStatus}
            onChange={(event) => setClinicalStatus(event.target.value as Patient["clinical_status"])}
          >
            <option value="first_time">First-time</option>
            <option value="established">Established</option>
          </select>
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="demo-phone">
            Phone (blank keeps current)
          </label>
          <input
            className="xi-input"
            id="demo-phone"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            autoComplete="off"
          />
        </div>
      </div>
      {error !== null && (
        <p className="xi-form-error" role="alert" tabIndex={-1} ref={errorRef} data-testid="demographics-status">
          {error}
        </p>
      )}
      {conflictServer !== null && (
        <div className="xi-notice" role="status">
          <p style={{ marginTop: 0 }}>
            Current server values (revision {conflictServer.revision}): {conflictServer.sex} ·{" "}
            {conflictServer.age} · {statusLabel(conflictServer.clinical_status)} ·{" "}
            {conflictServer.given_name} {conflictServer.family_name} ·{" "}
            {conflictServer.phone ?? "—"}.
          </p>
          <div className="xi-row-actions" style={{ marginBottom: 0 }}>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => {
                adopt(conflictServer);
                setConflictServer(null);
                setError(null);
              }}
            >
              Reload server values
            </button>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={saving}
              onClick={() => {
                setBaseRevision(conflictServer.revision);
                setConflictServer(null);
                setError(null);
                void save();
              }}
            >
              Retry save at revision {conflictServer.revision}
            </button>
          </div>
        </div>
      )}
      {saved !== null && error === null && (
        <p className="xi-status" role="status" data-testid="demographics-status">
          {saving ? "Saving…" : saved}
        </p>
      )}
      {saving && (
        <p className="xi-hint" role="status">
          Saving…
        </p>
      )}
      {staleRelevant && saved !== null && error === null && (
        <div className="xi-warning-panel" role="status" data-testid="demographics-freshness">
          <p style={{ margin: 0 }}>
            <span aria-hidden="true">◍</span> Out of date — a relevant demographic (age, sex, or
            clinical status) changed. Affected questions need a new run for the current inputs;
            unaffected questions stay valid. Notes never cause this. No draft content is shown
            here.
          </p>
        </div>
      )}
      <div className="xi-row-actions">
        <button
          className="xi-btn xi-btn-primary"
          type="submit"
          data-testid="demographics-save"
          disabled={saving || !dirty}
        >
          {saving ? "Saving…" : "Save demographics"}
        </button>
      </div>
      <p className="xi-hint" style={{ marginBottom: 0 }}>
        Shared edit (revision {baseRevision}): any active physician may edit; only acknowledged
        saves are durable. Age, sex, and clinical status are analysis-visible under the
        provisional policy; phone and names never mark questions out of date.
      </p>
    </form>
  );
}

export function ChartPage({ patientId }: { patientId: string }) {
  const { user, sessionExpired } = useAuth();
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const createErrorRef = useRef<HTMLParagraphElement>(null);
  // Persisted transient notices: child success state lifted here so it
  // survives reload() unmounting the panels before React paints. Cleared on
  // patient change so one patient's toast never leaks into another chart.
  const [demoNotice, setDemoNotice] = useState<{
    message: string;
    staleRelevant: boolean;
  } | null>(null);
  const [archiveDone, setArchiveDone] = useState<string | null>(null);

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
    setDemoNotice(null);
    setArchiveDone(null);
  }, [patientId]);

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
        // Generic slot/archived conflict: no author, no clinical content —
        // the canned hint carries the provisional-policy wording.
        setCreateError(conflictHint(err.code) ?? err.message);
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
  const isAdmin = user?.role === "admin";
  const isPhysician = user?.role === "physician";

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
  // S49/S50 signed reads (shared, no drafts): frozen snapshots + append-only
  // addenda per signed encounter. Absent on old payloads → empty (the
  // sections below stay honest instead of claiming no signatures).
  const snapshots = load.chart.signed_snapshots ?? [];
  const addenda = load.chart.addenda ?? [];
  const revisionOf = (encounterId: string): number =>
    signed_encounters.find((row) => row.id === encounterId)?.revision ?? 1;
  const canAppend = user?.role === "physician";

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
        {patient.archived && !isAdmin && (
          <p
            className="xi-badge"
            data-testid="chart-archived-badge"
            id="chart-archived-badge"
            role="status"
          >
            ■ Archived — read-only under the provisional policy (not owner-confirmed)
          </p>
        )}
        {isPhysician && !patient.archived && (
          <DemographicsForm
            patient={patient}
            initialNotice={demoNotice}
            onChanged={(notice) => {
              setDemoNotice(notice);
              void reload();
            }}
            onSessionExpired={sessionExpired}
          />
        )}
        {isPhysician && patient.archived && (
          <div className="xi-notice" role="status">
            <p style={{ margin: 0 }}>
              Archived patients are read-only under the provisional archive policy (not
              owner-confirmed): demographics editing is blocked. Shared reads stay available.
            </p>
          </div>
        )}
        <p
          className="xi-badge"
          data-testid="chart-draft-badge"
          id="chart-draft-badge"
          role="status"
        >
          {open_draft.exists
            ? patient.archived
              ? "Archived with a retained private draft — no contents shown; its author can resume it after unarchive"
              : "Open draft exists — ask its author to resume it"
            : "No open draft"}
        </p>
      </section>

      {isAdmin && (
        <ArchivePanel
          patient={patient}
          initialDone={archiveDone}
          onChanged={(done) => {
            setArchiveDone(done);
            void reload();
          }}
          onSessionExpired={sessionExpired}
        />
      )}

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

      {/* S50 §§3-4 signed views (plan.md §9.2; FR-20, FR-22): one read-only
        frozen record per signed encounter — original/final probabilities,
        frozen plan, signer/time, hash — plus the separately attributed
        addenda list and the any-physician append form. Another physician
        reads but cannot alter signed content; admins read but never append.
        Print permission follows the declared provisional policy (see hint:
        administrator print allowed, physician print provisional, never
        claimed owner-confirmed). */}
      {snapshots.length > 0 && (
        <section className="xi-card" aria-labelledby="chart-signed-heading">
          <h3 className="xi-section-title" id="chart-signed-heading">
            Signed records ({snapshots.length})
          </h3>
          <p className="xi-hint" style={{ marginTop: 0 }}>
            Each record below is immutable — original and final accepted
            probabilities, the frozen secondary plan, and exact signer/time
            attribution. Print permission follows the declared provisional
            policy: administrator printing is allowed; physician printing is
            provisional and not owner-confirmed.
          </p>
          <div className="xi-row-actions xi-no-print">
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => window.print()}
            >
              Print signed record
            </button>
            {/* S53 admin printable report: signed records only. No report
              button is rendered for physicians (provisional print policy,
              plan §§1.3, 2.1 — the server 403s them on the report route). */}
            {isAdmin && (
              <a
                className="xi-btn xi-btn-secondary"
                href={reportHash(patient.id)}
                data-testid="chart-report-link"
                id="chart-report-link"
              >
                Open printable longitudinal report
              </a>
            )}
          </div>
        </section>
      )}
      {snapshots.map((snapshot) => (
        <SignedEncounterView
          key={snapshot.id}
          snapshot={snapshot}
          addenda={addenda.filter((entry) => entry.encounter_id === snapshot.encounter_id)}
          encounterRevision={revisionOf(snapshot.encounter_id)}
          canAppend={canAppend}
          onSessionExpired={sessionExpired}
          onChanged={() => void reload()}
        />
      ))}

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
        {patient.archived && (
          <div className="xi-notice" role="status">
            <p style={{ margin: 0 }}>
              Follow-up creation is blocked while archived under the provisional archive policy
              (not owner-confirmed). Unarchive resumes the retained draft slot.
            </p>
          </div>
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
