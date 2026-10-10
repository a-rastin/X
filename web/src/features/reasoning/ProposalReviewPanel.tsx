/* Automatic proposal review and transparency UI (S48, plan.md §§8-9;
 * FR-15-16, FR-35-36, NFR-03).
 *
 * Wizard integration (not separate persistence): this section mounts inside
 * the shared S07 autosave wizard and uses its contract only — `flush()` on
 * entry, `hasUnsaved`/`saveState` as the start gate, and a fresh revision
 * read for every POST fence. It never writes draft_data and never touches
 * the chart: transparency shows exact SAVED inputs from persisted
 * `GET .../review` payloads, and only `safe_*` serializer fields are typed
 * or rendered (see ./api.ts), so page notes cannot leak by code path.
 *
 * Entry (S48 §1): flushes autosave, then automatically creates or reuses a
 * run — one POST per mount at most (`attemptedRef`), never per keystroke,
 * and never while a prerequisite gate is missing (dirty/saving/failed/
 * conflict autosave, unacknowledged below-threshold diagnosis, pending
 * medication reconciliation). Without previously-used packages there is
 * nothing honest to send (content bundles arrive in S39), so entry shows
 * the unavailable state instead of inventing packages; reloads resume from
 * GET via the stored batch id without a new POST.
 *
 * Progress (S48 §2): ordered per-question states derived by joining
 * `question_runs` + `baselines[].question_run_id` + `jobs[].
 * question_run_id` — pending/running/skipped/clarification/failed/completed/
 * stale, each with a text label plus a shape glyph (never color alone).
 * Partial sections stay visible but incomplete. Each failed question gets a
 * precise retry button that re-POSTs the same stored packages at the current
 * revision (the backend resumes at the failed stage, S47). Active runs poll
 * roughly every 2s with hidden-tab backoff; polling stops at terminal
 * states (workflow.complete or nothing left to run).
 *
 * Transparency (S48 §3): lazy per-question expansion fetches
 * `GET /question-runs/{id}/review` (persisted data, no recompute) and shows
 * all five fields — question_key, network_version, saved_patient_inputs
 * (source_path/revision/status), returned_cpt_percentages, and
 * deterministic_result {posteriors, section_text, query_nodes,
 * effective_hash} — plus prompt/template versions, hashes, and provenance.
 * CPT tables ("LLM-estimated") and posterior values ("computed") are labeled
 * distinctly. Failed originals expose no baseline (`adjustable` false).
 *
 * Proposal + sign (S48 §4): final proposal sections (position-ordered) with
 * skipped reasons and DDI coverage_warnings/ddi_report (coverage_unavailable
 * kept explicit, never "safe"). Failed/stale/partial runs render a disabled
 * sign entry with the exact reason while draft editing and retry stay
 * available. The secondary plan is a placeholder link only (S50 owns
 * editing). A null proposal renders the honest unavailable state, matching
 * the ChartPage precedent.
 *
 * Accessibility + theming: native headings/lists/tables/buttons/details
 * (keyboard free), status role=status, errors role=alert with focus,
 * semantic tokens only (both themes), ink text throughout, no animation
 * (global reduced-motion rule covers).
 *
 * E2E selector contract (for dev-test e2e/proposal-review.spec.ts):
 * - panel `data-testid="proposal-review"` (`#proposal-review`)
 * - per-question status `data-testid="proposal-status-<question_key>"`
 * - per-question freshness `data-testid="proposal-freshness-<question_key>"`
 *   (S48d, stale batches only: out-of-date ◍ vs current-inputs per question)
 * - per-question retry `data-testid="proposal-retry-<question_key>"`
 *   (failed runs with recorded packages only)
 * - per-question transparency region
 *   `data-testid="proposal-transparency-<question_key>"` (after expansion)
 * - per-question CPT review `data-testid="cpt-panel-<question_key>"` (own
 *   lazy toggle `cpt-toggle-<key>`; accept/reset/retry
 *   `cpt-accept/cpt-reset/cpt-retry-<key>` — see CptReviewPanel header;
 *   full journey assertions belong to dev-test e2e/probability-review.spec.ts)
 * - DDI block `data-testid="proposal-ddi"` (`#proposal-ddi`)
 * - blocked sign entry `data-testid="proposal-sign-blocked"`
 *   (`#proposal-sign-blocked`)
 * Storage contract (dev-test seeds this to attach the UI to an API-created
 * batch): localStorage `xi.proposal.<encounterId>` =
 * `{batchId, packages, sourceRevision}` (see ./api.ts).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../identity/api";
import { chartHash } from "../../app/router";
import { getEncounter } from "../encounters/api";
import type { useAutosave } from "../encounters/useAutosave";
import { CptReviewPanel } from "./CptReviewPanel";
import { getDiagnosis, type DiagnosisPreview } from "../assessments/diagnosis/api";
import { getMedications, type MedicationsPreview } from "../medications/api";
import {
  clearStoredProposal,
  getBatch,
  getQuestionInputFreshness,
  getQuestionReview,
  readStoredProposal,
  startGeneration,
  writeStoredProposal,
  type BaselineEntry,
  type BatchPayload,
  type InputFreshness,
  type Posterior,
  type ProjectionVariable,
  type QuestionRun,
  type QuestionTransparency,
  type ReasonJob,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

interface ProposalReviewPanelProps {
  encounterId: string;
  autosave: AutosaveApi;
  onSessionExpired: () => void;
}

type Phase =
  | { kind: "boot" }
  | { kind: "waiting-save" }
  | { kind: "blocked"; message: string }
  | { kind: "unavailable"; message: string }
  | { kind: "starting" }
  | { kind: "error"; message: string }
  | { kind: "tracking" };

type RunDisplay = "pending" | "running" | "skipped" | "clarification" | "failed" | "completed" | "stale";

const POLL_MS = 2000;
const HIDDEN_POLL_MS = 15000;

const GLYPH: Record<RunDisplay, string> = {
  pending: "○",
  running: "◐",
  skipped: "■",
  clarification: "▲",
  failed: "✕",
  completed: "●",
  stale: "◍",
};

const LABEL: Record<RunDisplay, string> = {
  pending: "Pending",
  running: "Running",
  skipped: "Skipped",
  clarification: "Needs clarification",
  failed: "Failed",
  completed: "Completed",
  stale: "Stale",
};

interface JoinedRun {
  run: QuestionRun;
  entry: BaselineEntry | null;
  job: ReasonJob | null;
  position: number;
  display: RunDisplay;
  detail: string;
}

function jobResultError(job: ReasonJob | null): string | null {
  if (job === null || typeof job.result !== "object" || job.result === null) {
    return null;
  }
  const code = (job.result as Record<string, unknown>)["error_code"];
  return typeof code === "string" && code !== "" ? code : null;
}

function classify(joined: Omit<JoinedRun, "display" | "detail">): Pick<JoinedRun, "display" | "detail"> {
  const { run, entry, job } = joined;
  if (run.status === "not_applicable") {
    return { display: "skipped", detail: run.gate_reason !== "" ? run.gate_reason : "Not applicable." };
  }
  if (run.status === "needs_clarification") {
    return { display: "clarification", detail: run.gate_reason !== "" ? run.gate_reason : "Required input is missing or conflicting." };
  }
  if (run.status === "stale") {
    return { display: "stale", detail: "Superseded by newer inputs." };
  }
  if (entry?.baseline !== null && entry?.baseline !== undefined) {
    return { display: "completed", detail: "Baseline completed and section retained." };
  }
  if (job !== null) {
    if (job.status === "failed") {
      const code = jobResultError(job);
      const attempts = `Failed after ${job.attempt_index} of ${job.max_attempts} attempts.`;
      return { display: "failed", detail: code !== null ? `${attempts} Last error: ${code}.` : `${attempts} Later questions wait.` };
    }
    if (job.status === "cancelled") {
      return { display: "stale", detail: "Cancelled — inputs changed while running." };
    }
    if (job.status === "leased") {
      return { display: "running", detail: "Worker is executing this question." };
    }
    if (job.status === "queued") {
      return {
        display: "pending",
        detail: job.next_eligible_at !== null ? "Queued — automatic retry is scheduled." : "Queued — waiting for a provider slot.",
      };
    }
    return { display: "running", detail: "Finishing." };
  }
  return { display: "pending", detail: "Waiting for earlier questions." };
}

function joinRuns(batch: BatchPayload): JoinedRun[] {
  const jobsByRun = new Map<string, ReasonJob>();
  for (const job of batch.jobs ?? []) {
    if (!jobsByRun.has(job.question_run_id)) {
      jobsByRun.set(job.question_run_id, job);
    }
  }
  const entryByRun = new Map<string, BaselineEntry>();
  for (const entry of batch.baselines ?? []) {
    entryByRun.set(entry.question_run_id, entry);
  }
  return (batch.question_runs ?? [])
    .map((run, index) => {
      const entry = entryByRun.get(run.id) ?? null;
      const job = jobsByRun.get(run.id) ?? null;
      const base = { run, entry, job, position: entry?.position ?? index };
      return { ...base, ...classify(base) };
    })
    .sort((a, b) => a.position - b.position || a.run.question_key.localeCompare(b.run.question_key));
}

/** Poll while work can still progress: an active job, or an unblocked run
 * still waiting for its first job. Failed/clarification predecessors block
 * their successors, so an exhausted batch stops polling (manual retry starts
 * a new batch instead). */
function pollActive(batch: BatchPayload, joined: JoinedRun[]): boolean {
  if (batch.workflow?.complete === true) {
    return false;
  }
  const jobs = batch.jobs ?? [];
  if (jobs.some((job) => job.status === "queued" || job.status === "leased")) {
    return true;
  }
  let blocked = false;
  for (const item of joined) {
    if (item.display === "failed" || item.display === "clarification" || item.display === "stale") {
      blocked = true;
    }
    if (!blocked && item.display === "pending" && item.run.status === "ready" && item.job === null) {
      return true;
    }
    if (item.display === "running") {
      return true;
    }
  }
  return false;
}

function signBlockReason(batch: BatchPayload, joined: JoinedRun[]): string | null {
  if (batch.freshness?.stale === true) {
    return `Run is stale — ${batch.freshness.reason}. Signing is blocked; start a new run for the current inputs. Draft editing stays available.`;
  }
  const failed = joined.filter((item) => item.display === "failed").map((item) => item.run.question_key);
  if (failed.length > 0) {
    return `Signing is blocked — ${failed.length} question${failed.length === 1 ? " has" : "s have"} failed (${failed.join(", ")}). Retry the failed question below; draft editing stays available.`;
  }
  const clarification = joined.filter((item) => item.display === "clarification").map((item) => item.run.question_key);
  if (clarification.length > 0) {
    return `Signing is blocked — ${clarification.join(", ")} need${clarification.length === 1 ? "s" : ""} clarification. Correct the draft inputs, then start a new run.`;
  }
  if (batch.proposal === null || batch.workflow?.complete !== true) {
    const pending = batch.workflow?.pending_question_keys ?? [];
    const tail = pending.length > 0 ? ` Waiting on: ${pending.join(", ")}.` : "";
    return `Signing is blocked — the proposal is incomplete (not review-ready).${tail} Partial sections above stay readable.`;
  }
  return null;
}

function incompleteReason(batch: BatchPayload): string {
  const pending = batch.workflow?.pending_question_keys ?? [];
  const clarification = batch.workflow?.needs_clarification ?? [];
  if (clarification.length > 0) {
    return `needs clarification for ${clarification.join(", ")} — correct the draft inputs, then start a new run.`;
  }
  if (pending.length > 0) {
    return `waiting on ${pending.join(", ")}.`;
  }
  if (batch.workflow !== undefined && batch.workflow.ddi_status !== "valid") {
    return `the pinned DDI report is ${batch.workflow.ddi_status}.`;
  }
  return "runs have not completed yet.";
}

function pairStatusText(status: string): string {
  if (status === "interaction_found") {
    return "Interaction found";
  }
  if (status === "covered_no_listed_interaction") {
    return "No listed interaction in this dataset";
  }
  return "Coverage unavailable";
}

function inputValueText(variable: ProjectionVariable): string {
  if (variable.status !== "observed") {
    return variable.status === "not_assessed" ? "Not assessed" : variable.status;
  }
  if (typeof variable.value === "string") {
    return variable.value === "" ? "—" : variable.value;
  }
  if (variable.value === null || variable.value === undefined) {
    return "—";
  }
  try {
    return JSON.stringify(variable.value) ?? "—";
  } catch {
    return "—";
  }
}

function posteriorSummary(posteriors: Posterior[]): string {
  return posteriors
    .map((post) => {
      const states = post.states ?? [];
      const probs = post.probabilities ?? [];
      const pairs = states.map((state, index) => {
        const prob = typeof probs[index] === "number" ? (probs[index] as number) * 100 : NaN;
        return `${state} ${Number.isFinite(prob) ? prob.toFixed(2) : "?"}%`;
      });
      return `${post.node_id}: ${pairs.join(", ")}`;
    })
    .join("; ");
}

interface ReviewState {
  status: "loading" | "ready" | "error";
  transparency: QuestionTransparency | null;
  baselineId: string | null;
  networkVersion: string | null;
  promptVersion: string | null;
  templateVersion: string | null;
  effectiveHash: string | null;
  provenance: Record<string, unknown>;
  message: string | null;
}

export function ProposalReviewPanel({ encounterId, autosave, onSessionExpired }: ProposalReviewPanelProps) {
  const [phase, setPhase] = useState<Phase>({ kind: "boot" });
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<BatchPayload | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [pkgsKnown, setPkgsKnown] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [reviews, setReviews] = useState<Record<string, ReviewState>>({});
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [patientId, setPatientId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  // S48d affected-only freshness: per-question input_freshness by run id,
  // fetched only while the batch is stale (fresh batches imply every
  // question is fresh). `freshDone` distinguishes "still checking" from a
  // failed fetch (failed entries stay absent; the batch banner still covers
  // them — never claim a question is fresh without a successful read).
  const [freshByRun, setFreshByRun] = useState<Record<string, InputFreshness>>({});
  const [freshDone, setFreshDone] = useState(false);
  const freshKeyRef = useRef<string | null>(null);
  const [prereq, setPrereq] = useState<{
    diag: DiagnosisPreview | null;
    meds: MedicationsPreview | null;
    failed: boolean;
  }>({ diag: null, meds: null, failed: false });

  const attemptedRef = useRef(false);
  const batchIdRef = useRef<string | null>(null);
  const actionErrorRef = useRef<HTMLParagraphElement>(null);

  const failAs = useCallback((err: unknown, fallback: string): string => {
    if (err instanceof ApiError) {
      return err.message;
    }
    return fallback;
  }, []);

  useEffect(() => {
    if (actionError !== null) {
      actionErrorRef.current?.focus();
    }
  }, [actionError]);

  // Mount: remember flush + load prerequisites + encounter meta + stored batch.
  useEffect(() => {
    attemptedRef.current = false;
    batchIdRef.current = null;
    setBatch(null);
    setBatchId(null);
    setPhase({ kind: "boot" });
    setActionError(null);
    setPollError(null);
    setExpanded({});
    setReviews({});
    setFreshByRun({});
    setFreshDone(false);
    freshKeyRef.current = null;
    const stored = readStoredProposal(encounterId);
    setPkgsKnown(stored !== null && Array.isArray(stored.packages) && stored.packages.length > 0);
    if (stored !== null) {
      batchIdRef.current = stored.batchId;
      setBatchId(stored.batchId);
      setPhase({ kind: "tracking" });
    } else {
      // Page-transition flush: persist pending edits before any generation
      // request so the run freezes acknowledged values.
      void autosave.flush().catch(() => {
        /* flush failure surfaces via saveState below; never throws here */
      });
      setPhase({ kind: "waiting-save" });
    }
    let cancelled = false;
    void (async () => {
      try {
        const [diag, meds, meta] = await Promise.all([
          getDiagnosis(encounterId),
          getMedications(encounterId),
          getEncounter(encounterId),
        ]);
        if (!cancelled) {
          setPrereq({ diag, meds, failed: false });
          const pid = (meta.encounter as { patient_id?: unknown }).patient_id;
          setPatientId(typeof pid === "string" ? pid : null);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 401) {
            onSessionExpired();
            return;
          }
          // Prerequisite reads failed: stay conservative — block automatic
          // start until the gates are readable (Check again re-runs entry).
          setPrereq({ diag: null, meds: null, failed: true });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // Mount-per-encounter only: later autosave revisions must not retrigger
    // entry (no run per keystroke); staleness is shown, refresh is explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [encounterId]);

  const runStart = useCallback(async () => {
    setActionError(null);
    setPollError(null);
    const stored = readStoredProposal(encounterId);
    const packages = stored?.packages ?? null;
    if (packages === null || packages.length === 0) {
      // No honest body to send: content bundles (S39) pin the question
      // packages server-side. Never invent packages for a real encounter.
      setPhase({
        kind: "unavailable",
        message:
          "No generation run yet for this draft and no question packages are recorded in this browser. Automatic generation starts once the pinned content bundle supplies the question packages (S39); the draft below stays editable.",
      });
      return;
    }
    setPhase({ kind: "starting" });
    setActionBusy(true);
    try {
      await autosave.flush().catch(() => {
        /* fence below reads the fresh revision regardless */
      });
      const meta = await getEncounter(encounterId);
      const started = await startGeneration(encounterId, meta.revision, packages);
      writeStoredProposal(encounterId, {
        batchId: started.batch.id,
        packages,
        sourceRevision: meta.revision,
      });
      setPkgsKnown(true);
      batchIdRef.current = started.batch.id;
      setBatch(null);
      setBatchId(started.batch.id);
      setPhase({ kind: "tracking" });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setPhase({
        kind: "error",
        message:
          err instanceof ApiError
            ? `Generation could not start: ${err.message}`
            : "Generation could not start. Check the connection and try again.",
      });
    } finally {
      setActionBusy(false);
    }
  }, [encounterId, autosave, onSessionExpired]);

  // Automatic entry: exactly one attempt per mount, only when the draft is
  // saved, the gates are readable, and no stored batch exists.
  const saveKind = autosave.saveState.kind;
  const hasUnsaved = autosave.hasUnsaved;
  useEffect(() => {
    // ponytail: ref guard — batchId state is still null in the mount commit
    // where restore already set tracking; state alone would POST over it.
    if (batchId !== null || batchIdRef.current !== null || attemptedRef.current) {
      return;
    }
    if (saveKind !== "saved" || hasUnsaved) {
      return;
    }
    if (prereq.diag === null || prereq.meds === null) {
      return;
    }
    attemptedRef.current = true;
    const diagBlocked =
      prereq.diag.requires_acknowledgment &&
      !prereq.diag.acknowledgment_valid &&
      !prereq.diag.bypass_valid;
    if (diagBlocked) {
      setPhase({
        kind: "blocked",
        message:
          "Diagnosis is completed below threshold without a valid acknowledgment or bypass. Generation stays off until the Diagnosis step is acknowledged or bypassed.",
      });
      return;
    }
    if (prereq.meds.reconciliation?.status === "pending") {
      setPhase({
        kind: "blocked",
        message:
          "Medication reconciliation is pending. Generation stays off until the copied list is reconciled in the Medications step.",
      });
      return;
    }
    void runStart();
  }, [batchId, saveKind, hasUnsaved, prereq, runStart]);

  // Surface save failures/conflicts and unreadable gates while still
  // pre-start (post-start, the wizard's own save UI owns those states).
  useEffect(() => {
    // ponytail: same mount-commit race — ref already holds the restored
    // batchId while state is still null; state alone would clobber
    // tracking back to waiting-save.
    if (batchId !== null || batchIdRef.current !== null || attemptedRef.current) {
      return;
    }
    if (saveKind === "failed" || saveKind === "conflict") {
      setPhase({
        kind: "blocked",
        message:
          "The draft save needs attention before generation can start. Resolve the save state above, then check again.",
      });
    } else if (prereq.failed) {
      setPhase({
        kind: "blocked",
        message:
          "Prerequisite checks (diagnosis, medications) could not be read. Generation stays off until they load.",
      });
    } else if (phase.kind === "blocked" || phase.kind === "unavailable" || phase.kind === "error") {
      // Keep terminal pre-start phases stable across save churn.
    } else {
      setPhase({ kind: "waiting-save" });
    }
    // Intentionally keyed on save/prereq transitions only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId, saveKind, prereq.failed]);

  // Tracking: GET + poll while active, hidden-tab backoff, stop at terminal.
  useEffect(() => {
    if (batchId === null) {
      return;
    }
    let cancelled = false;
    let timer: number | null = null;
    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const tick = async () => {
      clearTimer();
      if (cancelled || batchIdRef.current !== batchId) {
        return;
      }
      try {
        const payload = await getBatch(batchId);
        if (cancelled || batchIdRef.current !== batchId) {
          return;
        }
        setBatch(payload);
        setPollError(null);
        if (!pollActive(payload, joinRuns(payload))) {
          return;
        }
      } catch (err) {
        if (cancelled || batchIdRef.current !== batchId) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          onSessionExpired();
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          clearStoredProposal(encounterId);
          setBatch(null);
          setBatchId(null);
          batchIdRef.current = null;
          setPhase({
            kind: "unavailable",
            message: "The recorded run is no longer available. Check again to start a new run.",
          });
          return;
        }
        setPollError(failAs(err, "Run failed to refresh. Check the connection."));
      }
      timer = window.setTimeout(
        () => {
          void tick();
        },
        typeof document !== "undefined" && document.hidden ? HIDDEN_POLL_MS : POLL_MS,
      );
    };
    const onVisibility = () => {
      if (typeof document === "undefined" || document.hidden) {
        return;
      }
      void tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void tick();
    return () => {
      cancelled = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [batchId, reloadKey, encounterId, failAs, onSessionExpired]);

  // S48d affected-only labels (FR-59; seam T1): the whole-batch fingerprint
  // moves on any relevant edit, so a stale batch banner alone cannot say
  // which questions are affected. When (and only when) the batch is stale,
  // fetch each run's per-question input_freshness via the public GET review.
  // Skipped/clarification runs are not labeled here — their gate message
  // already explains them. Failures stay unlabeled (batch banner covers).
  const freshKey = batchId !== null ? `${batchId}|${reloadKey}` : null;
  useEffect(() => {
    if (phase.kind !== "tracking" || batch === null || batchId === null || freshKey === null) {
      return;
    }
    if (batch.freshness?.stale !== true) {
      return;
    }
    // ponytail: ref guard — batch identity changes on every poll tick;
    // one fetch per batch per explicit refresh, not per tick.
    if (freshKeyRef.current === freshKey) {
      return;
    }
    freshKeyRef.current = freshKey;
    setFreshByRun({});
    setFreshDone(false);
    let cancelled = false;
    void (async () => {
      const settled = await Promise.all(
        (batch.question_runs ?? []).map(async (run) => {
          try {
            return { id: run.id, fresh: await getQuestionInputFreshness(run.id) };
          } catch {
            return { id: run.id, fresh: null as InputFreshness | null };
          }
        }),
      );
      if (cancelled) {
        return;
      }
      const next: Record<string, InputFreshness> = {};
      for (const entry of settled) {
        if (entry.fresh !== null) {
          next[entry.id] = entry.fresh;
        }
      }
      setFreshByRun(next);
      setFreshDone(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [phase.kind, batch, batchId, freshKey]);

  /** Precise failed-question retry: re-POST the same stored packages at the
   * current revision. The backend carries completed baselines forward and
   * resumes at the failed stage (S47); unchanged inputs reuse the batch. */
  const retryRun = useCallback(async () => {
    const stored = readStoredProposal(encounterId);
    const packages = stored?.packages ?? null;
    if (packages === null || packages.length === 0) {
      setActionError(
        "Retry is unavailable — the original request packages were not recorded in this browser, so there is nothing exact to resend.",
      );
      return;
    }
    setActionError(null);
    setActionBusy(true);
    try {
      await autosave.flush().catch(() => {
        /* fence below reads the fresh revision regardless */
      });
      const meta = await getEncounter(encounterId);
      const started = await startGeneration(encounterId, meta.revision, packages);
      writeStoredProposal(encounterId, {
        batchId: started.batch.id,
        packages,
        sourceRevision: meta.revision,
      });
      setPkgsKnown(true);
      batchIdRef.current = started.batch.id;
      setBatch(null);
      setPollError(null);
      setBatchId(started.batch.id);
      setPhase({ kind: "tracking" });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setActionError(
        err instanceof ApiError
          ? `Retry failed: ${err.message}`
          : "Retry failed. Check the connection and try again.",
      );
    } finally {
      setActionBusy(false);
    }
  }, [encounterId, autosave, onSessionExpired]);

  const toggleTransparency = useCallback(
    async (questionKey: string, runId: string) => {
      const open = !(expanded[questionKey] ?? false);
      setExpanded((previous) => ({ ...previous, [questionKey]: open }));
      if (!open) {
        return;
      }
      const cached = reviews[runId];
      if (cached !== undefined && cached.status !== "error") {
        return;
      }
      setReviews((previous) => ({
        ...previous,
        [runId]: {
          status: "loading",
          transparency: null,
          baselineId: null,
          networkVersion: null,
          promptVersion: null,
          templateVersion: null,
          effectiveHash: null,
          provenance: {},
          message: null,
        },
      }));
      try {
        // Persisted review data only — never recomputed from current chart.
        const review = await getQuestionReview(runId);
        if (review.transparency === null || review.baseline === null) {
          setReviews((previous) => ({
            ...previous,
            [runId]: {
              status: "ready",
              transparency: null,
              baselineId: null,
              networkVersion: null,
              promptVersion: null,
              templateVersion: null,
              effectiveHash: null,
              provenance: {},
              message: "No completed baseline — this question has not completed.",
            },
          }));
          return;
        }
        setReviews((previous) => ({
          ...previous,
          [runId]: {
            status: "ready",
            transparency: review.transparency,
            baselineId: review.baseline?.id ?? null,
            networkVersion: review.baseline?.network_version ?? null,
            promptVersion: review.baseline?.prompt_version ?? null,
            templateVersion: review.baseline?.template_version ?? null,
            effectiveHash: review.baseline?.effective_hash ?? null,
            provenance: review.baseline?.provenance ?? {},
            message: null,
          },
        }));
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          onSessionExpired();
          return;
        }
        setReviews((previous) => ({
          ...previous,
          [runId]: {
            status: "error",
            transparency: null,
            baselineId: null,
            networkVersion: null,
            promptVersion: null,
            templateVersion: null,
            effectiveHash: null,
            provenance: {},
            message: failAs(err, "Transparency failed to load. Check the connection and retry."),
          },
        }));
      }
    },
    [expanded, reviews, failAs, onSessionExpired],
  );

  const checkAgain = useCallback(() => {
    setActionError(null);
    if (batchId !== null) {
      // Resume path: re-GET the recorded batch without a new POST.
      setBatch(null);
      setPollError(null);
      setReloadKey((value) => value + 1);
      setPhase({ kind: "tracking" });
      return;
    }
    // Pre-start path: re-read the gates and let the entry effect decide
    // (re-checks diagnosis/medication/save gates before any POST).
    attemptedRef.current = false;
    setPrereq({ diag: null, meds: null, failed: false });
    void (async () => {
      try {
        const [diag, meds] = await Promise.all([
          getDiagnosis(encounterId),
          getMedications(encounterId),
        ]);
        setPrereq({ diag, meds, failed: false });
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          onSessionExpired();
          return;
        }
        setPrereq({ diag: null, meds: null, failed: true });
      }
    })();
    void autosave.flush().catch(() => {
      /* save state drives the entry effect */
    });
    setPhase({ kind: "waiting-save" });
  }, [batchId, encounterId, autosave, onSessionExpired]);

  const joined = batch !== null ? joinRuns(batch) : [];
  const blockReason = batch !== null ? signBlockReason(batch, joined) : null;

  return (
    <section className="xi-card" aria-labelledby="proposal-review-heading" id="proposal-review" data-testid="proposal-review">
      <h3 className="xi-section-title" id="proposal-review-heading">
        Proposal review (step 8)
      </h3>
      <p className="xi-hint" style={{ marginTop: 0 }}>
        Entering this step flushes the draft and starts generation automatically once the draft is
        saved and the diagnosis and medication gates pass. Reloads resume the recorded run without
        a new request.
      </p>

      {actionError !== null && (
        <p className="xi-form-error" role="alert" tabIndex={-1} ref={actionErrorRef}>
          {actionError}
        </p>
      )}

      {(phase.kind === "boot" || phase.kind === "waiting-save") && (
        <p className="xi-hint" role="status">
          {saveKind === "saved" && !hasUnsaved
            ? "Checking prerequisites…"
            : "Saving draft changes… generation starts after the save is acknowledged."}
        </p>
      )}

      {phase.kind === "blocked" && (
        <div className="xi-warning-panel" role="status">
          <p style={{ margin: 0 }}>Generation paused: {phase.message}</p>
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            See the <a href="#diagnosis-preview-status">diagnosis status</a> and{" "}
            <a href="#meds-save-status">medications status</a> above.
          </p>
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button className="xi-btn xi-btn-secondary" type="button" onClick={checkAgain}>
              Check again
            </button>
          </div>
        </div>
      )}

      {phase.kind === "unavailable" && (
        <div className="xi-notice" role="status">
          <p style={{ margin: 0 }}>Proposal unavailable — {phase.message}</p>
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button className="xi-btn xi-btn-secondary" type="button" onClick={checkAgain}>
              Check again
            </button>
          </div>
        </div>
      )}

      {phase.kind === "starting" && <p role="status">Starting generation…</p>}

      {phase.kind === "error" && (
        <div>
          <p className="xi-form-error" role="alert">
            {phase.message}
          </p>
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={actionBusy}
              onClick={() => void checkAgain()}
            >
              {actionBusy ? "Retrying…" : "Try again"}
            </button>
          </div>
        </div>
      )}

      {phase.kind === "tracking" && batch === null && (
        <p role="status">{pollError ?? "Loading run…"}</p>
      )}

      {phase.kind === "tracking" && batch !== null && (
        <div>
          <BatchHeader batch={batch} />
          {batch.freshness?.stale === true && (
            <div className="xi-warning-panel" role="status">
              <p style={{ margin: 0 }}>
                This run is stale — {batch.freshness.reason}. Only affected questions are
                marked out of date below; unaffected questions stay valid and carry
                forward with their acceptance intact. New inputs need a new run; this
                run stays readable history.
              </p>
              {pkgsKnown ? (
                <div className="xi-row-actions" style={{ marginTop: 8 }}>
                  <button
                    className="xi-btn xi-btn-secondary"
                    type="button"
                    disabled={actionBusy}
                    onClick={() => void retryRun()}
                  >
                    {actionBusy ? "Starting…" : "Start new run with current inputs"}
                  </button>
                </div>
              ) : (
                <p className="xi-hint" style={{ marginBottom: 0 }}>
                  The original request packages were not recorded in this browser, so a new run
                  cannot be started from here.
                </p>
              )}
            </div>
          )}
          {pollError !== null && (
            <p className="xi-form-error" role="alert">
              {pollError}
            </p>
          )}

          <h4 className="xi-section-title" style={{ fontSize: 16 }}>
            Questions ({joined.length})
          </h4>
          {joined.length === 0 ? (
            <p className="xi-hint" role="status">
              No questions in this run.
            </p>
          ) : (
            <ul className="xi-proposal-runs">
              {joined.map((item) => (
                <li key={item.run.id} className="xi-proposal-run">
                  <QuestionBlock
                    item={item}
                    expanded={expanded[item.run.question_key] ?? false}
                    review={reviews[item.run.id] ?? null}
                    fresh={freshByRun[item.run.id] ?? null}
                    freshDone={freshDone}
                    batchStale={batch.freshness?.stale === true}
                    pkgsKnown={pkgsKnown}
                    actionBusy={actionBusy}
                    onSessionExpired={onSessionExpired}
                    onToggle={() => void toggleTransparency(item.run.question_key, item.run.id)}
                    onRetry={() => void retryRun()}
                    onReviewRetry={() => void toggleTransparency(item.run.question_key, item.run.id)}
                  />
                </li>
              ))}
            </ul>
          )}

          <ProposalBlock batch={batch} />

          {blockReason !== null ? (
            <div
              className="xi-warning-panel"
              data-testid="proposal-sign-blocked"
              id="proposal-sign-blocked"
              role="status"
            >
              <h4 className="xi-section-title" style={{ fontSize: 16 }}>
                Sign-off blocked
              </h4>
              <p style={{ marginTop: 0 }}>{blockReason}</p>
              <div className="xi-row-actions">
                <button className="xi-btn xi-btn-secondary" type="button" disabled>
                  Sign encounter (blocked)
                </button>
              </div>
              <p className="xi-hint" style={{ marginBottom: 0 }}>
                Draft editing above stays available, and each failed question keeps its own retry
                action.
              </p>
            </div>
          ) : (
            <div className="xi-notice" role="status">
              <p style={{ margin: 0 }}>
                Proposal complete and current. Secondary plan editing and sign-off arrive in
                S49/S50 — nothing here signs yet.
              </p>
            </div>
          )}

          <section className="xi-card" aria-labelledby="secondary-plan-heading" style={{ marginBottom: 0 }}>
            <h4 className="xi-section-title" id="secondary-plan-heading" style={{ fontSize: 16 }}>
              Secondary plan (S50)
            </h4>
            <p className="xi-hint" style={{ marginBottom: 8 }}>
              The physician-edited secondary plan lands in S50. This placeholder holds its place;
              no plan editing happens here.
            </p>
            {patientId !== null && (
              <p style={{ margin: 0 }}>
                <a href={chartHash(patientId)}>View shared chart</a>
              </p>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

function BatchHeader({ batch }: { batch: BatchPayload }) {
  const busy = batch.queue?.busy === true;
  const position = batch.queue?.queue_position;
  return (
    <div className="xi-proposal-meta">
      <p className="xi-hint" role="status" style={{ marginBottom: 4 }}>
        Run <code className="xi-mono">{batch.batch.id}</code> · batch gate {batch.batch.status} ·
        source revision {batch.batch.source_revision} ·{" "}
        {batch.workflow?.complete === true ? "complete" : "incomplete"} ·{" "}
        {batch.freshness?.stale === true ? "stale inputs" : "current inputs"}
      </p>
      <p className="xi-hint" style={{ marginTop: 0 }}>
        Fingerprint <code className="xi-mono">{batch.batch.fingerprint}</code>
      </p>
      {busy && (
        <p className="xi-warning-panel" role="status">
          Provider queue is busy
          {position !== null && position !== undefined ? ` — queue position ${position}` : ""}.
          This run waits; saved drafts are unaffected.
        </p>
      )}
    </div>
  );
}

function QuestionBlock({
  item,
  expanded,
  review,
  fresh,
  freshDone,
  batchStale,
  pkgsKnown,
  actionBusy,
  onSessionExpired,
  onToggle,
  onRetry,
  onReviewRetry,
}: {
  item: JoinedRun;
  expanded: boolean;
  review: ReviewState | null;
  fresh: InputFreshness | null;
  freshDone: boolean;
  batchStale: boolean;
  pkgsKnown: boolean;
  actionBusy: boolean;
  onSessionExpired: () => void;
  onToggle: () => void;
  onRetry: () => void;
  onReviewRetry: () => void;
}) {
  const key = item.run.question_key;
  const regionId = `proposal-transparency-region-${key}`;
  // S48d: gate-explained runs (skipped/clarification) keep their gate
  // message; per-question freshness labels apply to the rest so affected
  // runs show out-of-date ◍ with a regenerate affordance while unaffected
  // retain valid references + accepted state (shown in their CPT panel).
  const showFresh =
    batchStale && item.display !== "skipped" && item.display !== "clarification";
  return (
    <div data-testid={`proposal-status-${key}`} id={`proposal-status-${key}`}>
      <p className="xi-proposal-status">
        <span aria-hidden="true">{GLYPH[item.display]}</span>{" "}
        <span>
          <strong>{key}</strong> — {LABEL[item.display]}
        </span>
      </p>
      <p className="xi-hint" style={{ marginTop: 0 }}>
        Position {item.position} · {item.detail}
      </p>
      {showFresh && (
        <div data-testid={`proposal-freshness-${key}`} role="status">
          {fresh !== null && fresh.stale ? (
            <p className="xi-hint" style={{ marginTop: 0 }}>
              <span aria-hidden="true">◍</span> Out of date — {fresh.reason}. Only this
              question needs regeneration; starting a new run above regenerates
              affected questions while unaffected ones carry forward.
            </p>
          ) : fresh !== null ? (
            <p className="xi-hint" style={{ marginTop: 0 }}>
              <span aria-hidden="true">●</span> Current inputs — this question stays valid
              and is carried into the next run without recalculation; its acceptance
              stands.
            </p>
          ) : !freshDone ? (
            <p className="xi-hint" style={{ marginTop: 0 }}>
              Checking which questions are affected…
            </p>
          ) : null}
        </div>
      )}
      {item.display === "failed" && (
        <div>
          {pkgsKnown ? (
            <div className="xi-row-actions">
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                data-testid={`proposal-retry-${key}`}
                disabled={actionBusy}
                onClick={onRetry}
              >
                {actionBusy ? "Retrying…" : `Retry ${key}`}
              </button>
            </div>
          ) : (
            <p className="xi-hint">
              Retry unavailable — the original request packages were not recorded in this browser.
            </p>
          )}
        </div>
      )}
      <div className="xi-row-actions" style={{ marginTop: 8 }}>
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          aria-expanded={expanded}
          aria-controls={regionId}
          onClick={onToggle}
        >
          {expanded ? `Hide transparency for ${key}` : `Show transparency for ${key}`}
        </button>
      </div>
      {/* S48c complete CPT review (plan.md §9.1; FR-50-57): own lazy toggle +
        own GET .../review so the S48 transparency fetch counts above stay
        untouched. Per-question CPT sliders, comparison, reset/retry, and
        acceptance live in CptReviewPanel; testids cpt-panel-<key> /
        cpt-accept-<key>. */}
      <CptReviewPanel questionKey={key} runId={item.run.id} onSessionExpired={onSessionExpired} />
      {expanded && (
        <div data-testid={`proposal-transparency-${key}`} id={regionId}>
          {review === null || review.status === "loading" ? (
            <p role="status">Loading persisted transparency…</p>
          ) : review.status === "error" ? (
            <div>
              <p className="xi-form-error" role="alert">
                {review.message}
              </p>
              <div className="xi-row-actions" style={{ marginTop: 8 }}>
                <button className="xi-btn xi-btn-secondary" type="button" onClick={onReviewRetry}>
                  Retry transparency
                </button>
              </div>
            </div>
          ) : review.transparency === null ? (
            <p className="xi-hint" role="status">
              {review.message ?? "Transparency unavailable for this question."}
            </p>
          ) : (
            <TransparencyView
              transparency={review.transparency}
              baselineId={review.baselineId}
              networkVersion={review.networkVersion}
              promptVersion={review.promptVersion}
              templateVersion={review.templateVersion}
              effectiveHash={review.effectiveHash}
              provenance={review.provenance}
            />
          )}
        </div>
      )}
    </div>
  );
}

function TransparencyView({
  transparency,
  baselineId,
  networkVersion,
  promptVersion,
  templateVersion,
  effectiveHash,
  provenance,
}: {
  transparency: QuestionTransparency;
  baselineId: string | null;
  networkVersion: string | null;
  promptVersion: string | null;
  templateVersion: string | null;
  effectiveHash: string | null;
  provenance: Record<string, unknown>;
}) {
  const inputs = transparency.saved_patient_inputs ?? [];
  const tables = transparency.returned_cpt_percentages ?? [];
  const result = transparency.deterministic_result;
  const provenanceEntries = Object.entries(provenance ?? {});
  return (
    <div className="xi-proposal-transparency">
      <dl className="xi-proposal-facts">
        <div>
          <dt>Question</dt>
          <dd>
            <code className="xi-mono">{transparency.question_key}</code>
          </dd>
        </div>
        <div>
          <dt>Network version</dt>
          <dd>
            <code className="xi-mono">{networkVersion ?? transparency.network_version}</code>
          </dd>
        </div>
        <div>
          <dt>Prompt / template</dt>
          <dd>
            <code className="xi-mono">
              {promptVersion ?? "?"} / {templateVersion ?? "?"}
            </code>
          </dd>
        </div>
        <div>
          <dt>Baseline</dt>
          <dd>
            <code className="xi-mono">{baselineId ?? "—"}</code>
          </dd>
        </div>
        <div>
          <dt>Effective hash</dt>
          <dd>
            <code className="xi-mono">{effectiveHash ?? result?.effective_hash ?? "—"}</code>
          </dd>
        </div>
      </dl>

      <h5 className="xi-proposal-subhead">Saved patient inputs (frozen at run start)</h5>
      {inputs.length === 0 ? (
        <p className="xi-hint">No projected inputs.</p>
      ) : (
        <table className="xi-table" aria-label={`Saved patient inputs for ${transparency.question_key}`}>
          <thead>
            <tr>
              <th scope="col">Node</th>
              <th scope="col">Value</th>
              <th scope="col">Status</th>
              <th scope="col">Source path</th>
              <th scope="col">Source rev</th>
            </tr>
          </thead>
          <tbody>
            {inputs.map((input) => (
              <tr key={input.node_id}>
                <td>
                  <code className="xi-mono">{input.node_id}</code>
                </td>
                <td>{inputValueText(input)}</td>
                <td>{input.status}</td>
                <td>
                  <code className="xi-mono">{input.source_path}</code>
                </td>
                <td>{input.source_revision}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h5 className="xi-proposal-subhead">Returned CPT percentages (LLM-estimated values)</h5>
      {tables.length === 0 ? (
        <p className="xi-hint">No returned CPT tables.</p>
      ) : (
        tables.map((table) => (
          <div key={table.node_id} style={{ marginBottom: 8 }}>
            <p style={{ marginBottom: 4 }}>
              <strong>
                <code className="xi-mono">{table.node_id}</code>
              </strong>{" "}
              <span className="xi-hint">
                states {(table.states ?? []).join(" / ")}
                {(table.parent_ids ?? []).length > 0
                  ? ` · parents ${(table.parent_ids ?? []).join(", ")}`
                  : " · root distribution"}
              </span>
            </p>
            <table className="xi-table" aria-label={`Returned CPT table for ${table.node_id}`}>
              <thead>
                <tr>
                  <th scope="col">Parent states</th>
                  {(table.states ?? []).map((state) => (
                    <th scope="col" key={state}>
                      {state} %
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(table.rows ?? []).map((row, index) => (
                  // ponytail: index key is fine — CPT rows arrive in pinned
                  // Cartesian order; upgrade if reorderable.
                  <tr key={index}>
                    <td>
                      <code className="xi-mono">
                        {(row.parent_states ?? []).length > 0 ? (row.parent_states ?? []).join(", ") : "—"}
                      </code>
                    </td>
                    {(row.percentages ?? []).map((pct, cell) => (
                      // ponytail: index key is fine — cells follow declared
                      // state order; upgrade if reorderable.
                      <td key={cell}>
                        <code className="xi-mono">{pct}</code>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}

      <h5 className="xi-proposal-subhead">Deterministic network result (computed posteriors)</h5>
      {result === null || result === undefined ? (
        <p className="xi-hint">No deterministic result.</p>
      ) : (
        <div>
          {(result.posteriors ?? []).length > 0 && (
            <table className="xi-table" aria-label={`Posterior probabilities for ${transparency.question_key}`}>
              <thead>
                <tr>
                  <th scope="col">Node</th>
                  <th scope="col">State</th>
                  <th scope="col">Posterior %</th>
                </tr>
              </thead>
              <tbody>
                {(result.posteriors ?? []).flatMap((post) =>
                  (post.states ?? []).map((state, index) => {
                    const prob = post.probabilities?.[index];
                    return (
                      <tr key={`${post.node_id}|${state}`}>
                        <td>
                          <code className="xi-mono">{post.node_id}</code>
                        </td>
                        <td>{state}</td>
                        <td>
                          <code className="xi-mono">
                            {typeof prob === "number" ? (prob * 100).toFixed(2) : "?"}
                          </code>
                        </td>
                      </tr>
                    );
                  }),
                )}
              </tbody>
            </table>
          )}
          <p className="xi-hint">
            Query nodes <code className="xi-mono">{(result.query_nodes ?? []).join(", ")}</code> ·
            effective hash <code className="xi-mono">{result.effective_hash}</code>
          </p>
          <p style={{ marginBottom: 4 }}>
            <strong>Recommendation (templated section):</strong>
          </p>
          <p style={{ marginTop: 0 }}>{result.section_text}</p>
        </div>
      )}

      {provenanceEntries.length > 0 && (
        <details className="xi-proposal-details">
          <summary>Run provenance (persisted)</summary>
          <ul>
            {provenanceEntries.map(([factKey, factValue]) => (
              <li key={factKey}>
                <code className="xi-mono">{factKey}</code>:{" "}
                {typeof factValue === "string" ? factValue : JSON.stringify(factValue) ?? "—"}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="xi-hint">
        Full effective XML is retained server-side; only its hash is shown here.
      </p>
    </div>
  );
}

function ProposalBlock({ batch }: { batch: BatchPayload }) {
  const proposal = batch.proposal;
  if (proposal === null) {
    return (
      <div className="xi-notice" role="status">
        <p style={{ margin: 0 }}>Proposal unavailable — {incompleteReason(batch)}</p>
      </div>
    );
  }
  const sections = [...(proposal.sections ?? [])].sort(
    (a, b) => a.position - b.position || a.question_key.localeCompare(b.question_key),
  );
  const skipped = [...(proposal.skipped ?? [])].sort(
    (a, b) => a.position - b.position || a.question_key.localeCompare(b.question_key),
  );
  const report = proposal.ddi_report;
  return (
    <div>
      <h4 className="xi-section-title" style={{ fontSize: 16 }}>
        Proposal
      </h4>
      {sections.length === 0 ? (
        <p className="xi-hint" role="status">
          No applicable questions produced sections — every question was skipped. Skipped reasons
          are listed below.
        </p>
      ) : (
        <ol className="xi-proposal-sections">
          {sections.map((section) => (
            <li key={section.question_run_id}>
              <p style={{ marginBottom: 2 }}>
                <strong>
                  <code className="xi-mono">{section.question_key}</code>
                </strong>
              </p>
              <p style={{ marginTop: 0 }}>{section.section_text}</p>
              <p className="xi-hint" style={{ marginTop: 0 }}>
                {posteriorSummary(section.posteriors ?? [])} · network{" "}
                <code className="xi-mono">{section.network_version}</code> · template{" "}
                <code className="xi-mono">{section.template_version}</code>
              </p>
            </li>
          ))}
        </ol>
      )}
      {skipped.length > 0 && (
        <div>
          <h5 className="xi-proposal-subhead">Skipped questions</h5>
          <ul>
            {skipped.map((entry) => (
              <li key={`${entry.question_key}|${entry.position}`}>
                <code className="xi-mono">{entry.question_key}</code> — skipped: {entry.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div data-testid="proposal-ddi" id="proposal-ddi">
        <h5 className="xi-proposal-subhead">DDI coverage (pinned report)</h5>
        {(proposal.coverage_warnings ?? []).length > 0 && (
          <div className="xi-warning-panel" role="status">
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {proposal.coverage_warnings.map((warning, index) => (
                // ponytail: index key is fine — warnings are an ordered
                // server list; upgrade if reorderable.
                <li key={index}>{warning}</li>
              ))}
            </ul>
          </div>
        )}
        {report === null || report === undefined ? (
          <p className="xi-hint">No pinned DDI report.</p>
        ) : (
          <DdiSummary
            datasetVersion={report.dataset_version}
            catalogVersion={report.catalog_version}
            generatedAt={report.generated_at}
            uncovered={(report.coverage_unavailable_medications ?? []).map((med) =>
              String(med.catalog_drug_id),
            )}
            pairs={(report.pairs ?? []).map((pair) => ({
              label: `${pair.drug_a} + ${pair.drug_b}`,
              status: pairStatusText(pair.status),
              severity: pair.highest_known_severity,
              unknown: pair.has_unknown_severity,
              conflicts: pair.conflicts ?? [],
              evidence: (pair.evidence ?? []).map(
                (item) => item.raw_text ?? item.management ?? "Evidence assertion retained.",
              ),
            }))}
            limitations={(report.limitations ?? []).map((entry) => String(entry))}
          />
        )}
      </div>
    </div>
  );
}

function DdiSummary({
  datasetVersion,
  catalogVersion,
  generatedAt,
  uncovered,
  pairs,
  limitations,
}: {
  datasetVersion: string;
  catalogVersion: string;
  generatedAt: string;
  uncovered: string[];
  pairs: {
    label: string;
    status: string;
    severity: string | null;
    unknown: boolean;
    conflicts: string[];
    evidence: string[];
  }[];
  limitations: string[];
}) {
  return (
    <div>
      <p className="xi-hint" style={{ marginTop: 0 }}>
        Dataset <code className="xi-mono">{datasetVersion}</code> · catalog{" "}
        <code className="xi-mono">{catalogVersion}</code> · generated {generatedAt}
      </p>
      {uncovered.length > 0 && (
        <div className="xi-warning-panel" role="status">
          <p style={{ margin: "0 0 4px" }}>
            <strong>Coverage unavailable</strong>
          </p>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {uncovered.map((id) => (
              <li key={id}>
                {id} — coverage unavailable in this dataset
              </li>
            ))}
          </ul>
        </div>
      )}
      {pairs.length === 0 ? (
        <p className="xi-hint">No pairs to check — fewer than two medications in the frozen list.</p>
      ) : (
        <ul>
          {pairs.map((pair) => (
            <li key={pair.label}>
              <p style={{ marginBottom: 2 }}>
                <strong>{pair.label}</strong> — {pair.status}
                {pair.severity !== null && ` · highest known severity: ${pair.severity}`}
                {pair.unknown && " · includes unknown severity"}
              </p>
              {pair.conflicts.length > 0 && (
                <p className="xi-hint" style={{ margin: "4px 0" }}>
                  Conflicting severities reported: {pair.conflicts.join(", ")}
                </p>
              )}
              {pair.evidence.length > 0 && (
                <details className="xi-proposal-details">
                  <summary>
                    Evidence assertions ({pair.evidence.length})
                  </summary>
                  <ul>
                    {pair.evidence.map((text, index) => (
                      // ponytail: index key is fine — evidence arrives in
                      // source order; upgrade if reorderable.
                      <li key={index}>{text}</li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
      {limitations.length > 0 && (
        <div>
          <p style={{ marginBottom: 4 }}>
            <strong>Release limitations</strong>
          </p>
          <ul style={{ marginTop: 0 }}>
            {limitations.map((entry, index) => (
              // ponytail: index key is fine — limitations are an ordered
              // release list; upgrade if reorderable.
              <li key={index}>{entry}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
