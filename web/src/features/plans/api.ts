/* Final-plan editing, sign-off, and addenda HTTP client (S50, plan.md §9.2;
 * FR-15-16, FR-22, NFR-03).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Reads need any active authenticated session but only the draft
 * author receives the plan body (403 strangers, enforced server-side);
 * mutations need an active physician session plus the per-session CSRF token.
 *
 * Backend contract (see backend/src/x_insight/cases/router.py + signing.py,
 * read-only — no other payloads are invented here):
 * - GET /encounters/{id}/secondary-plan → {encounter, revision: planRev,
 *   text, encounter_revision} + ETag planRev. 401/403/404.
 * - PATCH /encounters/{id}/secondary-plan with If-Match planRev ==
 *   body expected_plan_revision, body {text <=10000, empty allowed,
 *   expected_plan_revision >=1} extra=forbid → 200 {encounter,
 *   revision: new, text, encounter_revision, server_timestamp}.
 *   Idempotency-Key op secondary_plan.save, 409 on key-conflict. Does NOT
 *   bump the encounter revision.
 * - POST /encounters/{id}/sign with If-Match encounterRev ==
 *   body expected_encounter_revision, body {expected_encounter_revision,
 *   expected_plan_revision, batch_id UUID, acceptances:
 *   [{question_run_id, acceptance_id, expected_review_revision}]} covering
 *   exactly the ready runs → 200 {snapshot: safe_snapshot, encounter,
 *   revision: newEncRev}. 409 PLAN_NOT_READY/PROPOSAL_INCOMPLETE/
 *   NO_SUCCESSFUL_RESULT/STALE_INPUTS/REFERENCE_MISMATCH/ALREADY_SIGNED,
 *   412 stale, 404/403/422. Idempotency-Key op encounters.sign.
 * - POST /encounters/{id}/addenda (any active physician, signed-only) with
 *   If-Match signedEncRev, body {text non-empty <=2000,
 *   expected_encounter_revision} → 201 {addendum: {id, encounter_id,
 *   author_id, author_display server, created_at server, text}, revision:
 *   unchanged}. 409 ENCOUNTER_NOT_SIGNED if draft, 404/412/422/403.
 *   Idempotency-Key op encounters.addendum.
 * - GET /patients/{id}/chart (shared, any physician+admin) carries
 *   signed_snapshots[] + addenda[] alongside demographics/chronology; no
 *   drafts (see features/patients/chart/api.ts).
 *
 * Note-leakage by code path: every type below mirrors a `safe_*` serializer
 * (safe_secondary_plan/safe_snapshot/safe_addendum) or the frozen snapshot
 * shapes. None carries draft free-text beyond the plan/addendum text being
 * edited, and the panels render only these typed fields.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../identity/api";
import { formatIfMatch } from "../encounters/api";
import type { CptTable, Posterior } from "../reasoning/api";

export interface SecondaryPlanPayload {
  encounter: {
    id: string;
    patient_id: string;
    kind: string;
    lifecycle: string;
    revision: number;
  };
  revision: number;
  text: string;
  encounter_revision: number;
  server_timestamp?: string;
}

export interface SignAcceptanceRef {
  question_run_id: string;
  acceptance_id: string;
  expected_review_revision: number;
}

export interface SignRequestBody {
  expected_encounter_revision: number;
  expected_plan_revision: number;
  batch_id: string;
  acceptances: SignAcceptanceRef[];
}

/** Frozen per-question record inside a signed snapshot (persisted data). */
export interface SignedQuestionFrozen {
  question_key: string;
  question_run_id: string;
  status: string;
  gate_reason: string;
  projection: Record<string, unknown>;
  projection_hash: string;
  pinned_versions: Record<string, unknown>;
  original_baseline: {
    id: string;
    validated_tables: CptTable[];
    posteriors: Posterior[];
    section_text: string;
    network_version: string;
    template_version: string;
    prompt_version: string;
    effective_hash: string;
  } | null;
  current_tables: CptTable[] | null;
  current_cpt_revision_id: string | null;
  cpt_hash: string | null;
  current_result: {
    id: string;
    cpt_revision_id: string | null;
    posteriors: Posterior[];
    section_text: string;
    reused_from_baseline_id: string | null;
  } | null;
  result_kind: string | null;
  result_id: string | null;
  acceptance: {
    id: string;
    baseline_id: string;
    cpt_revision_id: string | null;
    result_kind: string;
    result_id: string;
    actor_username: string;
    created_at: string;
  } | null;
  review_revision: number;
  calculation_state: string;
}

/** Immutable signed snapshot row (mirrors `safe_snapshot`). */
export interface SignedSnapshotRow {
  id: string;
  encounter_id: string;
  patient_id: string;
  batch_id: string;
  proposal_id: string;
  secondary_plan_revision: number;
  secondary_plan_text: string;
  snapshot: {
    questions?: SignedQuestionFrozen[];
    secondary_plan?: { revision: number; text: string };
    signer?: { signer_id: string; signer_username: string; signed_at: string };
    [key: string]: unknown;
  };
  snapshot_hash: string;
  signer_id: string;
  signer_username: string;
  signed_at: string;
  encounter_revision: number;
  created_at: string;
}

export interface SignPayload {
  snapshot: SignedSnapshotRow;
  encounter: SecondaryPlanPayload["encounter"];
  revision: number;
  server_timestamp?: string;
}

/** Append-only correction (mirrors `safe_addendum`; author/time server-set). */
export interface AddendumRow {
  id: string;
  encounter_id: string;
  author_id: string;
  author_display: string;
  created_at: string;
  text: string;
}

export interface AddendumPayload {
  addendum: AddendumRow;
  revision: number;
  server_timestamp: string;
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

/** Author-only secondary-plan read (draft only; 404 once signed). */
export async function getSecondaryPlan(encounterId: string): Promise<SecondaryPlanPayload> {
  return request<SecondaryPlanPayload>(
    `/encounters/${encodeURIComponent(encounterId)}/secondary-plan`,
  );
}

/** Author-only secondary-plan save with its own revision fence (412 stale).
 * Empty text is an explicit clear; signing requires non-empty (409
 * PLAN_NOT_READY server-side). Fresh key per attempt unless retrying the
 * same attempt. Never bumps the encounter revision. */
export async function saveSecondaryPlan(
  encounterId: string,
  text: string,
  expectedPlanRevision: number,
  options: { idempotencyKey?: string } = {},
): Promise<SecondaryPlanPayload> {
  return request<SecondaryPlanPayload>(
    `/encounters/${encodeURIComponent(encounterId)}/secondary-plan`,
    {
      method: "PATCH",
      ifMatch: expectedPlanRevision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: { text, expected_plan_revision: expectedPlanRevision },
    },
  );
}

/** Author-only atomic sign (single transaction server-side). Fresh key per
 * explicit Sign click; the button stays disabled while pending so a
 * double-click cannot fire a second POST (replay with the same key returns
 * the one signature). */
export async function signEncounter(
  encounterId: string,
  expectedEncounterRevision: number,
  body: SignRequestBody,
  options: { idempotencyKey?: string } = {},
): Promise<SignPayload> {
  return request<SignPayload>(
    `/encounters/${encodeURIComponent(encounterId)}/sign`,
    {
      method: "POST",
      ifMatch: expectedEncounterRevision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body,
    },
  );
}

/** Any-active-physician append to a signed encounter (signed-only 409,
 * server-derived author/time, no snapshot mutation, revision unchanged).
 * Fresh key per submit; disabled while pending so retry/double-click does
 * not duplicate. */
export async function createAddendum(
  encounterId: string,
  expectedEncounterRevision: number,
  text: string,
  options: { idempotencyKey?: string } = {},
): Promise<AddendumPayload> {
  return request<AddendumPayload>(
    `/encounters/${encodeURIComponent(encounterId)}/addenda`,
    {
      method: "POST",
      ifMatch: expectedEncounterRevision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: { text, expected_encounter_revision: expectedEncounterRevision },
    },
  );
}

export const MAX_SECONDARY_PLAN_CHARS = 10_000;
export const MAX_ADDENDUM_CHARS = 2000;

export { newIdempotencyKey };
