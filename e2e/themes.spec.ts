import { test, expect } from "@playwright/test";
import { uniqueName, ensurePhysician, loginAs, acknowledgeWarning } from "./helpers";

async function themeOf(page: import("@playwright/test").Page): Promise<string | null> {
  return page.evaluate(() => document.documentElement.getAttribute("data-theme"));
}

async function canvasColor(page: import("@playwright/test").Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.body).backgroundColor);
}

test("theme toggle persists via /me/preferences across reload", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2etheme");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);

  await expect.poll(() => themeOf(page)).toBe("light");
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect.poll(() => themeOf(page)).toBe("dark");

  await page.reload();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect.poll(() => themeOf(page)).toBe("dark");
  await expect(
    page.getByRole("button", { name: "Switch to light theme" }),
  ).toBeVisible();

  // And back: persistence is a round-trip, not a one-way sticky flag.
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect.poll(() => themeOf(page)).toBe("light");
  await page.reload();
  await expect.poll(() => themeOf(page)).toBe("light");
});

test("both themes load with distinct canvas surfaces", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2etheme");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);

  await expect.poll(() => themeOf(page)).toBe("light");
  // Poll: the canvas background transitions over 180ms, so read past it.
  await expect.poll(() => canvasColor(page)).toBe("rgb(255, 255, 255)");

  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect.poll(() => themeOf(page)).toBe("dark");
  const darkCanvas = await canvasColor(page);
  expect(darkCanvas).not.toBe("rgb(255, 255, 255)");
  await expect.poll(() => canvasColor(page)).toBe("rgb(11, 18, 32)");
});

test("primary action uses the accessible darker teal fill on light theme", async ({
  page,
}) => {
  await page.goto("/");
  const fill = await page
    .getByRole("button", { name: "Sign in" })
    .evaluate((el) => getComputedStyle(el).backgroundColor);
  // #06786D: deliberate darker action fill (teal #0A9E8F on white is 3.33:1).
  expect(fill).toBe("rgb(6, 120, 109)");
});

test("theme toggle is keyboard-focusable, operable, and visibly focused", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2etheme");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);

  // Pure keyboard path: skip link, Dashboard, Account, theme toggle.
  await page.reload();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  // Wait for the authenticated header before tabbing: the toggle only
  // exists once GET /me has resolved.
  await expect(
    page.getByRole("heading", { name: "Physician dashboard" }),
  ).toBeVisible();
  const toggle = page.getByRole("button", { name: /switch to/i });
  await page.keyboard.press("Tab"); // skip link
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Tab"); // Dashboard
  await page.keyboard.press("Tab"); // Account
  await page.keyboard.press("Tab"); // theme toggle
  await expect(toggle).toBeFocused();

  const outlineWidth = await toggle.evaluate(
    (el) => getComputedStyle(el).outlineWidth,
  );
  expect(outlineWidth).not.toBe("0px");

  await page.keyboard.press("Enter");
  await expect.poll(() => themeOf(page)).toBe("dark");
  // The toggle re-labels on save and unlocks when the PATCH settles;
  // both must land before the next keypress.
  const toggleBack = page.getByRole("button", { name: "Switch to light theme" });
  await expect(toggleBack).toBeVisible();
  await expect(toggleBack).toBeEnabled();
  // Saving disables the button, which drops focus by design; restore it by
  // keyboard-equivalent focus before operating it again.
  await toggleBack.focus();
  await expect(toggleBack).toBeFocused();
  await page.keyboard.press("Enter");
  await expect.poll(() => themeOf(page)).toBe("light");
});

test("dark theme keeps an accessible primary-action token", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2etheme");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);

  const tokenOf = (): Promise<string> =>
    page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--primary-action")
        .trim(),
    );

  // Light: deliberate darker fill, white label = 5.36:1.
  await expect.poll(tokenOf).toBe("#06786d");
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  // Dark: bright fill, near-black label (#04211d on #2dd4bf = 9.09:1).
  await expect.poll(tokenOf).toBe("#2dd4bf");
});

test("reduced-motion kill-switch removes transitions and animation", async ({
  page,
}) => {
  await page.goto("/");
  const hits = await page.evaluate(() => {
    const found: string[] = [];
    for (const sheet of Array.from(document.styleSheets)) {
      let rules: CSSRuleList | null = null;
      try {
        rules = sheet.cssRules;
      } catch {
        continue; // cross-origin sheets are unreadable; ours is same-origin
      }
      for (const rule of Array.from(rules ?? [])) {
        if (
          rule instanceof CSSMediaRule &&
          rule.conditionText.includes("prefers-reduced-motion")
        ) {
          found.push(rule.cssText);
        }
      }
    }
    return found;
  });
  expect(hits.length).toBeGreaterThan(0);
  const text = hits.join("\n");
  expect(text).toMatch(/transition:\s*none/);
  // cssText expands the `animation` shorthand per browser (chromium yields
  // `animation: auto ease 0s 1 normal none running none`), so match the
  // disablement loosely rather than the authored literal.
  expect(text).toMatch(/animation:[^;}]*none/);
});

test("admin theme preference persists independently", async ({ page }) => {
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  const toggle = page.getByRole("button", { name: /switch to/i });
  const before = await themeOf(page);
  await toggle.click();
  const after = before === "dark" ? "light" : "dark";
  await expect.poll(() => themeOf(page)).toBe(after);
  await page.reload();
  await expect.poll(() => themeOf(page)).toBe(after);
  // Restore light for a stable starting point for other specs.
  if (after === "dark") {
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await expect.poll(() => themeOf(page)).toBe("light");
  }
});
