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
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: {
    user: { id: "admin", username: "admin", role: "admin", theme: "light" },
    csrf_token: "test", research_warning: null,
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
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: {
    user: { id: "admin", username: "admin", role: "admin", theme: "light" },
    csrf_token: "test", research_warning: null,
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
