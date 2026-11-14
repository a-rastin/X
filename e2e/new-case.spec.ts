import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";
import {
  acknowledgeWarning,
  ensurePhysician,
  loginAs,
  uniqueName,
  uniquePatientId,
} from "./helpers";
import { message, type MessageKey } from "../web/src/shared/messages";

for (const language of ["en", "fa"] as const) {
  for (const lai of [false, true]) {
    test(`new case ${language} ${lai ? "LAI" : "oral"}: register, reload, edit and sign`, async ({
      page,
      request,
    }) => {
      test.setTimeout(120_000);
      const n = (key: string) => message(language, ("nc." + key) as MessageKey);
      const user = uniqueName("newcase");
      await ensurePhysician(request, user, "secret123");
      await loginAs(page, "physician", user, "secret123");
      await acknowledgeWarning(page);
      await page.goto("/#/patients/new");
      // A fresh document starts in English, so switch after navigation.
      if (language === "fa")
        await page.getByRole("button", { name: "Switch to Persian" }).click();
      await page.locator("#reg-given-name").fill("علی");
      await page.locator("#reg-family-name").fill("رضایی");
      await page.locator("#reg-identifier").fill(uniquePatientId());
      await page.locator("#reg-sex-f").check();
      await page.locator("#reg-status-first").check();
      const year = await page
        .locator("#reg-birth-year option")
        .nth(13)
        .getAttribute("value");
      await page.locator("#reg-birth-year").selectOption(year!);
      await page
        .getByRole("button", {
          name: message(language, "register"),
          exact: true,
        })
        .click();
      await expect(
        page.getByRole("button", { name: n("definite"), exact: true }),
      ).toBeVisible();
      const encounterUrl = page.url().split("#diagnosis")[0]!;
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await expect(
        page
          .getByRole("alert")
          .filter({ hasText: n("errors.diagnosis_not_positive") }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: n("definite"), exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: n("panss"), exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: n("harm"), exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: n("harmHelp"), exact: true })
        .focus();
      await page.keyboard.press("Enter");
      // The accessible magnifier also exposes the same guidance as a
      // hover/focus tooltip, so assert on the expanded disclosure paragraph.
      await expect(
        page.locator("p").filter({ hasText: n("harmHelp") }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await page.getByRole("radio", { name: n("normal"), exact: true }).check();
      const axe = await new AxeBuilder({ page }).analyze();
      expect(axe.violations).toEqual([]);
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await page.locator("#nc-drug-search").fill("not a catalog choice");
      await expect(
        page.getByRole("button", { name: n("continue"), exact: true }),
      ).toBeDisabled();
      await page.locator("#nc-drug-search").fill("");
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      if (lai)
        await page
          .getByRole("group", { name: n("lai"), exact: true })
          .getByRole("radio", { name: n("yes"), exact: true })
          .check();
      await page
        .getByRole("button", { name: n("generate"), exact: true })
        .click();
      await expect(page.locator("#nc-final-plan")).toBeVisible();
      if (lai)
        await expect(
          page.getByText(n("model_unavailable"), { exact: false }).first(),
        ).toBeVisible();
      await page.locator("#nc-final-plan").fill("Physician final plan — تست");
      await page.getByRole("button", { name: n("save"), exact: true }).click();
      await expect(
        page.getByRole("status").filter({ hasText: n("saved") }),
      ).not.toHaveCount(0);
      await page.reload();
      if (language === "fa")
        await page.getByRole("button", { name: "Switch to Persian" }).click();
      await expect(page.locator("#nc-final-plan")).toHaveValue(
        "Physician final plan — تست",
      );
      await page
        .getByRole("checkbox", { name: n("confirmHelp"), exact: true })
        .check();
      await page
        .getByRole("button", { name: n("confirm"), exact: true })
        .click();
      await expect(page).toHaveURL(/\/chart$/);
      await expect(
        page.getByText("Physician final plan — تست", { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("heading", { name: n("original"), exact: true }),
      ).toBeVisible();
      expect(encounterUrl).toContain("encounters");
    });
  }
}

test("personal formulation settings stale proposals without replacing physician text", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const user = uniqueName("availability");
  await ensurePhysician(request, user, "secret123");
  await loginAs(page, "physician", user, "secret123");
  await acknowledgeWarning(page);
  await page.goto("/#/unavailable-drugs");
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Select all", exact: true })).toHaveCount(0);
  const oral = page.getByRole("group", { name: "Oral", exact: true });
  await oral
    .getByRole("checkbox", { name: "Chlorpromazine", exact: true })
    .check();
  await page
    .getByRole("group", { name: "Long-acting injection", exact: true })
    .getByRole("checkbox", { name: /^haloperidol$/i })
    .check();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Saved");
  await page.reload();
  await expect(
    oral.getByRole("checkbox", { name: "Chlorpromazine", exact: true }),
  ).toBeChecked();
  const me = await (await page.request.get("/api/v1/me")).json();
  const h = { "X-CSRF-Token": me.csrf_token };
  const config = await (
    await page.request.get("/api/v1/new-case/config")
  ).json();
  const created = await page.request.post("/api/v1/patients", {
    headers: h,
    data: {
      identifier: uniquePatientId(),
      given_name: "Test",
      family_name: "Patient",
      sex: "F",
      birth_year: config.registration_year - 30,
      clinical_status: "first_time",
    },
  });
  expect(created.status()).toBe(201);
  const patient = await created.json();
  const url = "/api/v1/encounters/" + patient.draft.id;
  const draft = await (await page.request.get(url)).json();
  draft.draft_data.new_case.bmi = {
    ...draft.draft_data.new_case.bmi,
    source: "manual",
    manual_category: "normal",
  };
  expect(
    (
      await page.request.patch(url, {
        headers: { ...h, "If-Match": '"1"' },
        data: { draft_data: draft.draft_data },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await page.request.post(url + "/new-case/diagnosis-confirmation", {
        headers: { ...h, "If-Match": '"2"' },
        data: { confirm: true },
      })
    ).status(),
  ).toBe(200);
  await page.goto(`/#/encounters/${patient.draft.id}#considerations`);
  await expect(
    page.getByRole("heading", {
      name: "Treatment considerations",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Generate proposed plan", exact: true })
    .click();
  await expect(page.locator("#nc-final-plan")).toBeVisible();
  await expect(
    page
      .getByRole("heading", { name: "Original proposed plan", exact: true })
      .locator("..")
      .locator("pre"),
  ).not.toContainText("Chlorpromazine");
  await expect(
    page
      .getByRole("heading", { name: "Original proposed plan", exact: true })
      .locator("..")
      .locator("pre"),
  ).toContainText("Haloperidol");
  await page.locator("#nc-final-plan").fill("Kept physician text");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const settings = await (
    await page.request.get("/api/v1/me/unavailable-drugs")
  ).json();
  const selected = settings.catalog
    .filter((d: { group: string }) => d.group === "oral")
    .map((d: { id: string }) => d.id);
  expect(
    (
      await page.request.patch("/api/v1/me/unavailable-drugs", {
        headers: { ...h, "If-Match": `"${settings.revision}"` },
        data: { selected_ids: selected },
      })
    ).status(),
  ).toBe(200);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Regenerate before confirmation" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Generate proposed plan", exact: true })
    .click();
  await expect(
    page.getByText("No eligible oral candidates remain after filtering.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator("#nc-final-plan")).toHaveValue(
    "Kept physician text",
  );
});

test("definite-diagnosis retry replays a lost response without refilling discarded answers", async ({
  page,
  request,
}) => {
  const user = uniqueName("assertionretry");
  await ensurePhysician(request, user, "secret123");
  await loginAs(page, "physician", user, "secret123");
  await acknowledgeWarning(page);
  const me = await (await page.request.get("/api/v1/me")).json();
  const config = await (
    await page.request.get("/api/v1/new-case/config")
  ).json();
  const response = await page.request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": me.csrf_token },
    data: {
      identifier: uniquePatientId(),
      given_name: "Test",
      family_name: "Patient",
      sex: "M",
      birth_year: config.registration_year - 30,
      clinical_status: "first_time",
    },
  });
  expect(response.status()).toBe(201);
  const patient = await response.json();
  let dropped = false;
  await page.route(
    `**/encounters/${patient.draft.id}/new-case/diagnosis-confirmation`,
    async (route) => {
      if (!dropped) {
        dropped = true;
        await route.fetch();
        await route.abort();
      } else await route.continue();
    },
  );
  await page.goto(`/#/encounters/${patient.draft.id}#diagnosis`);
  await expect(
    page.getByRole("radio", { name: "Unknown", exact: true }).first(),
  ).toBeChecked();
  await page
    .getByRole("button", { name: "Diagnosis is definite", exact: true })
    .click();
  await expect(
    page.getByRole("alert").filter({ hasText: "The operation failed" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Diagnosis is definite", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "PANSS", exact: true }),
  ).toBeVisible();
  const saved = await (
    await page.request.get(`/api/v1/encounters/${patient.draft.id}`)
  ).json();
  expect(saved.revision).toBe(2);
  expect(saved.draft_data.diagnosis.answers).toEqual({});
  expect(saved.draft_data.diagnosis.new_case_assertion.actor_id).toBe(
    me.user.id,
  );
});
