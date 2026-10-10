/* Proposal review HTTP client (S48, plan.md §§8-9; FR-15, FR-35-36).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Reads need any active authenticated session but only the draft
 * author receives content (403 strangers, enforced server-side); the
 * generation POST needs an active physician session plus the per-session
 * CSRF token.
 *
 * Backend contract (see backend/src/x_insight/reasoning/router.py, read-only):
 * - POST /encounters/{id}/generation-batches {packages: [...]} with
 *   If-Match "<revision>" + fresh Idempotency-Key → 202 {batch,
 *   question_runs}. Same-fingerprint triggers reuse the existing batch (no
 *   duplicate); a 422 without packages means no question packages are pinned
 *   (content bundles arrive in S39) — the UI shows that honestly and never
 *   invents packages. There is no separate retry route: retry re-POSTs the
 *   same packages at the current revision and the backend resumes at the
 *   failed stage with artifact reuse (S47 carry-forward).
 * - GET /generation-batches/{id} → batch + question_runs (position-ordered)
 *   + freshness + job/jobs/attempts/queue + baseline/transparency (first-run
 *   compat) + baselines[] {question_run_id/question_key/position/status/
 *   baseline/transparency} + proposal {sections/skipped/coverage_warnings/
 *   ddi_report} (null while incomplete) + workflow {complete/status/...}.
 * - GET /question-runs/{id}/review → question_run + batch + baseline (null
 *   when failed) + adjustable + transparency (null when failed) + freshness
 *   + job/attempts/queue. Transparency expansion reads this persisted data;
 *   it never recomputes from current chart values.
 *
 * Note-leakage assertion by code path: every type below mirrors a `safe_*`
 * serializer (safe_batch/safe_run/safe_baseline/safe_proposal) or the
 * persisted projection/transparency shapes. None of them carries notes,
 * names, patient identifiers, or phone — the panel renders only these typed
 * fields and never reads draft_data or the chart.
 *
 * Batch discovery across reloads: the backend has no list-batches-by-
 * encounter route, so the browser remembers `{batchId, packages,
 * sourceRevision}` per encounter in localStorage (`xi.proposal.<id>`).
 * Reloads resume from GET without a new POST; retries re-POST the stored
 * packages. The e2e stage (dev-test) seeds the same key to attach the UI to
 * an API-created batch.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../identity/api";
import { formatIfMatch } from "../encounters/api";
import type { DdiReport } from "../medications/api";

/** One persisted projection variable (CPT-estimation context only). */
export interface ProjectionVariable {
  node_id: string;
  patient_type: string;
  status: "observed" | "not_assessed" | "missing" | "conflict";
  value: unknown;
  source_path: string;
  source_revision: number;
}

export interface CptRow {
  parent_states: string[];
  percentages: string[];
}

/** One returned CPT table (LLM-estimated percentages, exact strings). */
export interface CptTable {
  node_id: string;
  parent_ids: string[];
  states: string[];
  rows: CptRow[];
}

/** One deterministic posterior distribution (computed, 0-1 floats). */
export interface Posterior {
  node_id: string;
  states: string[];
  probabilities: number[];
}

/** Immutable original baseline (mirrors `safe_baseline`). */
export interface QuestionBaseline {
  id: string;
  question_run_id: string;
  batch_id: string;
  source_hash: string;
  effective_hash: string;
  effective_xml: string;
  raw_response: Record<string, unknown>;
  validated_tables: CptTable[];
  query_nodes: string[];
  posteriors: Posterior[];
  section_text: string;
  template_version: string;
  prompt_version: string;
  network_version: string;
  provider_model: string;
  projection_hash: string;
  provenance: Record<string, unknown>;
  created_at: string;
}

/** Five required transparency fields, from persisted data only. */
export interface QuestionTransparency {
  question_key: string;
  network_version: string;
  saved_patient_inputs: ProjectionVariable[];
  returned_cpt_percentages: CptTable[];
  deterministic_result: {
    posteriors: Posterior[];
    section_text: string;
    query_nodes: string[];
    effective_hash: string;
  };
}

/** Ordered per-run baseline entry (join via `question_run_id`). */
export interface BaselineEntry {
  question_run_id: string;
  question_key: string;
  position: number;
  status: string;
  baseline: QuestionBaseline | null;
  transparency: QuestionTransparency | null;
}

/** Frozen batch row (mirrors `safe_batch`). */
export interface GenerationBatch {
  id: string;
  encounter_id: string;
  author_id: string;
  source_revision: number;
  fingerprint: string;
  status: string;
  pinned_bundle: Record<string, unknown>;
  created_at: string;
}

/** Frozen question run (mirrors `safe_run`; array order is pinned order). */
export interface QuestionRun {
  id: string;
  batch_id: string;
  question_key: string;
  status: "ready" | "not_applicable" | "needs_clarification" | "stale";
  gate_reason: string;
  projection: {
    variables?: ProjectionVariable[];
    [key: string]: unknown;
  };
  projection_hash: string;
  fingerprint: string;
  created_at: string;
}

export interface ProposalSection {
  question_run_id: string;
  question_key: string;
  position: number;
  baseline_id: string;
  section_text: string;
  posteriors: Posterior[];
  query_nodes: string[];
  effective_hash: string;
  template_version: string;
  network_version: string;
}

export interface ProposalSkipped {
  question_key: string;
  position: number;
  reason: string;
}

/** Immutable assembled proposal (mirrors `safe_proposal`). */
export interface ProposalSnapshot {
  id: string;
  batch_id: string;
  fingerprint: string;
  sections: ProposalSection[];
  skipped: ProposalSkipped[];
  coverage_warnings: string[];
  ddi_report: DdiReport;
  created_at: string;
}

/** Derived complete/incomplete view (partial stays inspectable, never ready). */
export interface WorkflowView {
  complete: boolean;
  status: string;
  pending_question_keys: string[];
  needs_clarification: string[];
  skipped: ProposalSkipped[];
  coverage_warnings: string[];
  ddi_status: string;
}

/** Public job shape: progress only, never fencing tokens. */
export interface ReasonJob {
  id: string;
  batch_id: string;
  question_run_id: string;
  job_class: string;
  status: "queued" | "leased" | "succeeded" | "failed" | "cancelled";
  attempt_index: number;
  max_attempts: number;
  lease_deadline: string | null;
  last_heartbeat: string | null;
  next_eligible_at: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface BatchQueue {
  busy: boolean;
  leased_count: number;
  queued_count: number;
  max_provider_slots: number;
  max_queued_runs: number;
  queue_position: number | null;
}

export interface BatchFreshness {
  stale: boolean;
  reason: string;
  current_fingerprint: string;
}

export interface BatchPayload {
  batch: GenerationBatch;
  question_runs: QuestionRun[];
  freshness: BatchFreshness;
  job: ReasonJob | null;
  jobs: ReasonJob[];
  attempts: number;
  queue: BatchQueue;
  baseline: QuestionBaseline | null;
  transparency: QuestionTransparency | null;
  baselines: BaselineEntry[];
  proposal: ProposalSnapshot | null;
  workflow: WorkflowView;
}

export type CalculationState =
  | "unchanged"
  | "recalculating"
  | "successfully_recalculated"
  | "failed";

export interface InputFreshness {
  stale: boolean;
  reason: string;
  current_fingerprint: string;
}

/** Immutable CPT revision (mirrors `safe_revision`; persisted data only). */
export interface CptRevision {
  id: string;
  question_run_id: string;
  batch_id: string;
  parent_revision_id: string | null;
  sequence: number;
  kind: string;
  cpt_hash: string;
  cpt_artifact: CptTable[];
  direct_edit: Record<string, unknown>;
  before_row: Record<string, unknown>;
  after_row: Record<string, unknown>;
  actor_username: string;
  redistribution_version: string;
  created_at: string;
}

/** Local calculation result (mirrors `safe_calculation_result`). */
export interface CalculationResult {
  id: string;
  question_run_id: string;
  batch_id: string;
  cpt_revision_id: string;
  cpt_hash: string;
  network_hash: string;
  network_version: string;
  template_version: string;
  query_nodes: string[];
  posteriors: Posterior[];
  section_text: string;
  effective_hash: string;
  effective_xml: string;
  reused_from_baseline_id: string | null;
  provenance: Record<string, unknown>;
  created_at: string;
}

/** Exact current-result acceptance (mirrors `safe_acceptance`). */
export interface ProbabilityAcceptance {
  id: string;
  encounter_id: string;
  question_run_id: string;
  batch_id: string;
  question_key: string;
  baseline_id: string;
  cpt_revision_id: string | null;
  cpt_hash: string;
  result_kind: string;
  result_id: string;
  input_hash: string;
  projection_hash: string;
  actor_username: string;
  created_at: string;
}

export interface QuestionReviewPayload {
  question_run: QuestionRun;
  batch: GenerationBatch;
  baseline: QuestionBaseline | null;
  adjustable: boolean;
  transparency: QuestionTransparency | null;
  freshness: BatchFreshness;
  input_freshness: InputFreshness;
  job: ReasonJob | null;
  attempts: number;
  queue: BatchQueue;
  original_tables: CptTable[] | null;
  current_tables: CptTable[] | null;
  current_cpt_revision_id: string | null;
  displayed_result_revision_id: string | null;
  current_result_matches: boolean;
  calculation_state: CalculationState;
  calculation_result: CalculationResult | null;
  displayed_result: CalculationResult | null;
  calculation_results: CalculationResult[];
  local_jobs: ReasonJob[];
  local_job: ReasonJob | null;
  review_revision: number;
  revisions: CptRevision[];
  cpt_hash: string | null;
  outputs_read_only: boolean;
  acceptance: ProbabilityAcceptance | null;
  is_accepted: boolean;
  acceptances: ProbabilityAcceptance[];
}

export interface StartPayload {
  batch: GenerationBatch;
  question_runs: QuestionRun[];
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  ifMatch?: number;
  idempotencyKey?: string;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  if (options.ifMatch !== undefined) {
    headers["If-Match"] = formatIfMatch(options.ifMatch);
  }
  if (options.idempotencyKey !== undefined) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }
  const response = await fetch(`/api/v1${path}`, {
    method: options.method ?? "GET",
    credentials: "include",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const body = (payload ?? {}) as {
      code?: string;
      message?: string;
      field_errors?: Record<string, string[]>;
    };
    throw new ApiError(
      response.status,
      body.code ?? "REQUEST_FAILED",
      body.message ?? `Request failed (${response.status}).`,
      body.field_errors ?? {},
    );
  }
  return (payload ?? {}) as T;
}

/** Author-only generation start (or fingerprint reuse). Fresh key per attempt
 * unless retrying the same attempt. `packages` are passed through opaquely —
 * the UI stores what it sent for retry and never invents packages. */
export async function startGeneration(
  encounterId: string,
  revision: number,
  packages: Array<Record<string, unknown>>,
  options: { idempotencyKey?: string } = {},
): Promise<StartPayload> {
  return request<StartPayload>(
    `/encounters/${encodeURIComponent(encounterId)}/generation-batches`,
    {
      method: "POST",
      ifMatch: revision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: { packages },
    },
  );
}

/** Author-only batch read (frozen projection + progress + proposal views). */
export async function getBatch(batchId: string): Promise<BatchPayload> {
  return request<BatchPayload>(
    `/generation-batches/${encodeURIComponent(batchId)}`,
  );
}

/** Author-only per-question review (persisted baseline + transparency). */
export async function getQuestionReview(runId: string): Promise<QuestionReviewPayload> {
  return request<QuestionReviewPayload>(
    `/question-runs/${encodeURIComponent(runId)}/review`,
  );
}

export interface AdjustmentPayload {
  revision: CptRevision;
  review_state: { review_revision: number; current_revision_id: string | null };
  current_cpt_revision_id: string;
  review_revision: number;
  cpt_hash: string;
  current_tables: CptTable[];
}

/** One completed slider command (S48c §3): server redistributes authoritatively.
 * Sends both If-Match and the body revision (If-Match wins server-side). */
export async function postCptAdjustment(
  runId: string,
  args: {
    node_id: string;
    parent_states: string[];
    state: string;
    target_percentage: string;
    expected_review_revision: number;
  },
  options: { idempotencyKey?: string } = {},
): Promise<AdjustmentPayload> {
  return request<AdjustmentPayload>(
    `/question-runs/${encodeURIComponent(runId)}/cpt-adjustments`,
    {
      method: "POST",
      ifMatch: args.expected_review_revision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: args,
    },
  );
}

export interface ResetPayload {
  revision: CptRevision;
  review_state: { review_revision: number; current_revision_id: string | null };
  current_cpt_revision_id: string;
  review_revision: number;
  cpt_hash: string;
  current_tables: CptTable[];
  calculation_result: CalculationResult;
  reused_from_baseline_id: string;
}

/** Per-question reset to original values (S48c §3): audited baseline-equal
 * revision with verified reuse; one panel only, history retained. */
export async function postCptReset(
  runId: string,
  expectedReviewRevision: number,
  options: { idempotencyKey?: string } = {},
): Promise<ResetPayload> {
  return request<ResetPayload>(
    `/question-runs/${encodeURIComponent(runId)}/reset`,
    {
      method: "POST",
      ifMatch: expectedReviewRevision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: { expected_review_revision: expectedReviewRevision },
    },
  );
}

export interface RetryPayload {
  job: ReasonJob;
  current_cpt_revision_id: string;
  review_revision: number;
  cpt_hash: string;
}

/** Local-only retry for exactly the current revision (S48c §3). */
export async function postRetryCalculation(
  runId: string,
  expectedReviewRevision: number,
  currentCptRevisionId?: string | null,
  options: { idempotencyKey?: string } = {},
): Promise<RetryPayload> {
  return request<RetryPayload>(
    `/question-runs/${encodeURIComponent(runId)}/retry-calculation`,
    {
      method: "POST",
      ifMatch: expectedReviewRevision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: {
        expected_review_revision: expectedReviewRevision,
        ...(currentCptRevisionId !== undefined && currentCptRevisionId !== null
          ? { current_cpt_revision_id: currentCptRevisionId }
          : {}),
      },
    },
  );
}

export interface AcceptancePayload {
  acceptance: ProbabilityAcceptance;
  is_accepted: boolean;
  current_cpt_revision_id: string | null;
  review_revision: number;
  cpt_hash: string;
}

export interface AcceptanceBody {
  baseline_id: string;
  current_cpt_revision_id: string | null;
  cpt_hash: string;
  result_id: string;
  input_hash: string;
  expected_review_revision: number;
}

function sortDeep(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortDeep);
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
      a < b ? -1 : a > b ? 1 : 0,
    );
    const out: Record<string, unknown> = {};
    for (const [key, entry] of entries) {
      out[key] = sortDeep(entry);
    }
    return out;
  }
  return value;
}

/** Canonical JSON per plan.md §4.2 (sorted keys, compact, arrays preserved).
 * Mirrors backend `contracts.canonical_json` for string-shaped CPT tables. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortDeep(value));
}

/** SHA-256 hex over canonical JSON (mirrors `contracts.canonical_hash`).
 * Async: uses the platform primitive, no new dependency. */
export async function canonicalHash(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalJson(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/** Exact current references from a GET review (mirrors backend test helper
 * `_accept_body(review)`): unchanged originals hash the baseline tables and
 * accept the baseline result; adjusted revisions use the live cpt_hash and
 * the matching successful calculation result. Throws when no successful
 * current result exists (caller blocks instead of omitting the field). */
export async function buildAcceptanceBody(review: QuestionReviewPayload): Promise<AcceptanceBody> {
  const baseline = review.baseline;
  if (baseline === null) {
    throw new Error("No successful result exists to accept for this question run.");
  }
  if (review.current_cpt_revision_id === null) {
    return {
      baseline_id: baseline.id,
      current_cpt_revision_id: null,
      cpt_hash: await canonicalHash(baseline.validated_tables),
      result_id: baseline.id,
      input_hash: review.input_freshness.current_fingerprint,
      expected_review_revision: review.review_revision,
    };
  }
  if (review.cpt_hash === null || review.calculation_result === null) {
    throw new Error("The current revision has no successful result yet.");
  }
  return {
    baseline_id: baseline.id,
    current_cpt_revision_id: review.current_cpt_revision_id,
    cpt_hash: review.cpt_hash,
    result_id: review.calculation_result.id,
    input_hash: review.input_freshness.current_fingerprint,
    expected_review_revision: review.review_revision,
  };
}

/** UI-side accept blocker (mirrors server 409/412 order): stale inputs,
 * missing baseline, recalculating, failed. Null means acceptable. */
export function acceptBlockedReason(review: QuestionReviewPayload): string | null {
  if (review.input_freshness.stale) {
    return `Out of date — ${review.input_freshness.reason}. Regeneration is required before acceptance; reset cannot make stale inputs current.`;
  }
  if (review.baseline === null) {
    return "No successful result to accept — this question has no completed baseline.";
  }
  if (review.calculation_state === "recalculating") {
    return "Recalculating — the current revision is queued or running. Wait for the successful result, then accept.";
  }
  if (review.calculation_state === "failed") {
    return "Failed — the current revision is preserved but unsolved. Retry locally or reset first.";
  }
  if (review.current_cpt_revision_id !== null && review.calculation_result === null) {
    return "No successful current result yet — wait for local calculation to finish.";
  }
  return null;
}

/** Author-only acceptance of the exact current result (S48c §4 frontend half):
 * POSTs exact references from GET review with If-Match + Idempotency-Key.
 * Signing enforcement itself belongs to S49 — only the references are
 * exposed here. */
export async function postAcceptance(
  runId: string,
  body: AcceptanceBody,
  options: { idempotencyKey?: string } = {},
): Promise<AcceptancePayload> {
  return request<AcceptancePayload>(
    `/question-runs/${encodeURIComponent(runId)}/acceptance`,
    {
      method: "POST",
      ifMatch: body.expected_review_revision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body,
    },
  );
}

/** Deep link to the proposal review step of an encounter draft. The existing
 * hash router resolves `#/encounters/:id` (the trailing anchor only scrolls);
 * no new route table entry is needed. */
export function proposalReviewHash(encounterId: string): string {
  return `#/encounters/${encounterId}#proposal-review`;
}

export interface StoredProposal {
  batchId: string;
  packages: Array<Record<string, unknown>> | null;
  sourceRevision: number | null;
}

function storageKey(encounterId: string): string {
  return `xi.proposal.${encounterId}`;
}

/** Browser memory of the encounter's generation request: batch id for
 * resume-without-POST plus the exact packages sent for precise retry. */
export function readStoredProposal(encounterId: string): StoredProposal | null {
  try {
    const raw = localStorage.getItem(storageKey(encounterId));
    if (raw === null) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<StoredProposal>;
    if (typeof parsed.batchId !== "string" || parsed.batchId === "") {
      return null;
    }
    return {
      batchId: parsed.batchId,
      packages: Array.isArray(parsed.packages)
        ? (parsed.packages as Array<Record<string, unknown>>)
        : null,
      sourceRevision:
        typeof parsed.sourceRevision === "number" ? parsed.sourceRevision : null,
    };
  } catch {
    return null;
  }
}

export function writeStoredProposal(encounterId: string, value: StoredProposal): void {
  try {
    localStorage.setItem(storageKey(encounterId), JSON.stringify(value));
  } catch {
    /* quota/unavailable: resume degrades to re-entry, retry keeps working
     * only while the packages stay in memory for this page lifetime */
  }
}

export function clearStoredProposal(encounterId: string): void {
  try {
    localStorage.removeItem(storageKey(encounterId));
  } catch {
    /* storage unavailable: nothing cached anyway */
  }
}

export { newIdempotencyKey };
