/* Four adverse-effect panels with server-computed previews (S12,
 * plan.md §§2.2, 5; FR-20–21).
 *
 * Wizard integration (not separate persistence): effects live in the shared
 * S07 autosave body as `draft_data["effects"][key] = {status, severity,
 * questionnaire: {answers}}` and save through the same debounced PATCH +
 * revision fence. This component only edits that slice of `localData`; every
 * verdict comes from GET /encounters/{id}/effects, which runs the same
 * questionnaire evaluators as the backend — the browser never computes
 * scores, so the display cannot drift from the versioned drafts under
 * content/history/ (bars/sas/aims/acute-dystonia-dx-criteria v1, all awaiting
 * owner review; not approved).
 *
 * Status contract (mirrors the strict POST .../effects/{effect}/status
 * rules, enforced client-side on the autosave path):
 * - present reveals the complete questionnaire plus the reviewed severity
 *   select; full completion is required only when present;
 * - absent and not_assessed show null severity and impose no
 *   questionnaire-completion requirement (saved answers are kept verbatim,
 *   severity is suppressed to null in the preview even if a stale value
 *   lingers in storage);
 * - changing status to absent/not_assessed clears the severity in the same
 *   edit (explicit clear — no stale severity is ever sent); switching back
 *   to present leaves severity null until the reviewer chooses it again.
 * - Missing required items suppress calculated results to null (never zero)
 *   unless a reviewed source defines a missing-data rule (none does here).
 * - AIMS has no items: the tardive-dyskinesia panel shows the
 *   awaiting_source message with no invented items, and present cannot clear
 *   completion until the complete source is reviewed.
 * - The acute-dystonia form is named "Acute Dystonia Dx Criteria" exactly (5
 *   criteria, no total score); the urgent airway panel is driven by the
 *   addx_urgent_airway answer alone and persists independently of checklist
 *   completion, status, or severity.
 *
 * The 412 path stays inside the shared useAutosave hook (keeps edits +
 * reload/reconcile UI); a failed save never shows Saved (the wizard save
 * status owns that). No applyServerSnapshot resync is needed: there are no
 * command POSTs here, so previews simply refresh after each acknowledged
 * autosave (same pattern as PANSS/C-SSRS).
 *
 * Accessibility: native fieldset/legend + radios/selects (keyboard free),
 * preview verdict is an aria-live region, the urgent panel is role=alert
 * with a heading (never color alone), per-item errors use role=alert with
 * aria-describedby, styling uses semantic tokens only (both themes).
 *
 * E2E selector contract (for the test agent's e2e/history.spec.ts):
 * - section heading "Adverse effects (step 6)" (#effects-heading)
 * - status radios `#effect-{key}-present|absent|not_assessed`
 *   (name `effect-{key}`), e.g. `#effect-akathisia-present`
 * - BARS radios `#bars-{item}-{n}` (name `bars-{item}`), items
 *   bars_objective/bars_awareness/bars_distress (0-3) and bars_global (0-5)
 * - SAS radios `#sas-{item}-{n}` (name `sas-{item}`), 10 items 0-4
 * - Acute Dystonia Dx Criteria radios `#acute-{item}-yes|no|unknown`
 *   (name `acute-{item}`), 5 addx_* items
 * - severity selects `#effect-{key}-severity`
 * - verdict `#effects-preview-status` (aria-live polite)
 * - per-effect still-needed lists `#effect-{key}-still-needed`
 * - per-effect results `#effect-{key}-results`
 * - per-item errors `#bars-{item}-error`, `#sas-{item}-error`,
 *   `#acute-{item}-error` (role=alert)
 * - urgent airway panel `#effect-acute_dystonia-urgent` (role=alert)
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ApiError } from "../identity/api";
import type { DraftData } from "../encounters/api";
import type { useAutosave } from "../encounters/useAutosave";
import {
  getEffects,
  type EffectKey,
  type EffectStatus,
  type EffectsPreview,
  type EffectState,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface EffectsSectionProps {
  encounterId: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
}

const EFFECT_KEYS: EffectKey[] = [
  "tardive_dyskinesia",
  "akathisia",
  "parkinsonism",
  "acute_dystonia",
];

const EFFECT_TITLES: Record<EffectKey, string> = {
  tardive_dyskinesia: "Tardive dyskinesia (AIMS)",
  akathisia: "Akathisia (BARS)",
  parkinsonism: "Parkinsonism (SAS)",
  acute_dystonia: "Acute dystonia (Acute Dystonia Dx Criteria)",
};

const STATUSES: EffectStatus[] = ["present", "absent", "not_assessed"];

/** BARS items mirroring content/history/bars.v1.json (draft v1). */
interface ScaleItem {
  id: string;
  prompt: string;
  max: number;
}

const BARS_ITEMS: ScaleItem[] = [
  {
    id: "bars_objective",
    prompt:
      "Objective restlessness: characteristic restless movements during observation (0 normal/occasional fidgeting; 3 virtually constant).",
    max: 3,
  },
  {
    id: "bars_awareness",
    prompt:
      "Subjective awareness of restlessness: inner restlessness and urge to move (0 none; 3 intense compulsion most of the time).",
    max: 3,
  },
  {
    id: "bars_distress",
    prompt: "Distress related to restlessness (0 none; 3 severe).",
    max: 3,
  },
  {
    id: "bars_global",
    prompt:
      "Global clinical assessment of the overall syndrome (0 Absent; 1 Questionable; 2 Mild; 3 Moderate; 4 Marked; 5 Severe).",
    max: 5,
  },
];

const BARS_GLOBAL_LABELS: Record<number, string> = {
  0: "Absent",
  1: "Questionable",
  2: "Mild",
  3: "Moderate",
  4: "Marked",
  5: "Severe",
};

/** SAS items mirroring content/history/sas.v1.json (draft v1). */
const SAS_ITEMS: ScaleItem[] = [
  {
    id: "sas_gait",
    prompt: "Gait: posture, step pattern, arm swing (0 normal; 4 stooped shuffling with propulsion).",
    max: 4,
  },
  {
    id: "sas_arm_dropping",
    prompt: "Arm dropping: free fall and rebound (0 free; 4 very slow descent).",
    max: 4,
  },
  {
    id: "sas_shoulder_shaking",
    prompt: "Shoulder shaking: resistance to passive movement (0 normal tone; 4 extreme rigidity).",
    max: 4,
  },
  {
    id: "sas_elbow_rigidity",
    prompt: "Elbow rigidity on passive flexion/extension (0 normal; 4 extreme rigidity).",
    max: 4,
  },
  {
    id: "sas_wrist_rigidity",
    prompt: "Wrist rigidity on passive movement (0 normal; 4 extreme rigidity).",
    max: 4,
  },
  {
    id: "sas_leg_pendulousness",
    prompt: "Leg pendulousness: swing after release (0 free swing; 4 no swing).",
    max: 4,
  },
  {
    id: "sas_head_dropping",
    prompt: "Head dropping: fall after support is withdrawn; omit if unsafe (0 prompt fall; 4 rigid neck).",
    max: 4,
  },
  {
    id: "sas_glabellar_tap",
    prompt: "Glabellar tap: consecutive blinks (0: 0-5; 4: 21 or more).",
    max: 4,
  },
  {
    id: "sas_tremor",
    prompt: "Tremor at rest (0 none; 4 generalized or whole-body).",
    max: 4,
  },
  {
    id: "sas_salivation",
    prompt: "Salivation (0 normal; 4 overt persistent drooling).",
    max: 4,
  },
];

/** Acute Dystonia Dx Criteria items mirroring
 * content/history/acute-dystonia-dx-criteria.v1.json (draft v1). No total. */
const ACUTE_ITEMS: { id: string; prompt: string; urgent: boolean }[] = [
  {
    id: "addx_sustained_posture",
    prompt:
      "Sustained involuntary contractions with abnormal postures (neck twisting, jaw spasm, tongue protrusion, sustained eye deviation, abnormal limb or trunk postures).",
    urgent: false,
  },
  {
    id: "addx_medication_timeline",
    prompt:
      "Plausible medication timeline (initiation, rapid escalation, or reduction of EPS-preventive medication).",
    urgent: false,
  },
  {
    id: "addx_distribution_persistence",
    prompt: "Distribution and persistence of abnormal postures assessed and documented.",
    urgent: false,
  },
  {
    id: "addx_exclusions",
    prompt:
      "Catatonia, primary neurological/medical cause, and tardive movement disorder excluded as the explanation.",
    urgent: false,
  },
  {
    id: "addx_urgent_airway",
    prompt: "Urgent airway concern: stridor or breathing difficulty suggesting laryngeal dystonia.",
    urgent: true,
  },
];

const ACUTE_OPTIONS = ["yes", "no", "unknown"] as const;

const REVIEWED_SEVERITIES = ["mild", "moderate", "severe"] as const;

interface EffectSlice {
  status: EffectStatus | null;
  severity: unknown;
  answers: Record<string, unknown>;
}

function isStatus(value: unknown): value is EffectStatus {
  return (
    value === "present" || value === "absent" || value === "not_assessed"
  );
}

function slicesOf(data: DraftData): Record<EffectKey, EffectSlice> {
  const out = {} as Record<EffectKey, EffectSlice>;
  const raw = data["effects"];
  const section =
    typeof raw === "object" && raw !== null
      ? (raw as Record<string, unknown>)
      : {};
  for (const key of EFFECT_KEYS) {
    const entry = section[key];
    if (typeof entry === "object" && entry !== null) {
      const record = entry as Record<string, unknown>;
      const questionnaire = record["questionnaire"];
      const answers =
        typeof questionnaire === "object" &&
        questionnaire !== null &&
        typeof (questionnaire as Record<string, unknown>)["answers"] ===
          "object" &&
        (questionnaire as Record<string, unknown>)["answers"] !== null
          ? {
              ...((questionnaire as Record<string, unknown>)[
                "answers"
              ] as Record<string, unknown>),
            }
          : {};
      out[key] = {
        status: isStatus(record["status"]) ? record["status"] : null,
        severity: record["severity"] ?? null,
        answers,
      };
    } else {
      out[key] = { status: null, severity: null, answers: {} };
    }
  }
  return out;
}

function formatScore(value: unknown): string {
  return typeof value === "number" ? String(value) : "—";
}

export function EffectsSection({
  encounterId,
  autosave,
  onSessionExpired,
}: EffectsSectionProps) {
  const [preview, setPreview] = useState<EffectsPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const refreshPreview = useCallback(async (): Promise<void> => {
    try {
      const fresh = await getEffects(encounterId);
      setPreview(fresh);
      setPreviewError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setPreviewError(
        err instanceof ApiError
          ? err.message
          : "Adverse-effects preview failed to load. Check the connection and retry.",
      );
    } finally {
      setPreviewLoading(false);
    }
  }, [encounterId, onSessionExpired]);

  // Live preview follows every acknowledged save.
  const savedRevision =
    autosave.saveState.kind === "saved" ? autosave.saveState.revision : 0;
  useEffect(() => {
    void refreshPreview();
  }, [refreshPreview, savedRevision]);

  const slices = slicesOf(autosave.localData);

  function writeEffect(key: EffectKey, next: EffectSlice): void {
    const current = slicesOf(autosave.localData);
    const merged: Record<string, unknown> = {};
    for (const effectKey of EFFECT_KEYS) {
      const slice = effectKey === key ? next : current[effectKey];
      merged[effectKey] = {
        status: slice.status,
        severity: slice.severity,
        questionnaire: { answers: slice.answers },
      };
    }
    autosave.setDraftValue("effects", merged);
  }

  function setStatus(key: EffectKey, status: EffectStatus): void {
    const current = slices[key];
    if (current.status === status) {
      return;
    }
    // Explicit clear: absent/not_assessed always carry null severity, so a
    // status change drops any obsolete severity in the same edit — no stale
    // severity is ever sent. Returning to present leaves severity null until
    // the reviewer chooses it again.
    const next: EffectSlice = {
      status,
      severity: status === "present" ? (current.severity ?? null) : null,
      answers: current.answers,
    };
    writeEffect(key, next);
  }

  function setAnswer(key: EffectKey, itemId: string, value: unknown): void {
    const current = slices[key];
    if (current.answers[itemId] === value) {
      return;
    }
    writeEffect(key, {
      ...current,
      answers: { ...current.answers, [itemId]: value },
    });
  }

  function setSeverity(key: EffectKey, raw: string): void {
    const current = slices[key];
    let severity: unknown = null;
    if (raw !== "") {
      severity = key === "akathisia" ? Number(raw) : raw;
    }
    if (current.severity === severity) {
      return;
    }
    writeEffect(key, { ...current, severity });
  }

  const previewEffects = preview?.effects ?? null;
  const answersPending =
    previewEffects !== null &&
    EFFECT_KEYS.some((key) => {
      const local = slices[key];
      const saved = previewEffects[key];
      if (local.status !== saved.status) {
        return true;
      }
      if (JSON.stringify(local.severity ?? null) !== JSON.stringify(saved.severity ?? null)) {
        return true;
      }
      const localKeys = Object.keys(local.answers);
      const savedKeys = Object.keys(saved.questionnaire.answers);
      return (
        localKeys.length !== savedKeys.length ||
        localKeys.some(
          (item) => local.answers[item] !== saved.questionnaire.answers[item],
        )
      );
    });

  function effectClause(key: EffectKey, state: EffectState | null): string {
    const title = EFFECT_TITLES[key];
    const local = slices[key];
    const status = state?.status ?? local.status;
    if (status === null) {
      return `${title}: not recorded`;
    }
    if (status === "absent" || status === "not_assessed") {
      return `${title}: ${status} (severity null, no questionnaire required)`;
    }
    const evaluation = state?.questionnaire.evaluation ?? null;
    if (evaluation === null || evaluation.status !== "complete") {
      const missing = evaluation?.missing_item_ids.length ?? 0;
      if (key === "tardive_dyskinesia") {
        return `${title}: present — AIMS awaiting source, completion undefined (results null)`;
      }
      return `${title}: present — incomplete (${missing} still needed, results null)`;
    }
    const scores = evaluation.scores ?? {};
    if (key === "akathisia") {
      const global = scores["global"];
      const label =
        typeof global === "number" ? (BARS_GLOBAL_LABELS[global] ?? "") : "";
      return `${title}: present — BARS complete, global ${formatScore(global)} ${label}, component total ${formatScore(scores["component_total"])}`;
    }
    if (key === "parkinsonism") {
      return `${title}: present — SAS complete, raw total ${formatScore(scores["raw_total"])}, mean ${formatScore(scores["mean"])} (no severity bands)`;
    }
    const urgent = state?.urgent;
    return `${title}: present — criteria recorded, urgent ${urgent === true ? "yes" : urgent === false ? "no" : "unknown"} (no total score)`;
  }

  let verdict: string;
  if (preview === null) {
    verdict = "";
  } else {
    const clauses = EFFECT_KEYS.map((key) =>
      effectClause(key, previewEffects?.[key] ?? null),
    );
    verdict = `Adverse effects (server evaluation): ${clauses.join("; ")}. Severities exist only for present effects; absent and not assessed carry null severity and no completion requirement.`;
  }

  function renderScaleInputs(
    key: EffectKey,
    items: ScaleItem[],
    prefix: "bars" | "sas",
    itemErrors: Record<string, string[]>,
  ): ReactNode {
    return (
      <div>
        {items.map((item) => {
          const errorId =
            itemErrors[item.id] !== undefined
              ? `${prefix}-${item.id}-error`
              : undefined;
          const options: number[] = [];
          for (let n = 0; n <= item.max; n += 1) {
            options.push(n);
          }
          return (
            <fieldset key={item.id} className="xi-field">
              <legend className="xi-label">{item.prompt}</legend>
              <div
                className="xi-radio-group"
                role="radiogroup"
                aria-label={item.prompt}
              >
                {options.map((value) => (
                  <label key={value} htmlFor={`${prefix}-${item.id}-${value}`}>
                    <input
                      id={`${prefix}-${item.id}-${value}`}
                      type="radio"
                      name={`${prefix}-${item.id}`}
                      value={value}
                      checked={slices[key].answers[item.id] === value}
                      onChange={() => setAnswer(key, item.id, value)}
                      aria-describedby={errorId}
                    />
                    {value}
                  </label>
                ))}
              </div>
              {errorId !== undefined && (
                <p className="xi-field-error" id={errorId} role="alert">
                  {(itemErrors[item.id] ?? []).join(" ")}
                </p>
              )}
            </fieldset>
          );
        })}
      </div>
    );
  }

  function renderSeveritySelect(
    key: EffectKey,
    state: EffectState | null,
  ): ReactNode {
    const local = slices[key];
    // Absent/not_assessed carry null severity with no completion requirement.
    if (local.status !== "present") {
      const kept = Object.keys(local.answers).length;
      return (
        <p className="xi-hint">
          Severity: null (not required for {local.status ?? "unrecorded"}{" "}
          status).
          {kept > 0 &&
            ` ${kept} saved answer${kept === 1 ? " is" : "s are"} kept; only the severity was cleared.`}
        </p>
      );
    }
    if (key === "akathisia") {
      const evaluation = state?.questionnaire.evaluation ?? null;
      const global =
        evaluation?.status === "complete"
          ? evaluation.scores?.["global"]
          : null;
      return (
        <div className="xi-field">
          <label className="xi-label" htmlFor="effect-akathisia-severity">
            Reviewed severity (BARS global score, 0-5)
          </label>
          <select
            className="xi-select"
            id="effect-akathisia-severity"
            value={
              typeof local.severity === "number" ? String(local.severity) : ""
            }
            onChange={(event) => setSeverity(key, event.target.value)}
            aria-describedby="effect-akathisia-severity-hint"
          >
            <option value="">Not chosen</option>
            {[0, 1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n} — {BARS_GLOBAL_LABELS[n]}
              </option>
            ))}
          </select>
          <p className="xi-hint" id="effect-akathisia-severity-hint">
            Must equal the recorded BARS global score
            {typeof global === "number" ? ` (currently ${global})` : ""}; global
            2 or greater indicates present. The global item is the principal
            severity measure — no summed severity rule is used.
          </p>
        </div>
      );
    }
    return (
      <div className="xi-field">
        <label className="xi-label" htmlFor={`effect-${key}-severity`}>
          Reviewed severity (independent clinical judgment)
        </label>
        <select
          className="xi-select"
          id={`effect-${key}-severity`}
          value={typeof local.severity === "string" ? local.severity : ""}
          onChange={(event) => setSeverity(key, event.target.value)}
        >
          <option value="">Not chosen</option>
          {REVIEWED_SEVERITIES.map((level) => (
            <option key={level} value={level}>
              {level}
            </option>
          ))}
        </select>
      </div>
    );
  }

  function renderQuestionnaire(key: EffectKey, state: EffectState | null): ReactNode {
    const local = slices[key];
    if (local.status !== "present") {
      return null;
    }
    const evaluation = state?.questionnaire.evaluation ?? null;
    const itemErrors = evaluation?.item_errors ?? {};
    const missing = evaluation?.missing_item_ids ?? [];

    if (key === "tardive_dyskinesia") {
      return (
        <div className="xi-effects-panel" aria-label="AIMS status">
          <p style={{ margin: 0 }}>
            AIMS — complete form awaiting source. The tardive-dyskinesia
            criteria reference AIMS but do not contain its complete form, so
            no items are shown and none are invented. Present tardive
            dyskinesia cannot clear questionnaire completion until the
            complete source is reviewed. Research thresholds only
            (Schooler-Kane): a regional score of 3 or greater in one region,
            or 2 or greater in at least two regions, with at least 3 months
            cumulative exposure and no alternative cause — never a total
            score, never a diagnosis.
          </p>
        </div>
      );
    }

    if (key === "akathisia") {
      return (
        <div>
          <p className="xi-hint">
            Full BARS: observe seated then standing (2 minutes each, neutral
            conversation), note other movements, then ask about inner
            restlessness directly. All 4 items are required when present.
          </p>
          {renderScaleInputs(key, BARS_ITEMS, "bars", itemErrors)}
        </div>
      );
    }

    if (key === "parkinsonism") {
      return (
        <div>
          <p className="xi-hint">
            Full SAS: ten-item examination for dopamine-blocking-drug-induced
            parkinsonian signs (rigidity emphasis). Use the same conditions
            and, when possible, the same trained rater for serial assessments.
            All 10 items are required when present. Original convention: mean
            up to 0.3 within normal; greater than 0.3 abnormal. No
            mild/moderate/severe bands exist — reviewed severity is
            independent.
          </p>
          {renderScaleInputs(key, SAS_ITEMS, "sas", itemErrors)}
        </div>
      );
    }

    return (
      <div>
        <p className="xi-hint">
          Acute Dystonia Dx Criteria: criteria-based checklist drafted from
          the supplied criteria. There is no numeric scale and no total
          score. All 5 criteria are required when present. Screen for
          stridor or breathing difficulty at once — airway evaluation never
          waits for this checklist.
        </p>
        {ACUTE_ITEMS.map((item) => {
          const errorId =
            itemErrors[item.id] !== undefined
              ? `acute-${item.id}-error`
              : undefined;
          return (
            <fieldset key={item.id} className="xi-field">
              <legend className="xi-label">{item.prompt}</legend>
              <div
                className="xi-radio-group"
                role="radiogroup"
                aria-label={item.prompt}
              >
                {ACUTE_OPTIONS.map((value) => (
                  <label key={value} htmlFor={`acute-${item.id}-${value}`}>
                    <input
                      id={`acute-${item.id}-${value}`}
                      type="radio"
                      name={`acute-${item.id}`}
                      value={value}
                      checked={local.answers[item.id] === value}
                      onChange={() => setAnswer(key, item.id, value)}
                      aria-describedby={errorId}
                    />
                    {value === "yes" ? "Yes" : value === "no" ? "No" : "Unknown"}
                  </label>
                ))}
              </div>
              {errorId !== undefined && (
                <p className="xi-field-error" id={errorId} role="alert">
                  {(itemErrors[item.id] ?? []).join(" ")}
                </p>
              )}
            </fieldset>
          );
        })}
        {missing.length === 0 && evaluation?.status === "complete" && (
          <p className="xi-hint">
            Checklist complete — recorded per criterion with no total; urgent
            flag {state?.urgent === true ? "yes" : state?.urgent === false ? "no" : "unknown"}.
          </p>
        )}
      </div>
    );
  }

  function renderResults(key: EffectKey, state: EffectState | null): ReactNode {
    const evaluation = state?.questionnaire.evaluation ?? null;
    const missing = evaluation?.missing_item_ids ?? [];
    const scores = evaluation?.scores ?? null;

    if (key === "tardive_dyskinesia") {
      return (
        <div id={`effect-${key}-results`}>
          <p className="xi-hint">
            AIMS results: null (awaiting source — never zero, never a total).
          </p>
        </div>
      );
    }

    if (evaluation === null || evaluation.status !== "complete" || scores === null) {
      return (
        <div id={`effect-${key}-results`}>
          <p className="xi-hint">
            Results: null (never zero) until every required item is answered.
          </p>
          {missing.length > 0 && slices[key].status === "present" && (
            <div id={`effect-${key}-still-needed`}>
              <p className="xi-hint">Still needed:</p>
              <ul>
                {missing.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      );
    }

    if (key === "akathisia") {
      const findings = evaluation.findings ?? {};
      return (
        <div id={`effect-${key}-results`}>
          <table className="xi-table" aria-label="BARS scores">
            <thead>
              <tr>
                <th scope="col">Scale</th>
                <th scope="col">Score</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Objective /3</td>
                <td>{formatScore(scores["objective"])}</td>
              </tr>
              <tr>
                <td>Awareness /3</td>
                <td>{formatScore(scores["awareness"])}</td>
              </tr>
              <tr>
                <td>Distress /3</td>
                <td>{formatScore(scores["distress"])}</td>
              </tr>
              <tr>
                <td>Global /5 (principal severity)</td>
                <td>{formatScore(scores["global"])}</td>
              </tr>
              <tr>
                <td>Component total (descriptive, 0-9)</td>
                <td>{formatScore(scores["component_total"])}</td>
              </tr>
            </tbody>
          </table>
          <p className="xi-hint">
            {typeof findings["global_label"] === "string" &&
              `Global label: ${String(findings["global_label"])}. `}
            Threshold {findings["threshold_met"] === true ? "met" : "not met"} (global
            2 or greater indicates present).
            {typeof findings["pseudoakathisia_note"] === "string" &&
              ` ${String(findings["pseudoakathisia_note"])}`}
          </p>
        </div>
      );
    }

    if (key === "parkinsonism") {
      return (
        <div id={`effect-${key}-results`}>
          <table className="xi-table" aria-label="SAS scores">
            <thead>
              <tr>
                <th scope="col">Scale</th>
                <th scope="col">Score</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Raw total (0-40)</td>
                <td>{formatScore(scores["raw_total"])}</td>
              </tr>
              <tr>
                <td>Mean (0-4)</td>
                <td>{formatScore(scores["mean"])}</td>
              </tr>
            </tbody>
          </table>
          <p className="xi-hint">
            No mild/moderate/severe bands are defined from the total — the
            reviewed severity above is independent clinical judgment.
          </p>
        </div>
      );
    }

    return (
      <div id={`effect-${key}-results`}>
        <p className="xi-hint">
          Results: criteria recorded per item with no total score (never zero).
        </p>
      </div>
    );
  }

  // Urgent airway concern routes independently of checklist completion,
  // status, and severity: any yes answer (local edit or saved preview)
  // keeps the alert visible until the answer changes.
  const urgentLocal = slices.acute_dystonia.answers["addx_urgent_airway"] === "yes";
  const urgentSaved = previewEffects?.acute_dystonia?.urgent === true;
  const urgent = urgentLocal || urgentSaved;

  return (
    <section className="xi-card" aria-labelledby="effects-heading">
      <h3 className="xi-section-title" id="effects-heading">
        Adverse effects (step 6)
      </h3>
      <p className="xi-hint">
        Each of the four follow-up effects is present, absent, or not
        assessed. Nothing is pre-filled. Answers autosave through the same
        draft as the rest of this encounter (about one second after changing
        an answer). Full questionnaire completion is required only when the
        corresponding effect is present; absent and not-assessed effects
        carry null severity with no completion requirement. Every result
        below is computed by the server.
      </p>

      {urgent && (
        <div
          className="xi-urgent-panel"
          id="effect-acute_dystonia-urgent"
          role="alert"
          aria-labelledby="effect-urgent-heading"
        >
          <h4
            id="effect-urgent-heading"
            style={{ margin: "0 0 8px", fontSize: 16 }}
          >
            Urgent: possible airway compromise — arrange emergency assessment
            now
          </h4>
          <p style={{ marginTop: 0, marginBottom: 0 }}>
            Stridor or breathing difficulty was recorded (addx_urgent_airway
            yes), suggesting possible laryngeal dystonia. Do not delay airway
            evaluation to complete the checklist or wait for any other
            result. This warning persists until the answer changes.
          </p>
        </div>
      )}

      {EFFECT_KEYS.map((key) => {
        const local = slices[key];
        const state = previewEffects?.[key] ?? null;
        return (
          <div
            key={key}
            className="xi-effects-panel"
            aria-label={EFFECT_TITLES[key]}
          >
            <h4 className="xi-section-title" style={{ fontSize: 16 }}>
              {EFFECT_TITLES[key]}
            </h4>
            <fieldset className="xi-field">
              <legend className="xi-label">Effect status</legend>
              <div
                className="xi-radio-group"
                role="radiogroup"
                aria-label={`${EFFECT_TITLES[key]} status`}
              >
                {STATUSES.map((status) => (
                  <label key={status} htmlFor={`effect-${key}-${status}`}>
                    <input
                      id={`effect-${key}-${status}`}
                      type="radio"
                      name={`effect-${key}`}
                      value={status}
                      checked={local.status === status}
                      onChange={() => setStatus(key, status)}
                    />
                    {status === "present"
                      ? "Present"
                      : status === "absent"
                        ? "Absent"
                        : "Not assessed"}
                  </label>
                ))}
              </div>
            </fieldset>

            {local.status === null && (
              <p className="xi-hint">
                Choose present, absent, or not assessed. The questionnaire
                appears only for present.
              </p>
            )}

            {renderQuestionnaire(key, state)}
            {renderSeveritySelect(key, state)}
            {local.status === "present" && renderResults(key, state)}
          </div>
        );
      })}

      <div style={{ marginTop: 16 }}>
        <h4 className="xi-section-title" style={{ fontSize: 16 }}>
          Server effects preview
        </h4>
        {previewLoading && preview === null && (
          <p>Loading adverse-effects preview…</p>
        )}
        {previewError !== null && preview === null && (
          <div>
            <p className="xi-form-error" role="alert">
              {previewError}
            </p>
            <div className="xi-row-actions" style={{ marginTop: 8 }}>
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                onClick={() => {
                  setPreviewLoading(true);
                  setPreviewError(null);
                  void refreshPreview();
                }}
              >
                Retry
              </button>
            </div>
          </div>
        )}
        {preview !== null && (
          <div>
            {/* Verdict announces via a role-less live region (not
              role=status): the wizard's save status keeps the page's single
              status role per the S07 e2e contract. */}
            <p
              className="xi-status"
              id="effects-preview-status"
              aria-live="polite"
            >
              {verdict}
            </p>
            <p className="xi-hint">
              Server evaluation · definitions{" "}
              {EFFECT_KEYS.map(
                (key) =>
                  `${key} ${preview.definition_versions[key] ?? "?"}`,
              ).join(", ")}
              {" · "}saved revision {preview.revision}
              {answersPending &&
                " · unsaved edits pending — preview reflects the last saved revision"}
              {previewError !== null &&
                " · preview refresh failed; showing last loaded state"}
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
