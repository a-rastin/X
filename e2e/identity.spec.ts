import { test, expect } from "@playwright/test";
import {
  RESEARCH_WARNING,
  GENERIC_LOGIN_ERROR,
  uniqueName,
  ensurePhysician,
  loginAs,
  acknowledgeWarning,
  signOut,
} from "./helpers";

test("admin login lands on the administrator dashboard", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Physicians" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
});

test("login page shows Register guidance without a self-registration route", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Contact administrator")).toBeVisible();
  await expect(page.getByRole("link", { name: /register/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /register/i })).toHaveCount(0);
});

test("login failure is generic and grants nothing", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Role").selectOption("physician");
  await page.getByLabel("Username").fill(uniqueName("nosuchuser"));
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(GENERIC_LOGIN_ERROR)).toBeVisible();
  // Still on the login form; no dashboard leaked.
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /dashboard/i }),
  ).toHaveCount(0);
});

test("wrong password for a real user shows the identical generic error", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2eenum");
  await ensurePhysician(request, username, "secret123");
  await page.goto("/");
  await page.getByLabel("Role").selectOption("physician");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  const error = page.getByRole("alert");
  await expect(error).toBeVisible();
  const knownUserText = await error.textContent();
  expect(knownUserText).toBe(GENERIC_LOGIN_ERROR);

  // Unknown-user attempt yields byte-identical text: no enumeration.
  await page.getByLabel("Username").fill(uniqueName("nosuchuser"));
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(error).toBeVisible();
  expect(await error.textContent()).toBe(knownUserText);
});

test("login error is announced and tied to its inputs", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Role").selectOption("physician");
  await page.getByLabel("Username").fill(uniqueName("nosuchuser"));
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  const error = page.getByRole("alert");
  await expect(error).toBeVisible();
  await expect(error).toHaveAttribute("id", "login-error");
  await expect(page.getByLabel("Username")).toHaveAttribute(
    "aria-describedby",
    "login-error",
  );
  await expect(page.getByLabel("Password")).toHaveAttribute(
    "aria-describedby",
    "login-error",
  );
});

test("physician login shows the exact research warning before proceeding", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2ephys");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");

  // Blocking acknowledgement first: dashboard stays hidden until ack.
  await expect(page.getByRole("alertdialog")).toContainText(RESEARCH_WARNING);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toHaveCount(0);
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
});

test("research warning persists across a session refresh", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2ephys");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();

  // GET /me refresh must not force a second blocking acknowledgement,
  // but the exact warning remains surfaced on the physician dashboard.
  await page.reload();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
  await expect(page.getByText(RESEARCH_WARNING)).toBeVisible();
});

test("sign-out clears identity and guards the dashboard", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

  await page.goto("/#/dashboard");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /dashboard/i }),
  ).toHaveCount(0);
});

test("physician changes their own password", async ({ page, request }) => {
  const username = uniqueName("e2epw");
  await ensurePhysician(request, username, "oldpass123");
  await loginAs(page, "physician", username, "oldpass123");
  await acknowledgeWarning(page);

  await page.getByRole("link", { name: "Account" }).click();
  const form = page.getByRole("form", { name: "Change password" });
  await form.getByLabel("Current password").fill("oldpass123");
  await form.getByLabel("New password").fill("newpass456");
  await form.getByRole("button", { name: "Change password" }).click();
  await expect(form.getByText("Password changed.")).toBeVisible();

  // New password works; the old one fails with the generic message.
  await signOut(page);
  await loginAs(page, "physician", username, "newpass456");
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
  await signOut(page);

  await loginAs(page, "physician", username, "oldpass123");
  await expect(page.getByText(GENERIC_LOGIN_ERROR)).toBeVisible();
});

test("wrong current password fails with the generic message", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2epw");
  await ensurePhysician(request, username, "rightpass1");
  await loginAs(page, "physician", username, "rightpass1");
  await acknowledgeWarning(page);

  await page.getByRole("link", { name: "Account" }).click();
  const form = page.getByRole("form", { name: "Change password" });
  await form.getByLabel("Current password").fill("not-the-password");
  await form.getByLabel("New password").fill("anotherpass2");
  await form.getByRole("button", { name: "Change password" }).click();
  await expect(form.getByText(GENERIC_LOGIN_ERROR)).toBeVisible();
});

test("administrator creates a physician through the browser", async ({
  page,
}) => {
  const username = uniqueName("e2ecreate");
  await loginAs(page, "admin", "admin", "admin");
  await page.getByRole("link", { name: "Physicians" }).click();
  await expect(
    page.getByRole("heading", { name: "Physicians" }),
  ).toBeVisible();

  const form = page.getByRole("form", { name: "Create physician" });
  await form.getByLabel("Username").fill(username);
  await form.getByLabel("Password").fill("createdpass1");
  await form.getByRole("button", { name: "Create physician" }).click();
  await expect(form.getByText("Physician created.")).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Physicians" }).getByText(username),
  ).toBeVisible();
});

test("administrator deactivates and reactivates a physician", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2edeact");
  await ensurePhysician(request, username, "deactpass1");
  await loginAs(page, "admin", "admin", "admin");
  await page.getByRole("link", { name: "Physicians" }).click();

  const table = page.getByRole("table", { name: "Physicians" });
  const row = table.getByRole("row", { name: new RegExp(username) });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Deactivate" }).click();

  const form = page.getByRole("form", { name: "Deactivate physician" });
  await form.getByLabel(/Retain/).check();
  await expect(form.getByLabel("Draft set revision")).toHaveValue("0");
  await form.getByRole("button", { name: "Confirm deactivation" }).click();
  await expect(form.getByText("Physician deactivated.")).toBeVisible();

  // Deactivated account cannot log in.
  await signOut(page);
  await loginAs(page, "physician", username, "deactpass1");
  await expect(page.getByText(GENERIC_LOGIN_ERROR)).toBeVisible();

  // Reactivate restores login.
  await loginAs(page, "admin", "admin", "admin");
  await page.getByRole("link", { name: "Physicians" }).click();
  const rowAgain = page
    .getByRole("table", { name: "Physicians" })
    .getByRole("row", { name: new RegExp(username) });
  await rowAgain.getByRole("button", { name: "Reactivate" }).click();
  await expect(page.getByText("Physician reactivated.")).toBeVisible();
  await signOut(page);
  await loginAs(page, "physician", username, "deactpass1");
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
});

test("physician has no governance navigation and is denied the physicians route", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2ephys");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);

  await expect(page.getByRole("link", { name: "Physicians" })).toHaveCount(0);
  await page.goto("/#/physicians");
  await expect(page.getByText("Administrator access required.")).toBeVisible();
});

test("research warning blocks again on the next login", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2ephys");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();

  // Acknowledgement lives in the tab session: sign-out clears it, so the
  // next login must block on the exact warning again.
  await signOut(page);
  await loginAs(page, "physician", username, "secret123");
  await expect(page.getByRole("alertdialog")).toContainText(RESEARCH_WARNING);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toHaveCount(0);
});

test("administrator never sees the research warning", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.getByText(RESEARCH_WARNING)).toHaveCount(0);
});

test("administrator renames a physician through the browser", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2eedit");
  const renamed = uniqueName("e2erenamed");
  await ensurePhysician(request, username, "editpass1");
  await loginAs(page, "admin", "admin", "admin");
  await page.getByRole("link", { name: "Physicians" }).click();

  const table = page.getByRole("table", { name: "Physicians" });
  const row = table.getByRole("row", { name: new RegExp(username) });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Edit" }).click();

  const form = page.getByRole("form", { name: "Edit physician" });
  await form.getByLabel("Username").fill(renamed);
  await form.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Physician updated.")).toBeVisible();
  await expect(table.getByText(renamed)).toBeVisible();
});

test("unauthenticated physicians route degrades to sign-in", async ({
  page,
}) => {
  await page.goto("/#/physicians");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Physicians" }),
  ).toHaveCount(0);
});

test("physician dashboard shows no placeholder clinical content", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2ephys");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
  await expect(page.getByText(/No clinical content/)).toBeVisible();
  await expect(page.getByText(/recommendation/i)).toHaveCount(0);
});

test("physicians table surfaces endpoint failure with Retry and recovers", async ({
  page,
}) => {
  await loginAs(page, "admin", "admin", "admin");
  await page.getByRole("link", { name: "Physicians" }).click();
  await expect(
    page.getByRole("table", { name: "Physicians" }),
  ).toBeVisible();

  // Transport-level failure of the real list endpoint (no internal mocks:
  // the app, router, and server contract are untouched). The glob covers
  // the bounded list query (?limit=100).
  await page.route("**/api/v1/physicians*", (route) => route.abort());
  await page.reload();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText(/failed to load/i)).toBeVisible();
  const retry = page.getByRole("button", { name: "Retry" });
  await expect(retry).toBeVisible();

  await page.unroute("**/api/v1/physicians*");
  await retry.click();
  await expect(
    page.getByRole("table", { name: "Physicians" }),
  ).toBeVisible();
});
