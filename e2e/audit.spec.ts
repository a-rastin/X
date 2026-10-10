import { test, expect } from "@playwright/test";
import {
  uniqueName,
  uniqueLetters,
  uniquePatientId,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
} from "./helpers";

/* S52 append-only audit administration views (seams T1/T9).
 *
 * Backend (read-only): `BT/http/test_audit.py` (10 T1 tests) owns
 * admin-only `GET /audit-events` (actor/operation/since/until/patient/
 * encounter/question-run/batch/question-key filters, stable
 * (occurred_at, id) order, default 25 max 100), 401/403, 405 on writes,
 * stable attribution, required-event details, and append-only grants.
 * No backend/src changes here.
 *
 * Frontend (read-only): `web/src/features/admin/audit/api.ts` +
 * `AuditPage.tsx` (testids audit-heading/filters/table/detail,
 * audit-prev/next, filter ids audit-actor/operation/since/until/
 * patientId/encounterId/questionRunId/batchId/questionKey) mounted via
 * `#/audit` + `AuditRoutePage` guard (complement to server 403).
 *
 * Constraints: real endpoints only, no route mocks, no worker in e2e —
 * every row/detail/filter/page comes from `GET /audit-events` in the
 * shared dev DB. Fresh physician/patient creates via T1 give deterministic
 * target-filter proof without depending on exact dev-DB totals. No DB-row
 * asserts, no internal mocks; expected literals are worked fixtures
 * (operation names, empty-state/guard copy, pager shape).
 *
 * Selector contract: audit-heading, audit-filters, audit-table,
 * audit-detail (+ audit-detail-heading focus), audit-prev/audit-next,
 * filter labels Actor / Action (operation) / Patient id.
 *
 * Run with --workers=1: parallel workers contend on the shared dev DB
 * (physician/patient slots) and on growing audit totals.
 */

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

async function createPatientViaApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
): Promise<{ patientId: string; identifier: string }> {
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const payload = {
    identifier: uniquePatientId(),
    given_name: uniqueLetters("given"),
    family_name: uniqueLetters("family"),
    sex: "F",
    age: 30,
    clinical_status: "first_time",
  };
  const post = (csrfToken: string) =>
    request.post("/api/v1/patients", {
      headers: { "X-CSRF-Token": csrfToken, "Idempotency-Key": key },
      data: payload,
    });
  let create = await post(await physicianSession(request, physician));
  if (create.status() === 401) {
    create = await post(await physicianSession(request, physician));
  }
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return {
    patientId: body.patient.id as string,
    identifier: payload.identifier,
  };
}

async function gotoAuditViaNav(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.getByRole("link", { name: "Audit" }).click();
  await expect(page.getByTestId("audit-heading")).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByTestId("audit-table")).toBeVisible({
    timeout: 15000,
  });
}

// S52 §4: admin login → Audit nav → table with stable pager status.
test("S52 §4 admin audit path loads table with stable pager status", async ({
  page,
}) => {
  await loginAdmin(page);
  await expect(page.getByRole("link", { name: "Audit" })).toBeVisible();
  await gotoAuditViaNav(page);
  await expect(page.getByTestId("audit-filters")).toBeVisible();
  const table = page.getByTestId("audit-table");
  const rows = table.getByRole("row");
  // Header + at least one real event row from the shared dev trail.
  expect(await rows.count()).toBeGreaterThan(1);
  // Stable server-order pager copy (en-dash, exact wording from AuditPage).
  await expect(page.getByRole("status")).toContainText(
    /Showing \d+–\d+ of \d+ \(stable time order\)\./,
  );
  // Time column renders stored UTC (`Z`); actor/action/target columns exist.
  await expect(table.getByRole("columnheader", { name: "Time (UTC)" })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Actor" })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Action" })).toBeVisible();
  const firstTime = await table
    .getByRole("row")
    .nth(1)
    .getByRole("cell")
    .first()
    .textContent();
  expect(firstTime ?? "").toMatch(/Z/);
  // Pager buttons exist with honest disabled states on page one.
  await expect(page.getByTestId("audit-prev")).toBeDisabled();
  await expect(page.getByTestId("audit-prev")).toBeVisible();
  await expect(page.getByTestId("audit-next")).toBeVisible();
});

// S52 §4: detail panel shows UTC/local/correlation/bounded metadata + focus.
test("S52 §4 audit detail shows correlation refs, timezone, and focus", async ({
  page,
}) => {
  await loginAdmin(page);
  await gotoAuditViaNav(page);
  const table = page.getByTestId("audit-table");
  // Open the first real event row.
  await table.getByRole("button", { name: /View detail for/ }).first().click();
  const detail = page.getByTestId("audit-detail");
  await expect(detail).toBeVisible({ timeout: 10000 });
  await expect(detail).toContainText("Event id");
  await expect(detail).toContainText("Time (UTC, as stored)");
  await expect(detail).toContainText("Time (your timezone)");
  await expect(detail).toContainText("Actor");
  await expect(detail).toContainText("Action");
  await expect(detail).toContainText("Correlation references");
  await expect(detail).toContainText("Metadata (bounded)");
  await expect(detail).toContainText("Payload hash");
  // Focus moves to the detail heading for keyboard/screen-reader users.
  await expect(page.locator("#audit-detail-heading")).toBeFocused();
  // Toggle hides the panel again (aria-label is stable; only text flips).
  await table.getByRole("button", { name: /View detail for/ }).first().click();
  await expect(page.getByTestId("audit-detail")).toHaveCount(0);
});

// S52 §4: operation filter narrows, empty state, Clear restores.
test("S52 §4 audit operation filter narrows, empty state, clear restores", async ({
  page,
}) => {
  await loginAdmin(page);
  await gotoAuditViaNav(page);
  const table = page.getByTestId("audit-table");
  // Narrow to a real operation every login produces.
  await page.getByLabel("Action (operation)").fill("auth.login.success");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(table).toBeVisible({ timeout: 15000 });
  await expect(table).toContainText("auth.login.success", { timeout: 15000 });
  await expect(page.getByRole("status")).toContainText(/Showing \d+–\d+ of \d+/);
  // Impossible operation → honest empty state, no rows, no pager.
  await page.getByLabel("Action (operation)").fill("no.such.operation.s52xyz");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(
    page.getByText("No audit events found for these filters."),
  ).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("audit-table")).toHaveCount(0);
  // `datetime-local` → UTC `Z` round-trip through the real endpoint: a past
  // Since keeps rows, a future Since is honestly empty (same empty state).
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByTestId("audit-table")).toBeVisible({
    timeout: 15000,
  });
  await page.getByLabel("Since (your timezone)").fill("2000-01-01T00:00");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.getByTestId("audit-table")).toBeVisible({
    timeout: 15000,
  });
  await page.getByLabel("Since (your timezone)").fill("2999-01-01T00:00");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(
    page.getByText("No audit events found for these filters."),
  ).toBeVisible({ timeout: 15000 });
  // Clear restores the full trail.
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByTestId("audit-table")).toBeVisible({
    timeout: 15000,
  });
  expect(
    await page.getByTestId("audit-table").getByRole("row").count(),
  ).toBeGreaterThan(1);
});

// S52 §§1-2 (browser face): fresh real mutation is visible via target filter.
test("S52 §§1-2 fresh patient create appears under patient target filter", async ({
  page,
  request,
}) => {
  const physician = {
    username: uniqueName("e2es52audit"),
    password: "secret123",
  };
  await ensurePhysician(request, physician.username, physician.password);
  const { patientId } = await createPatientViaApi(request, physician);
  await loginAdmin(page);
  await gotoAuditViaNav(page);
  // Filter by the fresh patient id (real T1 mutation → real audit event).
  await page.getByLabel("Patient id").fill(patientId);
  await page.getByRole("button", { name: "Apply filters" }).click();
  const table = page.getByTestId("audit-table");
  await expect(table).toBeVisible({ timeout: 15000 });
  await expect(table).toContainText("patients.create.success", {
    timeout: 15000,
  });
  // Detail carries the full correlation id (target cell only shows a prefix).
  await table.getByRole("button", { name: /View detail for/ }).first().click();
  const detail = page.getByTestId("audit-detail");
  await expect(detail).toBeVisible({ timeout: 10000 });
  await expect(detail).toContainText(patientId);
  await expect(detail).toContainText("patients.create.success");
});

// S52 §3 (browser face): stable pagination walks pages without overlap.
test("S52 §3 audit pagination walks stable pages", async ({ page }) => {
  await loginAdmin(page);
  await gotoAuditViaNav(page);
  const status = page.getByRole("status");
  await expect(status).toContainText(/Showing \d+–\d+ of \d+/);
  const firstStatus = (await status.textContent()) ?? "";
  const total = Number(firstStatus.replace(/^.*of (\d+).*$/, "$1"));
  expect(Number.isFinite(total)).toBe(true);
  if (total <= 25) {
    // Single-page trail: both directions honestly disabled.
    await expect(page.getByTestId("audit-prev")).toBeDisabled();
    await expect(page.getByTestId("audit-next")).toBeDisabled();
    return;
  }
  expect(firstStatus).toMatch(/Showing 1–/);
  const firstRowTime =
    (await page
      .getByTestId("audit-table")
      .getByRole("row")
      .nth(1)
      .getByRole("cell")
      .first()
      .textContent()) ?? "";
  await page.getByTestId("audit-next").click();
  await expect(status).toContainText(/Showing 26–/, { timeout: 15000 });
  await expect(page.getByTestId("audit-prev")).toBeEnabled();
  const secondRowTime =
    (await page
      .getByTestId("audit-table")
      .getByRole("row")
      .nth(1)
      .getByRole("cell")
      .first()
      .textContent()) ?? "";
  // Stable order: page two starts where page one left off (different head).
  expect(secondRowTime).not.toBe(firstRowTime);
  await page.getByTestId("audit-prev").click();
  await expect(status).toContainText(/Showing 1–/, { timeout: 15000 });
  await expect(page.getByTestId("audit-prev")).toBeDisabled();
});

// S52 §3: physician has no Audit nav and the direct route is guarded.
test("S52 §3 physician sees no audit nav and direct route is guarded", async ({
  page,
  request,
}) => {
  await loginPhysician(page, request, "e2es52phys");
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible({ timeout: 15000 });
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Audit" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();
  // Direct navigation still renders the guard, never the trail.
  await page.goto("/#/audit");
  await expect(
    page.getByRole("heading", { name: "Audit trail" }),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByText("Administrator access required."),
  ).toBeVisible();
  await expect(page.getByTestId("audit-table")).toHaveCount(0);
  await expect(page.getByTestId("audit-filters")).toHaveCount(0);
});

// S52 §4 (cheap): dark theme renders the trail; keyboard drives filter+detail.
test("S52 §4 audit works in dark theme and via keyboard", async ({ page }) => {
  await loginAdmin(page);
  await gotoAuditViaNav(page);
  const themeOf = (): Promise<string | null> =>
    page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  // Admin is a shared singleton: starting theme persists across specs, so
  // drive to dark explicitly instead of assuming light.
  if ((await themeOf()) !== "dark") {
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
  }
  await expect.poll(themeOf).toBe("dark");
  await expect(page.getByTestId("audit-table")).toBeVisible();
  // Keyboard-only filter: Enter in the input submits the form (no click).
  await page.getByLabel("Action (operation)").fill("auth.login.success");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("audit-table")).toContainText(
    "auth.login.success",
    { timeout: 15000 },
  );
  // Keyboard-only detail: Tab to the first View button, Enter opens it.
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByTestId("audit-table")).toBeVisible({
    timeout: 15000,
  });
  const view = page
    .getByTestId("audit-table")
    .getByRole("button", { name: /View detail for/ })
    .first();
  await view.focus();
  await expect(view).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("audit-detail")).toBeVisible({
    timeout: 10000,
  });
  await expect(page.locator("#audit-detail-heading")).toBeFocused();
  // Restore light for a stable starting point for other specs.
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect.poll(themeOf).toBe("light");
});
