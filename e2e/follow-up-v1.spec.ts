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
import { followUpLabels } from "../web/src/features/follow-up/labels";

for (const language of ["en", "fa"] as const) {
  for (const model of [false, true]) {
    test(`follow-up ${language} ${model ? "model" : "review"}: edit, reload, generate and sign`, async ({
      page,
      request,
    }) => {
      test.setTimeout(120000);
      const n = (key: string) =>
        followUpLabels[key]?.[language === "fa" ? 1 : 0] ??
        message(language, `nc.${key}` as MessageKey);
      const user = uniqueName("followup");
      await ensurePhysician(request, user, "secret123");
      await loginAs(page, "physician", user, "secret123");
      await acknowledgeWarning(page);
      const me = await (await page.request.get("/api/v1/me")).json();
      const headers = { "X-CSRF-Token": me.csrf_token };
      const response = await page.request.post("/api/v1/patients", {
        headers,
        data: {
          identifier: uniquePatientId(),
          given_name: "Test",
          family_name: "Patient",
          sex: "F",
          age: 30,
          clinical_status: "established",
        },
      });
      expect(response.status(), await response.text()).toBe(201);
      const patient = await response.json();
      expect(
        (
          await page.request.post(
            `/api/v1/encounters/${patient.draft.id}/discard`,
            {
              headers: { ...headers, "If-Match": '"1"' },
              data: { confirm: true },
            },
          )
        ).ok(),
      ).toBeTruthy();
      const opened = await page.request.post(
        `/api/v1/patients/${patient.patient.id}/encounters`,
        { headers, data: { kind: "follow_up" } },
      );
      expect(opened.status(), await opened.text()).toBe(201);
      const visit = await opened.json();
      expect(visit.encounter.workflow_version).toBe("follow-up-v1");
      await page.goto(`/#/encounters/${visit.encounter.id}`);
      if (language === "fa")
        await page.getByRole("button", { name: "Switch to Persian" }).click();
      if (model) {
        await page
          .getByRole("button", {
            name: new RegExp(message(language, "switchDark")),
          })
          .click();
        await expect(page.locator("html")).toHaveAttribute(
          "data-theme",
          "dark",
        );
      }
      await expect(page.getByText(n("noPrior"), { exact: true })).toBeVisible();
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: n("harm"), exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: n("harmHelp"), exact: true })
        .press("Enter");
      await expect(
        page.getByRole("status").filter({ hasText: "C-SSRS:" }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: n("history"), exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await expect(
        page.getByRole("alert").first(),
        await page.locator("body").innerText(),
      ).toBeVisible();
      const answer = async (key: string, value = false) =>
        page
          .getByRole("group", {
            name: `${n(key)} (${n("required")})`,
            exact: true,
          })
          .getByRole("radio", { name: n(value ? "yes" : "no"), exact: true })
          .check();
      for (const key of [
        "psychiatric",
        "hepatic_failure",
        "renal_failure",
        "glucose_abnormality",
        "hyperlipidemia",
        "heart_failure",
        "tobacco",
        "substances",
        "pregnant",
        "breastfeeding",
        "focal_neurological_deficit",
        "suspected_brain_lesion",
        "suspected_autoimmune_encephalitis",
      ])
        await answer(key);
      await page
        .getByRole("combobox", { name: n("bmiMode"), exact: true })
        .selectOption("manual");
      await page
        .getByRole("combobox", { name: n("bmiCategory"), exact: true })
        .selectOption("normal");
      await page
        .getByRole("group", { name: n("hyperprolactinemia"), exact: true })
        .getByRole("radio", { name: n("no"), exact: true })
        .check();
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: n("medications"), exact: true }),
      ).toBeVisible();
      if (model) {
        await page.locator("#nc-drug-search").fill("Olanzapine");
        await page
          .getByRole("button", { name: "Olanzapine", exact: true })
          .click();
        const main = page.getByRole("combobox", {
          name: n("main"),
          exact: true,
        });
        await main.selectOption({ label: "Olanzapine" });
        await page
          .getByRole("combobox", {
            name: `${n("formulation")} (${n("required")})`,
            exact: true,
          })
          .selectOption("oral");
      }
      await page.locator("#nc-drug-search").fill("unmatched search");
      await expect(
        page.getByRole("button", { name: n("continue"), exact: true }),
      ).toBeDisabled();
      await page.locator("#nc-drug-search").fill("");
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await page
        .getByRole("button", { name: n("continue"), exact: true })
        .click();
      await page
        .getByRole("combobox", {
          name: `${n("recovery")} (${n("required")})`,
          exact: true,
        })
        .selectOption(model ? "partial" : "controlled");
      await answer("seizure_risk");
      await answer("qtc_risk");
      if (model) await answer("persistent_harm_risk");
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      await page
        .getByRole("button", { name: n("generate"), exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: n("original"), exact: true }),
      ).toBeVisible();
      await expect(page.locator("pre").first()).toContainText(
        n(model ? "oral_replacement" : "physician_review"),
      );
      await page
        .locator("#nc-final-plan")
        .fill("Physician edited final plan — متن پزشک");
      await page.getByRole("button", { name: n("save"), exact: true }).click();
      await page
        .getByRole("button", { name: n("generate"), exact: true })
        .click();
      await expect(page.locator("#nc-final-plan")).toHaveValue(
        "Physician edited final plan — متن پزشک",
      );
      await page.reload();
      if (language === "fa")
        await page.getByRole("button", { name: "Switch to Persian" }).click();
      await expect(page.locator("#nc-final-plan")).toHaveValue(
        "Physician edited final plan — متن پزشک",
      );
      await page
        .getByRole("checkbox", { name: n("confirmHelp"), exact: true })
        .check();
      await page
        .getByRole("button", { name: n("confirm"), exact: true })
        .click();
      await expect(page).toHaveURL(/\/chart$/);
      await expect(
        page.getByText("Physician edited final plan — متن پزشک", {
          exact: true,
        }),
      ).toBeVisible();
      await expect(
        page.getByRole("heading", { name: n("original"), exact: true }),
      ).toBeVisible();
      if (model) {
        const next = await page.request.post(
          `/api/v1/patients/${patient.patient.id}/encounters`,
          { headers, data: { kind: "follow_up" } },
        );
        expect(next.status(), await next.text()).toBe(201);
        const continued = await next.json();
        const frozen = continued.draft_data.follow_up.baseline;
        expect(frozen.source.encounter_id).toBe(visit.encounter.id);
        expect(frozen.summary.visit_ordinal).toBe(2);
        await page.goto(`/#/encounters/${continued.encounter.id}#history`);
        await expect(
          page.getByRole("heading", { name: n("history"), exact: true }),
        ).toBeVisible();
        // A current lab suggestion never erases an explicit current No.
        await answer("labs", true);
        const config = await (
          await page.request.get("/api/v1/follow-up/config")
        ).json();
        const ldl = page.getByLabel(
          `${config.lab_labels.ldl[language]} (${config.lab_fields.ldl.unit})`,
          { exact: true },
        );
        await ldl.fill("200");
        await page
          .getByRole("group", {
            name: `${n("hyperlipidemia")} (${n("required")})`,
            exact: true,
          })
          .getByRole("radio", { name: n("no"), exact: true })
          .click();
        await ldl.fill("250");
        await answer("labs", false);
        await expect(
          page
            .getByRole("group", {
              name: `${n("hyperlipidemia")} (${n("required")})`,
              exact: true,
            })
            .getByRole("radio", { name: n("no"), exact: true }),
        ).toBeChecked();
        await answer("psychiatric", true);
        await page
          .getByRole("checkbox", { name: n("depression"), exact: true })
          .check();
        await page
          .getByRole("button", { name: n("continue"), exact: true })
          .click();
        await expect(
          page
            .getByRole("alert")
            .filter({ hasText: n("current_antidepressant") }),
        ).toBeVisible();
        await answer("current_antidepressant", false);
        await page
          .getByRole("button", { name: n("continue"), exact: true })
          .click();
        await expect(
          page.getByRole("heading", { name: n("medications"), exact: true }),
        ).toBeVisible();
        await page
          .getByRole("button", { name: n("remove"), exact: true })
          .click();
        await expect(
          page.getByRole("combobox", { name: n("main"), exact: true }),
        ).toHaveValue("");
        await page
          .getByRole("button", { name: n("continue"), exact: true })
          .click();
        await expect(
          page.getByRole("heading", { name: n("effects"), exact: true }),
        ).toBeVisible();
        const saved = await (
          await page.request.get(`/api/v1/encounters/${continued.encounter.id}`)
        ).json();
        expect(saved.draft_data.follow_up.baseline).toEqual(frozen);
        expect(
          saved.draft_data.follow_up.treatment.main_catalog_drug_id,
        ).toBeNull();
        expect(
          saved.draft_data.follow_up.history.conditions.hyperlipidemia.manual,
        ).toBe(false);
      }
    });
  }
}

for (const language of ["en", "fa"] as const) {
  test(`follow-up ${language}: stale-tab reconciliation and lost responses preserve edits`, async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);
    const n = (key: string) =>
      followUpLabels[key]?.[language === "fa" ? 1 : 0] ??
      message(language, `nc.${key}` as MessageKey);
    const user = uniqueName("furecovery");
    await ensurePhysician(request, user, "secret123");
    await loginAs(page, "physician", user, "secret123");
    await acknowledgeWarning(page);
    const me = await (await page.request.get("/api/v1/me")).json();
    const auth = { "X-CSRF-Token": me.csrf_token };
    const patient = await (
      await page.request.post("/api/v1/patients", {
        headers: auth,
        data: {
          identifier: uniquePatientId(),
          given_name: "Synthetic",
          family_name: "Patient",
          sex: "M",
          age: 30,
          clinical_status: "established",
        },
      })
    ).json();
    await page.request.post(`/api/v1/encounters/${patient.draft.id}/discard`, {
      headers: { ...auth, "If-Match": '"1"' },
      data: { confirm: true },
    });
    const opened = await (
      await page.request.post(
        `/api/v1/patients/${patient.patient.id}/encounters`,
        { headers: auth, data: { kind: "follow_up" } },
      )
    ).json();
    const url = `/api/v1/encounters/${opened.encounter.id}`;
    await page.goto(`/#/encounters/${opened.encounter.id}#proposal`);
    if (language === "fa")
      await page.getByRole("button", { name: "Switch to Persian" }).click();
    await expect(page.getByRole("alert").first()).toBeVisible();
    await expect(
      page.getByRole("heading", { name: n("panss"), exact: true }),
    ).toBeVisible();
    const draft = opened.draft_data;
    const h = draft.follow_up.history;
    h.psychiatric.present = false;
    h.substances.present = false;
    h.heart_failure = false;
    h.tobacco = false;
    h.bmi = { ...h.bmi, source: "manual", manual_category: "normal" };
    for (const key of Object.keys(h.neurological)) h.neurological[key] = false;
    for (const key of [
      "hepatic_failure",
      "renal_failure",
      "hyperlipidemia",
      "glucose_abnormality",
      "hyperprolactinemia",
    ])
      h.conditions[key].manual = false;
    draft.follow_up.considerations = {
      ...draft.follow_up.considerations,
      recovery: "controlled",
      seizure_risk: false,
      qtc_risk: false,
    };
    const saved = await page.request.patch(url, {
      headers: { ...auth, "If-Match": '"1"' },
      data: { draft_data: draft },
    });
    expect(saved.status(), await saved.text()).toBe(200);
    await page.goto(`/#/encounters/${opened.encounter.id}#history`);
    await page.reload();
    if (language === "fa")
      await page.getByRole("button", { name: "Switch to Persian" }).click();
    await expect(
      page.getByRole("heading", { name: n("history"), exact: true }),
    ).toBeVisible();
    // A second tab saves first. The first tab must keep its own checked answer.
    const truth = await (await page.request.get(url)).json();
    truth.draft_data.follow_up.harm.self = true;
    expect(
      (
        await page.request.patch(url, {
          headers: { ...auth, "If-Match": `"${truth.revision}"` },
          data: { draft_data: truth.draft_data },
        })
      ).ok(),
    ).toBeTruthy();
    const tobacco = page
      .getByRole("group", {
        name: `${n("tobacco")} (${n("required")})`,
        exact: true,
      })
      .getByRole("radio", { name: n("yes"), exact: true });
    await tobacco.check();
    await expect(
      page.getByRole("button", { name: n("reconcile"), exact: true }),
    ).toBeVisible();
    await expect(tobacco).toBeChecked();
    await page
      .getByRole("button", { name: n("reconcile"), exact: true })
      .click();
    await page
      .getByRole("button", { name: n("considerations"), exact: true })
      .click();
    // The first generate commits, but its response is lost. Retry must reuse its key.
    const generationKeys: string[] = [];
    await page.route(
      `**/encounters/${opened.encounter.id}/follow-up/proposals`,
      async (route) => {
        generationKeys.push(route.request().headers()["idempotency-key"]!);
        if (generationKeys.length === 1) {
          await route.fetch();
          await route.abort("failed");
        } else await route.continue();
      },
    );
    await page
      .getByRole("button", { name: n("generate"), exact: true })
      .click();
    await expect(
      page.getByRole("alert").filter({ hasText: n("failed") }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: n("generate"), exact: true })
      .click();
    await expect(page.locator("#nc-final-plan")).toBeVisible();
    await expect(
      page.getByRole("alert").filter({ hasText: n("failed") }),
    ).toHaveCount(0);
    expect(generationKeys).toHaveLength(2);
    expect(generationKeys[0]).toBe(generationKeys[1]);
    await page
      .locator("#nc-final-plan")
      .fill("Preserved after response loss — متن نهایی");
    await page.getByRole("button", { name: n("save"), exact: true }).click();
    await page
      .getByRole("checkbox", { name: n("confirmHelp"), exact: true })
      .check();
    await page.route(
      `**/encounters/${opened.encounter.id}/follow-up/sign`,
      async (route) => {
        await route.fetch();
        await route.abort("failed");
      },
    );
    await page.getByRole("button", { name: n("confirm"), exact: true }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: n("failed") }),
    ).toBeVisible();
    await page.reload();
    await expect(page).toHaveURL(/\/chart$/);
    await expect(
      page.getByText("Preserved after response loss — متن نهایی", {
        exact: true,
      }),
    ).toBeVisible();
  });
}

for (const language of ["en", "fa"] as const) {
  test(`follow-up ${language}: original branches and effect precedence`, async ({
    page,
    request,
  }) => {
    test.setTimeout(240000);
    const n = (key: string) =>
      followUpLabels[key]?.[language === "fa" ? 1 : 0] ??
      message(language, `nc.${key}` as MessageKey);
    const username = uniqueName("fumatrix");
    await ensurePhysician(request, username, "secret123");
    await loginAs(page, "physician", username, "secret123");
    await acknowledgeWarning(page);
    const auth = {
      "X-CSRF-Token": (await (await page.request.get("/api/v1/me")).json())
        .csrf_token,
    };
    const patient = await (
      await page.request.post("/api/v1/patients", {
        headers: auth,
        data: {
          identifier: uniquePatientId(),
          given_name: "Branch",
          family_name: "Patient",
          sex: "M",
          age: 30,
          clinical_status: "established",
        },
      })
    ).json();
    await page.request.post(`/api/v1/encounters/${patient.draft.id}/discard`, {
      headers: { ...auth, "If-Match": '"1"' },
      data: { confirm: true },
    });
    const opened = await (
      await page.request.post(
        `/api/v1/patients/${patient.patient.id}/encounters`,
        { headers: auth, data: { kind: "follow_up" } },
      )
    ).json();
    const id = opened.encounter.id;
    const url = `/api/v1/encounters/${id}`;
    const config = await (
      await page.request.get("/api/v1/follow-up/config")
    ).json();
    const main = config.formulations.find(
      (e: { id: string }) => e.id === "oral:olanzapine",
    ).catalog_drug_id;
    type Scenario = {
      name: string;
      recovery: string;
      terminal: string;
      branches?: Record<string, boolean>;
      effects?: Record<string, boolean>;
      adherence?: boolean;
      lai?: boolean;
      formulation?: string | null;
      noMain?: boolean;
      reached: string[];
      adjunct?: string;
    };
    const cases: Scenario[] = [
      {
        name: "controlled continuation",
        recovery: "controlled",
        terminal: "continue_same",
        branches: { switch_needed: false, dose_reduction_possible: false },
        reached: ["switch_needed", "dose_reduction_possible"],
      },
      {
        name: "controlled dose reduction",
        recovery: "controlled",
        terminal: "reduce_dose",
        branches: { switch_needed: false, dose_reduction_possible: true },
        reached: ["switch_needed", "dose_reduction_possible"],
      },
      {
        name: "controlled oral switch",
        recovery: "controlled",
        terminal: "oral_replacement",
        branches: { switch_needed: true },
        reached: ["switch_needed"],
      },
      {
        name: "controlled LAI preference",
        recovery: "controlled",
        terminal: "same_drug_lai",
        lai: true,
        branches: { switch_needed: true },
        reached: ["switch_needed"],
      },
      {
        name: "partial nonadherence",
        recovery: "partial",
        terminal: "same_drug_lai",
        adherence: false,
        reached: [],
      },
      {
        name: "partial persistent harm",
        recovery: "partial",
        terminal: "clozapine",
        branches: { persistent_harm_risk: true },
        reached: ["persistent_harm_risk"],
      },
      {
        name: "partial without persistent harm",
        recovery: "partial",
        terminal: "oral_replacement",
        branches: { persistent_harm_risk: false },
        reached: ["persistent_harm_risk"],
      },
      {
        name: "resistant no improvement",
        recovery: "none_or_worse",
        terminal: "clozapine",
        branches: { treatment_resistant: true },
        reached: ["treatment_resistant"],
      },
      {
        name: "nonresistant nonadherence",
        recovery: "none_or_worse",
        terminal: "same_drug_lai",
        adherence: false,
        branches: { treatment_resistant: false },
        reached: ["treatment_resistant"],
      },
      {
        name: "nonresistant persistent harm",
        recovery: "none_or_worse",
        terminal: "clozapine",
        branches: { treatment_resistant: false, persistent_harm_risk: true },
        reached: ["treatment_resistant", "persistent_harm_risk"],
      },
      {
        name: "nonresistant without persistent harm",
        recovery: "none_or_worse",
        terminal: "oral_replacement",
        branches: { treatment_resistant: false, persistent_harm_risk: false },
        reached: ["treatment_resistant", "persistent_harm_risk"],
      },
      {
        name: "parkinsonism plus TD",
        recovery: "controlled",
        terminal: "oral_replacement",
        effects: { parkinsonism: true, tardive_dyskinesia: true },
        reached: [],
        adjunct: "vmat2_inhibitor",
      },
      {
        name: "unresolved dystonia plus TD",
        recovery: "controlled",
        terminal: "oral_replacement",
        effects: { acute_dystonia: true, tardive_dyskinesia: true },
        branches: { dystonia_resolved: false, dose_reduction_possible: false },
        reached: ["dystonia_resolved", "dose_reduction_possible"],
        adjunct: "vmat2_inhibitor",
      },
      {
        name: "effect replaces resistant clozapine",
        recovery: "none_or_worse",
        terminal: "oral_replacement",
        effects: { parkinsonism: true },
        branches: { treatment_resistant: true },
        reached: [],
      },
      {
        name: "effect replaces nonadherent LAI",
        recovery: "partial",
        terminal: "oral_replacement",
        effects: { akathisia: true },
        adherence: false,
        reached: [],
      },
      {
        name: "resolved dystonia adjunct",
        recovery: "controlled",
        terminal: "adjunct_only",
        effects: { acute_dystonia: true },
        branches: { dystonia_resolved: true, dose_reduction_possible: true },
        reached: ["dystonia_resolved", "dose_reduction_possible"],
        adjunct: "anticholinergic_advice",
      },
      {
        name: "current LAI review",
        recovery: "controlled",
        terminal: "physician_review",
        formulation: "lai",
        reached: [],
      },
      {
        name: "no main review",
        recovery: "controlled",
        terminal: "physician_review",
        noMain: true,
        reached: [],
      },
    ];
    for (const scenario of cases)
      await test.step(scenario.name, async () => {
        const current = await (await page.request.get(url)).json();
        const draft = current.draft_data;
        const fu = draft.follow_up;
        const h = fu.history;
        h.psychiatric.present = false;
        h.substances.present = false;
        h.heart_failure = false;
        h.tobacco = false;
        h.bmi = { ...h.bmi, source: "manual", manual_category: "normal" };
        for (const key of Object.keys(h.neurological))
          h.neurological[key] = false;
        for (const key of [
          "hepatic_failure",
          "renal_failure",
          "hyperlipidemia",
          "glucose_abnormality",
          "hyperprolactinemia",
        ])
          h.conditions[key].manual = false;
        draft.medications.entries = [{ catalog_drug_id: main }];
        draft.medications.selection_dataset_version =
          fu.content_pins.lexicomp_version;
        fu.treatment = {
          main_catalog_drug_id: scenario.noMain ? null : main,
          formulation: scenario.noMain
            ? null
            : (scenario.formulation ?? "oral"),
        };
        fu.considerations = {
          ...fu.considerations,
          recovery: scenario.recovery,
          adherence: scenario.adherence ?? true,
          lai_preference: scenario.lai ?? false,
          seizure_risk: false,
          qtc_risk: false,
        };
        fu.branches = Object.fromEntries(
          Object.keys(fu.branches).map((key) => [
            key,
            scenario.branches?.[key] ?? null,
          ]),
        );
        fu.effects = Object.fromEntries(
          Object.keys(fu.effects).map((key) => [
            key,
            key === "sedation" ? "none" : (scenario.effects?.[key] ?? false),
          ]),
        );
        const saved = await page.request.patch(url, {
          headers: { ...auth, "If-Match": `"${current.revision}"` },
          data: { draft_data: draft },
        });
        expect(saved.status(), await saved.text()).toBe(200);
        await page.goto(`/#/encounters/${id}#considerations`);
        await page.reload();
        if (language === "fa")
          await page.getByRole("button", { name: "Switch to Persian" }).click();
        await expect(
          page.getByRole("heading", { name: n("considerations"), exact: true }),
        ).toBeVisible();
        for (const key of Object.keys(fu.branches)) {
          const question = page.getByRole("group", {
            name: `${n(key)} (${n("required")})`,
            exact: true,
          });
          if (scenario.reached.includes(key))
            await expect(question).toBeVisible();
          else await expect(question).toHaveCount(0);
        }
        await page
          .getByRole("button", { name: n("generate"), exact: true })
          .click();
        await expect(page.locator("pre").first()).toContainText(
          n(scenario.terminal),
        );
        if (scenario.adjunct)
          await expect(page.locator("pre").first()).toContainText(
            n(scenario.adjunct),
          );
        await page
          .getByRole("button", { name: n("save"), exact: true })
          .click();
      });
  });
}
