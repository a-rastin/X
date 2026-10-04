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
  const login = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(login.ok()).toBeTruthy();
  const { csrf_token } = await login.json();
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const create = await request.post("/api/v1/physicians", {
    headers: { "X-CSRF-Token": csrf_token, "Idempotency-Key": key },
    data: { username, password },
  });
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
