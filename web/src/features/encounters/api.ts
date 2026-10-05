/* Author-owned draft HTTP client (S07, plan.md §§2.3, 4.3; FR-16, FR-22).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). All mutations need an active physician session plus the
 * per-session CSRF token. Reads need any active authenticated session, but
 * only the draft author receives the clinical body — strangers get 403
 * without content (transitive privacy, enforced server-side).
 *
 * Revision ownership for later assessment pages: every wizard page autosaves
 * through PATCH here with the encounter revision from GET (ETag + `revision`
 * field) as `If-Match` and a fresh `Idempotency-Key` per save attempt
 * (retries of the same attempt reuse the key). A 412 means another tab
 * saved first — keep local edits, GET server truth, reconcile, retry with
 * the new revision. Only a 2xx response is durable ("Saved").
 * `draft_data` stays one opaque versioned object so later pages extend its
 * keys instead of adding persistence mechanisms.
 *
 * Route choice (documented per S07 handoff): `#/encounters/:id` mirrors the
 * backend `/encounters/{id}` resource directly. The alternative
 * `#/patients/:id/draft` would imply a patient sub-resource, but the draft
 * id is the stable key across GET/PATCH/discard and the slot lookup has no
 * patient-scoped read route — so the encounter id belongs in the URL.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../identity/api";

export type EncounterKind = "registration" | "follow_up";
export type EncounterLifecycle = "draft" | "signed" | "discarded";

export interface EncounterReference {
  id: string;
  patient_id: string;
  kind: string;
  author_id: string;
  lifecycle: EncounterLifecycle;
  revision: number;
  created_at: string;
  updated_at: string;
}

/** Opaque autosave body shared by all wizard pages (S07+). */
export type DraftData = Record<string, unknown>;

export interface EncounterPayload {
  encounter: EncounterReference;
  draft_data: DraftData;
  revision: number;
  server_timestamp?: string;
}

export interface DiscardPayload {
  encounter: EncounterReference;
  revision: number;
  server_timestamp: string;
}

export function formatIfMatch(revision: number): string {
  return `"${revision}"`;
}

export function encounterHash(id: string): string {
  return `#/encounters/${id}`;
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

/** Optional inline baseline snapshot for follow-up creation (S14).
 * Mirrors the backend `baseline` object (history_values, prior_scores,
 * medications, provenance_note) plus the opaque `baseline_encounter_id`.
 * Omitted (undefined) means no baseline — the fresh follow-up starts with
 * an empty history shell and empty answers. */
export interface FollowupBaselineInput {
  history_values?: Record<string, unknown>;
  prior_scores?: Record<string, number | null>;
  medications?: Array<{ catalog_drug_id: string }>;
  provenance_note?: string;
  baseline_encounter_id?: string;
}

/** Single-slot creation: physician-only; occupied slot is a generic 409
 * OPEN_DRAFT_EXISTS with no author/content. Fresh key per attempt. */
export async function createEncounter(
  patientId: string,
  kind: EncounterKind = "follow_up",
  baseline?: FollowupBaselineInput,
): Promise<EncounterPayload> {
  const body: Record<string, unknown> = { kind };
  // Baseline travels only for follow-ups; registration drafts start empty.
  if (baseline !== undefined && kind === "follow_up") {
    const { baseline_encounter_id, ...rest } = baseline;
    if (Object.keys(rest).length > 0) {
      body["baseline"] = rest;
    }
    if (baseline_encounter_id !== undefined) {
      body["baseline_encounter_id"] = baseline_encounter_id;
    }
  }
  return request<EncounterPayload>(`/patients/${encodeURIComponent(patientId)}/encounters`, {
    method: "POST",
    idempotencyKey: newIdempotencyKey(),
    body,
  });
}

/** Author-only draft read: 403 strangers, 404 missing/released. */
export async function getEncounter(encounterId: string): Promise<EncounterPayload> {
  return request<EncounterPayload>(`/encounters/${encodeURIComponent(encounterId)}`);
}

export interface PatchOptions {
  /** Reuse only for retry of the same attempt; new edits need a fresh key. */
  idempotencyKey?: string;
}

/** Author-only autosave: replaces the full draft object, bumps revision.
 * Fresh key per attempt unless retrying the same attempt. */
export async function patchEncounter(
  encounterId: string,
  draftData: DraftData,
  revision: number,
  options: PatchOptions = {},
): Promise<EncounterPayload> {
  return request<EncounterPayload>(`/encounters/${encodeURIComponent(encounterId)}`, {
    method: "PATCH",
    ifMatch: revision,
    idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
    body: { draft_data: draftData },
  });
}

/** Author-only release: explicit confirm + current revision. Releases the
 * slot without deleting the patient. Fresh key per attempt. */
export async function discardEncounter(
  encounterId: string,
  revision: number,
): Promise<DiscardPayload> {
  return request<DiscardPayload>(
    `/encounters/${encodeURIComponent(encounterId)}/discard`,
    {
      method: "POST",
      ifMatch: revision,
      idempotencyKey: newIdempotencyKey(),
      body: { confirm: true },
    },
  );
}
