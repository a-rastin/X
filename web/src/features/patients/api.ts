/* Patient directory + registration HTTP client (S06, plan.md §§2.2-2.3, 4.3).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Reads need any active authenticated session; creation needs an
 * active physician session plus the per-session CSRF token and a fresh
 * Idempotency-Key per submit. The patient identifier stays text end-to-end
 * (never Number) so leading zeros survive JSON. No secrets in logs.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../identity/api";

export type Sex = "M" | "F";
export type ClinicalStatus = "first_time" | "established";
export type ClinicalStatusFilter = "all" | ClinicalStatus;

export interface Patient {
  id: string;
  identifier: string;
  given_name: string;
  family_name: string;
  sex: Sex;
  age: number;
  clinical_status: ClinicalStatus;
  phone: string | null;
  archived: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface RegistrationDraft {
  id: string;
  patient_id: string;
  kind: string;
  author_id: string;
  lifecycle: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface CreatePatientResult {
  patient: Patient;
  draft: RegistrationDraft;
  encounter: RegistrationDraft;
  server_timestamp: string;
  revision: number;
}

export interface PatientList {
  items: Patient[];
  total: number;
  limit: number;
  offset: number;
}

export interface RegistrationPayload {
  identifier: string;
  given_name: string;
  family_name: string;
  sex: Sex;
  age: number;
  clinical_status: ClinicalStatus;
  /** Omitted when the optional phone is blank. */
  phone?: string;
}

export const DIRECTORY_PAGE_SIZE = 25;

interface RequestOptions {
  method?: string;
  body?: unknown;
  idempotencyKey?: string;
  ifMatch?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  if (options.ifMatch !== undefined) {
    headers["If-Match"] = `"${options.ifMatch}"`;
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

export interface DirectoryQuery {
  query: string;
  clinicalStatus: ClinicalStatusFilter;
  limit?: number;
  offset?: number;
}

/** Shared directory search (plan §2.3): name/ID substring, status filter,
 * bounded pagination with server-stable ordering, no author scoping. */
export async function listPatients(directory: DirectoryQuery): Promise<PatientList> {
  const params = new URLSearchParams();
  const trimmed = directory.query.trim();
  if (trimmed !== "") {
    params.set("query", trimmed);
  }
  if (directory.clinicalStatus !== "all") {
    params.set("clinical_status", directory.clinicalStatus);
  }
  params.set("limit", String(directory.limit ?? DIRECTORY_PAGE_SIZE));
  params.set("offset", String(directory.offset ?? 0));
  const suffix = params.toString();
  return request<PatientList>(`/patients${suffix ? `?${suffix}` : ""}`);
}

export async function getPatient(id: string): Promise<Patient> {
  const result = await request<{ patient: Patient }>(
    `/patients/${encodeURIComponent(id)}`,
  );
  return result.patient;
}

/** Physician-only registration (plan §2.1): administrators get 403 here. */
export async function createPatient(
  payload: RegistrationPayload,
): Promise<CreatePatientResult> {
  // Fresh key per submit: same key + changed body would be a 409, so a
  // corrected re-submit after a conflict must never reuse the old key.
  return request<CreatePatientResult>("/patients", {
    method: "POST",
    idempotencyKey: newIdempotencyKey(),
    body: payload,
  });
}

/** Shared demographics edit (S51 §2, plan §2.1: physician writes, admin reads).
 * Any active physician may edit with If-Match on the patient revision
 * (412 stale, revision bump). The response carries the patient only — never
 * draft content, so this route grants no draft access. `identifier` and
 * `archived` travel via their own routes (never sent here). */
export interface DemographicsPatch {
  given_name?: string;
  family_name?: string;
  sex?: Sex;
  age?: number;
  clinical_status?: ClinicalStatus;
  phone?: string | null;
}

export interface PatientMutationResult {
  patient: Patient;
  revision: number;
  server_timestamp: string;
}

/** Relevant (analysis-visible) edit fields (S51 §2, provisional): age/sex/
 * clinical_status stale affected results; names/phone do not. */
export function isRelevantDemographicsEdit(patch: DemographicsPatch): boolean {
  return (
    patch.age !== undefined || patch.sex !== undefined || patch.clinical_status !== undefined
  );
}

export async function patchPatientDemographics(
  id: string,
  patch: DemographicsPatch,
  expectedRevision: number,
  options: { idempotencyKey?: string } = {},
): Promise<PatientMutationResult> {
  // Fresh key per explicit Save (same key + changed body is a server 409);
  // the button stays disabled while pending so double-click cannot
  // double-submit.
  return request<PatientMutationResult>(`/patients/${encodeURIComponent(id)}`, {
    method: "PATCH",
    ifMatch: expectedRevision,
    idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
    body: patch,
  });
}

/** Admin-only archive/unarchive (S51 §1, provisional policy): If-Match on
 * the patient revision (412 stale, 422 missing, 404 unknown) plus a fresh
 * Idempotency-Key per explicit confirm. No delete route exists. */
export async function archivePatient(
  id: string,
  expectedRevision: number,
  options: { idempotencyKey?: string } = {},
): Promise<PatientMutationResult> {
  return request<PatientMutationResult>(
    `/patients/${encodeURIComponent(id)}/archive`,
    {
      method: "POST",
      ifMatch: expectedRevision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: {},
    },
  );
}

export async function unarchivePatient(
  id: string,
  expectedRevision: number,
  options: { idempotencyKey?: string } = {},
): Promise<PatientMutationResult> {
  return request<PatientMutationResult>(
    `/patients/${encodeURIComponent(id)}/unarchive`,
    {
      method: "POST",
      ifMatch: expectedRevision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: {},
    },
  );
}
