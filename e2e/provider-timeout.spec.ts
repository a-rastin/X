import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers";

test("B01 saved timeout survives reload and appears on Status", async ({ page, request }) => {
  const login = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(login.ok()).toBeTruthy();
  // This test needs no provider credential or outbound provider call.
  const saved = await request.put("/api/v1/api-settings", {
    headers: { "X-CSRF-Token": (await login.json()).csrf_token },
    data: { base_url: "https://provider.example", model: "test", secret_action: "clear", timeout_seconds: 141 },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  await loginAs(page, "admin", "admin", "admin");
  await page.goto("/#/provider-settings");
  const timeout = page.getByLabel("Provider timeout (seconds)");
  await expect(timeout).toHaveValue("141");
  await timeout.fill("60");
  const response = page.waitForResponse((r) => r.url().endsWith("/api-settings") && r.request().method() === "PUT");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect((await response).ok()).toBeTruthy();
  await page.reload();
  await expect(timeout).toHaveValue("60");
  await page.goto("/#/status");
  await expect(page.getByRole("row", { name: /Provider timeout/ })).toContainText("60 s");
  // Clear the optional override using the form, restoring deployment/default resolution.
  await page.goto("/#/provider-settings");
  await timeout.fill("");
  const cleared = page.waitForResponse((r) => r.url().endsWith("/api-settings") && r.request().method() === "PUT");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect((await cleared).ok()).toBeTruthy();
  await page.reload();
  await expect(timeout).toHaveValue("");
  await page.goto("/#/status");
  const status = await (await request.get("/api/v1/ops/status")).json();
  await expect(page.getByRole("row", { name: /Provider timeout/ })).toContainText(`${status.provider.timeout_seconds} s`);
});
