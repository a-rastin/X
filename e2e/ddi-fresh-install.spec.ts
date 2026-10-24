import { test, expect } from "@playwright/test";
import { acknowledgeWarning, ensurePhysician, loginAs, uniqueLetters, uniqueName, uniquePatientId } from "./helpers";

// Run with bash e2e/run-ddi-fresh-install.sh: make dev starts automatic setup
// from real bundled CSVs in a fresh PostgreSQL DB without generated LFS artifacts.
test("fresh install supports registration and a persistent real bundled DDI report", async ({ page, request }) => {
  test.setTimeout(90000);
  const username = uniqueName("freshddi");
  await ensurePhysician(request, username, "secret123");
  await loginAs(page, "physician", username, "secret123");
  await acknowledgeWarning(page);
  await page.goto("/#/patients");
  await page.getByRole("link", { name: "Register new patient" }).click();
  await page.getByLabel("Given name", { exact: true }).fill(uniqueLetters("given"));
  await page.getByLabel("Family name", { exact: true }).fill(uniqueLetters("family"));
  await page.getByLabel("Patient ID", { exact: true }).fill(uniquePatientId());
  await page.getByRole("radio", { name: "M", exact: true }).check();
  await page.getByLabel("Age", { exact: true }).fill("30");
  await page.getByRole("radio", { name: "First-time", exact: true }).check();
  await page.getByRole("button", { name: "Register patient", exact: true }).click();
  await expect(page).toHaveURL(/\/encounters\/[^#]+#register$/);
  const encounterId = page.url().match(/\/encounters\/([^#]+)/)![1];
  await page.locator("#wizard-step-medications").click();
  const catalog = page.locator("#meds-catalog-info");
  for (const text of ["DDInter supplied CSV exports", "ddi-c45e2051cf16", "1939 drugs"]) {
    await expect(catalog).toContainText(text);
  }
  await expect(page.locator("#meds-save")).toBeEnabled();
  await expect(page.locator("#ddi-pending")).toBeVisible();
  await expect(page.getByText("DDI dataset is unavailable.", { exact: true })).toHaveCount(0);

  for (const [id, name] of [["DDInter1", "Abacavir"], ["DDInter1263", "Naltrexone"]]) {
    await page.locator("#meds-search").fill(id);
    const row = page.locator("#meds-search-results li").filter({ hasText: `(${id})` });
    await expect(row).toContainText(name);
    await row.getByRole("button", { name: /^Add / }).click();
  }
  await expect(page.locator("#meds-selected-list")).toContainText("Abacavir");
  await expect(page.locator("#meds-selected-list")).toContainText("Naltrexone");
  await page.locator("#meds-save").click();
  await expect(page.locator("#ddi-report-status")).toContainText("Current report · dataset ddi-c45e2051cf16");
  await expect(page.locator("#ddi-report")).toContainText("highest known severity: Moderate");
  await expect(page.locator("#ddi-report")).toContainText("Mechanism and management details were not supplied");
  await page.locator("#ddi-report-pair-0 summary").click();
  await expect(page.locator("#ddi-report-pair-0")).toContainText("ddinter_downloads_code_A.csv");
  await expect(page.locator("#ddi-report-pair-0")).toContainText("record 1");

  // The browser's authenticated request context proves API persistence too.
  const medications = await page.request.get(`/api/v1/encounters/${encounterId}/medications`);
  expect(medications.status(), await medications.text()).toBe(200);
  expect(await medications.json()).toMatchObject({
    dataset_version: "ddi-c45e2051cf16",
    entries: [
      { catalog_drug_id: "DDInter1" },
      { catalog_drug_id: "DDInter1263" },
    ],
  });
  const report = await page.request.get(`/api/v1/encounters/${encounterId}/ddi-report`);
  expect(report.status(), await report.text()).toBe(200);
  expect(await report.json()).toMatchObject({
    report_status: "current",
    report: {
      dataset_version: "ddi-c45e2051cf16",
      resolved_medications: [
        { catalog_drug_id: "DDInter1", canonical_name: "Abacavir" },
        { catalog_drug_id: "DDInter1263", canonical_name: "Naltrexone" },
      ],
    },
  });
  await page.reload();
  for (const name of ["Abacavir", "Naltrexone"]) await expect(page.locator("#meds-selected-list")).toContainText(name);
  await expect(page.locator("#meds-catalog-info")).toContainText("ddi-c45e2051cf16");
  await expect(page.locator("#ddi-report-status")).toContainText("Current report · dataset ddi-c45e2051cf16");
  await expect(page.locator("#ddi-report")).toContainText("highest known severity: Moderate");
});
