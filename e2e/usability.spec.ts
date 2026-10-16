import { expect, test, type Locator, type Page } from "@playwright/test";
import { loginAs } from "./helpers";

// 640 CSS px is a 1280px desktop at 200% zoom. No CSS zoom.
test.use({ viewport: { width: 640, height: 720 } });

const NAV_LINKS = [
  "Dashboard",
  "Patients",
  "Physicians",
  "Networks",
  "Audit",
  "Exports",
  "Backups",
  "Restores",
  "Status",
  "Account",
] as const;

async function expectHorizontallyInside(
  page: Page,
  locator: Locator,
  label: string,
): Promise<void> {
  const box = await locator.boundingBox();
  const viewport = page.viewportSize();
  expect(box, `${label}: no bounding box`).not.toBeNull();
  expect(viewport, `${label}: no viewport`).not.toBeNull();
  if (!box || !viewport) return;
  const right = box.x + box.width;
  const detail = `${label} box={x:${box.x},y:${box.y},w:${box.width},h:${box.height},right:${right}} viewport=${viewport.width}x${viewport.height}`;
  expect(box.x, detail).toBeGreaterThanOrEqual(-1);
  expect(right, detail).toBeLessThanOrEqual(viewport.width + 1);
}

test("S61 200% zoom keeps every admin nav link on screen", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav).toBeVisible({ timeout: 15000 });

  const theme = () =>
    page.evaluate(() => document.documentElement.dataset.theme ?? null);
  if ((await theme()) !== "light") {
    await page.getByRole("button", { name: "Switch to light theme" }).click();
  }
  await expect.poll(theme).toBe("light");

  const scrollX = await page.evaluate(() => window.scrollX);
  expect(scrollX, `scrollX=${scrollX}`).toBe(0);

  for (const name of NAV_LINKS) {
    const link = nav.getByRole("link", { name, exact: true });
    await expect(link).toHaveCount(1);
    await expectHorizontallyInside(page, link, name);
  }

  await expectHorizontallyInside(
    page,
    page.getByRole("button", { name: /switch to/i }),
    "theme",
  );
  await expectHorizontallyInside(
    page,
    page.getByRole("button", { name: "Sign out", exact: true }),
    "Sign out",
  );

  for (const name of ["Provider", "API settings", "Provider settings"] as const) {
    await expect(nav.getByRole("link", { name, exact: true })).toHaveCount(0);
  }
});
