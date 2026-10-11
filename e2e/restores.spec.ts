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

// S55 §§1+4 + S56 staging reality: admin stages a real S54 backup zip →
// terminal succeeded job with impact report + immutable digest, plus the S56
// commit section (disabled until the exact digest is typed — S56 reality,
// not the S55 zero-commit-button world). No commit is executed here.
test("S55 §§1+4 admin stages a real backup zip with report, digest, commit gated", async ({
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
  await expect(digest).toContainText("the commit step below must present");
  await expect(digest).toContainText("Staging alone never replaces live data");
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

  // S56 reality: the commit section exists but stays gated — the Commit
  // button is disabled until the operator types the exact staged digest.
  // (S55 asserted zero /commit/i buttons; S56 intentionally adds one.)
  const commitSection = page.getByTestId("restores-commit-section");
  await expect(commitSection).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#restores-commit-section")).toBeVisible();
  await expect(commitSection).toContainText("Commit staged restore");
  await expect(commitSection).toContainText("Replacement, never a merge");
  const commitInput = page.getByTestId("restores-commit-digest-input");
  await expect(commitInput).toBeVisible();
  await expect(page.locator("#restores-commit-digest-input")).toBeVisible();
  await expect(commitInput).toHaveAttribute(
    "placeholder",
    "64-character hex digest",
  );
  const commitSubmit = page.getByTestId("restores-commit-submit");
  await expect(commitSubmit).toBeVisible();
  await expect(page.locator("#restores-commit-submit")).toBeVisible();
  await expect(commitSubmit).toContainText("Commit restore");
  await expect(commitSubmit).toBeDisabled();
  await expect(commitSection).toContainText(
    "Type the exact 64-character staged digest to enable Commit",
  );
  // No commit has run: no progress, no reopen, no maintenance hold.
  await expect(page.getByTestId("restores-commit-progress")).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-section")).toHaveCount(0);
  await expect(
    page.getByTestId("restores-maintenance-banner"),
  ).toHaveCount(0);
  // Operator recovery procedure is always rendered (rollback path docs).
  const recovery = page.getByTestId("restores-recovery-notes");
  await expect(recovery).toBeVisible();
  await expect(page.locator("#restores-recovery-notes")).toBeVisible();
  await expect(recovery).toContainText("Operator recovery procedure");
  await expect(recovery).toContainText("Rollback path");
  await expect(recovery).toContainText("DEPLOYMENT_FENCED");

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

  // Same staging over the real API: digest/report stable, commit idle.
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
  // S56 commit polling rides the same read: idle before any commit,
  // maintenance clear, no pre-restore backup yet.
  expect(staged["commit_status"]).toBe("idle");
  expect(staged["maintenance_active"]).toBe(false);
  expect(staged["pre_restore_backup_id"]).toBeNull();
  expect(Array.isArray(staged["commit_history"])).toBe(true);
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
  // S56 reality: a failed staging offers no commit section at all (no
  // premature Commit), plus no progress/reopen/maintenance hold.
  const error = page.getByTestId("restores-error");
  await expect(error).toBeVisible({ timeout: 120000 });
  await expect(page.locator("#restores-error")).toBeVisible();
  await expect(error).toHaveAttribute("role", "alert");
  await expect(error).toContainText(/not a zip|unreadable|validation failed/i);
  await expect(page.getByTestId("restores-digest")).toHaveCount(0);
  await expect(page.getByTestId("restores-report")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-section")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-digest-input")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-submit")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-progress")).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-section")).toHaveCount(0);
  await expect(
    page.getByTestId("restores-maintenance-banner"),
  ).toHaveCount(0);
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

// S55 §3 + S56 physician commit/reopen guard (403 complement): physician
// has no Restores nav, the direct route renders the guard (never staging or
// S56 commit/reopen actions), and the real API denies every restore route
// including commit/reopen.
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
  // Guard renders no staging actions or content — including every S56
  // commit/reopen/maintenance hook.
  await expect(page.getByTestId("restores-heading")).toHaveCount(0);
  await expect(page.getByTestId("restores-upload")).toHaveCount(0);
  await expect(page.getByTestId("restores-status")).toHaveCount(0);
  await expect(page.getByTestId("restores-digest")).toHaveCount(0);
  await expect(page.getByTestId("restores-report")).toHaveCount(0);
  await expect(page.getByTestId("restores-error")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-section")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-digest-input")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-submit")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-progress")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-status")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-error")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-history")).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-section")).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-submit")).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-status")).toHaveCount(0);
  await expect(page.getByTestId("restores-maintenance-banner")).toHaveCount(0);
  await expect(page.getByTestId("restores-recovery-notes")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /commit/i }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /reopen|verify health/i }),
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

  // S56 commit/reopen deny: stage one real backup as admin (separate
  // context so the physician cookie jar is untouched), then the physician
  // session is denied on both commit and reopen for that real restore id.
  const adminCtx = await playwright.request.newContext({ baseURL: API_BASE });
  let stagedId = "";
  try {
    const adminLogin = await adminCtx.post("/api/v1/auth/login", {
      data: { username: "admin", password: "admin", role: "admin" },
    });
    expect(adminLogin.ok(), await adminLogin.text()).toBeTruthy();
    const { csrf_token: adminCsrf } = await adminLogin.json();
    const backupKey = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    const created = await adminCtx.post("/api/v1/backups", {
      headers: {
        "X-CSRF-Token": adminCsrf as string,
        "Idempotency-Key": backupKey,
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const backupId = (await created.json())["id"] as string;
    const zipped = await adminCtx.get(`/api/v1/backups/${backupId}/download`);
    expect(zipped.status(), await zipped.text()).toBe(200);
    const staged = await adminCtx.post("/api/v1/restores/validate", {
      headers: {
        "X-CSRF-Token": adminCsrf as string,
        "Content-Type": "application/zip",
      },
      data: Buffer.from(await zipped.body()),
    });
    expect(staged.status(), await staged.text()).toBe(201);
    stagedId = (await staged.json())["id"] as string;
    expect(stagedId).toMatch(/^[0-9a-f-]{36}$/i);
  } finally {
    await adminCtx.dispose();
  }
  const commitDenied = await request.post(`/api/v1/restores/${stagedId}/commit`, {
    headers: { "X-CSRF-Token": csrf },
    data: { confirmation_digest: "a".repeat(64) },
  });
  expect(commitDenied.status(), await commitDenied.text()).toBe(403);
  expect((await commitDenied.json())["code"]).toBe("FORBIDDEN");
  const reopenDenied = await request.post(
    `/api/v1/restores/${stagedId}/reopen`,
    { headers: { "X-CSRF-Token": csrf } },
  );
  expect(reopenDenied.status(), await reopenDenied.text()).toBe(403);

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
    // S56 commit/reopen are 401 anonymously too (never the mutation).
    const anonCommit = await anon.post(`/api/v1/restores/${stagedId}/commit`, {
      data: { confirmation_digest: "a".repeat(64) },
    });
    expect(anonCommit.status()).toBe(401);
    const anonReopen = await anon.post(`/api/v1/restores/${stagedId}/reopen`);
    expect(anonReopen.status()).toBe(401);
  } finally {
    await anon.dispose();
  }

  await signOut(page);
});

// S55 + S56 (cheap): dark theme renders the full staging + gated commit
// flow; keyboard stages, re-checks, and reaches the commit confirmation;
// status uses role=status and alert for errors; both themes end clean.
// No commit is executed here (the destructive commit lives in the S56
// commit test below).
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
  // S56 reality: the commit section is present but gated. Every S56 hook
  // carries both data-testid and id.
  const commitSection = page.getByTestId("restores-commit-section");
  await expect(commitSection).toBeVisible({ timeout: 15000 });
  for (const id of [
    "restores-commit-section",
    "restores-commit-digest-input",
    "restores-commit-submit",
    "restores-recovery-notes",
  ]) {
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
  const commitInput = page.getByTestId("restores-commit-digest-input");
  const commitSubmit = page.getByTestId("restores-commit-submit");
  await expect(commitSubmit).toBeDisabled();
  await expect(commitSubmit).toContainText("Commit restore");
  await expect(commitSection).toContainText(
    "Type the exact 64-character staged digest to enable Commit",
  );
  await expect(
    page.getByTestId("restores-maintenance-banner"),
  ).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-section")).toHaveCount(0);
  await expect(page.getByTestId("restores-recovery-notes")).toContainText(
    "Operator recovery procedure",
  );

  // Keyboard-only commit gating: focus the confirmation input, type a
  // mismatched digest (stays disabled with inline guidance), then type the
  // exact staged digest (becomes enabled) — all without a mouse and without
  // executing the destructive commit.
  const digestCode =
    ((await digest.locator("code").first().textContent()) ?? "").trim();
  expect(digestCode).toMatch(/^[0-9a-f]{64}$/i);
  await commitInput.focus();
  await expect(commitInput).toBeFocused();
  await page.keyboard.type("0".repeat(64));
  await expect(commitSection).toContainText("Does not match the staged digest");
  await expect(commitSubmit).toBeDisabled();
  await commitInput.fill("");
  await commitInput.focus();
  await page.keyboard.type(digestCode);
  await expect(commitInput).toHaveValue(digestCode);
  await expect(commitSubmit).toBeEnabled({ timeout: 15000 });
  // Keyboard reaches the enabled Commit without clicking it: Tab from the
  // input lands on an enabled control (focusable), then we clear again so
  // this cheap theme test never commits.
  await commitSubmit.focus();
  await expect(commitSubmit).toBeFocused();
  await expect(commitSubmit).toBeEnabled();
  // Associated label + roles: input labelled, commit status/roles sane.
  await expect(
    page.getByText("Confirmation digest — type exactly to enable Commit"),
  ).toBeVisible();
  await expect(page.getByTestId("restores-status")).toHaveAttribute(
    "role",
    "status",
  );
  await commitInput.fill("");
  await expect(commitSubmit).toBeDisabled();

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

/* S56 commit coverage (seam T9, real app, no mocks).
 *
 * Choice: extend this file (not a new restores-commit.spec.ts) — the S55
 * helpers (createBackupZipViaApi, stageZipViaUi, loginAdmin) and the
 * single-worker digest-expiry contract are already here; a split would
 * duplicate them. All tests below use real S54 backup zips, real
 * validate/commit/reopen endpoints, and real auth — never page.route mocks.
 * Destructive commit swaps the dev database (disposable, never the owner's
 * live DB): each commit test stages a fresh backup of the current dev DB
 * first, so the switch is a near-no-op replacement, and the suite stays
 * single-worker (`--workers=1`) because digests expire each other by design.
 */

// S56 §1 stale digest (409): re-validate supersedes the first confirmation;
// the old digest fails with re-validate guidance, no mutation, and the UI
// keeps Commit disabled with expired guidance (no premature Commit).
test("S56 §1 stale digest fails 409 with re-validate guidance, no mutation", async ({
  page,
  request,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  await gotoRestoresViaNav(page);

  const first = await createBackupZipViaApi(request);
  await stageZipViaUi(page, `x-insight-backup-${first.backupId}.zip`, first.bytes);
  await expect(page.getByTestId("restores-status")).toContainText(
    /Archive staged and validated\. Live data unchanged/,
    { timeout: 120000 },
  );
  const digestBox = page.getByTestId("restores-digest");
  await expect(digestBox).toBeVisible({ timeout: 15000 });
  const digestA = (
    ((await digestBox.locator("code").first().textContent()) ?? "").trim()
  ).toLowerCase();
  expect(digestA).toMatch(/^[0-9a-f]{64}$/);
  const bodyA = (await page.textContent("body")) ?? "";
  const matchA = bodyA.match(/Restore id:\s*([0-9a-f-]{36})/i);
  expect(matchA, "first restore id rendered").not.toBeNull();
  const restoreA = matchA![1];

  // Live baseline before the stale attempt (real reads).
  const adminLogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(adminLogin.ok(), await adminLogin.text()).toBeTruthy();
  const { csrf_token: adminCsrf } = await adminLogin.json();
  const beforePatients = await request.get("/api/v1/patients");
  expect(beforePatients.status(), await beforePatients.text()).toBe(200);
  const beforeTotal = (await beforePatients.json())["total"] as number;

  // Re-validate a second real backup through the API: this successful
  // replacing upload expires the first confirmation server-side.
  const second = await createBackupZipViaApi(request);
  const relogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(relogin.ok(), await relogin.text()).toBeTruthy();
  const { csrf_token: csrf2 } = await relogin.json();
  const staged2 = await request.post("/api/v1/restores/validate", {
    headers: {
      "X-CSRF-Token": csrf2 as string,
      "Content-Type": "application/zip",
    },
    data: second.bytes,
  });
  expect(staged2.status(), await staged2.text()).toBe(201);
  const job2 = await staged2.json();
  expect(job2["id"]).not.toBe(restoreA);
  expect(job2["status"]).toBe("succeeded");

  // The first confirmation is now stale: the API reports expired, and a
  // commit with the old digest fails 409 with re-validate guidance and no
  // mutation (no maintenance entered).
  const reread = await request.get(`/api/v1/restores/${restoreA}`);
  expect(reread.status(), await reread.text()).toBe(200);
  expect((await reread.json())["expired"]).toBe(true);
  const stale = await request.post(`/api/v1/restores/${restoreA}/commit`, {
    headers: { "X-CSRF-Token": csrf2 as string },
    data: { confirmation_digest: digestA },
  });
  expect(stale.status(), await stale.text()).toBe(409);
  const staleBody = await stale.json();
  expect(staleBody["code"]).toMatch(/STALE|MISMATCH|NOT_READY|ALREADY/);
  expect(staleBody["message"]).toMatch(/re-validate|expired|match/i);

  // Live unchanged, no maintenance hold.
  const afterPatients = await request.get("/api/v1/patients");
  expect(afterPatients.status(), await afterPatients.text()).toBe(200);
  expect((await afterPatients.json())["total"]).toBe(beforeTotal);
  const ready = await request.get("/api/v1/ready");
  expect(ready.status(), await ready.text()).toBe(200);
  await expect(page.getByTestId("restores-maintenance-banner")).toHaveCount(
    0,
  );

  // UI on the stale job: Re-check surfaces the expiry, the commit section
  // explains the digest is superseded, and Commit stays disabled (no
  // premature Commit even with the exact old digest typed).
  await page.getByRole("button", { name: "Re-check status" }).click();
  await expect(page.getByTestId("restores-status")).toContainText(
    /expired or superseded/,
    { timeout: 30000 },
  );
  const commitSection = page.getByTestId("restores-commit-section");
  await expect(commitSection).toBeVisible();
  await expect(commitSection).toContainText(
    /expired or been superseded — re-validate/,
  );
  const commitInput = page.getByTestId("restores-commit-digest-input");
  await commitInput.fill(digestA);
  await expect(page.getByTestId("restores-commit-submit")).toBeDisabled();
  // Mismatched digests also fail 409 without mutation (same-shape, one
  // nibble off the staged value).
  const wrong = (digestA[0] === "0" ? "1" : "0") + digestA.slice(1);
  const mismatch = await request.post(`/api/v1/restores/${restoreA}/commit`, {
    headers: { "X-CSRF-Token": csrf2 as string },
    data: { confirmation_digest: wrong },
  });
  expect(mismatch.status(), await mismatch.text()).toBe(409);
});

// S56 §4 rollback/maintenance surfacing (T9 UI contract; failure injection
// itself is backend-only).
//
// No UI failure-injection control exists by design: `RESTORE_COMMIT_FAIL_AT`
// (`before_switch`/`during_restart`/`health_check`) is a public env seam for
// the disposable backend drill only (`BT/recovery/test_restore_commit.py`
// covers 500 + prior-DB-restored + maintenance-held + reopen-200 for all
// three points). This test therefore asserts the observable frontend
// contract that the held path depends on: the maintenance banner + reopen
// section appear only while `maintenance_active`, the recovery notes
// document the held→verify→reopen procedure, and reads stay available for
// verification. It stages (no commit) so maintenance stays clear.
test("S56 §4 maintenance and reopen surfacing with rollback docs", async ({
  page,
  request,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  await gotoRestoresViaNav(page);

  // Recovery procedure is always rendered: backup-before-switch,
  // quiesce/fencing, switch/restart, re-login + key re-entry,
  // verify-then-reopen with DEPLOYMENT_FENCED, and the rollback path.
  const recovery = page.getByTestId("restores-recovery-notes");
  await expect(recovery).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#restores-recovery-notes")).toBeVisible();
  await expect(recovery).toContainText("Operator recovery procedure");
  await expect(recovery).toContainText("Back up before switching");
  await expect(recovery).toContainText("pre-restore backup");
  await expect(recovery).toContainText("503");
  await expect(recovery).toContainText("fenced");
  await expect(recovery).toContainText("never a merge");
  await expect(recovery).toContainText("Sign in again and re-enter keys");
  await expect(recovery).toContainText("Verify, then reopen");
  await expect(recovery).toContainText("DEPLOYMENT_FENCED");
  await expect(recovery).toContainText("Rollback path");
  await expect(recovery).toContainText("maintenance stays held until verified healthy");

  // Clear deployment: no maintenance banner, no reopen section (they render
  // only while maintenance_active). Reads stay available for verification.
  await expect(
    page.getByTestId("restores-maintenance-banner"),
  ).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-section")).toHaveCount(0);
  const adminLogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(adminLogin.ok(), await adminLogin.text()).toBeTruthy();
  const ready = await request.get("/api/v1/ready");
  expect(ready.status(), await ready.text()).toBe(200);
  const patients = await request.get("/api/v1/patients");
  expect(patients.status(), await patients.text()).toBe(200);

  // Stage (no commit): still clear — no progress, no reopen, banner hidden.
  // The staged job itself reports maintenance_active:false + idle commit.
  const { bytes, backupId } = await createBackupZipViaApi(request);
  await stageZipViaUi(page, `x-insight-backup-${backupId}.zip`, bytes);
  await expect(page.getByTestId("restores-status")).toContainText(
    /Archive staged and validated\. Live data unchanged/,
    { timeout: 120000 },
  );
  await expect(
    page.getByTestId("restores-maintenance-banner"),
  ).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-section")).toHaveCount(0);
  await expect(page.getByTestId("restores-commit-progress")).toHaveCount(0);
  const body = (await page.textContent("body")) ?? "";
  const match = body.match(/Restore id:\s*([0-9a-f-]{36})/i);
  expect(match, "restore id rendered").not.toBeNull();
  const staged = await request.get(`/api/v1/restores/${match![1]}`);
  expect(staged.status(), await staged.text()).toBe(200);
  const job = await staged.json();
  expect(job["maintenance_active"]).toBe(false);
  expect(job["commit_status"]).toBe("idle");
  expect(job["pre_restore_backup_id"]).toBeNull();
});

// S56 §§1+3 commit happy path (destructive — disposable dev DB only, never
// the owner's live DB): admin stages a real S54 backup zip, types the exact
// digest, commits behind maintenance, and sees the success panel (pre-restore
// id, generation bump, session-revoked + DEPLOYMENT_FENCED + key-reentry,
// 11-phase history, maintenance cleared). Runs last: it revokes every
// session and swaps the dev database to the just-staged snapshot.
test("S56 §§1+3 admin commits exact digest with success panel and re-login", async ({
  page,
  request,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  await gotoRestoresViaNav(page);

  const { bytes, backupId } = await createBackupZipViaApi(request);
  await stageZipViaUi(page, `x-insight-backup-${backupId}.zip`, bytes);
  await expect(page.getByTestId("restores-status")).toContainText(
    /Archive staged and validated\. Live data unchanged/,
    { timeout: 120000 },
  );
  const digestBox = page.getByTestId("restores-digest");
  await expect(digestBox).toBeVisible({ timeout: 15000 });
  const digestCode = (
    ((await digestBox.locator("code").first().textContent()) ?? "").trim()
  ).toLowerCase();
  expect(digestCode).toMatch(/^[0-9a-f]{64}$/);
  const bodyBefore = (await page.textContent("body")) ?? "";
  const matchBefore = bodyBefore.match(/Restore id:\s*([0-9a-f-]{36})/i);
  expect(matchBefore, "restore id rendered").not.toBeNull();
  const restoreId = matchBefore![1];

  // Commit is gated until the exact digest is typed.
  const commitSection = page.getByTestId("restores-commit-section");
  await expect(commitSection).toBeVisible();
  const commitInput = page.getByTestId("restores-commit-digest-input");
  const commitSubmit = page.getByTestId("restores-commit-submit");
  await expect(commitSubmit).toBeDisabled();
  await commitInput.fill(digestCode);
  await expect(commitSubmit).toBeEnabled({ timeout: 15000 });

  // Exact-digest commit: synchronous phased replacement behind maintenance.
  // NOTE (S56 blocker, dev-DB only): the clean-DB backend drill
  // (`BT/recovery/test_restore_commit.py` 12/12) succeeds, but the shared
  // dev DB currently fails the switch with
  // `signed_encounter_snapshots.batch_id` FKViolation (insert order), so
  // this UI commit returns the rolled-back job (500 + maintenance held)
  // instead of the success panel. The test below asserts the specified
  // success contract and therefore fails on dev until the backend ordering
  // is fixed; the rollback UI it actually produces is covered by the S56 §4
  // test + backend failure-injection suite. On failure we reopen to leave
  // the dev deployment clear for the next run.
  await commitSubmit.click();
  // Wait for a terminal commit state (success OR rollback) — fail fast with
  // the actual panel instead of timing out on success text.
  const commitStatusEarly = page.getByTestId("restores-commit-status");
  await expect(commitStatusEarly).toBeVisible({ timeout: 180000 });
  const earlyText = (await commitStatusEarly.textContent()) ?? "";
  if (!/succeeded/i.test(earlyText)) {
    // Surface the rollback the dev DB actually produced, then clear the
    // held maintenance so later runs start clear (reopen verifies health).
    const prog = (await page.getByTestId("restores-commit-progress").textContent()) ?? "";
    const preHeld = prog.match(/Pre-restore backup:\s*([0-9a-f-]{36})/i);
    // eslint-disable-next-line no-console
    console.log(
      `S56 commit did not succeed on dev DB (status: ${earlyText.trim().slice(0, 200)}). ` +
        `Progress: ${prog.trim().slice(0, 500)}`,
    );
    try {
      const relog = await request.post("/api/v1/auth/login", {
        data: { username: "admin", password: "admin", role: "admin" },
      });
      if (relog.ok()) {
        const { csrf_token: rc } = await relog.json();
        await request.post(`/api/v1/restores/${restoreId}/reopen`, {
          headers: { "X-CSRF-Token": rc as string },
        });
      }
    } catch {
      // Best-effort cleanup only; the assertion below reports the blocker.
    }
    void preHeld;
  }
  // Terminal success contract: commit-status must read succeeded +
  // maintenance clear (fails on dev with the actual "failed/rolled_back"
  // panel until the backend insert-order blocker is fixed).
  await expect(commitStatusEarly).toContainText(/Commit succeeded/, {
    timeout: 15000,
  });
  // Terminal success status (self-contained: sessions are revoked, so the
  // next authenticated read naturally 401s — the panel itself proves it).
  await expect(page.getByTestId("restores-status")).toContainText(
    /Restore committed and verified.*Pre-restore backup.*All sessions revoked/,
    { timeout: 15000 },
  );
  await expect(page.locator("#restores-status")).toBeVisible();

  // Commit progress: succeeded, maintenance clear, pre-restore id + bump.
  const progress = page.getByTestId("restores-commit-progress");
  await expect(progress).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#restores-commit-progress")).toBeVisible();
  const commitStatus = page.getByTestId("restores-commit-status");
  await expect(commitStatus).toBeVisible();
  await expect(page.locator("#restores-commit-status")).toBeVisible();
  await expect(commitStatus).toHaveAttribute("role", "status");
  await expect(commitStatus).toContainText(/Commit succeeded/);
  await expect(commitStatus).toContainText(/maintenance clear/);
  await expect(progress).toContainText(/Pre-restore backup:\s*[0-9a-f-]{36}/i);
  await expect(progress).toContainText(/Generation:\s*\d+\s*→\s*\d+/);
  // Session-revoked + fencing + key-reentry notes in the success panel.
  await expect(progress).toContainText("All sessions revoked — sign in again");
  await expect(progress).toContainText("DEPLOYMENT_FENCED");
  await expect(progress).toContainText(/session\(s\) revoked/i);
  await expect(progress).toContainText(/Pending jobs reconciled:/);
  await expect(progress).toContainText(/Key re-entry:/);
  // Validation report still carries the staged backup identity + key note.
  const report = page.getByTestId("restores-report");
  await expect(report).toBeVisible();
  await expect(report).toContainText(backupId);
  await expect(report).toContainText(/re-entry/);
  // 11-phase history (maintenance → … → reopen), all ok.
  const history = page.getByTestId("restores-commit-history");
  await expect(history).toBeVisible();
  await expect(page.locator("#restores-commit-history")).toBeVisible();
  const items = history.locator("li");
  await expect(items).toHaveCount(11, { timeout: 15000 });
  for (const phase of [
    "maintenance",
    "quiesce",
    "fence",
    "pre_backup",
    "switch",
    "restart",
    "revoke",
    "reconcile",
    "audit",
    "verify",
    "reopen",
  ]) {
    await expect(history).toContainText(phase);
  }
  await expect(history).toContainText("ok");
  // Maintenance cleared: banner + reopen hidden after verified health.
  await expect(
    page.getByTestId("restores-maintenance-banner"),
  ).toHaveCount(0);
  await expect(page.getByTestId("restores-reopen-section")).toHaveCount(0);
  await expect(page.getByTestId("restores-recovery-notes")).toContainText(
    "DEPLOYMENT_FENCED",
  );

  // Same commit over the real API: pre-restore id, generation bump,
  // sessions revoked (old cookies 401), re-login works, reads + ready 200.
  const progressText = (await progress.textContent()) ?? "";
  const preMatch = progressText.match(
    /Pre-restore backup:\s*([0-9a-f-]{36})/i,
  );
  expect(preMatch, "pre-restore backup id rendered").not.toBeNull();
  const preId = preMatch![1];
  const genMatch = progressText.match(/Generation:\s*(\d+)\s*→\s*(\d+)/);
  expect(genMatch, "generation bump rendered").not.toBeNull();
  expect(Number(genMatch![2])).toBeGreaterThan(Number(genMatch![1]));

  const staleMe = await request.get("/api/v1/me");
  expect(staleMe.status(), "old session revoked after commit").toBe(401);
  const freshLogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(freshLogin.ok(), await freshLogin.text()).toBeTruthy();
  const pre = await request.get(`/api/v1/backups/${preId}`);
  expect(pre.status(), await pre.text()).toBe(200);
  expect((await pre.json())["id"]).toBe(preId);
  const me = await request.get("/api/v1/me");
  expect(me.status(), await me.text()).toBe(200);
  const patients = await request.get("/api/v1/patients");
  expect(patients.status(), await patients.text()).toBe(200);
  const ready = await request.get("/api/v1/ready");
  expect(ready.status(), await ready.text()).toBe(200);
  const fetched = await request.get(`/api/v1/restores/${restoreId}`);
  expect(fetched.status(), await fetched.text()).toBe(200);
  const committed = await fetched.json();
  expect(committed["commit_status"]).toBe("succeeded");
  expect(committed["maintenance_active"]).toBe(false);
  expect(committed["pre_restore_backup_id"]).toBe(preId);
  expect(committed["generation_after"]).toBeGreaterThan(
    committed["generation_before"],
  );
  expect(committed["commit_history"].length).toBe(11);
});
