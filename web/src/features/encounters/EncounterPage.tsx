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
 * through the same autosave path (full object on each save). S20 adds the
 * demo-catalog medication list (`draft_data.medications.entries`, strict
 * POST .../medications pin) with the versioned DDI evidence panel.
 *
 * States: loading (GET), ready (editor + saving/saved/failed), conflict
 * (412 keeps edits + Reload/Retry reconcile UI, never silent overwrite),
 * error (403 author-only, 404 discarded/missing, transport with Retry).
 * Discard needs explicit confirmation + current revision, releases the slot
 * without deleting the patient. Navigation warns while edits remain
 * (beforeunload + in-app hash guard); flush persists on page transition.
 * Assessment steps are separate pages (`#/encounters/:id#<step>`), not one
 * stacked form: Diagnosis, PANSS, C-SSRS, History, then effects, medications,
 * and proposal review. Back/Continue and the step list move one page at a time.
 * Demographics notes and the working note are the Register patient page
 * (`#register` on a registration draft, and `#/patients/new` once the draft
 * exists) — not chrome on Encounter draft.
 *
 * E2E selector contract (dev-test owns the journey specs; S60
 * e2e/failure-recovery.spec.ts asserts the denied view):
 * - denied view `data-testid="encounter-denied"` (role=alert, author-only
 *   message + shared-directory hint, never draft content)
 * - load-error view `data-testid="encounter-error"` (role=alert + Retry)
 */

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { ApiError } from "../identity/api";
import { useAuth } from "../identity/auth";
import { getPatient, type Patient } from "../patients/api";
import { registerUnsavedGuard } from "../../app/navigationGuard";
import {
  discardEncounter,
  getEncounter,
  type EncounterReference,
} from "./api";
import { DiagnosisSection } from "../assessments/diagnosis/DiagnosisSection";
import { PanssSection } from "../assessments/panss/PanssSection";
import { CssrsSection } from "../assessments/cssrs/CssrsSection";
import { HistorySection } from "../history/HistorySection";
import { EffectsSection } from "../history/EffectsSection";
import { MedicationsSection } from "../medications/MedicationsSection";
import { ProposalReviewPanel } from "../reasoning/ProposalReviewPanel";
import { NotesSection } from "../notes/NotesSection";
import { FollowupBaselinePanel } from "../patients/chart/FollowupBaselinePanel";
import { useAutosave } from "./useAutosave";
import {
  DemographicsDraftPanels,
  DraftSaveStatus,
} from "./DemographicsDraftPanels";

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

/** Encounter-draft steps. Registration adds Register patient as step 1. */
const ASSESSMENT_STEPS = [
  { id: "diagnosis", label: "Diagnosis" },
  { id: "panss", label: "PANSS" },
  { id: "cssrs", label: "C-SSRS" },
  { id: "history", label: "History" },
  { id: "effects", label: "Adverse effects" },
  { id: "medications", label: "Medications" },
  { id: "proposal-review", label: "Proposal review" },
] as const;

const REGISTER_STEP = { id: "register", label: "Register patient" } as const;

type WizardStepId =
  | (typeof ASSESSMENT_STEPS)[number]["id"]
  | (typeof REGISTER_STEP)["id"];

function stepsFor(kind: string): ReadonlyArray<{ id: WizardStepId; label: string }> {
  return kind === "registration"
    ? [REGISTER_STEP, ...ASSESSMENT_STEPS]
    : ASSESSMENT_STEPS;
}

function wizardStepFromHash(hash: string, kind: string): WizardStepId {
  const steps = stepsFor(kind);
  if (!hash.startsWith("#/encounters/")) {
    return steps[0]?.id ?? "diagnosis";
  }
  // `#/encounters/:id#panss` — the fragment after the encounter id.
  // Missing fragment stays on Diagnosis so existing draft links keep that page.
  const fragment = hash.split("#")[2] ?? "";
  return steps.some((item) => item.id === fragment)
    ? (fragment as WizardStepId)
    : "diagnosis";
}

function encounterStepHash(encounterId: string, step: WizardStepId): string {
  return `#/encounters/${encounterId}#${step}`;
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
        <p className="xi-form-error" role="alert" data-testid="encounter-denied">
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
        <p className="xi-form-error" role="alert" data-testid="encounter-error">
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
  const discardDialogRef = useRef<HTMLDivElement>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const skipPageFocus = useRef(true);
  const steps = stepsFor(encounter.kind);
  const [step, setStep] = useState<WizardStepId>(() =>
    wizardStepFromHash(window.location.hash, encounter.kind),
  );
  const lastEncounterHashRef = useRef(window.location.hash);

  const stepIndex = steps.findIndex((item) => item.id === step);
  const currentStep = steps[stepIndex] ?? steps[0];
  const previousStep = stepIndex > 0 ? steps[stepIndex - 1] : null;
  const nextStep =
    stepIndex >= 0 && stepIndex < steps.length - 1 ? steps[stepIndex + 1] : null;

  function followStepLink(event: MouseEvent<HTMLAnchorElement>): void {
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      event.button !== 0
    ) {
      return;
    }
    event.preventDefault();
    const hash = event.currentTarget.getAttribute("href");
    if (hash === null || hash === "") {
      return;
    }
    // Hash change flushes (page transition). Answers live in the parent autosave,
    // so unmounting this step does not drop them.
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    } else {
      void autosave.flush();
    }
    setStep(wizardStepFromHash(hash, encounter.kind));
  }

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
  // (including the open step) when the user stays.
  useEffect(() => {
    const onHashChange = () => {
      if (window.location.hash.startsWith("#/encounters/")) {
        lastEncounterHashRef.current = window.location.hash;
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
        window.location.hash = lastEncounterHashRef.current;
      } else {
        void autosave.flush();
      }
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [autosave, encounterId]);

  // Browser back/forward and pasted step URLs. Clicks already set the step;
  // this covers the cases that only change the hash.
  const flushDraft = autosave.flush;
  useEffect(() => {
    const onHashChange = () => {
      if (!window.location.hash.startsWith("#/encounters/")) {
        return;
      }
      void flushDraft();
      setStep(wizardStepFromHash(window.location.hash, encounter.kind));
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [flushDraft, encounter.kind]);

  useEffect(() => {
    if (skipPageFocus.current) {
      skipPageFocus.current = false;
      return;
    }
    pageRef.current?.focus();
  }, [step]);

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

  const pageTitle =
    currentStep.id === "register" ? "Register patient" : "Encounter draft";

  return (
    <div>
      <h2 className="xi-page-title">{pageTitle}</h2>
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

      {/* Save status stays on encounter-draft steps. Demographics notes and
          the working note render only on Register patient (below). */}
      {currentStep.id !== "register" && (
        <section className="xi-card" aria-label="Draft save">
          <DraftSaveStatus autosave={autosave} />
        </section>
      )}

      <nav className="xi-wizard-nav" aria-label="Encounter pages" id="wizard-pages">
        {steps.map((item, index) => (
          <a
            key={item.id}
            id={`wizard-step-${item.id}`}
            href={encounterStepHash(encounterId, item.id)}
            aria-current={item.id === currentStep.id ? "page" : undefined}
            onClick={followStepLink}
          >
            {index + 1}. {item.label}
          </a>
        ))}
      </nav>

      <div
        className="xi-wizard-page"
        id="wizard-page"
        role="region"
        aria-label={`${currentStep.label}, step ${stepIndex + 1} of ${steps.length}`}
        tabIndex={-1}
        ref={pageRef}
      >
        {currentStep.id === "register" && (
          <DemographicsDraftPanels
            encounterId={encounterId}
            autosave={autosave}
            onSessionExpired={sessionExpired}
          />
        )}

        {currentStep.id === "diagnosis" && (
          <>
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
          </>
        )}

        {currentStep.id === "panss" && (
          <>
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
          </>
        )}

        {currentStep.id === "cssrs" && (
          <>
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
          </>
        )}

        {currentStep.id === "history" && (
          <>
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
          </>
        )}

        {currentStep.id === "effects" && (
          <>
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
          </>
        )}

        {currentStep.id === "medications" && (
          <>
            {/* S20 medications + DDI (plan.md §§2.2, 6.3, 9; FR-14-16, FR-20):
              demo-catalog drug-only selection in the shared autosave body with a
              strict validating save + explicit follow-up reconciliation, and the
              versioned DDI evidence panel with stale fencing. Page notes render
              separately below via the reusable control (page key matches backend
              ALLOWED_PAGES). */}
            <MedicationsSection
              encounterId={encounterId}
              autosave={autosave}
              onSessionExpired={sessionExpired}
            />
            <NotesSection
              encounterId={encounterId}
              page="medications"
              autosave={autosave}
              onSessionExpired={sessionExpired}
            />
          </>
        )}

        {currentStep.id === "proposal-review" && (
          <>
            {/* S48 proposal review (plan.md §§8-9; FR-15-16, FR-35-36): entry
              flushes the shared autosave and automatically creates/reuses a
              generation run once the draft is saved and the diagnosis/medication
              gates pass — no extra Generate click, no run per keystroke. Ordered
              per-question states, lazy persisted transparency, the immutable
              proposal with pinned DDI, and a sign entry blocked with reasons for
              failed/stale/partial runs (S49/S50 own signing). Page notes render
              separately below via the reusable control (page key matches backend
              ALLOWED_PAGES; notes never enter projections — see
              features/reasoning/api.ts). */}
            <ProposalReviewPanel
              encounterId={encounterId}
              autosave={autosave}
              onSessionExpired={sessionExpired}
            />
            <NotesSection
              encounterId={encounterId}
              page="proposal"
              autosave={autosave}
              onSessionExpired={sessionExpired}
            />
          </>
        )}
      </div>

      <div className="xi-wizard-pager">
        {previousStep !== null && (
          <a
            id="wizard-back"
            className="xi-btn xi-btn-secondary"
            href={encounterStepHash(encounterId, previousStep.id)}
            onClick={followStepLink}
          >
            Back to {previousStep.label}
          </a>
        )}
        {nextStep !== null && (
          <a
            id="wizard-continue"
            className="xi-btn xi-btn-primary"
            href={encounterStepHash(encounterId, nextStep.id)}
            onClick={followStepLink}
          >
            Continue to {nextStep.label}
          </a>
        )}
      </div>

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
