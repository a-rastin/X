/* Admin provider settings (S42).
 *
 * Same-origin /api/v1. GET 404 NOT_FOUND means nothing is saved: empty base
 * URL and model, key absent, capability untested, and the next PUT omits
 * If-Match. A loaded revision sends If-Match as a quoted revision.
 * secret_action "unchanged" and "clear" never include api_key. Responses
 * are masked ("configured" | "absent"); this client never reads a key back
 * and never puts a typed key on an error.
 */

import { ApiError, CSRF_HEADER, getCsrfToken } from "../../identity/api";
import { formatIfMatch } from "../../encounters/api";

export { ApiError };

export type KeyStatus = "configured" | "absent";
export type Capability = "untested" | "verified" | "failed";
export type SecretAction = "unchanged" | "replace" | "clear";

export interface ProviderSettings {
  revision: number | null;
  api_key: KeyStatus;
  base_url: string;
  model: string;
  capability: Capability;
}

export interface SavedProviderSettings {
  revision: number;
  api_key: KeyStatus;
  base_url: string;
  model: string;
  capability: Capability;
  affected_runs?: number;
}

export interface ProviderTestResult {
  capability: Capability;
  diagnostic: string;
  revision: number;
  base_url: string;
  model: string;
}

const UNSAVED: ProviderSettings = {
  revision: null,
  api_key: "absent",
  base_url: "",
  model: "",
  capability: "untested",
};

function mutationHeaders(revision: number | null): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  if (revision !== null) {
    headers["If-Match"] = formatIfMatch(revision);
  }
  return headers;
}

async function readJson(response: Response): Promise<unknown> {
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
    const body = (payload ?? {}) as { code?: string; message?: string };
    throw new ApiError(
      response.status,
      body.code ?? "REQUEST_FAILED",
      body.message ?? `Request failed (${response.status}).`,
    );
  }
  return payload;
}

function keyStatus(value: unknown): KeyStatus {
  if (value === "configured" || value === "absent") {
    return value;
  }
  throw new ApiError(200, "REQUEST_FAILED", "Provider settings response was incomplete.");
}

function capability(value: unknown): Capability {
  if (value === "untested" || value === "verified" || value === "failed") {
    return value;
  }
  throw new ApiError(200, "REQUEST_FAILED", "Provider settings response was incomplete.");
}

function masked(payload: unknown): SavedProviderSettings {
  const body = (payload ?? {}) as Record<string, unknown>;
  if (typeof body.revision !== "number") {
    throw new ApiError(200, "REQUEST_FAILED", "Provider settings response was incomplete.");
  }
  const saved: SavedProviderSettings = {
    revision: body.revision,
    api_key: keyStatus(body.api_key),
    base_url: typeof body.base_url === "string" ? body.base_url : "",
    model: typeof body.model === "string" ? body.model : "",
    capability: capability(body.capability),
  };
  if (typeof body.affected_runs === "number") {
    saved.affected_runs = body.affected_runs;
  }
  return saved;
}

/** Admin read. 404 NOT_FOUND is an empty unsaved form, not an error. */
export async function getProviderSettings(): Promise<ProviderSettings> {
  const response = await fetch("/api/v1/api-settings", {
    method: "GET",
    credentials: "include",
  });
  try {
    return masked(await readJson(response));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404 && err.code === "NOT_FOUND") {
      return UNSAVED;
    }
    throw err;
  }
}

export interface SaveProviderSettingsInput {
  baseUrl: string;
  model: string;
  revision: number | null;
  secretAction: SecretAction;
  apiKey: string;
}

/** PUT. Omits If-Match and secret_action when nothing is saved. unchanged
 * and clear never send api_key; replace sends it only when one was typed. */
export async function saveProviderSettings(
  input: SaveProviderSettingsInput,
): Promise<SavedProviderSettings> {
  const body: Record<string, string> = {
    base_url: input.baseUrl,
    model: input.model,
  };
  if (input.revision === null) {
    if (input.apiKey !== "") {
      body.api_key = input.apiKey;
    }
  } else {
    body.secret_action = input.secretAction;
    if (input.secretAction === "replace" && input.apiKey !== "") {
      body.api_key = input.apiKey;
    }
  }
  const response = await fetch("/api/v1/api-settings", {
    method: "PUT",
    credentials: "include",
    headers: mutationHeaders(input.revision),
    body: JSON.stringify(body),
  });
  return masked(await readJson(response));
}

/** Capability check of the saved row. CSRF, empty body, no If-Match. */
export async function testProviderSettings(): Promise<ProviderTestResult> {
  const response = await fetch("/api/v1/api-settings/test", {
    method: "POST",
    credentials: "include",
    headers: mutationHeaders(null),
    body: "{}",
  });
  const body = (await readJson(response) ?? {}) as Record<string, unknown>;
  if (typeof body.revision !== "number" || typeof body.diagnostic !== "string") {
    throw new ApiError(200, "REQUEST_FAILED", "Provider test response was incomplete.");
  }
  return {
    capability: capability(body.capability),
    diagnostic: body.diagnostic,
    revision: body.revision,
    base_url: typeof body.base_url === "string" ? body.base_url : "",
    model: typeof body.model === "string" ? body.model : "",
  };
}
