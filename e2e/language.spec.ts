import { test, expect, type Page, type Route } from "@playwright/test";
import { RESEARCH_WARNING } from "./helpers";

test.use({ locale: "fa-IR" });

const warningVersion = "research-warning-v2";

async function mockIdentity(page: Page, options: {
  role?: "admin" | "physician";
  warning?: string;
  version?: string;
  login?: (route: Route) => Promise<void>;
  acknowledgement?: (route: Route) => Promise<void>;
} = {}) {
  const mutations: { path: string; body: unknown }[] = [];
  let authenticated = false;
  const user = { id: "language-synthetic-user", username: "Syntheticپزشک", role: options.role ?? "physician", theme: "light" };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET") mutations.push({ path: url.pathname, body: request.postDataJSON() });
    const fulfill = (body: unknown) => route.fulfill({ json: body });
    if (url.pathname === "/api/v1/auth/login") {
      if (options.login) return options.login(route);
      authenticated = true;
      return fulfill({ user, csrf_token: "synthetic-csrf", research_warning: options.warning ?? null, research_warning_version: options.version ?? (options.warning ? warningVersion : null) });
    }
    if (url.pathname === "/api/v1/me") return fulfill(authenticated ? { user, csrf_token: "synthetic-csrf", research_warning: null, research_warning_version: null } : { user: null });
    if (url.pathname === "/api/v1/auth/logout") {
      authenticated = false;
      return fulfill({ ok: true });
    }
    if (url.pathname === "/api/v1/me/research-warning-acknowledgement") {
      if (options.acknowledgement) return options.acknowledgement(route);
      return fulfill({ research_warning: null, research_warning_version: null });
    }
    if (url.pathname === "/api/v1/patients") return fulfill({ items: [], total: 0, limit: 100, offset: 0 });
    return route.fulfill({ status: 500, json: { message: `Unexpected synthetic request: ${url.pathname}` } });
  });
  return mutations;
}

async function signIn(page: Page) {
  await page.getByLabel("Username", { exact: true }).fill("Syntheticپزشک");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

test("Persian browser starts English, switches without losing login input and resets on reload", async ({ page }) => {
  const mutations = await mockIdentity(page);
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  const username = page.locator("#login-username");
  const password = page.locator("#login-password");
  await username.fill("نام mixed");
  await password.fill("unsaved-password");
  await page.keyboard.press("Control+Home");
  await page.getByRole("link", { name: "Skip to content" }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Switch to Persian" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("lang", "fa");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "ورود", exact: true })).toBeVisible();
  await expect(username).toHaveValue("نام mixed");
  await expect(password).toHaveValue("unsaved-password");
  expect(mutations).toEqual([]);
  await page.getByRole("button", { name: "تغییر زبان به انگلیسی" }).click();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Switch to Persian" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
});

test("retained error changes presentation and switching a pending login sends no extra request", async ({ page }) => {
  let releaseLogin!: () => void;
  const pending = new Promise<void>((resolve) => { releaseLogin = resolve; });
  const mutations = await mockIdentity(page, { login: async (route) => {
    await pending;
    await route.fulfill({ status: 401, json: { code: "INVALID_CREDENTIALS", message: "Invalid username, password, or role." } });
  } });
  await page.goto("/");
  await signIn(page);
  await expect(page.getByRole("button", { name: "Signing in…" })).toBeDisabled();
  await page.getByRole("button", { name: "Switch to Persian" }).click();
  await expect(page.getByRole("button", { name: "در حال ورود…" })).toBeDisabled();
  releaseLogin();
  await expect(page.getByRole("alert")).toHaveText("نام کاربری، گذرواژه یا نقش نامعتبر است.");
  await page.getByRole("button", { name: "تغییر زبان به انگلیسی" }).click();
  await expect(page.getByRole("alert")).toHaveText("Invalid username, password, or role.");
  expect(mutations).toEqual([{ path: "/api/v1/auth/login", body: { username: "Syntheticپزشک", password: "synthetic-password", role: "physician" } }]);
});

test("an unsent registration keeps values, validation and route across language changes", async ({ page }) => {
  const mutations = await mockIdentity(page);
  await page.goto("/");
  await signIn(page);
  await page.getByRole("link", { name: "Register new patient" }).click();
  await page.locator("#reg-given-name").fill("علی");
  await page.locator("#reg-family-name").fill("Mixed فارسی");
  await page.locator("#reg-identifier").fill("0012345678");
  await page.locator("#reg-age").fill("۳۰");
  await page.locator("#reg-age").blur();
  await expect(page.locator("#age-error")).toHaveText("Age must be an integer 18-99.");
  const url = page.url();
  await page.getByRole("button", { name: "Switch to Persian" }).click();
  await expect(page.locator("#age-error")).toHaveText("سن باید عدد صحیح بین ۱۸ تا ۹۹ باشد.");
  await expect(page.locator("#reg-given-name")).toHaveValue("علی");
  await expect(page.locator("#reg-family-name")).toHaveValue("Mixed فارسی");
  await expect(page.locator("#reg-identifier")).toHaveValue("0012345678");
  await expect(page.locator("#reg-age")).toHaveValue("۳۰");
  expect(page.url()).toBe(url);
  await expect(page.getByRole("button", { name: "ثبت", exact: true })).toBeDisabled();
  expect(mutations).toHaveLength(1);
});

for (const role of ["physician", "admin"] as const) {
  test(`${role} header switch stays physically left in both directions, themes, and desktop zoom width`, async ({ page }) => {
    const mutations = await mockIdentity(page, { role });
    await page.goto("/");
    await page.getByLabel("Role", { exact: true }).selectOption(role);
    await signIn(page);
    await expect(page.getByRole("heading", { name: role === "admin" ? "Administrator dashboard" : "Physician dashboard" })).toBeVisible();
    for (const width of [1440, 640]) {
      await page.setViewportSize({ width, height: 900 });
      for (const theme of ["light", "dark"]) {
        await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
        for (const language of ["en", "fa"] as const) {
          if (await page.locator("html").getAttribute("lang") !== language) {
            await page.locator(".xi-header-language button").click();
          }
          const button = page.locator(".xi-header-language button");
          const buttonBounds = await button.boundingBox();
          const headerBounds = await page.locator(".xi-header").boundingBox();
          expect(buttonBounds).not.toBeNull();
          expect(headerBounds).not.toBeNull();
          expect(buttonBounds!.x).toBeLessThan(width / 2);
          expect(buttonBounds!.y - headerBounds!.y).toBeLessThan(20);
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        }
      }
    }
    expect(mutations).toHaveLength(1);
    await page.emulateMedia({ media: "print" });
    await expect(page.locator(".xi-header-language button")).toBeHidden();
    await page.emulateMedia({ media: "screen" });
    await page.locator(".xi-header-language button").click();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
  });
}

test("modal switch preserves focus containment and acknowledges the server version after a 409", async ({ page }) => {
  let attempts = 0;
  const updatedSource = "Complete new server wording. It must remain visible without substitution.";
  const mutations = await mockIdentity(page, { warning: RESEARCH_WARNING, acknowledgement: async (route) => {
    attempts += 1;
    await route.fulfill(attempts === 1 ? { status: 409, json: { code: "RESEARCH_WARNING_CHANGED", message: "Research warning changed.", research_warning: updatedSource, research_warning_version: "research-warning-v3" } } : { json: { research_warning: null, research_warning_version: null } });
  } });
  await page.goto("/");
  await signIn(page);
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(RESEARCH_WARNING);
  await dialog.getByRole("button", { name: "Switch to Persian" }).click();
  await expect(dialog).toContainText("از نظر بالینی اعتبارسنجی نشده");
  await expect(dialog).toContainText("استانداردهای قابل‌اعمال مراقبت");
  expect(mutations).toHaveLength(1);
  await expect(dialog.getByRole("button", { name: "تغییر زبان به انگلیسی" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "تأیید و ادامه" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toContainText(updatedSource);
  await expect(dialog).toContainText("ترجمه در دسترس نیست");
  await dialog.getByRole("button", { name: "تأیید و ادامه" }).click();
  await expect(dialog).toBeHidden();
  expect(mutations.filter((entry) => entry.path.endsWith("acknowledgement")).map((entry) => entry.body)).toEqual([{ version: warningVersion }, { version: "research-warning-v3" }]);
  await expect(page.getByRole("heading", { name: "داشبورد پزشک" })).toBeVisible();
  await page.getByRole("button", { name: "خروج", exact: true }).click();
  await expect(page.getByRole("heading", { name: "ورود", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "fa");
});
