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

/* S54 consistent full backups, admin backup UI (seam T9).
 *
 * Backend (read-only): `BT/recovery/test_backup.py` (T1/T10) owns
 * `POST /api/v1/backups` (admin-only mutation, CSRF + Idempotency-Key,
 * synchronous bounded build, 201 with the terminal job), `GET
 * /api/v1/backups/{id}` (admin-only progress/manifest read, private,
 * no-store), and `GET /api/v1/backups/{id}/download` (admin-only bounded
 * zip, private, no-store; failed jobs 409, purged files 404). No
 * backend/src changes here.
 *
 * Frontend (read-only): `web/src/features/admin/backups/api.ts` +
 * `BackupsPage.tsx` mounted via `#/backups` + `BackupsRoutePage` guard in
 * app/pages.tsx (complement to the server 401/403, never a replacement) +
 * admin `Backups` nav entry in app/App.tsx. No web/src changes here.
 *
 * Constraints: real endpoints only — NO page.route mocks anywhere in this
 * spec. Every status/manifest/download comes from the live backend over
 * the shared dev database with real admin/physician auth flows.
 *
 * Sync-terminal adaptation (tasks.md S54 §4): the backend builds the
 * archive inside POST and returns a terminal job (`succeeded`/`failed`),
 * so there is no observable pending/running window to poll through. The
 * retry test therefore asserts the adapted observable contract: every
 * create terminates (no stuck "In progress"), a second create mints a
 * fresh archive id (never overwrites the prior backup), and a succeeded
 * backup always exposes its own download. The failed-job 409 / retry-new
 * shape stays owned by the T1 suite (`test_backup_retry_fresh_key_…
 * preserves_prior…`), which can force failures the UI cannot.
 *
 * Selector contract: backups-heading, backups-create, backups-status
 * (role=status; errors share the testid with role=alert), backups-download,
 * backups-manifest — each with a matching DOM id (#backups-*).
 *
 * Run with --workers=1 when the shared dev DB is under parallel pressure:
 * npx playwright test e2e/backups.spec.ts --project=chromium --project=firefox
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

async function gotoBackupsViaNav(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.getByRole("link", { name: "Backups" }).click();
  await expect(page.getByTestId("backups-heading")).toBeVisible({
    timeout: 15000,
  });
  // Stable-hook contract: every hook carries both data-testid and id.
  for (const id of ["backups-heading", "backups-create"]) {
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
}

async function createBackupAndWaitReady(
  page: import("@playwright/test").Page,
): Promise<string> {
  await page.getByTestId("backups-create").click();
  // Synchronous bounded build: POST returns the terminal job, so the page
  // lands on "Backup ready" without an observable polling window.
  await expect(page.getByRole("status")).toContainText(
    /Backup ready \(\d+ bytes\)\./,
    { timeout: 120000 },
  );
  const body = (await page.textContent("body")) ?? "";
  const match = body.match(/Backup id:\s*([0-9a-f-]{36})/i);
  expect(match, "backup id rendered after create").not.toBeNull();
  return match![1];
}

// S54 §§1+3: admin login → Backups nav → create → ready status → manifest
// with inventory + key-reentry note → real zip download (+ API header check).
test("S54 §§1+3 admin creates a ready backup with inventory, key note, zip download", async ({
  page,
  request,
  playwright,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  await expect(page.getByRole("link", { name: "Backups" })).toBeVisible();
  await gotoBackupsViaNav(page);

  const backupId = await createBackupAndWaitReady(page);

  // Manifest: inventory table (headers + known owned tables with
  // counts/hashes), artifact hashes, key-reentry note, exclusions, retention.
  const manifest = page.getByTestId("backups-manifest");
  await expect(manifest).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#backups-manifest")).toBeVisible();
  await expect(
    manifest.getByRole("columnheader", { name: "Table" }),
  ).toBeVisible();
  await expect(
    manifest.getByRole("columnheader", { name: "Rows" }),
  ).toBeVisible();
  await expect(
    manifest.getByRole("columnheader", { name: "SHA-256" }),
  ).toBeVisible();
  for (const table of [
    "users",
    "patients",
    "encounters",
    "audit_events",
    "backup_jobs",
  ]) {
    await expect(manifest.getByText(table, { exact: true }).first()).toBeVisible();
  }
  await expect(manifest).toContainText("Key re-entry note");
  await expect(manifest).toContainText(/re-entry/);
  await expect(manifest).toContainText("sessions are excluded");
  await expect(manifest).toContainText(".part");
  await expect(manifest).toContainText("Manifest");
  await expect(page.getByText("Status:").first()).toBeVisible();
  await expect(page.getByText("succeeded").first()).toBeVisible();

  // Real zip download through the UI blob path (no mocks, no href stub).
  const download = page.waitForEvent("download");
  await expect(page.getByTestId("backups-download")).toBeVisible();
  await expect(page.locator("#backups-download")).toBeVisible();
  await page.getByTestId("backups-download").click();
  const attachment = await download;
  expect(attachment.suggestedFilename()).toMatch(
    /^x-insight-backup-[0-9a-f-]{36}\.zip$/,
  );
  expect(attachment.suggestedFilename()).toContain(backupId);
  await expect(page.getByRole("status")).toContainText(
    new RegExp(
      `Downloaded x-insight-backup-${backupId}\\.zip \\(\\d+ bytes\\)\\.`,
    ),
    { timeout: 30000 },
  );

  // Same archive over the real API: manifest shape + zip headers.
  const adminLogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(adminLogin.ok()).toBeTruthy();
  const fetched = await request.get(`/api/v1/backups/${backupId}`);
  expect(fetched.status(), await fetched.text()).toBe(200);
  expect(
    fetched.headers()["cache-control"] ?? "",
    "manifest read is private, no-store",
  ).toContain("no-store");
  const job = await fetched.json();
  expect(job["id"]).toBe(backupId);
  expect(job["status"]).toBe("succeeded");
  expect(job["manifest"]["schema_version"]).toBe("backup-v1");
  expect(job["manifest"]["backup_id"]).toBe(backupId);
  expect(Object.keys(job["manifest"]["inventory"]).length).toBeGreaterThan(10);
  expect(job["manifest"]["inventory"]["users"]["rows"]).toBeGreaterThanOrEqual(1);
  expect(job["manifest"]["key_reentry_note"]).toMatch(/re-entry/);

  const zipped = await request.get(`/api/v1/backups/${backupId}/download`);
  expect(zipped.status(), await zipped.text()).toBe(200);
  expect(zipped.headers()["content-type"] ?? "").toContain("application/zip");
  expect(zipped.headers()["content-disposition"] ?? "").toContain(backupId);
  expect(zipped.headers()["cache-control"] ?? "").toContain("no-store");
  const bytes = await zipped.body();
  expect(bytes.length).toBeGreaterThan(1000);

  // Anonymous complement: no session downloads nothing (backend T1 owns the
  // 401 body; here the observable browser-facing denial is enough).
  const anon = await playwright.request.newContext({ baseURL: API_BASE });
  try {
    const denied = await anon.get(`/api/v1/backups/${backupId}/download`);
    expect(denied.status()).toBe(401);
  } finally {
    await anon.dispose();
  }
});

// S54 §4 (sync-terminal adaptation): a second create mints a fresh archive
// id, polling terminates (no stuck progress), and the new good backup keeps
// its own download — retries never overwrite a prior archive.
test("S54 §4 second create mints a fresh archive and polling terminates ready", async ({
  page,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  await gotoBackupsViaNav(page);

  const firstId = await createBackupAndWaitReady(page);
  const createButton = page.getByTestId("backups-create");
  // Terminal state: the button is enabled and offers a new backup, never a
  // stuck "In progress…" poll label.
  await expect(createButton).toBeEnabled();
  await expect(createButton).toContainText("Create new backup");
  await expect(page.getByText("In progress")).toHaveCount(0);

  await createButton.click();
  await expect(page.getByRole("status")).toContainText(
    /Backup ready \(\d+ bytes\)\./,
    { timeout: 120000 },
  );
  const body = (await page.textContent("body")) ?? "";
  const match = body.match(/Backup id:\s*([0-9a-f-]{36})/i);
  expect(match, "second backup id rendered").not.toBeNull();
  const secondId = match![1];
  // Fresh UUID/path per attempt: the retry never overwrote the prior backup.
  expect(secondId).not.toBe(firstId);
  await expect(createButton).toBeEnabled();
  await expect(createButton).toContainText("Create new backup");
  await expect(page.getByText("In progress")).toHaveCount(0);
  // The new good backup keeps its own download.
  const download = page.waitForEvent("download");
  await page.getByTestId("backups-download").click();
  const attachment = await download;
  expect(attachment.suggestedFilename()).toContain(secondId);
  await expect(page.getByRole("status")).toContainText(
    new RegExp(`Downloaded x-insight-backup-${secondId}\\.zip`),
    { timeout: 30000 },
  );
});

// S54 §3 (403 complement): physician has no Backups nav, the direct route
// renders the guard (never actions), and the real API denies every backup
// route — including reads of an existing archive id.
test("S54 §3 physician sees no backups nav, route guards, API denies", async ({
  page,
  request,
  playwright,
}) => {
  test.setTimeout(180000);
  const physician = await loginPhysician(page, request, "e2es54phys");
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible({ timeout: 15000 });
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Backups" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();

  await page.goto("/#/backups");
  await expect(
    page.getByRole("heading", { name: "Full backups" }),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByText("Administrator access required."),
  ).toBeVisible();
  // Guard renders no backup actions or content.
  await expect(page.getByTestId("backups-heading")).toHaveCount(0);
  await expect(page.getByTestId("backups-create")).toHaveCount(0);
  await expect(page.getByTestId("backups-download")).toHaveCount(0);
  await expect(page.getByTestId("backups-manifest")).toHaveCount(0);

  // Real API denials with the physician's own session (CSRF + idempotency
  // on the mutation, plain read on the others — all 403, never the data).
  const csrf = await physicianSession(request, physician);
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const create = await request.post("/api/v1/backups", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": key },
  });
  expect(create.status(), await create.text()).toBe(403);
  const read = await request.get(`/api/v1/backups/${randomUUID()}`);
  expect(read.status(), await read.text()).toBe(403);
  const zipped = await request.get(`/api/v1/backups/${randomUUID()}/download`);
  expect(zipped.status(), await zipped.text()).toBe(403);

  // Anonymous mutation is 401 (complements the T1 gate suite).
  const anon = await playwright.request.newContext({ baseURL: API_BASE });
  try {
    const denied = await anon.post("/api/v1/backups");
    expect(denied.status()).toBe(401);
  } finally {
    await anon.dispose();
  }

  await signOut(page);
});

// S54 (cheap): dark theme renders the full backup flow; keyboard creates and
// downloads; print keeps the manifest readable while chrome hides; status
// uses role=status; both themes end clean.
test("S54 backups work in dark theme via keyboard with print-safe manifest", async ({
  page,
}) => {
  test.setTimeout(300000);
  await loginAdmin(page);
  const themeOf = (): Promise<string | null> =>
    page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  if ((await themeOf()) !== "dark") {
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
  }
  await expect.poll(themeOf).toBe("dark");

  await gotoBackupsViaNav(page);
  // Keyboard-only create: focus + Enter drives the real mutation.
  const createButton = page.getByTestId("backups-create");
  await createButton.focus();
  await expect(createButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText(
    /Backup ready \(\d+ bytes\)\./,
    { timeout: 120000 },
  );
  const manifest = page.getByTestId("backups-manifest");
  await expect(manifest).toBeVisible({ timeout: 15000 });
  await expect(manifest).toContainText(/re-entry/);
  await expect(
    manifest.getByRole("columnheader", { name: "Table" }),
  ).toBeVisible();

  // Keyboard-only download: Tab reaches the download action, Enter fetches
  // the real zip.
  const downloadButton = page.getByTestId("backups-download");
  await downloadButton.focus();
  await expect(downloadButton).toBeFocused();
  const download = page.waitForEvent("download");
  await page.keyboard.press("Enter");
  const attachment = await download;
  expect(attachment.suggestedFilename()).toMatch(
    /^x-insight-backup-[0-9a-f-]{36}\.zip$/,
  );
  await expect(page.getByRole("status")).toContainText(
    /Downloaded x-insight-backup-.*\.zip \(\d+ bytes\)\./,
    { timeout: 30000 },
  );

  // Print: heading + manifest stay readable; nav and action buttons hide
  // (theme.css @media print keeps .xi-card, hides .xi-no-print/buttons).
  await page.emulateMedia({ media: "print" });
  await expect(page.getByTestId("backups-heading")).toBeVisible();
  await expect(page.getByTestId("backups-manifest")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Primary" }),
  ).toBeHidden();
  await expect(page.getByTestId("backups-create")).toBeHidden();
  await page.emulateMedia({ media: "screen" });

  // Light theme renders the same content (both-themes evidence).
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect.poll(themeOf).toBe("light");
  await expect(page.getByTestId("backups-heading")).toBeVisible();
  await expect(page.getByTestId("backups-manifest")).toBeVisible();
  await expect(page.getByTestId("backups-download")).toBeVisible();
});
