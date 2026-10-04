import { test, expect } from "@playwright/test";

test("loads X-INSIGHT shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "X-INSIGHT" })).toBeVisible();
});
