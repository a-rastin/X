/* Authenticated HTTP client for the identity seam (S05, plan.md §4.3).
 *
 * All routes live under /api/v1 at the same origin (dev/preview proxy
 * forwards /api to the backend). Mutations carry the per-session CSRF token
 * in the X-CSRF-Token header plus the HttpOnly session cookie. The token is
 * kept in memory with a sessionStorage backup so a page refresh (GET /me)
 * does not lose mutation ability within the tab session.
 */

export type Role = "admin" | "physician";
export type Theme = "light" | "dark";

export interface PublicUser {
  id: string;
  username: string;
  role: Role;
  theme: Theme;
}

export interface SafePhysician {
  id: string;
  username: string;
  role: string;
  active: boolean;
  theme: Theme;
  revision: number;
}

export const CSRF_HEADER = "X-CSRF-Token";
const CSRF_STORAGE_KEY = "xi_csrf_token";

let csrfToken: string | null = null;
try {
  csrfToken = sessionStorage.getItem(CSRF_STORAGE_KEY);
} catch {
  csrfToken = null;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
  try {
    if (token === null) {
      sessionStorage.removeItem(CSRF_STORAGE_KEY);
    } else {
      sessionStorage.setItem(CSRF_STORAGE_KEY, token);
    }
  } catch {
    /* storage unavailable: memory copy still works for this page lifetime */
  }
}

export class ApiError extends Error {
  status: number;
  code: string;
  fieldErrors: Record<string, string[]>;

  constructor(
    status: number,
    code: string,
    message: string,
    fieldErrors: Record<string, string[]> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  csrf?: boolean;
  headers?: Record<string, string>;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers ?? {}),
  };
  if (options.csrf) {
    const token = getCsrfToken();
    if (token) {
      headers[CSRF_HEADER] = token;
    }
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

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `key-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
}

function idempotencyKey(): string {
  return newIdempotencyKey();
}

export interface LoginResult {
  user: PublicUser;
  csrf_token: string;
  research_warning: string | null;
}

export async function login(
  username: string,
  password: string,
  role: Role,
): Promise<LoginResult> {
  const result = await request<LoginResult>("/auth/login", {
    method: "POST",
    body: { username, password, role },
  });
  setCsrfToken(result.csrf_token);
  return result;
}

export interface MeResult {
  user: PublicUser;
  research_warning: string | null;
}

export async function me(): Promise<MeResult> {
  return request<MeResult>("/me");
}

export async function logout(): Promise<void> {
  try {
    await request<{ status: string }>("/auth/logout", {
      method: "POST",
      csrf: true,
    });
  } finally {
    setCsrfToken(null);
  }
}

export interface PasswordResult {
  user: PublicUser;
  csrf_token: string;
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<PasswordResult> {
  // The server revokes every prior session and issues a fresh one, so the
  // replacement CSRF token must be adopted immediately.
  const result = await request<PasswordResult>("/me/password", {
    method: "POST",
    csrf: true,
    body: { current_password: currentPassword, new_password: newPassword },
  });
  setCsrfToken(result.csrf_token);
  return result;
}

export async function setTheme(theme: Theme): Promise<PublicUser> {
  const result = await request<{ user: PublicUser }>("/me/preferences", {
    method: "PATCH",
    csrf: true,
    body: { theme },
  });
  return result.user;
}

export interface PhysicianList {
  items: SafePhysician[];
  total: number;
  limit: number;
  offset: number;
}

export async function listPhysicians(): Promise<PhysicianList> {
  // S05 shows the full admin table without pager chrome: fewer than ten
  // physicians share the pool (plan.md §2.1), so one bounded page at the
  // server maximum (100) plus the rendered total keeps any future
  // truncation visible instead of silent. No offset paging at this scale.
  return request<PhysicianList>("/physicians?limit=100");
}

export async function createPhysician(
  username: string,
  password: string,
): Promise<SafePhysician> {
  const result = await request<{ user: SafePhysician }>("/physicians", {
    method: "POST",
    csrf: true,
    headers: { "Idempotency-Key": idempotencyKey() },
    body: { username, password },
  });
  return result.user;
}

export async function patchPhysician(
  id: string,
  patch: { username?: string; password?: string },
  revision: number,
): Promise<SafePhysician> {
  const result = await request<{ user: SafePhysician }>(`/physicians/${id}`, {
    method: "PATCH",
    csrf: true,
    headers: {
      "If-Match": `"${revision}"`,
      "Idempotency-Key": idempotencyKey(),
    },
    body: patch,
  });
  return result.user;
}

export type DraftAction = "retain" | "discard";

export async function deactivatePhysician(
  id: string,
  draftAction: DraftAction,
  draftSetRevision: number,
): Promise<SafePhysician> {
  const result = await request<{ user: SafePhysician }>(
    `/physicians/${id}/deactivate`,
    {
      method: "POST",
      csrf: true,
      headers: { "Idempotency-Key": idempotencyKey() },
      body: {
        draft_action: draftAction,
        draft_set_revision: draftSetRevision,
      },
    },
  );
  return result.user;
}

export async function reactivatePhysician(id: string): Promise<SafePhysician> {
  const result = await request<{ user: SafePhysician }>(
    `/physicians/${id}/reactivate`,
    {
      method: "POST",
      csrf: true,
      headers: { "Idempotency-Key": idempotencyKey() },
    },
  );
  return result.user;
}
