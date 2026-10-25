import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { uniqueName, uniqueLetters, uniquePatientId, ensurePhysician, physicianSession, loginAs, acknowledgeWarning } from "./helpers";

// T9 browser + T1 public HTTP. Only historical fixture arrangement writes the
// isolated database. Complete proposals use the real worker/MCP/inference
// pipeline with a local deterministic provider; sign/addenda are never mocked.
test.setTimeout(120_000);
test.skip(!process.env.E2E_ESTABLISHED_DATABASE_URL, "Requires the dedicated isolated established-case browser fixture database");
const prefix = "candidate/history/established_case_clozapine.v1/";
const definition = JSON.parse(readFileSync("content/history/history.established_case_clozapine.v1.json", "utf8")) as {
  fields: { source_path: string; definition: string; permitted: string[] }[];
};

function fixture(mode: string, ...args: string[]): void {
  expect(process.env.E2E_ESTABLISHED_DATABASE_URL, "Explicit isolated fixture database required").toBeTruthy();
  execFileSync("backend/.venv/bin/python", ["e2e/established_seed.py", mode, ...args], { timeout: 60_000, stdio: "pipe" });
}

async function patient(page: Page, request: APIRequestContext) {
  const physician = { username: uniqueName("e2ecloz"), password: "secret123" };
  await ensurePhysician(request, physician.username, physician.password);
  await loginAs(page, "physician", physician.username, physician.password);
  await acknowledgeWarning(page);
  const csrf = await physicianSession(request, physician);
  const response = await request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": uniqueName("clozpatient") },
    data: { identifier: uniquePatientId(), given_name: uniqueLetters("given"), family_name: uniqueLetters("family"), sex: "F", age: 30, clinical_status: "first_time" },
  });
  expect(response.status(), await response.text()).toBe(201);
  const body = await response.json();
  return { physician, patientId: body.patient.id as string, draftId: body.draft.id as string };
}

async function history(page: Page, encounter: string): Promise<void> {
  await page.goto(`/#/encounters/${encounter}#history`);
  await expect(page.locator("#history-phone-update")).toBeEnabled({ timeout: 20_000 });
}

async function sign(page: Page, request: APIRequestContext, encounter: string, username: string, password: string, inclusion: "yes" | "unknown") {
  fixture("prepare", encounter, username, password);
  await page.goto(`/#/encounters/${encounter}#proposal-review`);
  await expect(page.getByTestId("sign-button")).toBeEnabled({ timeout: 20_000 });
  await expect(page.getByTestId("secondary-plan-clozapine-unknown")).toBeChecked();
  // One shared plan fence persists every tri-state choice through reload.
  for (const choice of ["yes", "no", "unknown"] as const) {
    await page.getByTestId(`secondary-plan-clozapine-${choice}`).check();
    if (choice !== "unknown" || await page.getByTestId("secondary-plan-save").isEnabled()) {
      await page.getByTestId("secondary-plan-save").click();
    }
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision");
    await page.reload();
    await expect(page.getByTestId(`secondary-plan-clozapine-${choice}`)).toBeChecked();
  }
  await page.getByTestId("secondary-plan-textarea").fill("Synthetic final treatment plan for browser verification.");
  await page.getByTestId(`secondary-plan-clozapine-${inclusion}`).check();
  // Sign immediately, without explicit Save or waiting for debounce.
  const signed = page.waitForResponse((response) => response.url().endsWith(`/encounters/${encounter}/sign`) && response.request().method() === "POST");
  await page.getByTestId("sign-button").click();
  const response = await signed;
  expect(response.status(), await response.text()).toBe(200);
  const frozen = (await response.json()).snapshot;
  expect(frozen.snapshot.secondary_plan.clozapine_included).toBe(inclusion);
  expect(frozen.secondary_plan_text).toBe("Synthetic final treatment plan for browser verification.");
  await expect(page.getByTestId("sign-status")).toContainText("Signed ✓");
  return frozen;
}

test("registration resumes all fifteen legacy answers without controls, navigation or hidden readiness blockers", async ({ page, request }) => {
  const { draftId } = await patient(page, request);
  fixture("legacy", draftId);
  await history(page, draftId);
  for (const reloaded of [false, true]) {
    if (reloaded) { await page.reload(); await expect(page.locator("#history-phone-update")).toBeEnabled(); }
    await expect(page.locator("#history-workflow-established_case_clozapine")).toHaveCount(0);
    await expect(page.locator(`select[id^="hist-${prefix}"]`)).toHaveCount(0);
    await expect(page.locator(`a[href*="established_case_clozapine"]`)).toHaveCount(0);
    const preview = await (await request.get(`/api/v1/encounters/${draftId}/history`)).json();
    expect(preview.workflow_fields.some((field: { id: string }) => field.id.startsWith(prefix))).toBe(false);
    expect(preview.proposal_readiness.blockers.some((blocker: { source_path: string }) => blocker.source_path.startsWith(prefix))).toBe(false);
    expect(Object.keys(preview.values).some((id) => id.startsWith(prefix))).toBe(false);
    expect(Object.keys(preview.provenance).some((id) => id.startsWith(prefix))).toBe(false);
  }
  await page.locator("#history-phone-update").fill("Synthetic contact update");
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision", { timeout: 15_000 });
  await page.reload();
  await expect(page.locator("#history-phone-update")).toHaveValue("Synthetic contact update");
});

test("immediate final-plan Yes signing enables exact fifteen questions before no-improvement with saved answers", async ({ page, request }) => {
  const { physician, patientId, draftId } = await patient(page, request);
  const frozen = await sign(page, request, draftId, physician.username, physician.password, "yes");
  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId(`signed-view-clozapine-${draftId}`)).toContainText("Yes");
  await page.getByTestId("chart-create-followup").click();
  await expect(page).toHaveURL(/#\/encounters\//);
  const followup = page.url().split("#/encounters/")[1]!.split("#")[0]!;
  await history(page, followup);
  const section = page.locator("section[aria-labelledby='history-workflow-established_case_clozapine']");
  await expect(section.locator("select")).toHaveCount(15);
  for (const field of definition.fields) {
    const control = page.locator(`[id="hist-${field.source_path}"]`);
    await expect(page.getByLabel(field.definition, { exact: true })).toBeVisible();
    const choices = await control.locator("option").evaluateAll((nodes) => nodes.map((node) => (node as HTMLOptionElement).value).filter(Boolean));
    expect(choices).toEqual(field.permitted);
  }
  const headings = await page.locator("[id^='history-workflow-']").evaluateAll((nodes) => nodes.map((node) => node.id));
  expect(headings.indexOf("history-workflow-established_case_clozapine") + 1).toBe(headings.indexOf("history-workflow-no_improvement_clozapine"));
  const first = definition.fields[0]!;
  await page.locator(`[id="hist-${first.source_path}"]`).selectOption(first.permitted[0]!);
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision", { timeout: 15_000 });
  await page.reload();
  await expect(page.locator(`[id="hist-${first.source_path}"]`)).toHaveValue(first.permitted[0]!);
  const chart = await (await request.get(`/api/v1/patients/${patientId}/chart`)).json();
  expect(chart.signed_snapshots[0].snapshot_hash).toBe(frozen.snapshot_hash);
});

test("Unknown signed plan stays hidden until real structured addendum; open History refreshes on focus", async ({ page, request }) => {
  const { physician, patientId, draftId } = await patient(page, request);
  const frozen = await sign(page, request, draftId, physician.username, physician.password, "unknown");
  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId(`signed-view-clozapine-${draftId}`)).toContainText("Unknown");
  await page.getByTestId("chart-create-followup").click();
  await expect(page).toHaveURL(/#\/encounters\//);
  const followup = page.url().split("#/encounters/")[1]!.split("#")[0]!;
  await history(page, followup);
  await expect(page.locator("#history-workflow-established_case_clozapine")).toHaveCount(0);
  const chartTab = await page.context().newPage();
  await chartTab.goto(`/#/patients/${patientId}/chart`);
  await chartTab.getByTestId(`addendum-textarea-${draftId}`).fill("Explicit confirmation of clozapine in the signed final plan.");
  await chartTab.getByTestId(`addendum-clozapine-${draftId}`).selectOption("yes");
  await chartTab.getByTestId(`addendum-submit-${draftId}`).click();
  await expect(chartTab.getByTestId(`addendum-list-${draftId}`)).toContainText("Clozapine included in final treatment plan: Yes");
  await expect(chartTab.getByTestId(`signed-view-clozapine-${draftId}`)).toContainText("Unknown");
  const chart = await (await request.get(`/api/v1/patients/${patientId}/chart`)).json();
  expect(chart.signed_snapshots[0].snapshot_hash).toBe(frozen.snapshot_hash);
  await page.bringToFront();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator(`select[id^="hist-${prefix}"]`)).toHaveCount(15);
  await chartTab.close();
});
