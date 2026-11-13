import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../identity/auth";
import { ApiError, newIdempotencyKey } from "../identity/api";
import { useAutosave } from "../encounters/useAutosave";
import { type DraftData, type EncounterReference } from "../encounters/api";
import { getPatient, type Patient } from "../patients/api";

import { PanssSection } from "../assessments/panss/PanssSection";
import { registerUnsavedGuard } from "../../app/navigationGuard";
import {
  command,
  latestProposal,
  followUpConfig,
  validateStep,
  preview,
  type FollowUpConfig,
  type FollowUpRaw,
  type FollowUpState,
} from "./api";
import type { Proposal, FieldError } from "../new-case/api";
import { HarmSection, MedicationsSection } from "../new-case/NewCaseSections";
import { NewCasePlanPage } from "../new-case/NewCasePlanPage";
import { FollowUpHistory, Answer } from "./FollowUpSections";
import { useFollowUpText } from "./text";
const steps = [
  "panss",
  "harm",
  "history",
  "medications",
  "effects",
  "considerations",
  "proposal",
] as const;
const hashStep = () => {
  const name = window.location.hash.split("#")[2] ?? "panss";
  return steps.includes(name as (typeof steps)[number]) ? name : "panss";
};
export function FollowUpWizard({
  encounter,
  initialData,
  initialRevision,
}: {
  encounter: EncounterReference;
  initialData: DraftData;
  initialRevision: number;
}) {
  const n = useFollowUpText();
  const { sessionExpired } = useAuth();
  const id = encounter.id;
  const autosave = useAutosave({
    encounterId: id,
    initialData,
    initialRevision,
    onSessionExpired: sessionExpired,
  });
  const [content, setContent] = useState<FollowUpConfig | null>(null);
  const [sex, setSex] = useState("");
  const [patient, setPatient] = useState<Patient | null>(null);
  const [step, setStep] = useState("panss");
  const stepRef = useRef("panss");
  const [state, setState] = useState<FollowUpState | null>(null);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [unmatched, setUnmatched] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const generateAttempt = useRef<{ revision: number; key: string } | null>(
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
      followUpConfig(),
      getPatient(encounter.patient_id),
      latestProposal(id),
    ])
      .then(([c, p, r]) => {
        if (cancelled) return;
        setContent(c);
        setSex(p.sex);
        setPatient(p);
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
        if (!cancelled) {
          setState(s);
          void latestProposal(id)
            .then((r) => {
              if (!cancelled) setProposal(r.proposal);
            })
            .catch(fail);
        }
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
      const s = await validateStep(
        id,
        target as import("./api").FollowUpStep,
        saved.revision,
      );
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
    if (hashStep() !== "panss") void goRef.current(hashStep(), true);
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
  const raw = autosave.localData.follow_up as FollowUpRaw;
  const update = (value: FollowUpRaw) => {
    autosave.setDraftValue("follow_up", value);
    if (proposal) setProposal({ ...proposal, stale: true });
  };
  const meds = autosave.localData.medications as {
    entries: { catalog_drug_id: string }[];
    selection_dataset_version: string;
    pending: {
      source_id: string;
      resolution: string;
      replacement_catalog_drug_id: string | null;
    }[];
  };
  const generate = async () => {
    setBusy(true);
    setError("");
    setErrors([]);
    try {
      if (unmatched) {
        setError("unmatched");
        return;
      }
      if (!(await validate("proposal"))) return;
      const saved = await autosave.ensureSaved();
      if (!saved.ok) return;
      if (planFlush.current) await planFlush.current();
      if (generateAttempt.current?.revision !== saved.revision)
        generateAttempt.current = {
          revision: saved.revision,
          key: newIdempotencyKey(),
        };
      const r = await command<{ proposal: Proposal }>(
        id,
        "proposals",
        saved.revision,
        {},
        generateAttempt.current.key,
      );
      generateAttempt.current = null;
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
      {patient && (
        <p className="nc-patient-context">
          <strong>
            <bdi>
              {patient.given_name} {patient.family_name}
            </bdi>
          </strong>
          <bdi dir="ltr">{patient.identifier}</bdi>
          <small>
            {n("followUp")} — {n("age")}: <bdi>{state?.age}</bdi>
          </small>
        </p>
      )}
      <FrozenSummary raw={raw} names={content.medication_names} />
      <h2 className="xi-page-title">{n(step)}</h2>
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
            {n("requiredInputs")}:{" "}
            {n(e.field.split(".").at(e.field.endsWith(".present") ? -2 : -1)!)}
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
        {step === "panss" && (
          <PanssSection
            encounterId={id}
            autosave={autosave}
            onSessionExpired={sessionExpired}
            stepNumber={1}
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
            result={state?.cssrs_evaluation.scores.ideation_severity_recent}
            onAnswers={(answers) =>
              autosave.setDraftValue("cssrs", { answers })
            }
          />
        )}
        {step === "history" && (
          <FollowUpHistory
            raw={raw}
            onRaw={update}
            state={state}
            config={content}
            sex={sex}
          />
        )}
        <div hidden={step !== "medications"}>
          <MedicationsSection
            storageKey={"xi-follow-up-medication:" + id}
            ids={meds.entries.map((e) => e.catalog_drug_id)}
            names={content.medication_names}
            version={String(raw.content_pins.lexicomp_version)}
            onChange={(ids) => {
              autosave.setDraftValue("medications", {
                ...meds,
                entries: ids.map((catalog_drug_id) => ({ catalog_drug_id })),
              });
              if (
                raw.treatment.main_catalog_drug_id &&
                !ids.includes(raw.treatment.main_catalog_drug_id)
              )
                update({
                  ...raw,
                  treatment: { main_catalog_drug_id: null, formulation: null },
                  branches: Object.fromEntries(
                    Object.keys(raw.branches).map((k) => [k, null]),
                  ) as FollowUpRaw["branches"],
                });
              if (proposal) setProposal({ ...proposal, stale: true });
            }}
            onUnmatched={setUnmatched}
          />
          <label>
            {n("main")}
            <select
              className="xi-input"
              value={raw.treatment.main_catalog_drug_id ?? ""}
              onChange={(e) =>
                update({
                  ...raw,
                  treatment: {
                    main_catalog_drug_id: e.target.value || null,
                    formulation: null,
                  },
                })
              }
            >
              <option value="">{n("none")}</option>
              {content.formulations
                .filter(
                  (f) =>
                    f.catalog_drug_id &&
                    meds.entries.some(
                      (e) => e.catalog_drug_id === f.catalog_drug_id,
                    ),
                )
                .filter(
                  (f, i, all) =>
                    all.findIndex(
                      (e) => e.catalog_drug_id === f.catalog_drug_id,
                    ) === i,
                )
                .map((f) => (
                  <option key={f.id} value={f.catalog_drug_id!}>
                    {f.name}
                  </option>
                ))}
            </select>
          </label>
          {raw.treatment.main_catalog_drug_id ? (
            <label>
              {n("formulation")} ({n("required")})
              <select
                className="xi-input"
                value={raw.treatment.formulation ?? ""}
                onChange={(e) =>
                  update({
                    ...raw,
                    treatment: {
                      ...raw.treatment,
                      formulation: e.target.value as "oral" | "lai",
                    },
                  })
                }
              >
                <option value="">{n("select")}</option>
                {["oral", "lai"]
                  .filter((group) =>
                    content.formulations.some(
                      (f) =>
                        f.catalog_drug_id ===
                          raw.treatment.main_catalog_drug_id &&
                        (f.group === group ||
                          (group === "lai" &&
                            content.lai_equivalences.some(
                              (pair) => pair.oral === f.id,
                            ))),
                    ),
                  )
                  .map((group) => (
                    <option value={group} key={group}>
                      {n(group)}
                    </option>
                  ))}
              </select>
            </label>
          ) : (
            <p>{n("noMain")}</p>
          )}
          {meds.pending
            .filter((e) => e.resolution === "pending")
            .map((item) => (
              <section key={item.source_id}>
                <p>
                  {n("pending")}:{" "}
                  {String(
                    (
                      raw.baseline.unmatched_medications as {
                        source_id: string;
                        name?: string;
                      }[]
                    )?.find((e) => e.source_id === item.source_id)?.name ??
                      n("unknown"),
                  )}
                </p>
                <button
                  onClick={() =>
                    autosave.setDraftValue("medications", {
                      ...meds,
                      pending: meds.pending.map((e) =>
                        e.source_id === item.source_id
                          ? {
                              ...e,
                              resolution: "removed",
                              replacement_catalog_drug_id: null,
                            }
                          : e,
                      ),
                    })
                  }
                >
                  {n("removed")}
                </button>
                <label>
                  {n("replacement")}
                  <select
                    value=""
                    onChange={(event) => {
                      const id = event.target.value;
                      if (id)
                        autosave.setDraftValue("medications", {
                          ...meds,
                          pending: meds.pending.map((e) =>
                            e.source_id === item.source_id
                              ? {
                                  ...e,
                                  resolution: "replaced",
                                  replacement_catalog_drug_id: id,
                                }
                              : e,
                          ),
                        });
                    }}
                  >
                    <option value="">{n("select")}</option>
                    {meds.entries.map((e) => (
                      <option key={e.catalog_drug_id} value={e.catalog_drug_id}>
                        {content.medication_names[e.catalog_drug_id] ??
                          e.catalog_drug_id}
                      </option>
                    ))}
                  </select>
                </label>
              </section>
            ))}
        </div>
        {step === "effects" && (
          <section>
            <label>
              {n("sedation")}
              <select
                className="xi-input"
                value={raw.effects.sedation}
                onChange={(e) =>
                  update({
                    ...raw,
                    effects: {
                      ...raw.effects,
                      sedation: e.target
                        .value as FollowUpRaw["effects"]["sedation"],
                    },
                  })
                }
              >
                {["none", "tolerable", "intervention"].map((key) => (
                  <option value={key} key={key}>
                    {n(key)}
                  </option>
                ))}
              </select>
            </label>
            {(Object.keys(raw.effects) as (keyof FollowUpRaw["effects"])[])
              .filter((key) => key !== "sedation")
              .map((key) => (
                <Answer
                  key={key}
                  label={n(key)}
                  value={raw.effects[key] as boolean}
                  onChange={(v) =>
                    update({ ...raw, effects: { ...raw.effects, [key]: v } })
                  }
                />
              ))}
          </section>
        )}
        {step === "considerations" && (
          <section>
            <label>
              {n("recovery")} ({n("required")})
              <select
                className="xi-input"
                value={raw.considerations.recovery ?? ""}
                onChange={(e) =>
                  update({
                    ...raw,
                    considerations: {
                      ...raw.considerations,
                      recovery: e.target
                        .value as FollowUpRaw["considerations"]["recovery"],
                    },
                  })
                }
              >
                <option value="">{n("select")}</option>
                {["controlled", "partial", "none_or_worse"].map((key) => (
                  <option value={key} key={key}>
                    {n(key)}
                  </option>
                ))}
              </select>
            </label>
            {(
              Object.keys(
                raw.considerations,
              ) as (keyof FollowUpRaw["considerations"])[]
            )
              .filter((key) => key !== "recovery")
              .map((key) => (
                <Answer
                  key={key}
                  label={n(key)}
                  value={raw.considerations[key] as boolean | null}
                  onChange={(v) =>
                    update({
                      ...raw,
                      considerations: { ...raw.considerations, [key]: v },
                    })
                  }
                />
              ))}
            {state?.reached_questions.map((key) => (
              <Answer
                key={key}
                label={n(key)}
                value={raw.branches[key]}
                onChange={(v) =>
                  update({ ...raw, branches: { ...raw.branches, [key]: v } })
                }
              />
            ))}
            {state?.errors_by_step.considerations.some((e) =>
              e.field.includes("Hyperprolactinemia"),
            ) && (
              <Answer
                label={n("hyperprolactinemia")}
                value={raw.history.conditions.hyperprolactinemia.manual}
                onChange={(manual) =>
                  update({
                    ...raw,
                    history: {
                      ...raw.history,
                      conditions: {
                        ...raw.history.conditions,
                        hyperprolactinemia: {
                          ...raw.history.conditions.hyperprolactinemia,
                          manual,
                        },
                      },
                    },
                  })
                }
              />
            )}
          </section>
        )}
      </fieldset>
      {!proposal && step === "proposal" && (
        <button
          className="xi-btn xi-btn-primary"
          disabled={busy || unmatched}
          onClick={() => void generate()}
        >
          {n("generate")}
        </button>
      )}
      {proposal && (
        <div hidden={step !== "proposal"}>
          <NewCasePlanPage
            workflow="follow-up"
            encounterId={id}
            proposal={proposal}
            onProposal={(value) => {
              setProposal(value);
              setError("");
            }}
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

function FrozenSummary({
  raw,
  names,
}: {
  raw: FollowUpRaw;
  names: Record<string, string>;
}) {
  const n = useFollowUpText();
  const baseline = raw.baseline;
  const summary = baseline.summary as Record<string, unknown>;
  const display = (value: unknown): string =>
    value == null
      ? n("unknown")
      : typeof value === "boolean"
        ? n(value ? "yes" : "no")
        : Array.isArray(value)
          ? value.map(display).join(", ")
          : typeof value === "object"
            ? Object.entries(value)
                .filter(([k]) => !k.endsWith("_id") && !k.includes("hash"))
                .map(([k, v]) => `${n(k)}: ${display(v)}`)
                .join("; ")
            : (names[String(value)] ?? n(String(value)));
  return (
    <section className="xi-card">
      <h2>{n("prior")}</h2>
      <p>
        {n("recordedVisits")}: <bdi>{String(summary?.visit_ordinal ?? 1)}</bdi>
      </p>
      {!baseline.has_source ? (
        <p>{n("noPrior")}</p>
      ) : (
        <>
          <dl>
            {Object.entries(summary ?? {})
              .filter(([k]) => !["text", "note", "signed_at"].includes(k))
              .map(([k, v]) => (
                <Fragment key={k}>
                  <dt>{n(k)}</dt>
                  <dd>
                    <bdi>{display(v)}</bdi>
                  </dd>
                </Fragment>
              ))}
          </dl>
          <details>
            <summary>{n("details")}</summary>
            {["prior_labs", "prior_scores", "prior_contraception"].map(
              (key) => (
                <section key={key}>
                  <h4>{n(key)}</h4>
                  <p>
                    <bdi>{display(baseline[key])}</bdi>
                  </p>
                </section>
              ),
            )}
          </details>
        </>
      )}
    </section>
  );
}
