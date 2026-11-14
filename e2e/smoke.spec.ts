import { test, expect } from "@playwright/test";

test("loads INSIGHT shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "INSIGHT" })).toBeVisible();
});
