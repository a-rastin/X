/* Medications + DDI report HTTP client (S20, plan.md §§2.2, 6.3, 9;
 * FR-14-16, FR-20).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Reads need any active authenticated session but only the draft
 * author receives content (403 strangers, 404 missing/released — enforced
 * server-side, never client-side); the strict save needs an active physician
 * session plus the per-session CSRF token.
 *
 * Contract (see backend/src/x_insight/cases/medications.py, read-only):
 * - GET /drugs?query=&limit=&offset=&dataset_version= — authenticated
 *   demo-catalog search for the form (no CSRF on reads, like other GET
 *   previews). Returns {dataset_version, catalog_version, items[]
 *   {catalog_drug_id, concept_id, canonical_name, concept_type}, total,
 *   limit, offset}.
 * - GET /encounters/{id}/medications — author-only preview over
 *   draft_data["medications"] (entries, provenance, reconciliation, pinned
 *   dataset/catalog/fingerprint/generated_at, evaluation, revision) + ETag.
 * - POST /encounters/{id}/medications — author-only strict drug-only save
 *   {medications: [{catalog_drug_id}], dataset_version?, reconciliation?}
 *   (extra=forbid — dose/unit/route/frequency/status/unknown-label forgeries
 *   are 422, unknown catalog ids are 422 never free text). If-Match
 *   "<revision>" is required (stale is 412 STALE_REVISION); fresh
 *   Idempotency-Key per save (same key + same body replays, same key +
 *   changed body is 409). Success stamps provenance, settles reconciliation,
 *   and pins the report reference (dataset/catalog/fingerprint/generated_at)
 *   for later run snapshots. 200 returns the state + revision +
 *   server_timestamp + ETag.
 * - GET /encounters/{id}/ddi-report — author-only versioned report with
 *   stale fencing: {report_status: current|pending|error, reason, report|null,
 *   medication_fingerprint, stored_fingerprint, ...}. A fingerprint mismatch,
 *   pending reconciliation, missing pin, or unavailable dataset never returns
 *   old rows as current — the caller shows pending/error instead.
 *
 * Selection edits travel through the shared S07 autosave PATCH
 * (draft_data["medications"].entries) so resume + navigation warnings stay on
 * the one persistence mechanism; the strict POST above is the validating pin
 * (see features/medications/MedicationsSection.tsx). No provider/MCP/LLM is
 * involved — DDI works while provider configuration is absent.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../identity/api";
import { formatIfMatch } from "../encounters/api";

export interface CatalogDrug {
  catalog_drug_id: string;
  concept_id: string;
  canonical_name: string;
  concept_type: string;
}

export interface DrugSearchResult {
  dataset_version: string;
  catalog_version: string;
  items: CatalogDrug[];
  total: number;
  limit: number;
  offset: number;
}

export interface MedicationEntry {
  catalog_drug_id: string;
  [key: string]: unknown;
}

export interface MedicationsReconciliation {
  status: "not_required" | "pending" | "reconciled";
  baseline_encounter_id?: string | null;
  [key: string]: unknown;
}

export interface MedicationsPreview {
  entries: MedicationEntry[];
  provenance: Record<string, Record<string, unknown>>;
  reconciliation: MedicationsReconciliation | null;
  dataset_version: string | null;
  catalog_version: string | null;
  medication_fingerprint: string | null;
  generated_at: string | null;
  evaluation: {
    status: string;
    item_errors: Record<string, string[]>;
  };
  revision: number;
  server_timestamp?: string;
}

export interface DdiEvidence {
  source_severity: string | null;
  direction: string | null;
  raw_text: string | null;
  management: string | null;
  source_path: string | null;
  span_start: number | null;
  span_end: number | null;
  checksum: string | null;
  source_hash: string | null;
  [key: string]: unknown;
}

export interface DdiPair {
  drug_a: string;
  drug_b: string;
  status:
    | "interaction_found"
    | "covered_no_listed_interaction"
    | "coverage_unavailable";
  highest_known_severity: string | null;
  has_unknown_severity: boolean;
  conflicts: string[];
  evidence: DdiEvidence[];
  coverage_basis: Record<string, unknown>;
}

export interface DdiReport {
  dataset_version: string;
  catalog_version: string;
  medication_fingerprint: string;
  resolved_medications: CatalogDrug[];
  coverage_unavailable_medications: CatalogDrug[];
  pairs: DdiPair[];
  limitations: unknown[];
  generated_at: string;
}

export interface DdiReportPayload {
  report_status: "current" | "pending" | "error";
  reason: string | null;
  report: DdiReport | null;
  medication_fingerprint: string | null;
  stored_fingerprint: string | null;
  dataset_version: string | null;
  catalog_version: string | null;
  generated_at: string | null;
  reconciliation: MedicationsReconciliation | null;
  revision: number;
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

/** Demo-catalog search for the medication form (reads need no CSRF). */
export async function searchDrugs(
  query: string,
  limit = 25,
  offset = 0,
): Promise<DrugSearchResult> {
  const params = new URLSearchParams({
    query,
    limit: String(limit),
    offset: String(offset),
  });
  return request<DrugSearchResult>(`/drugs?${params.toString()}`);
}

/** Author-only medications preview (reads need no CSRF, like GET previews). */
export async function getMedications(
  encounterId: string,
): Promise<MedicationsPreview> {
  return request<MedicationsPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/medications`,
  );
}

export interface SaveMedicationsOptions {
  /** Reuse only for retry of the same attempt; new edits need a fresh key. */
  idempotencyKey?: string;
  reconciliation?: MedicationsReconciliation | null;
}

/** Author-only strict drug-only save (If-Match-fenced, revision-bumping).
 * Fresh key per attempt unless retrying the same attempt. Entries are sent
 * drug-only; any forged extra key is a server 422 (never stored). */
export async function saveMedications(
  encounterId: string,
  entries: Array<{ catalog_drug_id: string }>,
  revision: number,
  options: SaveMedicationsOptions = {},
): Promise<MedicationsPreview> {
  const body: Record<string, unknown> = { medications: entries };
  if (options.reconciliation !== undefined) {
    body["reconciliation"] = options.reconciliation;
  }
  return request<MedicationsPreview>(
    `/encounters/${encodeURIComponent(encounterId)}/medications`,
    {
      method: "POST",
      ifMatch: revision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body,
    },
  );
}

/** Author-only versioned DDI report with stale fencing (reads need no CSRF). */
export async function getDdiReport(
  encounterId: string,
): Promise<DdiReportPayload> {
  return request<DdiReportPayload>(
    `/encounters/${encodeURIComponent(encounterId)}/ddi-report`,
  );
}

export { newIdempotencyKey };
