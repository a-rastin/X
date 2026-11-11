import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
const require = createRequire(
  new URL("../../../package.json", import.meta.url),
);
const { chromium } = require("@playwright/test");
const { default: AxeBuilder } = require("@axe-core/playwright");
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const network = [];
page.on("request", (r) => {
  if (/^https?:/.test(r.url())) network.push(r.url());
});
const results = [];
mkdirSync("verification", { recursive: true });
try {
  await page.goto(new URL("wireframe.html", import.meta.url).href);
  for (const width of [1440, 728, 390]) {
    await page.setViewportSize({ width, height: 1100 });
    for (const view of [
      "patients",
      "assessment",
      "medications",
      "proposal",
      "chart",
      "admin",
      "login",
    ]) {
      await page
        .getByRole("combobox", { name: "Preview screen", exact: true })
        .selectOption(view);
      await page.locator(".product-frame h1").waitFor();
      const overflow = await page.evaluate(
        () => document.body.scrollWidth > innerWidth + 1,
      );
      results.push({ width, view, overflow });
      if (overflow) throw new Error(`Page overflow: ${width} ${view}`);
      if (width === 1440)
        await page.screenshot({
          path: `verification/${view}.png`,
          fullPage: true,
        });
    }
  }
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page
    .getByRole("combobox", { name: "Preview screen", exact: true })
    .selectOption("patients");
  await page
    .getByRole("textbox", { name: "Search by name or patient ID" })
    .fill("DEMO-001");
  if ((await page.locator("tbody tr").count()) !== 1)
    throw new Error("Patient search failed");
  await page
    .getByRole("textbox", { name: "Search by name or patient ID" })
    .fill("absent-record");
  await page
    .getByRole("heading", { name: "No matching patients", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await page
    .getByText("Preview loading and error states", { exact: true })
    .click();
  await page
    .getByRole("button", { name: "Connection error", exact: true })
    .click();
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Preview screen", exact: true })
    .selectOption("assessment");
  await page.locator('input[name=P4][value="3"]').check();
  if (!(await page.locator('input[name=P4][value="3"]').isChecked()))
    throw new Error("Rating selection failed");
  for (const view of ["patients", "assessment", "proposal", "login"]) {
    await page
      .getByRole("combobox", { name: "Preview screen", exact: true })
      .selectOption(view);
    const violations = (await new AxeBuilder({ page }).analyze()).violations;
    if (violations.length)
      throw new Error(
        `Accessibility findings on ${view}: ${violations.map((v) => v.id).join(", ")}`,
      );
    results.push({
      view,
      axe: violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        nodes: v.nodes.map((n) => n.target),
      })),
    });
  }
  await page.getByRole("button", { name: "Dark", exact: true }).click();
  await page.getByRole("button", { name: "فارسی", exact: true }).click();
  for (const width of [1440, 728, 390]) {
    await page.setViewportSize({ width, height: 1100 });
    for (const view of [
      "patients",
      "assessment",
      "proposal",
      "admin",
      "login",
    ]) {
      await page
        .getByRole("combobox", { name: "Preview screen", exact: true })
        .selectOption(view);
      const overflow = await page.evaluate(
        () => document.body.scrollWidth > innerWidth + 1,
      );
      results.push({ width, view, mode: "dark-fa", overflow });
      if (overflow) throw new Error(`Persian overflow: ${width} ${view}`);
      if (
        width === 1440 &&
        ["patients", "assessment", "proposal", "login"].includes(view)
      ) {
        const violations = (await new AxeBuilder({ page }).analyze())
          .violations;
        if (violations.length)
          throw new Error(
            `Dark Persian accessibility: ${view}: ${violations.map((v) => v.id).join(", ")}`,
          );
        results.push({ view, mode: "dark-fa", axe: [] });
      }
      if (width === 1440 && view === "assessment")
        await page.screenshot({
          path: "verification/persian-dark.png",
          fullPage: true,
        });
    }
  }
  results.push({ pageErrors: errors, externalRequests: network });
  if (errors.length || network.length)
    throw new Error("Runtime errors or external requests");
  writeFileSync(
    "verification/results.json",
    JSON.stringify(results, null, 2) + "\n",
  );
  console.log(JSON.stringify(results, null, 2));
} finally {
  await browser.close();
}
