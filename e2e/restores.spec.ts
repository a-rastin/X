import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  uniqueName,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
  signOut,
} from "./helpers";

/* S55 staged restore validation, admin restores UI (seam T9).
 *
 * Backend (read-only): `BT/recovery/test_restore_validation.py` (T1/T10)
 * owns `POST /api/v1/restores/validate` (admin-only upload-to-staging with
 * CSRF; raw zip body, synchronous bounded staging, 201 with the terminal
 * `succeeded`/`failed` job) and `GET /api/v1/restores/{id}`
 * (admin-only progress/impact read, private, no-store). No commit route
 * exists server-side until S56. No backend/src changes here.
 *
 * Frontend (read-only): `web/src/features/admin/restores/api.ts` +
 * `RestoresPage.tsx` mounted via `#/restores` + `RestoresRoutePage` guard in
 * app/pages.tsx (complement to the server 401/403, never a replacement) +
 * admin `Restores` nav entry in app/App.tsx. No web/src changes here.
 *
 * Constraints: real endpoints only — NO page.route mocks anywhere in this
 * spec. Every staged report/digest/error comes from the live backend over
 * the shared dev database with real admin/physician auth flows. The valid
 * archive in every test is a real S54 backup zip (created via the real
 * `POST /api/v1/backups` + downloaded bytes, never a fixture zip, never
 * fake rows).
 *
 * Selector contract: restores-heading, restores-upload, restores-status
 * (role=status), restores-digest, restores-report, restores-error
 * (role=alert) — each with a matching DOM id (#restores-*).
 *
 * Run with --workers=1 when the shared dev DB is under parallel pressure:
 * npx playwright test e2e/restores.spec.ts --project=chromium --project=firefox
 */

const API_BASE = "http://127.0.0.1:8000";

async function loginAdmin(page: import("@playwright/test").Page): Promise<void> {
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible({ timeout: 15000 });
}

async function loginPhysician(
  page: import("@playwright/test").Page,
  request: import("@playwright/test").APIRequestContext,
  prefix: string,
): Promise<{ username: string; password: string }> {
  const username = uniqueName(prefix);
  const password = "secret123";
  await ensurePhysician(request, username, password);
  await loginAs(page, "physician", username, password);
  await acknowledgeWarning(page);
  return { username, password };
}

async function gotoRestoresViaNav(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.getByRole("link", { name: "Restores" }).click();
  await expect(page.getByTestId("restores-heading")).toBeVisible({
    timeout: 15000,
  });
  // Stable-hook contract: every hook carries both data-testid and id.
  for (const id of ["restores-heading", "restores-upload"]) {
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
}

/** Create a real S54 backup zip over the live API (T1): admin login,
 * `POST /backups` with CSRF + fresh Idempotency-Key, then download bytes.
 * Returns the raw zip bytes plus the S54 backup id for report cross-checks.
 */
async function createBackupZipViaApi(
  request: import("@playwright/test").APIRequestContext,
): Promise<{ bytes: Buffer; backupId: string }> {
  const login = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(login.ok(), await login.text()).toBeTruthy();
  const { csrf_token } = await login.json();
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const created = await request.post("/api/v1/backups", {
    headers: { "X-CSRF-Token": csrf_token as string, "Idempotency-Key": key },
  });
  expect(created.status(), await created.text()).toBe(201);
  const job = await created.json();
  expect(job["status"], JSON.stringify(job)).toBe("succeeded");
  const backupId = job["id"] as string;
  expect(backupId).toMatch(/^[0-9a-f-]{36}$/i);

  const zipped = await request.get(`/api/v1/backups/${backupId}/download`);
  expect(zipped.status(), await zipped.text()).toBe(200);
  expect(zipped.headers()["content-type"] ?? "").toContain("application/zip");
  const body = await zipped.body();
  expect(body.length).toBeGreaterThan(1000);
  return { bytes: Buffer.from(body), backupId };
}

async function stageZipViaUi(
  page: import("@playwright/test").Page,
  filename: string,
  bytes: Buffer,
): Promise<void> {
  await page.getByTestId("restores-upload").setInputFiles({
    name: filename,
    mimeType: "application/zip",
    buffer: bytes,
  });
  // File pick echoes the selection; the Validate action becomes enabled.
  await expect(page.getByTestId("restores-status")).toContainText(
    new RegExp(`Selected ${filename} \\(\\d+ bytes\\)\\.`.replace(".", "\\.")),
    { timeout: 15000 },
  );
  const validate = page.getByRole("button", { name: "Validate in staging" });
  await expect(validate).toBeEnabled();
  await validate.click();
}

// S55 §§1+4: admin stages a real S54 backup zip → terminal succeeded job
// with impact report + immutable digest, and no Commit control anywhere.
test("S55 §§1+4 admin stages a real backup zip with report, digest, no Commit", async ({
  page,
  request,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  await expect(page.getByRole("link", { name: "Restores" })).toBeVisible();
  await gotoRestoresViaNav(page);

  const { bytes, backupId } = await createBackupZipViaApi(request);
  await stageZipViaUi(page, `x-insight-backup-${backupId}.zip`, bytes);

  // Terminal staged status: live data explicitly untouched.
  await expect(page.getByTestId("restores-status")).toContainText(
    /Archive staged and validated\. Live data unchanged/,
    { timeout: 120000 },
  );
  await expect(page.locator("#restores-status")).toBeVisible();
  await expect(page.getByTestId("restores-status")).toHaveAttribute(
    "role",
    "status",
  );

  // Immutable confirmation digest renders exactly once, as 64 hex.
  const digest = page.getByTestId("restores-digest");
  await expect(digest).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#restores-digest")).toBeVisible();
  await expect(digest).toContainText("Confirmation digest");
  await expect(digest).toContainText("Immutable staging digest");
  await expect(digest).toContainText("never renders a Commit control");
  await expect(digest).toContainText("belongs to S56");
  const digestCode = (await digest.locator("code").first().textContent()) ?? "";
  expect(digestCode.trim()).toMatch(/^[0-9a-f]{64}$/i);

  // Impact report: backup date/schema/content/checksums/impact + key note.
  const report = page.getByTestId("restores-report");
  await expect(report).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#restores-report")).toBeVisible();
  await expect(report).toContainText("Validation report");
  await expect(report).toContainText(backupId);
  await expect(report).toContainText("backup-v1");
  await expect(report).toContainText(/Staged content/);
  await expect(
    report.getByRole("columnheader", { name: "Table" }).first(),
  ).toBeVisible();
  await expect(
    report.getByRole("columnheader", { name: "Staged rows" }).first(),
  ).toBeVisible();
  await expect(
    report.getByRole("columnheader", { name: "SHA-256" }),
  ).toBeVisible();
  await expect(
    report.getByRole("columnheader", { name: "Live rows" }),
  ).toBeVisible();
  for (const table of ["users", "patients", "encounters"]) {
    await expect(report.getByText(table, { exact: true }).first()).toBeVisible();
  }
  await expect(report).toContainText("database.json");
  await expect(report).toContainText("policies.json");
  await expect(report).toContainText("Replacement impact");
  await expect(report).toContainText(/re-entry/);
  await expect(report).toContainText(/Staging only/);
  await expect(report).toContainText(/S56/);
  await expect(page.getByText("Status:").first()).toBeVisible();
  await expect(page.getByText("succeeded").first()).toBeVisible();

  // No enabled Commit control anywhere on the staged page: no commit
  // button exists at all (the digest/report mention commit only as prose
  // saying it belongs to S56 / is staging-only).
  await expect(
    page.getByRole("button", { name: /commit/i }),
  ).toHaveCount(0);

  // Re-check re-GETs the same terminal job (progress/impact read path).
  const recheck = page.getByRole("button", { name: "Re-check status" });
  await expect(recheck).toBeVisible();
  await recheck.click();
  await expect(page.getByTestId("restores-status")).toContainText(
    /Archive staged and validated\. Live data unchanged/,
    { timeout: 30000 },
  );
  const redigest = (
    (await digest.locator("code").first().textContent()) ?? ""
  ).trim();
  expect(redigest).toBe(digestCode.trim());

  // Same staging over the real API: digest/report stable, commit unavailable.
  const body = (await page.textContent("body")) ?? "";
  const restoreMatch = body.match(/Restore id:\s*([0-9a-f-]{36})/i);
  expect(restoreMatch, "restore id rendered after staging").not.toBeNull();
  const restoreId = restoreMatch![1];

  const adminLogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(adminLogin.ok(), await adminLogin.text()).toBeTruthy();
  const fetched = await request.get(`/api/v1/restores/${restoreId}`);
  expect(fetched.status(), await fetched.text()).toBe(200);
  expect(
    fetched.headers()["cache-control"] ?? "",
    "staging read is private, no-store",
  ).toContain("no-store");
  const staged = await fetched.json();
  expect(staged["id"]).toBe(restoreId);
  expect(staged["status"]).toBe("succeeded");
  expect(staged["expired"]).toBe(false);
  expect(staged["confirmation_digest"]).toBe(digestCode.trim());
  expect(staged["report"]["confirmation_digest"]).toBe(digestCode.trim());
  expect(staged["report"]["backup_id"]).toBe(backupId);
  expect(staged["report"]["manifest_schema"]).toBe("backup-v1");
  expect(staged["report"]["commit_available"]).toBe(false);
  expect(staged["report"]["archive_sha256"]).toMatch(/^[0-9a-f]{64}$/i);
  expect(Object.keys(staged["report"]["inventory"]).length).toBeGreaterThan(10);
  const dumped = JSON.stringify(staged).toLowerCase();
  expect(dumped).not.toContain("archive_path");
  expect(dumped).not.toContain("commit_url");
  expect(dumped).not.toContain("commit_endpoint");
});

// S55 §2+§4: corrupt zip upload shows the #restores-error validation
// message with no digest/report, and live patients/login stay unchanged.
test("S55 §2+§4 corrupt zip shows validation error, live state unchanged", async ({
  page,
  request,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  await gotoRestoresViaNav(page);

  // Live baseline before the failed staging (real reads, no mocks).
  const adminLogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(adminLogin.ok(), await adminLogin.text()).toBeTruthy();
  const beforePatients = await request.get("/api/v1/patients");
  expect(beforePatients.status(), await beforePatients.text()).toBe(200);
  const beforeTotal = (await beforePatients.json())["total"] as number;
  const beforeMe = await request.get("/api/v1/me");
  expect(beforeMe.status(), await beforeMe.text()).toBe(200);
  const beforeUser = (await beforeMe.json())["user"]["username"] as string;

  const corrupt = Buffer.from("this is not a zip file \u2014 corrupt", "utf-8");
  await stageZipViaUi(page, "corrupt.zip", corrupt);

  // Failed staging surfaces the validation message as role=alert; no
  // digest/report stages (staging writes only restore_jobs + audit rows).
  const error = page.getByTestId("restores-error");
  await expect(error).toBeVisible({ timeout: 120000 });
  await expect(page.locator("#restores-error")).toBeVisible();
  await expect(error).toHaveAttribute("role", "alert");
  await expect(error).toContainText(/not a zip|unreadable|validation failed/i);
  await expect(page.getByTestId("restores-digest")).toHaveCount(0);
  await expect(page.getByTestId("restores-report")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /commit/i }),
  ).toHaveCount(0);

  // Live patients/login unchanged after the failure (same totals, same
  // admin identity, fresh logins still 200, heading still live).
  const afterPatients = await request.get("/api/v1/patients");
  expect(afterPatients.status(), await afterPatients.text()).toBe(200);
  expect((await afterPatients.json())["total"]).toBe(beforeTotal);
  const afterMe = await request.get("/api/v1/me");
  expect(afterMe.status(), await afterMe.text()).toBe(200);
  expect((await afterMe.json())["user"]["username"]).toBe(beforeUser);
  const relogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(relogin.ok(), await relogin.text()).toBeTruthy();
  await expect(page.getByTestId("restores-heading")).toBeVisible();
});

// S55 §3 (403 complement): physician has no Restores nav, the direct route
// renders the guard (never staging actions), and the real API denies every
// restore route.
test("S55 §3 physician sees no restores nav, route guards, API denies", async ({
  page,
  request,
  playwright,
}) => {
  test.setTimeout(180000);
  const physician = await loginPhysician(page, request, "e2es55phys");
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible({ timeout: 15000 });
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Restores" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();

  await page.goto("/#/restores");
  await expect(
    page.getByRole("heading", { name: "Staged restores" }),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByText("Administrator access required."),
  ).toBeVisible();
  // Guard renders no staging actions or content.
  await expect(page.getByTestId("restores-heading")).toHaveCount(0);
  await expect(page.getByTestId("restores-upload")).toHaveCount(0);
  await expect(page.getByTestId("restores-status")).toHaveCount(0);
  await expect(page.getByTestId("restores-digest")).toHaveCount(0);
  await expect(page.getByTestId("restores-report")).toHaveCount(0);
  await expect(page.getByTestId("restores-error")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /commit/i }),
  ).toHaveCount(0);

  // Real API denials with the physician's own session (CSRF on the
  // mutation, plain read on the other — both 403, never the staging).
  const csrf = await physicianSession(request, physician);
  const denied = await request.post("/api/v1/restores/validate", {
    headers: {
      "X-CSRF-Token": csrf,
      "Content-Type": "application/zip",
    },
    data: Buffer.from("not a zip", "utf-8"),
  });
  expect(denied.status(), await denied.text()).toBe(403);
  expect((await denied.json())["code"]).toBe("FORBIDDEN");
  const read = await request.get(`/api/v1/restores/${randomUUID()}`);
  expect(read.status(), await read.text()).toBe(403);

  // Anonymous staging is 401 (complements the T1 gate suite).
  const anon = await playwright.request.newContext({ baseURL: API_BASE });
  try {
    const anonPost = await anon.post("/api/v1/restores/validate", {
      headers: { "Content-Type": "application/zip" },
      data: Buffer.from("not a zip", "utf-8"),
    });
    expect(anonPost.status()).toBe(401);
    const anonGet = await anon.get(`/api/v1/restores/${randomUUID()}`);
    expect(anonGet.status()).toBe(401);
  } finally {
    await anon.dispose();
  }

  await signOut(page);
});

// S55 (cheap): dark theme renders the full staging flow; keyboard stages a
// real backup zip and re-checks; status uses role=status and alert for
// errors; both themes end clean.
test("S55 restores work in dark theme via keyboard", async ({
  page,
  request,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  const themeOf = (): Promise<string | null> =>
    page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  if ((await themeOf()) !== "dark") {
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
  }
  await expect.poll(themeOf).toBe("dark");

  await gotoRestoresViaNav(page);
  const { bytes, backupId } = await createBackupZipViaApi(request);

  // Keyboard-only staging: the file is picked programmatically (file
  // choosers have no keyboard path), then focus + Enter drives the real
  // upload mutation and the re-check read.
  await page.getByTestId("restores-upload").setInputFiles({
    name: `x-insight-backup-${backupId}.zip`,
    mimeType: "application/zip",
    buffer: bytes,
  });
  await expect(page.getByTestId("restores-status")).toContainText(
    /Selected x-insight-backup-.*\.zip \(\d+ bytes\)\./,
    { timeout: 15000 },
  );
  const validate = page.getByRole("button", { name: "Validate in staging" });
  await validate.focus();
  await expect(validate).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("restores-status")).toContainText(
    /Archive staged and validated\. Live data unchanged/,
    { timeout: 120000 },
  );
  const digest = page.getByTestId("restores-digest");
  await expect(digest).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("restores-report")).toContainText(backupId);
  await expect(
    page.getByRole("button", { name: /commit/i }),
  ).toHaveCount(0);

  // Keyboard-only re-check: Tab/Enter walks the staged actions without a
  // mouse; the terminal confirmation stays staged.
  const recheck = page.getByRole("button", { name: "Re-check status" });
  await recheck.focus();
  await expect(recheck).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("restores-status")).toContainText(
    /Archive staged and validated\. Live data unchanged/,
    { timeout: 30000 },
  );
  await expect(page.getByTestId("restores-digest")).toBeVisible();

  // Print: heading + staged sections stay readable; nav and file actions
  // hide (theme.css @media print keeps .xi-card, hides .xi-no-print).
  await page.emulateMedia({ media: "print" });
  await expect(page.getByTestId("restores-heading")).toBeVisible();
  await expect(page.getByTestId("restores-digest")).toBeVisible();
  await expect(page.getByTestId("restores-report")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Primary" }),
  ).toBeHidden();
  await expect(page.getByTestId("restores-upload")).toBeHidden();
  await page.emulateMedia({ media: "screen" });

  // Light theme renders the same staging (both-themes evidence).
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect.poll(themeOf).toBe("light");
  await expect(page.getByTestId("restores-heading")).toBeVisible();
  await expect(page.getByTestId("restores-digest")).toBeVisible();
  await expect(page.getByTestId("restores-report")).toBeVisible();
});
