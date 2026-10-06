/* Model version administration HTTP client (S24, seams T1/T9; plan.md §7.3).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Every route is admin-only: 401 unauthenticated, 403 otherwise
 * (physician included). The route guard in pages.tsx only chooses what to
 * render — authority stays server-side. Mutations carry the per-session CSRF
 * token plus a fresh Idempotency-Key per submit; activation/rollback carry
 * an optional If-Match pointer revision (empty = first activation, no
 * precondition). Validation wording stays structural/pipeline-only: XSD
 * success is never labeled executable or clinically valid.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../../identity/api";

export { ApiError };

export type ReviewDecision = "draft" | "awaiting_review" | "approved";

export interface ReviewInput {
  reviewer: string;
  decision: ReviewDecision;
  date: string;
}

export interface NetworkItem {
  id: string;
  name: string;
  created_at: string;
  version_count: number;
  latest_version?: {
    id: string;
    version_number: number;
    source_hash: string;
    status: string;
  } | null;
}

export interface NetworkList {
  items: NetworkItem[];
  total: number;
}

export interface StoredReview {
  reviewer: string;
  decision: string;
  date: string;
}

export interface VersionSummary {
  xsd_valid: boolean;
  activatable_v1: boolean;
  nonactivatable_reasons?: string[];
  semantic_valid?: boolean;
  code?: string;
}

export interface VersionItem {
  id: string;
  network_id: string;
  version_number: number;
  source_hash: string;
  status: string;
  review: StoredReview;
  created_at: string;
  validation: VersionSummary;
}

export interface VersionList {
  network_id: string;
  items: VersionItem[];
  total: number;
}

export interface ImportResult {
  network: { id: string; name: string; created_at: string };
  version: VersionItem;
}

export interface StructuralReport {
  xsd_valid: boolean;
  xsd_errors: { line: number; column: number; message: string }[];
  activatable_v1: boolean;
  nonactivatable_reasons: string[];
  source_hash: string;
  network_count: number;
}

export interface SemanticReport {
  valid: boolean;
  errors: { code: string; message: string; node: string }[];
}

export interface ContentReport {
  valid: boolean;
  errors: { code: string; message: string }[];
}

export interface AdmissionReport {
  executable: boolean;
  diagnostics: { code: string; message: string }[];
}

export interface ValidationReport {
  version_id: string;
  network_id: string;
  version_number: number;
  source_hash: string;
  status: string;
  review: StoredReview;
  structural: StructuralReport;
  semantic: SemanticReport;
  content: ContentReport;
  admission: AdmissionReport;
}

export interface GraphNetwork {
  network_name: string;
  /** VARIABLE order from the stored XML. */
  nodes: string[];
  /** [parent, child] pairs in DEFINITION/GIVEN order. */
  edges: [string, string][];
  /** OUTCOME order per node. */
  states: Record<string, string[]>;
}

export interface GraphPayload {
  version_id: string;
  network_id: string;
  version_number: number;
  source_hash: string;
  graphs: GraphNetwork[];
  validation: {
    xsd_valid: boolean;
    activatable_v1: boolean;
    nonactivatable_reasons: string[];
    semantic_valid: boolean;
  };
}

export type Workflow = "registration" | "followup";

export interface BundleResult {
  workflow: string;
  revision: number;
  bundle: unknown;
  updated_at: string | null;
  etag: string | null;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  idempotencyKey?: string;
  ifMatch?: string;
}

async function requestWithHeaders<T>(
  path: string,
  options: RequestOptions = {},
): Promise<{ body: T; etag: string | null }> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  if (options.idempotencyKey !== undefined) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }
  if (options.ifMatch !== undefined) {
    headers["If-Match"] = options.ifMatch;
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
  return { body: (payload ?? {}) as T, etag: response.headers.get("etag") };
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body } = await requestWithHeaders<T>(path, options);
  return body;
}

export async function listNetworks(): Promise<NetworkList> {
  // Small admin pool (same reasoning as the physician table): one bounded
  // page plus the rendered total keeps truncation visible, not silent.
  return request<NetworkList>("/networks?limit=100");
}

export async function importNetwork(
  name: string,
  xmlText: string,
  review: ReviewInput | null,
): Promise<ImportResult> {
  const result = await request<{ network: ImportResult["network"]; version: VersionItem }>(
    "/networks",
    {
      method: "POST",
      // Fresh key per submit: same key + changed body would be a 409, so a
      // corrected re-submit after a conflict must never reuse the old key.
      idempotencyKey: newIdempotencyKey(),
      body: { name, xml_text: xmlText, review },
    },
  );
  return result;
}

export async function listVersions(networkId: string): Promise<VersionList> {
  return request<VersionList>(
    `/networks/${encodeURIComponent(networkId)}/versions?limit=100`,
  );
}

export async function createVersion(
  networkId: string,
  xmlText: string,
  review: ReviewInput | null,
): Promise<{ version: VersionItem }> {
  return request<{ version: VersionItem }>(
    `/networks/${encodeURIComponent(networkId)}/versions`,
    {
      method: "POST",
      idempotencyKey: newIdempotencyKey(),
      body: { xml_text: xmlText, review },
    },
  );
}

export async function validateVersion(versionId: string): Promise<ValidationReport> {
  return request<ValidationReport>(`/network-versions/${encodeURIComponent(versionId)}/validate`, {
    method: "POST",
    idempotencyKey: newIdempotencyKey(),
    body: {},
  });
}

export async function readGraph(versionId: string): Promise<GraphPayload> {
  return request<GraphPayload>(`/network-versions/${encodeURIComponent(versionId)}/graph`);
}

/** Exact preserved bytes (byte identity with the stored import). */
export async function exportXml(versionId: string): Promise<string> {
  const response = await fetch(
    `/api/v1/network-versions/${encodeURIComponent(versionId)}/xml`,
    { credentials: "include" },
  );
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    let code = "REQUEST_FAILED";
    let fieldErrors: Record<string, string[]> = {};
    try {
      const payload = (await response.json()) as {
        code?: string;
        message?: string;
        field_errors?: Record<string, string[]>;
      };
      code = payload.code ?? code;
      message = payload.message ?? message;
      fieldErrors = payload.field_errors ?? {};
    } catch {
      /* non-JSON failure: keep the generic message */
    }
    throw new ApiError(response.status, code, message, fieldErrors);
  }
  return response.text();
}

export interface BundleInput {
  workflow: Workflow;
  networkId: string;
  versionId: string;
  review: ReviewInput;
  /** Pointer revision for If-Match; null omits the precondition. */
  expectedRevision: number | null;
}

async function moveBundle(path: "/model-bundles/activate" | "/model-bundles/rollback", input: BundleInput): Promise<BundleResult> {
  const { body, etag } = await requestWithHeaders<{
    workflow: string;
    revision: number;
    bundle: unknown;
    updated_at: string | null;
  }>(path, {
    method: "POST",
    idempotencyKey: newIdempotencyKey(),
    ifMatch: input.expectedRevision === null ? undefined : `"${input.expectedRevision}"`,
    body: {
      workflow: input.workflow,
      selections: [{ network_id: input.networkId, version_id: input.versionId }],
      review: input.review,
    },
  });
  return { ...body, etag };
}

export async function activateBundle(input: BundleInput): Promise<BundleResult> {
  return moveBundle("/model-bundles/activate", input);
}

export async function rollbackBundle(input: BundleInput): Promise<BundleResult> {
  return moveBundle("/model-bundles/rollback", input);
}
