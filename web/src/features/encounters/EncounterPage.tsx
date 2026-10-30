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
 * stacked form. Registration is seven steps and does not include adverse
 * effects: Register patient, Diagnosis, PANSS, C-SSRS, History, Medications,
 * Proposal review. Follow-up is Diagnosis, PANSS, C-SSRS, History, Adverse
 * effects, Medications, Proposal review (FR-20). Back/Continue and the step
 * list move one page at a time — except registration Diagnosis Continue,
 * which checks the saved server evaluation first and asks for confirmation
 * unless the criteria are satisfied (no bypass/acknowledge is recorded).
 * Registration opens on that same wizard (`#/patients/new` before a draft
 * exists). Saved demographics and the working note are step 1
 * (`#register`) only — not a second page, and not chrome on later steps.
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
import {
  getDiagnosis,
  type DiagnosisPreview,
} from "../assessments/diagnosis/api";
import { PanssSection } from "../assessments/panss/PanssSection";
import { CssrsSection } from "../assessments/cssrs/CssrsSection";
import { HistorySection } from "../history/HistorySection";
import { EffectsSection } from "../history/EffectsSection";
import { MedicationsSection } from "../medications/MedicationsSection";
import { ProposalReviewPanel } from "../reasoning/ProposalReviewPanel";
import { FollowupBaselinePanel } from "../patients/chart/FollowupBaselinePanel";
import { useAutosave } from "./useAutosave";
import {
  DemographicsDraftPanels,
  DraftSaveStatus,
  registrationNoticeFor,
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

const REGISTER_STEP = { id: "register", label: "Register patient" } as const;

/** Seven steps on `#/patients/new` and a registration draft. No adverse effects. */
export const REGISTRATION_WIZARD_STEPS = [
  REGISTER_STEP,
  { id: "diagnosis", label: "Diagnosis" },
  { id: "panss", label: "PANSS" },
  { id: "cssrs", label: "C-SSRS" },
  { id: "history", label: "History" },
  { id: "medications", label: "Medications" },
  { id: "proposal-review", label: "Proposal review" },
] as const;

/** Follow-up only. Adverse effects sits between history and medications. */
const FOLLOW_UP_STEPS = [
  { id: "diagnosis", label: "Diagnosis" },
  { id: "panss", label: "PANSS" },
  { id: "cssrs", label: "C-SSRS" },
  { id: "history", label: "History" },
  { id: "effects", label: "Adverse effects" },
  { id: "medications", label: "Medications" },
  { id: "proposal-review", label: "Proposal review" },
] as const;

type WizardStepId =
  | (typeof REGISTRATION_WIZARD_STEPS)[number]["id"]
  | (typeof FOLLOW_UP_STEPS)[number]["id"];

function stepsFor(kind: string): ReadonlyArray<{ id: WizardStepId; label: string }> {
  return kind === "registration" ? REGISTRATION_WIZARD_STEPS : FOLLOW_UP_STEPS;
}

function wizardStepFromHash(hash: string, kind: string): WizardStepId {
  const steps = stepsFor(kind);
  if (!hash.startsWith("#/encounters/")) {
    return steps[0]?.id ?? "diagnosis";
  }
  // `#/encounters/:id#panss` — the fragment after the encounter id.
  // Missing fragment stays on Diagnosis so existing draft links keep that page.
  const fragment = (hash.split("#")[2] ?? "").split("?")[0] ?? "";
  return steps.some((item) => item.id === fragment)
    ? (fragment as WizardStepId)
    : "diagnosis";
}

function encounterStepHash(encounterId: string, step: WizardStepId): string {
  return `#/encounters/${encounterId}#${step}`;
}

/** Registration Diagnosis Continue consults the server evaluation before
 * navigating (issue-03): direct navigation only for a complete
 * criteria-satisfied result; every other state asks for confirmation.
 * No other transition consults assessment state. */
function diagnosisConfirms(preview: DiagnosisPreview): boolean {
  return (
    preview.evaluation?.status === "complete" &&
    preview.evaluation?.findings?.overall === "criteria_satisfied"
  );
}

function sameAnswerRecord(
  a: Record<string, string>,
  b: Record<string, string>,
): boolean {
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  return (
    keysA.length === keysB.length && keysA.every((key) => b[key] === a[key])
  );
}

function savedDiagnosisAnswers(data: Record<string, unknown>): Record<string, string> {
  const section = data["diagnosis"];
  if (typeof section !== "object" || section === null) {
    return {};
  }
  const answers = (section as Record<string, unknown>)["answers"];
  return typeof answers === "object" && answers !== null
    ? { ...(answers as Record<string, string>) }
    : {};
}

function RegisteredDemographics({
  patient,
}: {
  patient: Patient | null;
}) {
  const status =
    patient === null
      ? ""
      : patient.clinical_status === "first_time"
        ? "First-time"
        : "Established";
  return (
    <>
      <section className="xi-card" aria-labelledby="demographics-heading">
        <h3 className="xi-section-title" id="demographics-heading">
          Demographics (step 1)
        </h3>
        <p className="xi-hint">
          Registered with this draft. Further demographic edits are on the
          patient chart.
        </p>
        {patient === null ? (
          <p>Loading demographics…</p>
        ) : (
          <div className="xi-form-grid">
            <ReadOnlyField id="saved-given-name" label="Given name" value={patient.given_name} />
            <ReadOnlyField id="saved-family-name" label="Family name" value={patient.family_name} />
            <ReadOnlyField
              id="saved-identifier"
              label="Patient ID"
              value={patient.identifier}
              mono
            />
            <ReadOnlyField id="saved-sex" label="Sex" value={patient.sex} />
            <ReadOnlyField id="saved-age" label="Age" value={String(patient.age)} />
            <ReadOnlyField id="saved-clinical-status" label="Clinical status" value={status} />
            <ReadOnlyField
              id="saved-phone"
              label="Phone (optional)"
              value={patient.phone ?? ""}
            />
          </div>
        )}
      </section>
    </>
  );
}

function ReadOnlyField({
  id,
  label,
  value,
  mono = false,
}: {
  id: string;
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="xi-field">
      <label className="xi-label" htmlFor={id}>
        {label}
      </label>
      <input
        className={mono ? "xi-input xi-mono" : "xi-input"}
        id={id}
        value={value}
        readOnly
        autoComplete="off"
      />
    </div>
  );
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
  /** Guards the registration Diagnosis Continue check: only the latest
   * server read may decide, so a slow earlier response never approves a
   * newer answer set. */
  const diagCheckSeqRef = useRef(0);
  const continueBtnRef = useRef<HTMLButtonElement>(null);
  const confirmCancelRef = useRef<HTMLButtonElement>(null);
  /** Registration Diagnosis → PANSS confirmation state. Null means no
   * dialog; transient per Continue press, rechecked on the next press. */
  const [diagConfirm, setDiagConfirm] = useState<DiagnosisPreview | null>(null);
  /** The exact server response the check decided on: the Assessment
   * summary adopts it, so the verdict shown matches the decision. */
  const [diagCheckedPreview, setDiagCheckedPreview] =
    useState<DiagnosisPreview | null>(null);
  const [diagChecking, setDiagChecking] = useState(false);
  const [diagnosisReady, setDiagnosisReady] = useState(false);
  const [diagCheckError, setDiagCheckError] = useState<string | null>(null);
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

  function goToStep(hash: string, step: WizardStepId): void {
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    }
    setStep(step);
  }

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

  const guardedDiagnosisContinue =
    encounter.kind === "registration" &&
    currentStep.id === "diagnosis" &&
    nextStep?.id === "panss";
  /** Diagnosis edits lock while checking or confirming; the Continue button
   * locks only while checking so focus can return to an enabled control
   * after the dialog closes. */
  const diagnosisLocked = diagChecking || diagConfirm !== null;

  function closeDiagConfirm(): void {
    setDiagConfirm(null);
  }

  /** Registration Diagnosis → PANSS Continue: save first, then navigate
   * directly only on a complete criteria-satisfied server evaluation.
   * Every other result opens a confirmation dialog; only its explicit
   * confirm navigates. Never calls bypass/acknowledge, never writes a
   * skip sentinel or completed assessment. */
  async function handleGuardedContinue(): Promise<void> {
    if (diagChecking || diagConfirm !== null || !diagnosisReady) {
      return;
    }
    setDiagCheckError(null);
    setDiagChecking(true);
    try {
      const saved = await autosave.ensureSaved();
      if (!saved.ok) {
        if (saved.reason === "conflict") {
          setDiagCheckError(
            "The draft changed in another tab. Reconcile with the conflict panel above, then retry Continue.",
          );
        } else if (saved.reason === "session") {
          setDiagCheckError(
            "Your session expired. Sign in again, then retry Continue — your answers are preserved.",
          );
        } else {
          setDiagCheckError(
            "Could not save your answers. Check the connection and retry Continue — your answers are preserved.",
          );
        }
        return;
      }
      const seq = (diagCheckSeqRef.current += 1);
      let preview: DiagnosisPreview;
      try {
        preview = await getDiagnosis(encounterId);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          sessionExpired();
          return;
        }
        setDiagCheckError(
          err instanceof ApiError
            ? err.message
            : "Diagnosis check failed. Check the connection and retry Continue — your answers are preserved.",
        );
        return;
      }
      if (seq !== diagCheckSeqRef.current) {
        return;
      }
      // Verify the read covers the just-saved snapshot: an older satisfied
      // preview must never approve a newer nonconfirming answer set.
      const savedAnswers = savedDiagnosisAnswers(saved.data);
      if (
        preview.revision !== saved.revision ||
        !sameAnswerRecord(preview.answers, savedAnswers)
      ) {
        try {
          const reread = await getDiagnosis(encounterId);
          if (seq !== diagCheckSeqRef.current) {
            return;
          }
          if (
            reread.revision !== saved.revision ||
            !sameAnswerRecord(reread.answers, savedAnswers)
          ) {
            setDiagCheckError(
              "The diagnosis check returned a stale result. Retry Continue to recheck the current answers.",
            );
            return;
          }
          preview = reread;
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            sessionExpired();
            return;
          }
          setDiagCheckError(
            err instanceof ApiError
              ? err.message
              : "Diagnosis check failed. Check the connection and retry Continue — your answers are preserved.",
          );
          return;
        }
      }
      if (diagnosisConfirms(preview)) {
        goToStep(
          encounterStepHash(encounterId, "panss"),
          "panss",
        );
      } else {
        // Dialog-scoped only: a satisfied direct navigation leaves no stale
        // preview behind, so Back remounts with a fresh fetch.
        setDiagCheckedPreview(preview);
        setDiagConfirm(preview);
      }
    } finally {
      setDiagChecking(false);
    }
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

  // Checked preview is dialog-scoped: clear on navigation so a Back
  // remount fetches fresh instead of re-adopting a stale decision.
  useEffect(() => {
    setDiagCheckedPreview(null);
  }, [step]);

  // Confirmation dialog: initial focus on Return, Escape dismisses without
  // navigating. Focus restoration runs in the effect below, after the
  // Continue button is enabled again.
  useEffect(() => {
    if (diagConfirm === null) {
      return;
    }
    confirmCancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDiagConfirm();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [diagConfirm]);

  const diagConfirmWasOpenRef = useRef(false);
  useEffect(() => {
    if (diagConfirm !== null) {
      diagConfirmWasOpenRef.current = true;
      return;
    }
    if (diagConfirmWasOpenRef.current) {
      diagConfirmWasOpenRef.current = false;
      // Dialog dismissed without navigating: Continue is enabled again
      // (disabled only while checking), so restore focus there. After a
      // confirm-navigation the guarded button is unmounted and this is a
      // no-op; the page-focus effect handles the new step.
      continueBtnRef.current?.focus();
    }
  }, [diagConfirm]);

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

  const notice = registrationNoticeFor(encounterId);
  const streamlinedStep = ["diagnosis", "panss", "cssrs", "history", "medications", "proposal-review"].includes(currentStep.id);
  const pageTitle =
    encounter.kind === "registration"
      ? "Add New Patient"
      : currentStep.id === "register" ? "Register patient" : "Encounter draft";

  const discardButton = (
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
  );

  return (
    <div>
      <h2 className="xi-page-title">{pageTitle}</h2>
      {notice !== null && (
        <p className="xi-status" role="status">
          Patient {notice.identifier} registered. Server time{" "}
          {notice.serverTimestamp}, revision {notice.revision}.
        </p>
      )}
      {!streamlinedStep && encounter.kind !== "registration" && (
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
      )}

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

      {/* Assessment pages only show save status when recovery is needed. */}
      {currentStep.id !== "register" &&
        (!streamlinedStep ||
          autosave.saveState.kind === "failed" ||
          autosave.saveState.kind === "conflict") && (
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
          <>
            <RegisteredDemographics patient={patient} />
            <DemographicsDraftPanels autosave={autosave} />
          </>
        )}

        {currentStep.id === "diagnosis" && (
          <DiagnosisSection
            encounterId={encounterId}
            autosave={autosave}
            onSessionExpired={sessionExpired}
            stepNumber={stepIndex + 1}
            checkedPreview={diagCheckedPreview}
            locked={diagnosisLocked}
            onInitializationReady={setDiagnosisReady}
          />
        )}

        {currentStep.id === "panss" && (
          <PanssSection
            encounterId={encounterId}
            autosave={autosave}
            onSessionExpired={sessionExpired}
            stepNumber={stepIndex + 1}
          />
        )}

        {currentStep.id === "cssrs" && (
          <CssrsSection
            isRegistration={encounter.kind === "registration"}
            encounterId={encounterId}
            autosave={autosave}
            onSessionExpired={sessionExpired}
            stepNumber={stepIndex + 1}
          />
        )}

        {currentStep.id === "history" && (
          <HistorySection
            encounterId={encounterId}
            autosave={autosave}
            onSessionExpired={sessionExpired}
            stepNumber={stepIndex + 1}
          />
        )}

        {currentStep.id === "effects" && (
          <EffectsSection
            encounterId={encounterId}
            autosave={autosave}
            onSessionExpired={sessionExpired}
            stepNumber={stepIndex + 1}
          />
        )}

        {currentStep.id === "medications" && (
          <>
            {/* S20 medications + DDI (plan.md §§2.2, 6.3, 9; FR-14-16, FR-20):
              demo-catalog drug-only selection in the shared autosave body with a
              strict validating save + explicit follow-up reconciliation, and the
              versioned DDI evidence panel with stale fencing. */}
            <MedicationsSection
              encounterId={encounterId}
              autosave={autosave}
              onSessionExpired={sessionExpired}
              stepNumber={stepIndex + 1}
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
              failed/stale/partial runs (S49/S50 own signing). */}
            <ProposalReviewPanel
              encounterId={encounterId}
              autosave={autosave}
              onSessionExpired={sessionExpired}
              stepNumber={stepIndex + 1}
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
        {nextStep !== null &&
          (guardedDiagnosisContinue ? (
            // Registration Diagnosis → PANSS is a guarded action button, not
            // a link: an anchor's modified-click/new-tab would bypass the
            // confirmation handler. All other transitions keep the pager link.
            <button
              id="wizard-continue"
              ref={continueBtnRef}
              className="xi-btn xi-btn-primary"
              type="button"
              disabled={diagChecking || !diagnosisReady}
              onClick={() => void handleGuardedContinue()}
            >
              {diagChecking
                ? "Checking diagnosis…"
                : `Continue to ${nextStep.label}`}
            </button>
          ) : (
            <a
              id="wizard-continue"
              className="xi-btn xi-btn-primary"
              href={encounterStepHash(encounterId, nextStep.id)}
              onClick={followStepLink}
            >
              Continue to {nextStep.label}
            </a>
          ))}
      </div>

      {diagCheckError !== null && guardedDiagnosisContinue && (
        <p className="xi-form-error" role="alert">
          {diagCheckError}{" "}
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            disabled={diagChecking}
            onClick={() => void handleGuardedContinue()}
          >
            Retry
          </button>
        </p>
      )}

      {diagConfirm !== null && (
        <div className="xi-modal-backdrop">
          <div
            className="xi-modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="diag-continue-title"
            aria-describedby="diag-continue-text"
          >
            <h2 className="xi-section-title" id="diag-continue-title">
              Continue without a confirmed diagnosis?
            </h2>
            <p id="diag-continue-text">
              The current assessment does not confirm the diagnosis. Do you
              want to continue to PANSS anyway?
            </p>
            <div className="xi-row-actions">
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                ref={confirmCancelRef}
                onClick={closeDiagConfirm}
              >
                Return to diagnosis
              </button>
              <button
                className="xi-btn xi-btn-primary"
                type="button"
                onClick={() => {
                  setDiagConfirm(null);
                  goToStep(
                    encounterStepHash(encounterId, "panss"),
                    "panss",
                  );
                }}
              >
                Continue to PANSS anyway
              </button>
            </div>
          </div>
        </div>
      )}

      {encounter.kind === "registration" ? (
        discardButton
      ) : (
        <section className="xi-card" aria-labelledby="draft-danger-heading">
          <h3 className="xi-section-title" id="draft-danger-heading">
            Discard draft
          </h3>
          <p className="xi-hint">
            Discarding releases the patient slot without deleting the patient.
            It needs explicit confirmation and the current revision (
            {autosave.baseRevision}).
          </p>
          {discardButton}
        </section>
      )}

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
