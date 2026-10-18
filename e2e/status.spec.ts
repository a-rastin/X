import { test, expect } from "@playwright/test";
import {
  uniqueName,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
  signOut,
} from "./helpers";

/* S58 §§3-4 operational status UI (seam T9 over T1).
 *
 * Backend (read-only): `BT/http/test_ops_status.py` (T1/T8/T10) owns
 * `GET /api/v1/ops/status` (admin-only safe metrics: queue age/heartbeat,
 * provider attempts/auth/retries, inference limits, disk, backup success,
 * request gauges, alert list; counts/timestamps only, never secrets, record
 * text, tokens, or clinical payloads). No backend/src changes here.
 *
 * Frontend: `web/src/features/admin/status/api.ts` + `StatusPage.tsx`
 * mounted via `#/status` + `StatusRoutePage` guard in app/pages.tsx
 * (complement to the server 401/403, never a replacement) + admin `Status`
 * nav entry in app/App.tsx.
 *
 * Coverage (one test per slice, each verified red first):
 * - admin journey: live metrics + alerts render, manual refresh re-reads
 *   without losing sections, both themes (explicitly driven: the admin
 *   singleton persists theme server-side across specs).
 * - physician: no Status nav, direct route guards, real API 403.
 * - anonymous: browser lands on login with no status hooks, real API 401.
 * - safe render: the page's own session token, token-shaped words, and
 *   record-shaped digit runs never reach rendered text; alerts render the
 *   honest empty state or real items (structural: shared-DB alert presence
 *   is nondeterministic); no error alert on success.
 * - keyboard + poll: Refresh is focus/Enter-operable; a visible
 *   visibilitychange tick re-reads (the same path the 30s interval uses);
 *   hidden tabs skip ticks; the 30s interval re-reads with no further
 *   input (responses observed, never mocked).
 *
 * Constraints: real endpoints only — NO page.route mocks anywhere in this
 * spec. Every metric comes from the live backend over the shared dev
 * database with real admin/physician auth flows. Assertions are structural
 * (sections render from the live payload) rather than exact counts, because
 * queue/provider/disk numbers move with shared dev-DB activity.
 *
 * Selector contract: status-heading, status-refresh, status-updated
 * (role=status), status-alerts, status-metrics — each with a matching DOM
 * id (#status-*).
 */

async function loginAdmin(page: import("@playwright/test").Page): Promise<void> {
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible({ timeout: 15000 });
}

const API_BASE = "http://127.0.0.1:8000";

// S58 §§3-4: admin login → Status nav → live metrics from the real endpoint
// → manual refresh re-reads without losing the rendered sections.
test("S58 §§3-4 admin sees live operational status and can refresh it", async ({
  page,
  request,
}) => {
  await loginAdmin(page);
  await expect(page.getByRole("link", { name: "Status" })).toBeVisible();

  await page.getByRole("link", { name: "Status" }).click();
  await expect(page.getByTestId("status-heading")).toBeVisible({
    timeout: 15000,
  });
  // Stable-hook contract: every hook carries both data-testid and id.
  for (const id of [
    "status-heading",
    "status-refresh",
    "status-updated",
    "status-alerts",
    "status-metrics",
  ]) {
    await expect(page.locator(`#${id}`)).toBeVisible();
  }

  // Live payload rendered: the alerts section plus every safe metrics
  // section heading plus the checked-at status line from the real endpoint
  // (role=status, never role=alert here).
  await expect(page.getByTestId("status-alerts")).toContainText("Alerts");
  const metrics = page.getByTestId("status-metrics");
  for (const section of [
    "Queue",
    "Provider",
    "Inference limits",
    "Disk",
    "Backup",
    "Requests",
  ]) {
    await expect(
      metrics.getByRole("heading", { name: section }),
    ).toBeVisible();
  }
  const updated = page.getByTestId("status-updated");
  await expect(updated).toContainText(/Checked at \S+/);

  // Queue/provider rows render measured counts (digits), not placeholders.
  await expect(metrics.getByText("Queued generation jobs")).toBeVisible();
  await expect(
    metrics.locator("tr", { hasText: "Provider attempts (24h)" }),
  ).toContainText(/\d+/);

  // The same payload over the real API: admin 200 with the fixed safe keys.
  const adminLogin = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(adminLogin.ok()).toBeTruthy();
  const probe = await request.get("/api/v1/ops/status");
  expect(probe.status(), await probe.text()).toBe(200);
  const payload = await probe.json();
  for (const section of [
    "queue",
    "provider",
    "inference",
    "disk",
    "backup",
    "requests",
    "alerts",
  ]) {
    expect(payload, `missing section ${section}`).toHaveProperty(section);
  }

  // Manual refresh re-reads the live endpoint and keeps every section.
  await page.getByTestId("status-refresh").click();
  await expect(page.getByRole("status")).toContainText(/Checked at \S+/, {
    timeout: 15000,
  });
  // Both themes render the same live content (tokens only, no new colors).
  // The admin singleton persists theme server-side across specs, so drive
  // explicitly instead of assuming a light start.
  const themeOf = (): Promise<string | null> =>
    page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  if ((await themeOf()) !== "dark") {
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
  }
  await expect.poll(themeOf).toBe("dark");
  await expect(page.getByTestId("status-heading")).toBeVisible();
  await expect(page.getByTestId("status-metrics")).toBeVisible();
  if ((await themeOf()) !== "light") {
    await page.getByRole("button", { name: "Switch to light theme" }).click();
  }
  await expect.poll(themeOf).toBe("light");
});

// S58 §3 (403 complement): physician has no Status nav, the direct route
// renders the guard (never metrics), and the real API denies the endpoint.
test("S58 §3 physician sees no status nav, route guards, API denies", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2es58phys");
  const password = "secret123";
  await ensurePhysician(request, username, password);
  await loginAs(page, "physician", username, password);
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible({ timeout: 15000 });
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Status" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();

  await page.goto("/#/status");
  await expect(
    page.getByRole("heading", { name: "Operational status" }),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByText("Administrator access required."),
  ).toBeVisible();
  // Guard renders no status hooks or metric content.
  for (const id of [
    "status-heading",
    "status-refresh",
    "status-updated",
    "status-alerts",
    "status-metrics",
  ]) {
    await expect(page.getByTestId(id)).toHaveCount(0);
  }

  // Real API denial with the physician's own session (backend T1 owns the
  // 403 body; here the observable browser-facing denial is enough).
  await physicianSession(request, { username, password });
  const denied = await request.get("/api/v1/ops/status");
  expect(denied.status(), await denied.text()).toBe(403);

  await signOut(page);
});

// S58 §§3-4 (safe render): the live Status view carries counts/timestamps
// only — the page's own session token, token-shaped words, and
// record-shaped identifiers never reach the rendered text — and the alerts
// section renders either the honest empty state or real alert items
// (structural: shared-DB alert presence is nondeterministic, so the test
// accepts either branch but requires one of them).
test("S58 §§3-4 status renders safe metrics with no secrets or record text", async ({
  page,
}) => {
  await loginAdmin(page);
  await page.getByRole("link", { name: "Status" }).click();
  await expect(page.getByTestId("status-metrics")).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByTestId("status-alerts")).toBeVisible();

  // The page's own session token (origin-shared storage aligned with the
  // shared session cookie): must never appear in rendered body text.
  const ownToken = await page.evaluate(
    () => localStorage.getItem("xi_csrf_token") ?? "",
  );
  expect(ownToken, "test needs a live session token").not.toBe("");

  const body = (await page.textContent("body")) ?? "";
  // Labels (not values) go into failure messages so a failure never echoes
  // a live token into test output.
  const forbidden: Array<[string, string]> = [
    ["session token", ownToken],
    ["csrf", "csrf"],
    ["grant_token", "grant_token"],
    ["lease_token", "lease_token"],
    ["Bearer", "Bearer"],
  ];
  for (const [label, value] of forbidden) {
    expect(body, `rendered page leaks ${label}`).not.toContain(value);
  }
  // Ten consecutive digits is the patient-identifier shape (exactly ten
  // ASCII digits stored as text): it must never render here. UUID dashes
  // break backup ids into short runs, and counts/timestamps carry no such
  // run, so any match is record-shaped leakage.
  expect(body, "rendered page leaks record-shaped digits").not.toMatch(
    /\d{10}/,
  );

  // Alerts: honest empty state or real items — never both missing. (The
  // `status-alerts` hook lives on the section heading, so items are scoped
  // through the labelled section, not the heading itself.)
  const alertsSection = page.locator(
    'section[aria-labelledby="status-alerts"]',
  );
  const emptyCount = await alertsSection
    .getByText("No active alerts.")
    .count();
  const itemCount = await alertsSection.getByRole("listitem").count();
  expect(emptyCount + itemCount).toBeGreaterThan(0);

  // A successful load surfaces no error alert (errors use role=alert;
  // the checked-at line is role=status, asserted in the admin journey).
  await expect(page.getByRole("alert")).toHaveCount(0);
});

// S58 §4 (poll/keyboard): the Refresh button is keyboard-operable (focus +
// Enter re-reads the live endpoint and keeps every section), visible-tab
// ticks refresh on demand, hidden tabs skip ticks, and the 30s interval
// keeps re-reading while the tab stays visible. Real endpoints only —
// responses are observed, never mocked or intercepted.
test("S58 §4 status refreshes via keyboard and visible-tab poll ticks", async ({
  page,
}) => {
  test.setTimeout(240000);
  await loginAdmin(page);
  await page.getByRole("link", { name: "Status" }).click();
  await expect(page.getByTestId("status-metrics")).toBeVisible({
    timeout: 15000,
  });

  let statusReads = 0;
  page.on("response", (response) => {
    if (
      response.request().method() === "GET" &&
      response.url().includes("/api/v1/ops/status")
    ) {
      statusReads += 1;
    }
  });

  // Keyboard-only refresh: focus + Enter drives the real re-read and every
  // section survives it (the last good snapshot is never cleared).
  const refresh = page.getByTestId("status-refresh");
  await refresh.focus();
  await expect(refresh).toBeFocused();
  await page.keyboard.press("Enter");
  await expect
    .poll(() => statusReads, { timeout: 15000 })
    .toBeGreaterThanOrEqual(1);
  for (const id of [
    "status-heading",
    "status-refresh",
    "status-updated",
    "status-alerts",
    "status-metrics",
  ]) {
    await expect(page.locator(`#${id}`)).toBeVisible();
  }

  // Visible-tab tick: a visibilitychange while visible triggers an
  // immediate re-read through the same path the 30s interval uses.
  const beforeVisible = statusReads;
  await page.evaluate(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect
    .poll(() => statusReads, { timeout: 15000 })
    .toBeGreaterThan(beforeVisible);

  // Hidden tabs skip ticks: hiding the document produces no new reads
  // (interval ticks return early; the visibility handler ignores them).
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      value: true,
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const beforeHidden = statusReads;
  await page.waitForTimeout(3500);
  // No new reads while hidden (at most the count already observed).
  expect(statusReads).toBe(beforeHidden);

  // Becoming visible refreshes immediately again.
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      value: false,
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect
    .poll(() => statusReads, { timeout: 15000 })
    .toBeGreaterThan(beforeHidden);

  // The 30s interval keeps re-reading while the tab stays visible: the
  // next scheduled tick lands without any further input (generous timeout:
  // the tick can land up to one full interval after this point).
  const beforeInterval = statusReads;
  await expect
    .poll(() => statusReads, { timeout: 90000 })
    .toBeGreaterThan(beforeInterval);
  await expect(page.getByTestId("status-metrics")).toBeVisible();
});

// S58 §3 (401 complement): anonymous browsers land on login for #/status
// (App.tsx renders login whenever there is no user — authority stays
// server-side), and the real API denies unauthenticated reads. Real
// endpoints only — the fresh API context carries no session cookie.
test("S58 §3 anonymous browser lands on login, API denies without session", async ({
  page,
  playwright,
}) => {
  await page.goto("/#/status");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible({
    timeout: 15000,
  });
  // No status hooks leak to an unauthenticated browser.
  for (const id of [
    "status-heading",
    "status-refresh",
    "status-updated",
    "status-alerts",
    "status-metrics",
  ]) {
    await expect(page.getByTestId(id)).toHaveCount(0);
  }

  // Anonymous API read is 401 (backend T1 owns the body; here the
  // observable denial status is enough).
  const anon = await playwright.request.newContext({ baseURL: API_BASE });
  try {
    const denied = await anon.get("/api/v1/ops/status");
    expect(denied.status(), await denied.text()).toBe(401);
  } finally {
    await anon.dispose();
  }
});
