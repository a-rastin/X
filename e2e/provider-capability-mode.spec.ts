import { expect, test } from "@playwright/test";

test("provider mode selection shows detection and deployment override", async ({ page }) => {
  await page.route("**/api/v1/me", (route) => route.fulfill({ json: {
    user: { id: "synthetic-admin", username: "admin", role: "admin", theme: "light" },
    csrf_token: "synthetic-csrf", research_warning: null,
  } }));
  let saved = {
    revision: 1, api_key: "configured", base_url: "https://provider.example/v1",
    model: "fictional", timeout_seconds: null, capability: "untested",
    effective_endpoint: "https://provider.example/v1", local_endpoint_approved: false,
    trusted_proxy_approved: false, capability_mode: "schema", effective_capability_mode: "json",
    capability_override_active: true, detected_capability_mode: null as string | null,
  };
  await page.route("**/api/v1/api-settings**", async (route) => {
    const request = route.request();
    if (request.url().endsWith("/test")) {
      expect(request.headers()["if-match"]).toBe(`"${saved.revision}"`);
      saved = { ...saved, revision: saved.revision + 1, capability: "verified", detected_capability_mode: "schema" };
      await route.fulfill({ json: { ...saved, diagnostic: "Synthetic test passed." } });
    } else if (request.method() === "PUT") {
      const body = request.postDataJSON();
      expect(body.capability_mode).toBe("json");
      saved = { ...saved, revision: saved.revision + 1, capability_mode: body.capability_mode };
      await route.fulfill({ json: saved });
    } else {
      await route.fulfill({ json: saved });
    }
  });
  await page.goto("/#/provider-settings");
  await expect(page.getByLabel("Capability mode")).toHaveValue("schema");
  await expect(page.getByText(/deployment override.*json/i)).toBeVisible();
  await page.getByLabel("Capability mode").selectOption("json");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("button", { name: "Test provider" }).click();
  await expect(page.getByText(/detected.*schema/i)).toBeVisible();
  await expect(page.getByLabel("Capability mode")).toHaveValue("json");
  await page.reload();
  await expect(page.getByLabel("Capability mode")).toHaveValue("json");
  await expect(page.getByText(/detected provider mode: schema.*last test/i)).toBeVisible();
  await expect(page.getByText(/tool probe: verified/i)).toBeVisible();
  await expect(page.getByText(/deployment override.*json/i)).toBeVisible();
});
