import { expect, test, type APIResponse, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { acknowledgeWarning, ensurePhysician, loginAs, uniqueName, uniquePatientId } from "./helpers";
import { message } from "../web/src/shared/messages";
import type { SimulationRequest, SimulationResponse } from "../web/src/features/reasoning-explorer/types";

async function json(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

async function openProposal(page: Page, workflow: "new-case" | "follow-up") {
  const api = page.request;
  const { csrf_token } = await json(await api.get("/api/v1/me"));
  const headers = { "X-CSRF-Token": csrf_token };
  const { registration_year } = await json(await api.get("/api/v1/new-case/config"));
  const followUp = workflow === "follow-up";
  const patient = await json(await api.post("/api/v1/patients", {
    headers,
    data: {
      identifier: uniquePatientId(), given_name: "Test", family_name: "Patient",
      sex: followUp ? "M" : "F", birth_year: registration_year - 30,
      clinical_status: followUp ? "established" : "first_time",
    },
  }));
  let id = patient.draft.id;
  const revision = (value: number) => ({
    ...headers, "If-Match": `"${value}"`, "Idempotency-Key": randomUUID(),
  });
  if (followUp) {
    await json(await api.post(`/api/v1/encounters/${id}/discard`, {
      headers: revision(1), data: { confirm: true },
    }));
    const opened = await json(await api.post(`/api/v1/patients/${patient.patient.id}/encounters`, {
      headers, data: { kind: "follow_up" },
    }));
    id = opened.encounter.id;
  }
  const url = `/api/v1/encounters/${id}`;
  const draft = await json(await api.get(url));
  const data = draft.draft_data;
  if (followUp) {
    const config = await json(await api.get("/api/v1/follow-up/config"));
    const main = config.formulations.find((entry: { id: string }) => entry.id === "oral:olanzapine").catalog_drug_id;
    const fu = data.follow_up;
    const history = fu.history;
    history.psychiatric.present = false;
    history.substances.present = false;
    history.heart_failure = false;
    history.tobacco = false;
    history.bmi = { ...history.bmi, source: "manual", manual_category: "normal" };
    for (const key of Object.keys(history.neurological)) history.neurological[key] = false;
    for (const key of Object.keys(history.conditions)) history.conditions[key].manual = false;
    data.medications.entries = [{ catalog_drug_id: main }];
    data.medications.selection_dataset_version = fu.content_pins.lexicomp_version;
    fu.treatment = { main_catalog_drug_id: main, formulation: "oral" };
    Object.assign(fu.considerations, {
      recovery: "controlled", adherence: true, lai_preference: false,
      seizure_risk: false, qtc_risk: false,
    });
    Object.assign(fu.branches, { switch_needed: false, dose_reduction_possible: false });
    for (const key of Object.keys(fu.effects)) fu.effects[key] = key === "sedation" ? "none" : false;
  } else {
    Object.assign(data.new_case.bmi, { source: "manual", manual_category: "normal" });
  }
  const saved = await json(await api.patch(url, {
    headers: revision(draft.revision), data: { draft_data: data },
  }));
  const ready = followUp ? saved : await json(
    await api.post(`${url}/new-case/diagnosis-confirmation`, {
      headers: revision(saved.revision), data: { confirm: true },
    }),
  );
  await json(await api.post(`${url}/${workflow}/proposals`, {
    headers: revision(ready.revision), data: {},
  }));
  await page.goto(`/#/encounters/${id}#proposal`);
  await page.reload();
  await expect(page.getByRole("region", { name: "Simulation comparison" })).toBeVisible();
  return url;
}

test.describe("reasoning explorer", () => {
  test.use({ reducedMotion: "reduce" });
  test.beforeEach(async ({ page, request }) => {
    const username = uniqueName("reasoning");
    await ensurePhysician(request, username, "secret123");
    await loginAs(page, "physician", username, "secret123");
    await acknowledgeWarning(page);
  });

  test("keeps patient marks and model reference fixed through changes, failure and reset", async ({ page }) => {
    test.setTimeout(90_000);
    const url = await openProposal(page, "new-case");
    const explorer = page.getByRole("region", { name: "What-if simulation" });
    const banner = explorer.getByRole("region", { name: "Simulation comparison" });
    const heading = (name: string | RegExp) => banner.getByRole("heading", { name });
    const chart = explorer.getByRole("region", { name: "Treatment probabilities" });
    const graph = explorer.getByRole("region", { name: "Pathway diagram" });
    const reference = explorer.getByText(/^Model reference ·/);
    const reset = explorer.getByRole("button", { name: "Reset to Patient Data" });
    const draft = page.locator("#nc-final-plan");
    const endpoint = `${url}/reasoning/simulate`;
    const changes = (body: SimulationRequest) => body.changes;
    const snapshot = await json(await page.request.get(url));
    const text = await draft.inputValue();
    const ticks = () => chart.locator(".xi-patient-tick").evaluateAll((marks) => marks.map((mark) => ({
      x: Number(mark.getAttribute("x1")), dash: mark.getAttribute("stroke-dasharray"),
    })));
    const widths = () => chart.locator(".xi-scenario-bar").evaluateAll((marks) =>
      marks.map((mark) => Number(mark.getAttribute("width"))));
    await expect(heading("No output change")).toBeVisible();
    await chart.getByRole("button", { name: "Show all treatments" }).click();
    await expect(chart.locator(".xi-patient-tick")).toHaveCount(22);
    const patient = await ticks();
    expect(patient.every(({ dash }) => dash === "1 3")).toBe(true);
    const baselineBars = await widths();
    baselineBars.forEach((width, index) => expect(width + 2).toBeCloseTo(patient[index]!.x, 8));

    await explorer.getByText("PANSS", { exact: true }).click();
    const p1 = explorer.getByRole("combobox", { name: /^P1\. Delusions:/ });
    await p1.selectOption({ label: "6" });
    await expect(heading("Assessment changed")).toBeVisible();
    await expect(banner.getByText("Treatment ranking unchanged")).toBeVisible();
    await expect(graph.getByRole("button", { name: /^PANSS ·/ })).toBeVisible();
    expect(await widths()).toEqual(baselineBars);

    const treatment = chart.getByRole("button", { name: "Aripiprazole", exact: true });
    await treatment.click();
    const tornado = explorer.getByRole("region", { name: "Factor attribution · Aripiprazole" });
    await expect(tornado).toBeVisible();
    const modelReference = await reference.innerText();
    const factor = tornado.getByRole("button", { name: "QTc prolongation risk", exact: true });
    if (!(await factor.count())) await tornado.getByRole("button", { name: /Show zero contributions/ }).click();
    await factor.focus();
    const node = graph.getByRole("button", { name: /^QTc prolongation risk ·/ });
    await expect(node).toHaveAttribute("aria-pressed", "true");
    const qtc = explorer.getByRole("combobox", { name: "QT/QTc prolongation risk" });
    await expect(qtc).toHaveAttribute("aria-describedby", /linked/);

    const output = page.waitForResponse((response) =>
      response.url().endsWith(endpoint) && changes(response.request().postDataJSON()).length === 2);
    await qtc.selectOption({ label: "Yes" });
    const result: SimulationResponse = await (await output).json();
    await expect(banner.getByText("Combined scenario inputs")).toBeVisible();
    await expect(heading("Top eligible treatment changed")).toBeVisible();
    await expect(banner.getByText(/Equal top probability/).first()).toBeVisible();
    await expect(reference).toHaveText(modelReference);
    await expect(treatment).toHaveAttribute("aria-pressed", "true");
    expect(await ticks()).toEqual(patient);
    expect(await widths()).not.toEqual(baselineBars);
    const axisOrdered = [...result.comparison_rows].sort((a, b) => a.treatment_id.localeCompare(b.treatment_id, "en"));
    (await widths()).forEach((width, index) => {
      const row = axisOrdered[index]!;
      expect(width).toBeCloseTo(100 * row.scenario_probability!, 8);
      expect(patient[index]!.x).toBeCloseTo(2 + 100 * row.patient_probability!, 8);
    });
    await tornado.getByText("Table of values", { exact: true }).click();
    await expect(tornado.getByRole("table").getByRole("row")).toHaveCount(17);

    await page.route(`**${endpoint}`, (route) => route.fulfill({ status: 503 }), { times: 1 });
    await p1.selectOption({ label: "5" });
    await expect(explorer.getByText("Update failed. The previous result is shown.")).toBeVisible();
    await expect(reference).toHaveText(modelReference);
    await expect(banner.getByText("Combined scenario inputs")).toBeVisible();

    let release!: () => void;
    const pause = new Promise<void>((resolve) => { release = resolve; });
    let received!: () => void;
    const arrived = new Promise<void>((resolve) => { received = resolve; });
    const held = page.route(`**${endpoint}`, async (route) => {
      const response = await route.fetch();
      received();
      await pause;
      await route.fulfill({ response });
    }, { times: 1 });
    await held;
    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET" && request.url().includes(url)) writes.push(request.url());
    });
    try {
      await p1.selectOption({ label: "4" });
      await arrived;
      await reset.click();
      await expect(heading("No output change")).toBeVisible();
    } finally {
      release();
    }
    await page.unrouteAll({ behavior: "wait" });
    await expect(heading("No output change")).toBeVisible();
    expect(await widths()).toEqual(baselineBars);
    expect(await ticks()).toEqual(patient);
    expect(writes).toEqual([expect.stringContaining(endpoint)]);
    await expect(draft).toHaveValue(text);
    const after = await json(await page.request.get(url));
    expect(after.revision).toBe(snapshot.revision);
    expect(after.draft_data).toEqual(snapshot.draft_data);

    await page.getByRole("button", { name: "Switch to Persian" }).click();
    await page.getByRole("button", { name: message("fa", "switchDark") }).click();
    await page.setViewportSize({ width: 360, height: 860 });
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const rtlChart = page.getByRole("region", { name: "احتمال‌های درمان" });
    await expect(rtlChart.locator("svg").first()).toHaveCSS("direction", "ltr");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  });

  test("follow-up shows a partial branch and a direct treatment, then restores the baseline", async ({ page }) => {
    test.setTimeout(90_000);
    await openProposal(page, "follow-up");
    const explorer = page.getByRole("region", { name: "What-if simulation" });
    const banner = explorer.getByRole("region", { name: "Simulation comparison" });
    const graph = explorer.getByRole("region", { name: "Pathway diagram" });
    await explorer.getByText("Clinical factors and considerations", { exact: true }).click();
    await explorer.getByText("C-SSRS", { exact: true }).click();
    const ideation = explorer.getByRole("combobox", { name: /^Ideation 4 / });
    await ideation.selectOption({ label: "Yes" });
    const recovery = explorer.getByRole("combobox", { name: "Recovery", exact: true });
    await recovery.selectOption({ label: "No improvement / worse" });
    await expect(banner.getByRole("heading", { name: "Decision branch changed" })).toBeVisible();
    await expect(banner.getByText("Some scenario outputs still need answers.")).toBeVisible();
    await expect(graph.locator("path[data-path-state=patient]").first()).toBeAttached();
    await expect(graph.locator("path[data-path-state=scenario]").first()).toBeAttached();
    await explorer.getByRole("button", { name: /^Revert — Ideation 4/ }).click();
    await explorer.getByRole("combobox", { name: "Are treatment-resistance criteria present?" })
      .selectOption({ label: "Yes" });
    await expect(banner.getByText(/Direct treatment ·/)).toBeVisible();
    await expect(explorer.locator(".xi-scenario-bar")).toHaveCount(0);
    await explorer.getByRole("button", { name: "Reset to Patient Data" }).click();
    await expect(banner.getByRole("heading", { name: "No output change" })).toBeVisible();
    await expect(recovery.locator("option:checked")).toHaveText("Controlled / remission");
  });
});
