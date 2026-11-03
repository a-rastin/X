import { test, expect, type Page } from "@playwright/test";
import {
  RESEARCH_WARNING,
  GENERIC_LOGIN_ERROR,
  uniqueName,
  ensurePhysician,
  loginAs,
  acknowledgeWarning,
  acknowledgeWarningIfPresent,
  signOut,
} from "./helpers";

test("research warning blocks the administrator first login with the exact server text", async ({
  page,
}) => {
  await loginAs(page, "admin", "admin", "admin");
  // Wait for the session to be ready before asserting: the gate commits in
  // the same state update, so a missing dialog after this is a real failure.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await expect(page.getByRole("alertdialog")).toContainText(RESEARCH_WARNING);
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toHaveCount(0);
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
});

test("research warning blocks the physician first login with the exact server text", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2efirst");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  // Wait for the session to be ready before asserting: the gate commits in
  // the same state update, so a missing dialog after this is a real failure.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await expect(page.getByRole("alertdialog")).toContainText(RESEARCH_WARNING);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toHaveCount(0);
  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
});

for (const role of ["admin", "physician"] as const) {
  test(`login URL: ${role} replaces the login entry and survives reload`, async ({ page, request }) => {
    const username = role === "admin" ? "admin" : uniqueName("e2eurl");
    const password = role === "admin" ? "admin" : "secret123";
    if (role === "physician") await ensurePhysician(request, username, password);

    await page.goto("/?source=history#/account");
    await page.evaluate(() => { window.location.hash = "#/login"; });
    await expect(page).toHaveURL(/\?source=history#\/login$/);
    await page.getByLabel("Role").selectOption(role);
    await page.getByLabel("Username").fill(username);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\?source=history#\/dashboard$/);
    if (role === "physician") {
      await expect(page.getByRole("alertdialog")).toContainText(RESEARCH_WARNING);
      await expect(page.getByRole("heading", { name: "Physician dashboard" })).toHaveCount(0);
      await acknowledgeWarning(page);
    } else {
      // The administrator gate appears only until the persisted
      // acknowledgement is stored; later logins show no modal.
      await acknowledgeWarningIfPresent(page);
    }
    await expect(page.getByRole("heading", { name: role === "admin" ? "Administrator dashboard" : "Physician dashboard" })).toBeVisible();
    await page.reload();
    await expect(page).toHaveURL(/\?source=history#\/dashboard$/);
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/\?source=history#\/account$/);
    await expect(page.getByRole("button", { name: "Sign in" })).toHaveCount(0);
  });
}

test("login URL: authenticated login route redirects while deep links survive restoration", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
  await expect(page).toHaveURL(/#\/dashboard$/);
  // Changing the query forces a document load and exercises startup restoration.
  await page.goto("/?restore=1#/login");
  await expect(page).toHaveURL(/\?restore=1#\/dashboard$/);
  await page.evaluate(() => { window.location.hash = "#/login"; });
  await expect(page).toHaveURL(/#\/dashboard$/);
  await page.goto("/#/account");
  await expect(page.getByRole("form", { name: "Change password" })).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/#\/account$/);
  await expect(page.getByRole("form", { name: "Change password" })).toBeVisible();
});

test("login URL: login from an anonymous protected route redirects", async ({ page }) => {
  await page.goto("/#/account");
  await page.getByLabel("Role").selectOption("admin");
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("admin");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/#\/dashboard$/);
});

test("admin login lands on the administrator dashboard", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
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
  await expect(page).toHaveURL(/\/$/);
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

test("research warning acknowledgement persists across a session refresh", async ({
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

  // GET /me refresh must not force a second blocking acknowledgement.
  await page.reload();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
  await expect(page.getByText(RESEARCH_WARNING)).toHaveCount(0);
});

test("sign-out clears identity and guards the dashboard", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
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

  // New password works; the old one fails with the generic message. The
  // acknowledgement already persisted, so the second login shows no modal.
  await signOut(page);
  await loginAs(page, "physician", username, "newpass456");
  await acknowledgeWarningIfPresent(page);
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
  await acknowledgeWarningIfPresent(page);
  await page.getByRole("link", { name: "Physicians" }).click();
  await expect(
    page.getByRole("heading", { name: "Physicians" }),
  ).toBeVisible();

  const form = page.getByRole("form", { name: "Create physician" });
  await form.getByLabel("Username").fill(username);
  await form.getByLabel("Password").fill("createdpass1");
  await form.getByRole("button", { name: "Create physician" }).click();
  await expect(form.getByText("Physician created.")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Physician created." })).toHaveCount(1);
  await expect(
    page.getByRole("table", { name: "Physicians" }).getByText(username),
  ).toBeVisible();
});

test("tabs keep mutation CSRF aligned with the shared session cookie", async ({
  page,
  context,
}) => {
  const otherTab = await context.newPage();
  await otherTab.goto("/");
  await expect(otherTab.getByRole("button", { name: "Sign in" })).toBeVisible();

  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  const firstToken = await page.evaluate(
    () => localStorage.getItem("xi_csrf_token") ?? "",
  );
  expect(firstToken).not.toBe("");

  // The second login replaces the cookie shared by both tabs. Its token must
  // become the token used by the first tab's next mutation as well.
  await otherTab.getByLabel("Role").selectOption("admin");
  await otherTab.getByLabel("Username").fill("admin");
  await otherTab.getByLabel("Password").fill("admin");
  await otherTab.getByRole("button", { name: "Sign in" }).click();
  await expect(
    otherTab.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("xi_csrf_token") ?? ""))
    .not.toBe(firstToken);

  const username = uniqueName("e2ecsrftab");
  await page.getByRole("link", { name: "Physicians" }).click();
  const form = page.getByRole("form", { name: "Create physician" });
  await form.getByLabel("Username").fill(username);
  await form.getByLabel("Password").fill("createdpass1");
  await form.getByRole("button", { name: "Create physician" }).click();
  await expect(form.getByText("Physician created.")).toBeVisible();
});

test("administrator deactivates and reactivates a physician", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2edeact");
  await ensurePhysician(request, username, "deactpass1");
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
  await page.getByRole("link", { name: "Physicians" }).click();

  const table = page.getByRole("table", { name: "Physicians" });
  const row = table.getByRole("row", { name: new RegExp(username) });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Deactivate" }).click();

  const form = page.getByRole("form", { name: "Deactivate physician" });
  await form.getByLabel(/Retain/).check();
  // S51: no manual Draft set revision input — the form submits the preview revision.
  await form.getByRole("button", { name: "Confirm deactivation" }).click();
  await expect(form.getByText("Physician deactivated.")).toBeVisible();

  // Deactivated account cannot log in.
  await signOut(page);
  await loginAs(page, "physician", username, "deactpass1");
  await expect(page.getByText(GENERIC_LOGIN_ERROR)).toBeVisible();

  // Reactivate restores login.
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
  await page.getByRole("link", { name: "Physicians" }).click();
  const rowAgain = page
    .getByRole("table", { name: "Physicians" })
    .getByRole("row", { name: new RegExp(username) });
  await rowAgain.getByRole("button", { name: "Reactivate" }).click();
  await expect(page.getByText("Physician reactivated.")).toBeVisible();
  await signOut(page);
  await loginAs(page, "physician", username, "deactpass1");
  // Acknowledged long before deactivation, so the gate stays dismissed.
  await acknowledgeWarningIfPresent(page);
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

test("acknowledged warning stays dismissed across refresh, sign-out, and fresh contexts", async ({
  page,
  request,
  browser,
}) => {
  const username = uniqueName("e2epersist");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);

  // GET /me refresh must not force a second blocking acknowledgement.
  await page.reload();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();

  // The next login needs no modal: the acknowledgement persisted server-side.
  await signOut(page);
  await loginAs(page, "physician", username, "secret123");
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);

  // A fresh browser context shares no tab or cookie state, yet the committed
  // acknowledgement still applies.
  const fresh = await browser.newContext();
  const second = await fresh.newPage();
  try {
    await loginAs(second, "physician", username, "secret123");
    await expect(
      second.getByRole("heading", { name: "Physician dashboard" }),
    ).toBeVisible();
    await expect(second.getByRole("alertdialog")).toHaveCount(0);
  } finally {
    await fresh.close();
  }

  // Isolation: a different account still gets its own gate.
  const other = uniqueName("e2eisolate");
  await ensurePhysician(request, other, "secret123");
  await signOut(page);
  await loginAs(page, "physician", other, "secret123");
  await expect(page.getByRole("alertdialog")).toContainText(RESEARCH_WARNING);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toHaveCount(0);
  await acknowledgeWarning(page);
});

test("pending warning survives a direct patient-route reload and withholds route content", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2eroute");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await expect(page.getByRole("alertdialog")).toContainText(RESEARCH_WARNING);

  // Deep-linking past the gate, then reloading, still blocks on the modal
  // with no route content or route dialogs above it.
  await page.goto("/#/patients");
  await page.reload();
  await expect(page.getByRole("alertdialog")).toContainText(RESEARCH_WARNING);
  await expect(
    page.getByRole("heading", { name: "Patient directory" }),
  ).toHaveCount(0);

  await acknowledgeWarning(page);
  await expect(
    page.getByRole("heading", { name: "Patient directory" }),
  ).toBeVisible();
});

test("research gate keeps native focus, traps Tab, ignores Escape, and acks by keyboard in a short viewport", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2ekeys");
  await ensurePhysician(request, username, "secret123");
  await page.setViewportSize({ width: 360, height: 400 });
  await loginAs(page, "physician", username, "secret123");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(RESEARCH_WARNING);

  // Initial focus lands on the dialog title for long-form reading.
  const title = page.getByRole("heading", {
    name: "Research prototype — clinical use disclaimer",
  });
  await expect(title).toBeFocused();

  // Escape must not dismiss the blocking gate.
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();

  // Tab cycles inside the modal: scrollable body text, then the action.
  const body = page.locator("#research-warning-text");
  await page.keyboard.press("Tab");
  await expect(body).toBeFocused();
  const ack = dialog.getByRole("button", {
    name: "Acknowledge and continue",
  });
  await page.keyboard.press("Tab");
  await expect(ack).toBeFocused();

  // Tab past the last control wraps back into the modal, never behind it.
  await page.keyboard.press("Tab");
  await expect(body).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(ack).toBeFocused();

  // Keyboard-operate the action: the gate commits and the dashboard opens.
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
});

test("failed acknowledgement keeps the gate with an announced retryable error", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2eackfail");
  await ensurePhysician(request, username, "secret123");
  await page.route(
    "**/api/v1/me/research-warning-acknowledgement",
    (route) => route.abort(),
  );
  await loginAs(page, "physician", username, "secret123");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(RESEARCH_WARNING);

  await dialog
    .getByRole("button", { name: "Acknowledge and continue" })
    .click();
  // The failure is announced inline, the gate stays open, and the action
  // re-enables for a retry: no local success is recorded.
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Acknowledge and continue" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toHaveCount(0);

  // Retry by keyboard once the endpoint recovers: the committed write
  // dismisses the gate.
  await page.unroute("**/api/v1/me/research-warning-acknowledgement");
  await dialog
    .getByRole("button", { name: "Acknowledge and continue" })
    .focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
});

test("stale acknowledgement with nothing pending closes the gate without an error", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2eackstale");
  await ensurePhysician(request, username, "secret123");
  // Crafted-request edge: stale version while nothing is pending. The real
  // backend answers 409 RESEARCH_WARNING_CHANGED with null pending text;
  // the gate must close quietly with no error announced.
  await page.route(
    "**/api/v1/me/research-warning-acknowledgement",
    (route) =>
      route.fulfill({
        status: 409,
        json: {
          code: "RESEARCH_WARNING_CHANGED",
          message:
            "The research warning changed. Review the current text and acknowledge it.",
          field_errors: { version: ["Reviewed version is stale."] },
          research_warning: null,
          research_warning_version: null,
        },
      }),
  );
  await loginAs(page, "physician", username, "secret123");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(RESEARCH_WARNING);

  await dialog
    .getByRole("button", { name: "Acknowledge and continue" })
    .click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
  await page.unroute("**/api/v1/me/research-warning-acknowledgement");
});

test("administrator renames a physician through the browser", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2eedit");
  const renamed = uniqueName("e2erenamed");
  await ensurePhysician(request, username, "editpass1");
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
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

test("physician dashboard shows the patient directory without recommendations", async ({
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
  await expect(
    page.getByRole("heading", { name: "Patient directory" }),
  ).toBeVisible();
  await expect(page.getByText(/recommendation/i)).toHaveCount(0);
});

test("physicians table surfaces endpoint failure with Retry and recovers", async ({
  page,
}) => {
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
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

test("S61 physician list says when the table is shorter than the total", async ({
  page,
}) => {
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
  await page.getByRole("link", { name: "Physicians" }).click();

  const heading = page.getByRole("heading", {
    name: /Physician accounts \(\d+\)/,
  });
  await expect(heading).toBeVisible();
  const total = Number(
    (await heading.innerText())
      .replace(/\s+/g, " ")
      .match(/Physician accounts \((\d+)\)/)?.[1],
  );

  const table = page.getByRole("table", { name: "Physicians" });
  const dataRows = (await table.getByRole("row").count()) - 1;

  if (dataRows < total) {
    await expect(
      page.getByText(`Showing ${dataRows} of ${total}`, { exact: true }),
    ).toBeVisible();
  } else {
    expect(dataRows).toBe(total);
    await expect(page.getByText("Showing ")).toHaveCount(0);
  }
});

for (const oldResponse of ["success", "failure"] as const) {
test(`physician creation guards submissions and ignores older ${oldResponse}`, async ({ page }) => {
  await page.route("**/api/v1/me?optional=true", (route) => route.fulfill({ json: {
    user: { id: "admin", username: "admin", role: "admin", theme: "light" },
    csrf_token: "test", research_warning: null, research_warning_version: null,
  } }));
  const physician = (name: string) => ({ id: name, username: name, role: "physician", active: true, theme: "light", revision: 1 });
  const firstPage = Array.from({ length: 100 }, (_, i) => physician(`a${String(i).padStart(3, "0")}`));
  let posts = 0;
  let gets = 0;
  let releasePost!: () => void;
  let releaseOldGet!: () => void;
  let oldGetFinished!: () => void;
  const postGate = new Promise<void>((resolve) => { releasePost = resolve; });
  const oldGetGate = new Promise<void>((resolve) => { releaseOldGet = resolve; });
  const oldGetDone = new Promise<void>((resolve) => { oldGetFinished = resolve; });
  await page.route("**/api/v1/physicians**", async (route) => {
    if (route.request().method() === "POST") {
      posts++;
      if (posts === 1) {
        await postGate;
        await route.fulfill({ status: 400, json: { code: "INVALID", message: "Invalid account" } });
      } else {
        const { username } = route.request().postDataJSON();
        await route.fulfill({ status: 201, json: { user: physician(username) } });
      }
      return;
    }
    const itemPath = new URL(route.request().url()).pathname.match(/\/physicians\/([^/]+)$/);
    if (itemPath) {
      await route.fulfill({ json: { user: physician(itemPath[1]) } });
      return;
    }
    if (posts === 0) {
      await route.fulfill({ json: { items: firstPage, total: 103, limit: 100, offset: 0 } });
      return;
    }
    gets++;
    if (gets === 1) {
      await oldGetGate;
      if (oldResponse === "success") {
        await route.fulfill({ json: { items: [physician("oldresponse")], total: 1, limit: 100, offset: 0 } });
      } else {
        await route.abort();
      }
      oldGetFinished();
    } else if (gets === 3) {
      await route.abort();
    } else {
      const items = gets === 2 ? [...firstPage.slice(0, 99), physician("zsecond")] : firstPage;
      await route.fulfill({ json: { items, total: 103, limit: 100, offset: 0 } });
    }
  });
  await page.goto("/#/physicians");
  const form = page.getByRole("form", { name: "Create physician" });
  await expect(page.getByText("Showing 100 of 103")).toBeVisible();
  await form.getByLabel("Username").fill("zfirst");
  await form.getByLabel("Password").fill("password123");
  await form.getByRole("button", { name: "Create physician" }).click();
  await expect(form.getByRole("button", { name: "Creating…" })).toBeDisabled();
  await form.evaluate((element) => { element.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  expect(posts).toBe(1);
  releasePost();
  await expect(form.getByRole("alert")).toBeVisible();
  await expect(form.getByRole("button", { name: "Create physician" })).toBeEnabled();
  await form.getByRole("button", { name: "Create physician" }).click();
  await expect(page.getByRole("table", { name: "Physicians" }).getByText("zfirst")).toBeVisible();
  await expect.poll(() => gets).toBe(1);
  await expect(page.getByRole("status").filter({ hasText: "Physician created." })).toHaveCount(1);
  await form.getByLabel("Username").fill("zsecond");
  await form.getByLabel("Password").fill("password123");
  await form.getByRole("button", { name: "Create physician" }).click();
  await expect.poll(() => gets).toBe(2);
  await expect(page.getByRole("table", { name: "Physicians" }).getByText("zsecond")).toHaveCount(1);
  await expect(page.getByRole("table", { name: "Physicians" }).getByText("zfirst")).toHaveCount(0);
  releaseOldGet();
  await oldGetDone;
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("table", { name: "Physicians" }).getByText("zsecond")).toBeVisible();
  await form.getByLabel("Username").fill("zthird");
  await form.getByLabel("Password").fill("password123");
  await form.getByRole("button", { name: "Create physician" }).click();
  await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: "Physicians" })).toHaveCount(0);
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("table", { name: "Physicians" }).getByText("zthird")).toBeVisible();
  await expect(page.getByText("Showing the first page and the newly created physician.")).toBeVisible();
});
}

test("an off-page created physician refreshes after an edit conflict", async ({ page }) => {
  await page.route("**/api/v1/me?optional=true", (route) => route.fulfill({ json: {
    user: { id: "admin", username: "admin", role: "admin", theme: "light" },
    csrf_token: "test", research_warning: null, research_warning_version: null,
  } }));
  const physician = (name: string) => ({ id: name, username: name, role: "physician", active: true, theme: "light", revision: 1 });
  const firstPage = Array.from({ length: 100 }, (_, i) => physician(`a${String(i).padStart(3, "0")}`));
  let current = physician("zcreated");
  let patches = 0;
  await page.route("**/api/v1/physicians**", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      await route.fulfill({ status: 201, json: { user: current } });
    } else if (request.method() === "PATCH") {
      patches++;
      if (patches === 1) {
        expect(request.headers()["if-match"]).toBe('"1"');
        current = { ...current, username: "zexternal", active: false, revision: 2 };
        await route.fulfill({ status: 412, json: { code: "PRECONDITION_FAILED", message: "Account changed" } });
      } else {
        expect(request.headers()["if-match"]).toBe('"2"');
        current = { ...current, username: "zsaved", revision: 3 };
        await route.fulfill({ json: { user: current } });
      }
    } else if (new URL(request.url()).pathname.endsWith("/zcreated")) {
      await route.fulfill({ json: { user: current } });
    } else {
      await route.fulfill({ json: { items: firstPage, total: 101, limit: 100, offset: 0 } });
    }
  });
  await page.goto("/#/physicians");
  await expect(page.getByText("Showing 100 of 101")).toBeVisible();
  const create = page.getByRole("form", { name: "Create physician" });
  await create.getByLabel("Username").fill("zcreated");
  await create.getByLabel("Password").fill("password123");
  await create.getByRole("button", { name: "Create physician" }).click();
  const row = page.getByRole("row").filter({ hasText: "zcreated" });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Edit" }).click();
  const edit = page.getByRole("form", { name: "Edit physician" });
  await edit.getByLabel("Username").fill("zsaved");
  await edit.getByRole("button", { name: "Save changes" }).click();
  await expect(edit.getByRole("alert")).toContainText("account changed");
  await expect(page.getByRole("row").filter({ hasText: "zexternal" })).toContainText("Inactive");
  await edit.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("row").filter({ hasText: "zsaved" })).toContainText("Inactive");
  expect(patches).toBe(2);
});

// ---- Issue 29: real favicon and the anonymous /me start-up probe ----
//
// Every test below is path-agnostic: it only speaks /, relative links and
// the real API through the same-origin proxy, so the whole block is meant to
// be exercised twice — once against `vite dev` and once against the built
// site served by `vite preview` (the static path Caddy serves in production,
// where a missing path falls back to index.html).

/** A response the browser actually received during the load under test. The
 * alias keeps the Playwright type out of the assertions without shadowing the
 * DOM `Response`. */
type NetworkResponse = import("@playwright/test").Response;

interface StartupTraffic {
  responses: NetworkResponse[];
  /** Console messages of type `error`/`warning`, as the browser reported them. */
  consoleMessages: { type: string; text: string }[];
}

/** Record every network response and every console error/warning from before
 * the first document request, so the assertions read the whole set once the
 * page has settled instead of racing individual events. */
function recordStartupTraffic(page: Page): StartupTraffic {
  const responses: NetworkResponse[] = [];
  const consoleMessages: { type: string; text: string }[] = [];
  page.on("response", (response) => {
    responses.push(response);
  });
  page.on("console", (message) => {
    const type = message.type();
    // Only the levels the browser uses to report a failed request.
    // verbose/debug/info/log lines carry nothing about a failure.
    if (type === "error" || type === "warning") {
      consoleMessages.push({ type, text: message.text() });
    }
  });
  return { responses, consoleMessages };
}

function responsePath(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return url;
  }
}

function responsesToPath(
  traffic: StartupTraffic,
  path: string,
): NetworkResponse[] {
  return traffic.responses.filter(
    (response) => responsePath(response.url()) === path,
  );
}

/** The theme the server confirmed, read from the document attribute the app
 * applies only from the authenticated /me payload or a saved mutation. */
function documentTheme(page: Page): Promise<string | null> {
  return page.evaluate(() =>
    document.documentElement.getAttribute("data-theme"),
  );
}

/** Save the theme preference through the real header control and return the
 * theme the server confirmed. The control is a CSRF-protected
 * PATCH /me/preferences: a missing, stale or mismatched token is rejected by
 * the CSRF gate before the handler runs, so this doubles as the proof that a
 * restored token still authorises a mutation. */
async function saveThemeThroughHeaderControl(
  page: Page,
): Promise<"light" | "dark"> {
  const before = await documentTheme(page);
  expect(before, "the session carries a theme").not.toBeNull();
  const target: "light" | "dark" = before === "dark" ? "light" : "dark";
  // The control is labelled with the theme it switches to, whatever the
  // current one happens to be (admin's theme persists across specs).
  const switchTo = `Switch to ${target} theme`;
  const switchBack = `Switch to ${before} theme`;
  await page.getByRole("button", { name: switchTo }).click();
  // The label and the document follow only the server-confirmed theme; a
  // rejected save reverts the label and announces its own error instead.
  await expect(page.getByRole("button", { name: switchBack })).toBeVisible();
  await expect.poll(() => documentTheme(page)).toBe(target);
  await expect(
    page.getByText("Theme preference could not be saved."),
  ).toHaveCount(0);
  return target;
}

test("clean context start-up shows sign-in, serves a real favicon, and asks /me quietly", async ({
  page,
}) => {
  // Playwright gives each test its own context, so this load starts with empty
  // localStorage and no session cookie: the HttpOnly cookie is the only thing
  // that could establish a session here, and there is none.
  const traffic = recordStartupTraffic(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

  // --- favicon: the document declares the icon, and the URL serves one ---
  // The link is what makes a real browser (with a tab strip) request the icon.
  expect(
    await page.evaluate(() => {
      const link = document.querySelector('link[rel="icon"]');
      return link === null
        ? null
        : new URL((link as HTMLLinkElement).href, document.baseURI).pathname;
    }),
    "index.html declares the icon",
  ).toBe("/favicon.ico");

  // Headless Chromium and headless Firefox make no /favicon.ico request of
  // their own: both run without the tab strip that asks for one, so the
  // recorded page traffic usually holds no icon response at all. That is a
  // browser gap, not an application failure, so the declared URL is requested
  // from the page itself (the page's own network stack and origin, no
  // credentials, cache bypassed) and what the server answers with is asserted
  // there. Any icon response a browser did request on load is still checked
  // for a failure status below.
  const icon = await page.evaluate(async () => {
    const response = await fetch("/favicon.ico", { cache: "no-store" });
    const bytes = new Uint8Array(await response.arrayBuffer());
    return {
      url: response.url,
      status: response.status,
      contentType: response.headers.get("content-type") ?? "",
      // The first bytes decide icon vs. SPA HTML fallback, so they are carried
      // back as numbers instead of being decoded here.
      firstBytes: Array.from(bytes.subarray(0, 32)),
    };
  });
  expect(icon.status, `${icon.status} ${icon.url}`).toBe(200);
  // Vite (dev) and the preview server (the static path Caddy serves) both
  // answer with the asset's own image type.
  expect(icon.contentType.toLowerCase()).toMatch(
    /^image\/(x-icon|vnd\.microsoft\.icon|icon|png|gif)/,
  );
  // ICO magic is ICONDIR's reserved-zero count + type-1 marker. An HTML
  // fallback (index.html) never starts with it, so this is the check that the
  // status code alone cannot give.
  expect(icon.firstBytes.slice(0, 4)).toEqual([0x00, 0x00, 0x01, 0x00]);
  const opening = icon.firstBytes
    .map((byte) => String.fromCharCode(byte))
    .join("")
    .trimStart()
    .toLowerCase();
  expect(opening.startsWith("<!doctype")).toBeFalsy();
  expect(opening.startsWith("<html")).toBeFalsy();

  // --- no favicon failure anywhere in the load ---------------------------
  const faviconFailures = traffic.responses.filter(
    (response) =>
      responsePath(response.url()).endsWith("favicon.ico") &&
      response.status() >= 400,
  );
  expect(faviconFailures.map((r) => `${r.status()} ${r.url()}`)).toEqual([]);

  // --- /me: the opt-in probe is asked and answered 200, never a 401 ------
  const probes = responsesToPath(traffic, "/api/v1/me");
  expect(
    probes.length,
    "the client probed /api/v1/me on start-up",
  ).toBeGreaterThan(0);
  for (const probe of probes) {
    expect(probe.status(), `${probe.status()} ${probe.url()}`).toBe(200);
    // The opt-in flag is what keeps this a documented anonymous probe; the
    // default route keeps its 401 contract for every other client.
    expect(new URL(probe.url()).searchParams.get("optional")).toBe("true");
  }

  // --- console: nothing about the icon or the probe was reported ---------
  // Only what this issue is about is asserted. The framework and the dev
  // tooling emit unrelated warnings that say nothing about the favicon or the
  // start-up probe, so they are deliberately not treated as failures here.
  const probeFailures = traffic.consoleMessages.filter((entry) =>
    /favicon|\.ico|\/me\b|\b401\b|\b404\b/i.test(entry.text),
  );
  expect(probeFailures.map((entry) => `${entry.type}: ${entry.text}`)).toEqual(
    [],
  );
});

test("signed-in reload restores the identity and a working CSRF token", async ({
  page,
}) => {
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();

  await page.reload();
  // Identity is restored from the cookie alone: the header is back, the route
  // renders, and the persisted acknowledgement is still committed so the
  // warning gate does not reopen.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);

  // /me re-issues the token for that cookie, so localStorage carries one again.
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("xi_csrf_token") ?? ""))
    .not.toBe("");

  // The restored token authorises a CSRF-protected mutation.
  const saved = await saveThemeThroughHeaderControl(page);
  // The save really reached the server, so the confirmed theme is what the
  // next /me payload restores after another document load.
  await page.reload();
  await expect.poll(() => documentTheme(page)).toBe(saved);
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  // Return the shared admin singleton to the theme this test found it in.
  const flippedBack = await saveThemeThroughHeaderControl(page);
  expect(flippedBack).not.toBe(saved);
});

test("cleared localStorage keeps the session from the cookie and re-issues the CSRF token", async ({
  page,
}) => {
  await loginAs(page, "admin", "admin", "admin");
  await acknowledgeWarningIfPresent(page);
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();

  // localStorage is not a session authority: drop every key while the
  // HttpOnly session cookie stays, exactly as a storage eviction would.
  await page.evaluate(() => {
    localStorage.clear();
  });
  expect(await page.evaluate(() => localStorage.getItem("xi_csrf_token"))).toBeNull();

  await page.reload();
  // The cookie alone still restores the identity, with no gate re-opened.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);

  // The start-up probe re-issues the matching token for that cookie, so the
  // next mutation can present it.
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("xi_csrf_token") ?? ""))
    .not.toBe("");

  // With storage cleared, the re-issued token still authorises a mutation.
  const saved = await saveThemeThroughHeaderControl(page);
  await page.reload();
  await expect.poll(() => documentTheme(page)).toBe(saved);
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  const flippedBack = await saveThemeThroughHeaderControl(page);
  expect(flippedBack).not.toBe(saved);
});
