import { createServer, type Server } from "node:http";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { test, expect, type Page, type Response } from "@playwright/test";
import {
  uniqueName,
  ensurePhysician,
  loginAs,
  acknowledgeWarning,
} from "./helpers";

/* S42 provider settings (seam T9). HTTP contract stays in
 * BT/http/test_api_settings.py. This file drives the admin form and the
 * physician route guard only.
 *
 * Selector contract (ProviderSettingsPage.tsx / ProviderSettingsRoutePage):
 * - admin nav "Provider" → #/provider-settings
 * - #provider-settings-heading, #provider-key-status, #provider-capability
 * - #provider-base-url, #provider-model, #provider-secret-action
 * - #provider-api-key (password, cleared after save)
 * - #provider-save, #provider-test
 * - #provider-affected-runs only when the save returned affected_runs
 * - #provider-diagnostic after a test (xi-status when verified, xi-form-error when failed)
 * Physician guard is a plain h2 plus role=alert, not those ids.
 *
 * ponytail: mkdir lock because both browser projects share one If-Match
 * revision. Per-project rows if settings ever stop being a singleton.
 */

const SENTINEL = "sk-e2e-s42-do-not-echo";
const VERIFIED = "Provider supports tools and JSON.";
const COULD_NOT_CONNECT = "Provider capability check could not connect.";
const LOCK_DIR = "/tmp/x-insight-s42-provider-settings.lock";

let holdingLock = false;

function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}

function releaseProviderLock(): void {
  if (!holdingLock) {
    return;
  }
  holdingLock = false;
  rmSync(LOCK_DIR, { recursive: true, force: true });
}

async function acquireProviderLock(): Promise<void> {
  const deadline = Date.now() + 180_000;
  for (;;) {
    try {
      mkdirSync(LOCK_DIR);
      writeFileSync(`${LOCK_DIR}/pid`, String(process.pid));
      holdingLock = true;
      process.once("exit", releaseProviderLock);
      return;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "EEXIST") {
        throw err;
      }
      let owner = 0;
      try {
        owner = Number(readFileSync(`${LOCK_DIR}/pid`, "utf8"));
      } catch {
        owner = 0;
      }
      if (!Number.isInteger(owner) || owner <= 0 || !pidAlive(owner)) {
        rmSync(LOCK_DIR, { recursive: true, force: true });
        continue;
      }
      if (Date.now() > deadline) {
        throw new Error("timed out waiting for the provider settings lock");
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
}

/** Local chat/tool endpoint. Drains the POST and does not log the
 * Authorization header or the body. First POST is a ping tool call; the
 * next is a JSON object. */
function startProbe(): Promise<{ server: Server; port: number }> {
  let step = 0;
  const server = createServer((req, res) => {
    req.on("data", () => undefined);
    req.on("end", () => {
      if (req.method !== "POST") {
        res.writeHead(405);
        res.end();
        return;
      }
      step += 1;
      const body =
        step === 1
          ? JSON.stringify({
              choices: [
                {
                  message: {
                    role: "assistant",
                    tool_calls: [
                      {
                        id: "call_ping",
                        type: "function",
                        function: { name: "ping", arguments: "{}" },
                      },
                    ],
                  },
                },
              ],
            })
          : JSON.stringify({
              choices: [
                { message: { role: "assistant", content: '{"ready":true}' } },
              ],
            });
      res.writeHead(200, {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      });
      res.end(body);
    });
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        reject(new Error("capability probe did not bind a port"));
        return;
      }
      resolve({ server, port: address.port });
    });
  });
}

function closeProbe(server: Server): Promise<void> {
  server.closeAllConnections();
  return new Promise((resolve, reject) => {
    server.close((err) => (err ? reject(err) : resolve()));
  });
}

function readObject(body: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(body);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("settings response was not an object");
  }
  return parsed as Record<string, unknown>;
}

async function readMutation(
  page: Page,
  method: "PUT" | "POST",
): Promise<Record<string, unknown>> {
  const pending = page.waitForResponse((res: Response) => {
    const path = new URL(res.url()).pathname;
    if (method === "PUT") {
      return res.request().method() === "PUT" && path === "/api/v1/api-settings";
    }
    return res.request().method() === "POST" && path === "/api/v1/api-settings/test";
  });
  await page.locator(method === "PUT" ? "#provider-save" : "#provider-test").click();
  const response = await pending;
  const body = await response.text();
  expect(body, `${method} ${response.status()}`).not.toContain(SENTINEL);
  expect(response.status(), body).toBe(200);
  return readObject(body);
}

async function expectNoSentinel(page: Page): Promise<void> {
  await expect(page.locator("#provider-api-key")).toHaveValue("");
  await expect.poll(async () => page.content()).not.toContain(SENTINEL);
}

test("admin saves, tests, and clears provider settings without echoing the key", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await acquireProviderLock();
  let probe: { server: Server; port: number } | undefined;
  try {
    probe = await startProbe();
    const model = `s42-e2e-${uniqueName("m")}`;
    const liveUrl = `http://127.0.0.1:${probe.port}/v1/chat/completions`;
    const deadUrl = "http://127.0.0.1:9/v1/chat/completions";

    const login = page.waitForResponse(
      (res) =>
        res.request().method() === "POST" &&
        new URL(res.url()).pathname === "/api/v1/auth/login",
    );
    await loginAs(page, "admin", "admin", "admin");
    expect((await login).status(), "admin login").toBe(200);
    await expect(
      page.getByRole("heading", { name: "Administrator dashboard" }),
    ).toBeVisible({ timeout: 15000 });

    // A cookie-authenticated tab with no client token must recover the token
    // through GET /me before the provider mutation.
    await page.evaluate(() => localStorage.removeItem("xi_csrf_token"));
    await page.reload();
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem("xi_csrf_token") ?? ""))
      .not.toBe("");

    await page.getByRole("link", { name: "Provider", exact: true }).click();
    await expect(page).toHaveURL(/#\/provider-settings$/);
    await expect(page.locator("#provider-settings-heading")).toHaveText(
      "Provider settings",
      { timeout: 15000 },
    );
    await expect(page.getByText("Saving does not verify the provider.")).toBeVisible();
    await expect(page.locator("#provider-key-status")).toHaveText(
      /^Key status: (configured|absent)$/,
      { timeout: 15000 },
    );
    await expect(page.locator("#provider-capability")).toHaveText(
      /^Capability: (untested|verified|failed)$/,
    );
    await expect(page.locator("#provider-api-key")).toHaveAttribute("type", "password");
    await expect(page.locator("#provider-api-key")).toHaveValue("");
    const hasSavedRevision = (await page.locator("#provider-secret-action").count()) === 1;
    if (hasSavedRevision) {
      const secretValues = await page
        .locator("#provider-secret-action option")
        .evaluateAll((nodes) => nodes.map((node) => (node as HTMLOptionElement).value));
      expect(secretValues).toEqual(["unchanged", "replace", "clear"]);
    }
    await expectNoSentinel(page);

    await page.locator("#provider-base-url").fill(liveUrl);
    await page.locator("#provider-model").fill(model);
    if (hasSavedRevision) {
      await page.locator("#provider-secret-action").selectOption("replace");
    }
    await page.locator("#provider-api-key").fill(SENTINEL);
    const replaced = await readMutation(page, "PUT");
    expect(replaced.api_key).toBe("configured");
    expect(replaced.capability).toBe("untested");
    if (hasSavedRevision) {
      expect(typeof replaced.affected_runs).toBe("number");
    } else {
      expect(replaced).not.toHaveProperty("affected_runs");
    }
    await expect(page.locator("#provider-key-status")).toHaveText("Key status: configured");
    await expect(page.locator("#provider-capability")).toHaveText("Capability: untested");
    if (hasSavedRevision) {
      await expect(page.locator("#provider-affected-runs")).toHaveText(
        /^Affected runs: \d+$/,
      );
    } else {
      await expect(page.locator("#provider-affected-runs")).toHaveCount(0);
    }
    await expectNoSentinel(page);

    const verified = await readMutation(page, "POST");
    expect(verified.capability).toBe("verified");
    expect(verified.diagnostic).toBe(VERIFIED);
    expect(String(verified.diagnostic)).not.toContain(SENTINEL);
    await expect(page.locator("#provider-capability")).toHaveText("Capability: verified");
    await expect(page.locator("#provider-diagnostic")).toHaveText(VERIFIED);
    await expect(page.locator("#provider-diagnostic")).toHaveClass(/xi-status/);
    await expectNoSentinel(page);

    await page.locator("#provider-base-url").fill(deadUrl);
    await page.locator("#provider-secret-action").selectOption("unchanged");
    const kept = await readMutation(page, "PUT");
    expect(kept.capability).toBe("untested");
    expect(kept).not.toHaveProperty("affected_runs");
    await expect(page.locator("#provider-capability")).toHaveText("Capability: untested");
    await expect(page.locator("#provider-affected-runs")).toHaveCount(0);
    await expect(page.locator("#provider-diagnostic")).toHaveCount(0);
    await expectNoSentinel(page);

    const failed = await readMutation(page, "POST");
    expect(failed.capability).toBe("failed");
    expect(failed.diagnostic).toBe(COULD_NOT_CONNECT);
    expect(String(failed.diagnostic)).not.toContain(SENTINEL);
    await expect(page.locator("#provider-capability")).toHaveText("Capability: failed");
    await expect(page.locator("#provider-diagnostic")).toHaveText(COULD_NOT_CONNECT);
    await expect(page.locator("#provider-diagnostic")).toHaveClass(/xi-form-error/);
    await expectNoSentinel(page);

    await page.locator("#provider-secret-action").selectOption("clear");
    const cleared = await readMutation(page, "PUT");
    expect(cleared.api_key).toBe("absent");
    expect(cleared.capability).toBe("untested");
    expect(typeof cleared.affected_runs).toBe("number");
    await expect(page.locator("#provider-key-status")).toHaveText("Key status: absent");
    await expect(page.locator("#provider-affected-runs")).toHaveText(
      /^Affected runs: \d+$/,
    );
    await expectNoSentinel(page);
  } finally {
    if (probe !== undefined) {
      await closeProbe(probe.server);
    }
    releaseProviderLock();
  }
});

test("admin cannot test provider settings before the first save", async ({ page }) => {
  const login = page.waitForResponse(
    (res) =>
      res.request().method() === "POST" &&
      new URL(res.url()).pathname === "/api/v1/auth/login",
  );
  await loginAs(page, "admin", "admin", "admin");
  expect((await login).status(), "admin login").toBe(200);

  await page.route("**/api/v1/api-settings", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({
        code: "NOT_FOUND",
        message: "Provider settings are not configured.",
      }),
    }),
  );
  await page.getByRole("link", { name: "Provider", exact: true }).click();
  await expect(page.locator("#provider-key-status")).toHaveText("Key status: absent");
  await expect(page.locator("#provider-test")).toBeDisabled();

  await page.locator("#provider-base-url").fill("https://provider.example/v1");
  await page.locator("#provider-model").fill("example-model");
  await page.locator("#provider-api-key").fill(SENTINEL);
  await expect(page.locator("#provider-test")).toBeDisabled();
});

test("physician is refused provider settings", async ({ page, request }) => {
  const username = uniqueName("s42phys");
  await ensurePhysician(request, username, "secret123");
  const login = page.waitForResponse(
    (res) =>
      res.request().method() === "POST" &&
      new URL(res.url()).pathname === "/api/v1/auth/login",
  );
  await loginAs(page, "physician", username, "secret123");
  expect((await login).status(), "physician login").toBe(200);
  await acknowledgeWarning(page);
  await expect(page.getByRole("link", { name: "Provider", exact: true })).toHaveCount(0);
  await page.goto("/#/provider-settings");
  await expect(page.getByRole("heading", { name: "Provider settings" })).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByRole("alert")).toContainText("Administrator access required.");
  await expect(page.locator("#provider-settings-heading")).toHaveCount(0);
  await expect(page.locator("#provider-api-key")).toHaveCount(0);
});
