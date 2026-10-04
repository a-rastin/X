import { test, expect } from "@playwright/test";

export const RESEARCH_WARNING =
  "This application is a research prototype and must not be used as the sole basis for treating patients.";
export const GENERIC_LOGIN_ERROR = "Invalid username, password, or role.";

export function uniqueName(prefix: string): string {
  const rand = Math.random().toString(36).slice(2, 8);
  return `${prefix}${Date.now().toString(36)}${rand}`.toLowerCase();
}

/** Create a physician through the real backend API (seam T1). */
export async function ensurePhysician(
  request: import("@playwright/test").APIRequestContext,
  username: string,
  password: string,
): Promise<void> {
  const post = (csrfToken: string, idempotencyKey: string) =>
    request.post("/api/v1/physicians", {
      headers: { "X-CSRF-Token": csrfToken, "Idempotency-Key": idempotencyKey },
      data: { username, password },
    });
  const loginAdmin = async (): Promise<string> => {
    const login = await request.post("/api/v1/auth/login", {
      data: { username: "admin", password: "admin", role: "admin" },
    });
    expect(login.ok()).toBeTruthy();
    const { csrf_token } = await login.json();
    return csrf_token as string;
  };
  // One payload per call: a retry resends the identical body with the same key.
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  let create = await post(await loginAdmin(), key);
  if (create.status() === 401) {
    // Same backend commit-visibility race as in ensurePatient below: one
    // fresh admin login, then fail loudly.
    create = await post(await loginAdmin(), key);
  }
  expect(create.status(), await create.text()).toBe(201);
}

export async function loginAs(
  page: import("@playwright/test").Page,
  role: "admin" | "physician",
  username: string,
  password: string,
): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Role").selectOption(role);
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export async function acknowledgeWarning(page: import("@playwright/test").Page): Promise<void> {
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(RESEARCH_WARNING);
  await dialog.getByRole("button", { name: "Acknowledge and continue" }).click();
  await expect(dialog).toBeHidden();
}

export async function signOut(page: import("@playwright/test").Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  // Wait for the round trip to finish: navigating away mid-logout would
  // abort the revocation and leave the acknowledgement behind.
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
}

export function uniquePatientId(): string {
  // Exactly ten ASCII digits as text; leading zeros preserved by
  // construction (never a number).
  let digits = "";
  for (let i = 0; i < 10; i += 1) {
    digits += Math.floor(Math.random() * 10).toString();
  }
  return digits;
}

export function uniqueLetters(prefix: string): string {
  // Patient names accept Unicode letters only: no digits, spaces, or
  // punctuation, so the suffix is drawn from lowercase ASCII letters.
  let suffix = "";
  for (let i = 0; i < 6; i += 1) {
    suffix += String.fromCharCode(97 + Math.floor(Math.random() * 26));
  }
  return `${prefix}${suffix}`.toLowerCase();
}

export interface PhysicianCredentials {
  username: string;
  password: string;
}

/** Log in as a physician through the real backend API (seam T1) and return
 * the session CSRF token. The session cookie lands in the request jar. */
export async function physicianSession(
  request: import("@playwright/test").APIRequestContext,
  physician: PhysicianCredentials,
): Promise<string> {
  const login = await request.post("/api/v1/auth/login", {
    data: {
      username: physician.username,
      password: physician.password,
      role: "physician",
    },
  });
  expect(login.ok()).toBeTruthy();
  const { csrf_token } = await login.json();
  return csrf_token as string;
}

/** Register a patient through the real backend API (seam T1) as a physician.
 * Returns the created patient row. Pass a `physicianSession` CSRF token to
 * reuse one login across many calls in the same test jar. */
export async function ensurePatient(
  request: import("@playwright/test").APIRequestContext,
  physician: PhysicianCredentials,
  overrides: Record<string, unknown> = {},
  csrf?: string,
): Promise<Record<string, unknown>> {
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  // One payload per call: a retry must resend the identical body with the
  // same key, or the server correctly answers 409 IDEMPOTENCY_CONFLICT.
  const payload = {
    identifier: uniquePatientId(),
    given_name: uniqueLetters("given"),
    family_name: uniqueLetters("family"),
    sex: "F",
    age: 30,
    clinical_status: "first_time",
    ...overrides,
  };
  const post = (csrfToken: string) =>
    request.post("/api/v1/patients", {
      headers: { "X-CSRF-Token": csrfToken, "Idempotency-Key": key },
      data: payload,
    });
  let create = await post(csrf ?? (await physicianSession(request, physician)));
  if (create.status() === 401 && csrf === undefined) {
    // Backend commit-visibility race: a login 200 can reach the client
    // before its session row is visible to the very next request, so an
    // immediate create sporadically 401s under parallel load and succeeds
    // on retry with the same session (proven with a direct-HTTP repro, no
    // browser involved). One fresh login, then fail loudly — a persistent
    // 401 still fails the expect below. Backend owner must order
    // commit-before-response; until then this keeps setup robust.
    create = await post(await physicianSession(request, physician));
  }
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return body.patient as Record<string, unknown>;
}
