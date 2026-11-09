import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../identity/auth";
import { ApiError, newIdempotencyKey } from "../identity/api";
import { useAutosave } from "../encounters/useAutosave";
import { type DraftData, type EncounterReference } from "../encounters/api";
import { getPatient } from "../patients/api";
import { DiagnosisSection } from "../assessments/diagnosis/DiagnosisSection";
import { PanssSection } from "../assessments/panss/PanssSection";
import { registerUnsavedGuard } from "../../app/navigationGuard";
import {
  command,
  latestProposal,
  newCaseConfig,
  preview,
  type ClinicalContent,
  type FieldError,
  type NewCaseRaw,
  type Proposal,
  type State,
} from "./api";
import {
  ConsiderationsSection,
  HarmSection,
  HistorySection,
  MedicationsSection,
  useNewCaseText,
} from "./NewCaseSections";
import { NewCasePlanPage } from "./NewCasePlanPage";
const steps = [
  "diagnosis",
  "panss",
  "harm",
  "history",
  "medications",
  "considerations",
  "proposal",
] as const;
const hashStep = () => {
  const name = window.location.hash.split("#")[2] ?? "diagnosis";
  return steps.includes(name as (typeof steps)[number]) ? name : "diagnosis";
};
export function NewCaseWizard({
  encounter,
  initialData,
  initialRevision,
}: {
  encounter: EncounterReference;
  initialData: DraftData;
  initialRevision: number;
}) {
  const n = useNewCaseText();
  const { sessionExpired } = useAuth();
  const id = encounter.id;
  const autosave = useAutosave({
    encounterId: id,
    initialData,
    initialRevision,
    onSessionExpired: sessionExpired,
  });
  const [content, setContent] = useState<ClinicalContent | null>(null);
  const [sex, setSex] = useState("");
  const [step, setStep] = useState("diagnosis");
  const stepRef = useRef("diagnosis");
  const [state, setState] = useState<State | null>(null);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [unmatched, setUnmatched] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const assertionAttempt = useRef<{ revision: number; key: string } | null>(
    null,
  );
  const planFlush = useRef<(() => Promise<void>) | null>(null);
  const planDirty = useRef(false);
  const errorRef = useRef<HTMLDivElement>(null);
  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        sessionExpired();
        return;
      }
      setError(
        e instanceof ApiError && e.code === "STALE_INPUTS" ? "stale" : "failed",
      );
    },
    [sessionExpired],
  );
  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      newCaseConfig(),
      getPatient(encounter.patient_id),
      latestProposal(id),
    ])
      .then(([c, p, r]) => {
        if (cancelled) return;
        setContent(c);
        setSex(p.sex);
        setProposal(r.proposal);
      })
      .catch(fail);
    return () => {
      cancelled = true;
    };
  }, [id, encounter.patient_id, fail]);
  useEffect(() => {
    let cancelled = false;
    void preview(id)
      .then((s) => {
        if (!cancelled) setState(s);
      })
      .catch(fail);
    return () => {
      cancelled = true;
    };
  }, [id, autosave.baseRevision, fail]);
  useEffect(() => {
    if (errors.length || error) errorRef.current?.focus();
  }, [errors, error]);
  const validate = useCallback(
    async (target: string) => {
      const saved = await autosave.ensureSaved();
      if (!saved.ok) return false;
      if (
        unmatched &&
        steps.indexOf(target as (typeof steps)[number]) >
          steps.indexOf("medications")
      ) {
        setError("unmatched");
        return false;
      }
      const s = await command<State>(id, "validate-step", saved.revision, {
        step: target,
      });
      setState(s);
      setErrors(s.navigation_errors ?? []);
      return s.can_navigate === true;
    },
    [id, autosave.ensureSaved, unmatched],
  );
  const go = useCallback(
    async (target: string, fromHash = false) => {
      setBusy(true);
      setError("");
      setErrors([]);
      try {
        const backwards =
          steps.indexOf(target as (typeof steps)[number]) <
          steps.indexOf(stepRef.current as (typeof steps)[number]);
        if (planFlush.current) await planFlush.current();
        const ok = backwards
          ? (await autosave.ensureSaved()).ok
          : await validate(target);
        if (!ok) {
          window.history.replaceState(
            null,
            "",
            `#/encounters/${id}#${stepRef.current}`,
          );
          return;
        }
        if (target === "proposal") {
          const r = await latestProposal(id);
          if (!r.proposal) {
            return;
          }
          setProposal(r.proposal);
        }
        stepRef.current = target;
        setStep(target);
        window.history[fromHash ? "replaceState" : "pushState"](
          null,
          "",
          `#/encounters/${id}#${target}`,
        );
      } catch (e) {
        fail(e);
      } finally {
        setBusy(false);
      }
    },
    [id, autosave.ensureSaved, validate, fail],
  );
  const goRef = useRef(go);
  goRef.current = go;
  useEffect(() => {
    if (hashStep() !== "diagnosis") void goRef.current(hashStep(), true);
    const onHash = () => {
      if (window.location.hash.startsWith(`#/encounters/${id}#`))
        void goRef.current(hashStep(), true);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [id]);
  useEffect(() => {
    registerUnsavedGuard({
      isDirty: () => autosave.isDirty() || planDirty.current,
      save: async () => {
        try {
          if (planFlush.current) await planFlush.current();
          return await autosave.ensureSaved();
        } catch (e) {
          fail(e);
          return { ok: false };
        }
      },
    });
    return () => registerUnsavedGuard(null);
  }, [autosave.isDirty, autosave.ensureSaved, fail]);
  useEffect(() => {
    const unload = (e: BeforeUnloadEvent) => {
      if (autosave.isDirty() || planDirty.current) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", unload);
    return () => window.removeEventListener("beforeunload", unload);
  }, [autosave.isDirty]);
  const raw = autosave.localData.new_case as NewCaseRaw;
  const update = (value: NewCaseRaw) => {
    autosave.setDraftValue("new_case", value);
    if (proposal) setProposal({ ...proposal, stale: true });
  };
  const meds = autosave.localData.medications as {
    entries: { catalog_drug_id: string }[];
    selection_dataset_version: string;
  };
  const assertion = async () => {
    setBusy(true);
    try {
      const saved = await autosave.ensureSaved();
      if (!saved.ok) return;
      if (assertionAttempt.current?.revision !== saved.revision)
        assertionAttempt.current = {
          revision: saved.revision,
          key: newIdempotencyKey(),
        };
      const r = await command<{ draft_data: DraftData; revision: number }>(
        id,
        "diagnosis-confirmation",
        saved.revision,
        { confirm: true },
        assertionAttempt.current.key,
      );
      assertionAttempt.current = null;
      autosave.applyServerSnapshot(r.draft_data, r.revision, null);
      setErrors([]);
      setError("");
      stepRef.current = "panss";
      setStep("panss");
      window.history.pushState(null, "", `#/encounters/${id}#panss`);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  const generate = async () => {
    setBusy(true);
    try {
      if (unmatched) {
        setError("unmatched");
        return;
      }
      if (!(await validate("proposal"))) return;
      const saved = await autosave.ensureSaved();
      if (!saved.ok) return;
      const r = await command<{ proposal: Proposal }>(
        id,
        "proposals",
        saved.revision,
        {},
      );
      setProposal(r.proposal);
      stepRef.current = "proposal";
      setStep("proposal");
      window.history.pushState(null, "", `#/encounters/${id}#proposal`);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  if (!content || !sex)
    return (
      <p role="status">
        {n("loading")}
        {error && (
          <button onClick={() => window.location.reload()}>{n("retry")}</button>
        )}
      </p>
    );
  const index = steps.indexOf(step as (typeof steps)[number]);
  return (
    <div>
      <h2>{n(step)}</h2>
      <nav className="nc-steps" aria-label={n("proposal")}>
        {steps.map((s) => (
          <button
            className="xi-btn xi-btn-secondary"
            key={s}
            aria-current={step === s ? "step" : undefined}
            disabled={busy}
            onClick={() => void go(s)}
          >
            {n(s)}
          </button>
        ))}
      </nav>
      <p role="status">{n(autosave.saveState.kind)}</p>
      <div ref={errorRef} tabIndex={-1}>
        {error && <p role="alert">{n(error)}</p>}
        {errors.map((e) => (
          <p role="alert" key={e.field}>
            {n("errors." + e.code)}
          </p>
        ))}
      </div>
      {autosave.saveState.kind === "failed" && (
        <button onClick={() => void autosave.retry()}>{n("retry")}</button>
      )}
      {autosave.saveState.kind === "conflict" && (
        <div role="alert">
          <p>{n("planConflict")}</p>
          <button onClick={() => void autosave.reloadTruth()}>
            {n("reload")}
          </button>
          {autosave.serverTruth && (
            <>
              <button onClick={autosave.adoptServerVersion}>
                {n("reload")}
              </button>
              <button onClick={() => void autosave.retryOnFreshRevision()}>
                {n("reconcile")}
              </button>
            </>
          )}
        </div>
      )}
      <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
        {step === "diagnosis" && (
          <>
            <DiagnosisSection
              newCase
              encounterId={id}
              autosave={autosave}
              onSessionExpired={sessionExpired}
              stepNumber={2}
            />
            <button
              className="xi-btn xi-btn-secondary"
              onClick={() => void assertion()}
            >
              {n("definite")}
            </button>
          </>
        )}
        {step === "panss" && (
          <PanssSection
            encounterId={id}
            autosave={autosave}
            onSessionExpired={sessionExpired}
            stepNumber={3}
          />
        )}
        {step === "harm" && (
          <HarmSection
            raw={raw}
            answers={
              (autosave.localData.cssrs as { answers: Record<string, string> })
                .answers
            }
            onRaw={update}
            onAnswers={(answers) =>
              autosave.setDraftValue("cssrs", { answers })
            }
          />
        )}
        {step === "history" && (
          <HistorySection
            raw={raw}
            onRaw={update}
            state={state}
            content={content}
            sex={sex}
          />
        )}
        <div hidden={step !== "medications"}>
          <MedicationsSection
            storageKey={"xi-new-case-medication:" + id}
            ids={meds.entries.map((e) => e.catalog_drug_id)}
            names={content.medication_names}
            version={raw.content_pins.lexicomp_version}
            onChange={(ids) =>
              autosave.setDraftValue("medications", {
                ...meds,
                entries: ids.map((catalog_drug_id) => ({ catalog_drug_id })),
              })
            }
            onUnmatched={setUnmatched}
          />
        </div>
        {step === "considerations" && (
          <ConsiderationsSection raw={raw} onRaw={update} state={state} />
        )}
      </fieldset>
      {proposal && (
        <div hidden={step !== "proposal"}>
          <NewCasePlanPage
            encounterId={id}
            proposal={proposal}
            onProposal={setProposal}
            flushInputs={autosave.ensureSaved}
            flushRef={planFlush}
            dirtyRef={planDirty}
            onError={fail}
            onSigned={() => {
              registerUnsavedGuard(null);
              window.location.hash = `#/patients/${encounter.patient_id}/chart`;
            }}
          />
        </div>
      )}
      <div className="nc-actions">
        {index > 0 && (
          <button
            className="xi-btn xi-btn-secondary"
            disabled={busy}
            onClick={() => void go(steps[index - 1]!)}
          >
            {n("back")}
          </button>
        )}
        {index < 5 && (
          <button
            className="xi-btn xi-btn-primary"
            disabled={busy || unmatched}
            onClick={() => void go(steps[index + 1]!)}
          >
            {n("continue")}
          </button>
        )}
        {step === "considerations" && (
          <button
            className="xi-btn xi-btn-primary"
            disabled={busy || unmatched}
            onClick={() => void generate()}
          >
            {n("generate")}
          </button>
        )}
      </div>
    </div>
  );
}
