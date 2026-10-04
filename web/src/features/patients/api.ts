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
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
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
