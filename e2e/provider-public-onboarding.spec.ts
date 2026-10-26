import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers";

// Run against provider-public-fixture.py + a dedicated fresh PostgreSQL database.
// The controlled external HTTPS fixture replaces OS DNS/routing only; application
// transport, TLS verification, browser auth/settings/CSRF/storage execute for real.
test("fresh public provider onboarding needs no host allowlist", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  await page.getByRole("link", { name: "Provider", exact: true }).click();
  await expect(page.locator("#provider-key-status")).toHaveText("Key status: absent");
  await expect(page.locator("#provider-secret-action")).toHaveCount(0);
  await expect(page.locator("#provider-test")).toBeDisabled();

  // A real rejected destination reaches the input-specific error presentation.
  await page.locator("#provider-base-url").fill("https://127.0.0.1");
  await page.locator("#provider-model").fill("fixture-model");
  await page.locator("#provider-api-key").fill("sk-public-browser-do-not-echo");
  const denied = page.waitForResponse((res) => res.request().method() === "PUT" && new URL(res.url()).pathname === "/api/v1/api-settings");
  await page.locator("#provider-save").click();
  expect((await denied).status()).toBe(422);
  await expect(page.locator("#provider-base-url-error")).toHaveText("Provider URL targets a blocked address.");
  await expect(page.locator("#provider-base-url")).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("#provider-test")).toBeDisabled();

  const endpoints = [
    ["https://provider-one.example", "https://provider-one.example/v1/chat/completions"],
    ["https://provider-two.example/v1", "https://provider-two.example/v1/chat/completions"],
    ["https://provider-one.example/v1/chat/completions", "https://provider-one.example/v1/chat/completions"],
    ["https://provider-two.example/custom/chat", "https://provider-two.example/custom/chat"],
  ];
  for (const [index, [url, endpoint]] of endpoints.entries()) {
    await page.locator("#provider-base-url").fill(url);
    await page.locator("#provider-model").fill(`fixture-model-${index}`);
    await expect(page.locator("#provider-local-approval")).not.toBeChecked();
    await expect(page.locator("#provider-proxy-approval")).not.toBeChecked();
    if (index > 0) await page.locator("#provider-secret-action").selectOption("replace");
    await page.locator("#provider-api-key").fill("sk-public-browser-do-not-echo");
    const saving = page.waitForResponse((res) => res.request().method() === "PUT" && new URL(res.url()).pathname === "/api/v1/api-settings");
    await page.locator("#provider-save").click();
    const saved = await saving;
    expect(saved.status(), await saved.text()).toBe(200);
    expect(await saved.json()).toMatchObject({ api_key: "configured", capability: "untested", local_endpoint_approved: false, trusted_proxy_approved: false });
    await expect(page.locator("#provider-api-key")).toHaveValue("");
    await expect(page.locator("#provider-key-status")).toHaveText("Key status: configured");
    await expect(page.locator("#provider-effective-endpoint")).toHaveText(`Saved endpoint: ${endpoint}`);
    await page.reload();
    await expect(page.locator("#provider-base-url")).toHaveValue(endpoint);
    await expect(page.locator("#provider-api-key")).toHaveValue("");
    await page.locator("#provider-test").click();
    await expect(page.locator("#provider-capability")).toHaveText("Capability: verified");
    await expect(page.locator("#provider-diagnostic")).toHaveText("Provider supports tools and JSON.");
    expect(await page.content()).not.toContain("sk-public-browser-do-not-echo");
  }

  await page.locator("#provider-secret-action").selectOption("clear");
  await page.locator("#provider-save").click();
  await expect(page.locator("#provider-key-status")).toHaveText("Key status: absent");
  await expect(page.locator("#provider-api-key")).toHaveValue("");
});
