import { test, expect } from "@playwright/test";
import { uniqueName, ensurePhysician, loginAs, acknowledgeWarning } from "./helpers";

async function themeOf(page: import("@playwright/test").Page): Promise<string | null> {
  return page.evaluate(() => document.documentElement.getAttribute("data-theme"));
}

async function canvasColor(page: import("@playwright/test").Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.body).backgroundColor);
}

/** WCAG 2.x contrast of computed `color` on the nearest non-transparent background. */
async function contrastRatio(
  locator: import("@playwright/test").Locator,
): Promise<number> {
  return locator.evaluate((el) => {
    const parsed = (color: string): number[] | null => {
      const match = color.match(
        /rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)/,
      );
      if (!match) return null;
      return [
        Number(match[1]),
        Number(match[2]),
        Number(match[3]),
        match[4] === undefined ? 1 : Number(match[4]),
      ];
    };
    // Walk parents while background alpha is 0; stop at the first painted one.
    let node: Element | null = el;
    let bg: number[] | null = null;
    while (node) {
      const color = parsed(getComputedStyle(node).backgroundColor);
      if (color && color[3] !== 0) {
        bg = color;
        break;
      }
      node = node.parentElement;
    }
    if (!bg) bg = [255, 255, 255, 1];
    const fg = parsed(getComputedStyle(el).color);
    if (!fg) throw new Error(`unparsed color ${getComputedStyle(el).color}`);
    const channel = (c: number): number => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    const lum = (rgb: number[]): number =>
      0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
    const lighter = Math.max(lum(fg), lum(bg));
    const darker = Math.min(lum(fg), lum(bg));
    return (lighter + 0.05) / (darker + 0.05);
  });
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

  // Pure keyboard path: skip link, Dashboard, Patients, Account, theme toggle.
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
  await page.keyboard.press("Tab"); // Patients (S06 directory navigation)
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

test("S61 light-theme success and active text meets 4.5:1", async ({ page }) => {
  const username = uniqueName("e2econtrast");
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  // Admin preference persists; this case measures light theme only.
  if ((await themeOf(page)) !== "light") {
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await expect.poll(() => themeOf(page)).toBe("light");
  }

  await page.getByRole("link", { name: "Physicians" }).click();
  const form = page.getByRole("form", { name: "Create physician" });
  await form.locator("#create-username").fill(username);
  await form.locator("#create-password").fill("secret123");
  await form.getByRole("button", { name: "Create physician" }).click();
  const status = form.getByText("Physician created.");
  await expect(status).toBeVisible();

  // ?limit=100 can omit the new username. Any visible active badge is that surface.
  await expect(page.getByRole("table", { name: "Physicians" })).toBeVisible();
  const badge = page.locator(".xi-badge-active").first();
  if (!(await badge.isVisible())) {
    throw new Error(
      "the list cap hid every active badge, not a contrast failure",
    );
  }

  const statusRatio = await contrastRatio(status);
  const badgeRatio = await contrastRatio(badge);
  const detail = `light-theme contrast: status ${statusRatio.toFixed(2)}:1, active badge ${badgeRatio.toFixed(2)}:1`;
  expect(statusRatio, detail).toBeGreaterThanOrEqual(4.5);
  expect(badgeRatio, detail).toBeGreaterThanOrEqual(4.5);
});

test("S61 dark-theme success and active text meets 4.5:1", async ({ page }) => {
  const username = uniqueName("e2econtrast");
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  if ((await themeOf(page)) !== "dark") {
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
  }
  await expect.poll(() => themeOf(page)).toBe("dark");

  await page.getByRole("link", { name: "Physicians" }).click();
  const form = page.getByRole("form", { name: "Create physician" });
  await form.locator("#create-username").fill(username);
  await form.locator("#create-password").fill("secret123");
  await form.getByRole("button", { name: "Create physician" }).click();
  const status = form.getByText("Physician created.");
  await expect(status).toBeVisible();

  await expect(page.getByRole("table", { name: "Physicians" })).toBeVisible();
  const badge = page.locator(".xi-badge-active").first();
  if (!(await badge.isVisible())) {
    throw new Error(
      "the list cap hid every active badge, not a contrast failure",
    );
  }

  const statusRatio = await contrastRatio(status);
  const badgeRatio = await contrastRatio(badge);
  const detail = `dark-theme contrast: status ${statusRatio.toFixed(2)}:1, active badge ${badgeRatio.toFixed(2)}:1`;
  expect(statusRatio, detail).toBeGreaterThanOrEqual(4.5);
  expect(badgeRatio, detail).toBeGreaterThanOrEqual(4.5);

  // Restore light for a stable starting point for other specs.
  if ((await themeOf(page)) === "dark") {
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await expect.poll(() => themeOf(page)).toBe("light");
  }
});
