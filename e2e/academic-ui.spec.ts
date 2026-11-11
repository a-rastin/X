import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function mockSession(page: Page, role: "admin" | "physician") {
  let user = { id: "user-1", username: "example", name_en: "Example physician", role, theme: "light" };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/me/preferences") {
      user = { ...user, theme: route.request().postDataJSON().theme };
      return route.fulfill({ json: { user } });
    }
    if (path === "/api/v1/me") {
      return route.fulfill({ json: { user, csrf_token: "test", research_warning: null, research_warning_version: null } });
    }
    if (path === "/api/v1/patients") {
      return route.fulfill({ json: { items: [{ id: "patient-1", identifier: "DEMO-001", given_name: "Example", family_name: "Patient", sex: "F", age: 30, clinical_status: "first_time", archived: false }], total: 1, limit: 25, offset: 0 } });
    }
    return route.fulfill({ json: {} });
  });
}

for (const role of ["physician", "admin"] as const) {
  test(`${role} navigation and directory remain usable at narrow widths`, async ({ page }) => {
    await page.setViewportSize({ width: 640, height: 900 });
    await mockSession(page, role);
    await page.goto("/");
    const navigation = page.getByRole("navigation", { name: "Primary" });
    await expect(navigation.getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    await navigation.getByRole("link", { name: "Patients", exact: true }).click();
    await expect(page).toHaveURL(/#\/patients$/);
    await expect(page.getByRole("heading", { name: "Patient directory (1)" })).toBeVisible();
    await expect(page.getByRole("link", { name: /view chart for DEMO-001/i })).toBeVisible();
    await expect(page.getByRole("link", { name: "Register new patient" })).toHaveCount(role === "physician" ? 1 : 0);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const inBounds = await navigation.locator("a").evaluateAll((links) => links.every((link) => {
      const box = link.getBoundingClientRect();
      return box.left >= 0 && box.right <= innerWidth;
    }));
    expect(inBounds).toBe(true);
    await page.getByRole("button", { name: "Switch to Persian" }).click();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    const languageLeft = await page.locator(".xi-header-language").evaluate((el) => el.getBoundingClientRect().left);
    expect(languageLeft).toBeLessThan(30);
    await page.getByRole("button", { name: /تغییر.*تیره|تیره/i }).first().click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("keyboard navigation and accessible colors are retained", async ({ page }) => {
  await mockSession(page, "physician");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Physician dashboard" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Switch to Persian" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Dashboard" })).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: /Switch to dark theme/ }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
