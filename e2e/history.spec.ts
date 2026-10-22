import { test, expect } from "@playwright/test";
import {
  uniqueName,
  uniqueLetters,
  uniquePatientId,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
  signOut,
} from "./helpers";

/* S12 structured history + adverse-effects journeys (seam T9 browser;
 * evaluator and HTTP enforcement covered by BT/http/test_history.py and not
 * duplicated here). Every verdict comes from the server preview — the browser
 * never computes completeness or scores.
 *
 * Selector contract (see HistorySection.tsx / EffectsSection.tsx headers):
 * - history heading "History (step 5)" (#history-heading)
 * - history radios `#hist-{fieldId}-{value}` (name `hist-{fieldId}`)
 * - history verdict `#history-preview-status` (aria-live polite)
 * - history still-needed `#history-still-needed`
 * - history per-field errors `#hist-{fieldId}-error` (role=alert)
 * - history provenance `#hist-{fieldId}-provenance`
 * - reconciliation `#history-reconciliation-status`, `#history-reconciliation-baseline`
 * - phone `#history-phone-update`
 * - effects heading "Adverse effects (step 5)" (#effects-heading) on a follow-up
 *   draft only (registration has no effects step; `#wizard-step-effects` absent)
 * - status radios `#effect-{key}-{present|absent|not_assessed}`
 * - BARS radios `#bars-{item}-{n}` (e.g. `#bars-bars_global-3`)
 * - SAS radios `#sas-{item}-{n}` (e.g. `#sas-sas_gait-1`)
 * - Acute radios `#acute-{item}-{yes|no|unknown}` (e.g. `#acute-addx_urgent_airway-yes`)
 * - severity selects `#effect-{key}-severity` (akathisia: `#effect-akathisia-severity`)
 * - verdict `#effects-preview-status` (aria-live polite)
 * - per-effect results `#effect-{key}-results`, still-needed `#effect-{key}-still-needed`
 * - urgent panel `#effect-acute_dystonia-urgent` (role=alert)
 */

async function loginPhysician(
  page: import("@playwright/test").Page,
  request: import("@playwright/test").APIRequestContext,
  prefix: string,
): Promise<{ username: string; password: string }> {
  const username = uniqueName(prefix);
  const password = "secret123";
  await ensurePhysician(request, username, password);
  await loginAs(page, "physician", username, password);
  await acknowledgeWarning(page);
  return { username, password };
}

async function createPatientWithDraft(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
): Promise<{ draftId: string; patientId: string }> {
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const payload = {
    identifier: uniquePatientId(),
    given_name: uniqueLetters("given"),
    family_name: uniqueLetters("family"),
    sex: "F",
    age: 30,
    clinical_status: "first_time",
  };
  const post = (csrfToken: string) =>
    request.post("/api/v1/patients", {
      headers: { "X-CSRF-Token": csrfToken, "Idempotency-Key": key },
      data: payload,
    });
  let create = await post(await physicianSession(request, physician));
  if (create.status() === 401) {
    // Backend commit-visibility race (see helpers.ts ensurePatient): one
    // fresh login, then fail loudly.
    create = await post(await physicianSession(request, physician));
  }
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return {
    draftId: body.draft.id as string,
    patientId: body.patient.id as string,
  };
}

async function discardDraftApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  revision = 1,
): Promise<void> {
  const csrf = await physicianSession(request, physician);
  const response = await request.post(`/api/v1/encounters/${draftId}/discard`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"${revision}"` },
    data: { confirm: true },
  });
  expect(response.status(), await response.text()).toBe(200);
}

/** Registration occupies the one-open-draft slot. Effects capture is follow-up only. */
async function createFollowupDraft(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
): Promise<string> {
  const { draftId, patientId } = await createPatientWithDraft(request, physician);
  await discardDraftApi(request, physician, draftId);
  const csrf = await physicianSession(request, physician);
  const response = await request.post(`/api/v1/patients/${patientId}/encounters`, {
    headers: {
      "X-CSRF-Token": csrf,
      "Idempotency-Key": `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`,
    },
    data: { kind: "follow_up" },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()).encounter.id as string;
}

async function patchDraftApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  draftData: Record<string, unknown>,
  revision: number,
): Promise<number> {
  const csrf = await physicianSession(request, physician);
  const saved = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"${revision}"` },
    data: { draft_data: draftData },
  });
  expect(saved.status(), await saved.text()).toBe(200);
  return (await saved.json()).revision as number;
}

async function gotoEncounter(
  page: import("@playwright/test").Page,
  draftId: string,
  step: "history" | "effects" = "history",
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}#${step}`);
  const heading = step === "history" ? "History (step 5)" : "Adverse effects (step 5)";
  await expect(page.getByRole("heading", { name: heading })).toBeVisible();
}

test("fresh history defaults to Unknown with two applicable fields and no retired controls", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ehistfresh");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoEncounter(page, draftId);
  const exposure = page.getByRole("group", { name: "History of lifetime antipsychotic exposure", exact: true });
  await expect(exposure.getByRole("radio")).toHaveCount(3);
  for (const label of ["Yes", "No", "Unknown"]) await expect(exposure.getByRole("radio", { name: label, exact: true })).toBeVisible();
  await expect(exposure.getByRole("radio", { name: "Unknown", exact: true })).toBeChecked();
  await expect(page.locator('input[name^="hist-"]:checked')).toHaveCount(2);
  await expect(page.locator(".xi-diagnosis-overview")).toContainText("2 / 2 responses");
  await expect(page.locator("#history-preview-status")).toContainText("Complete — all 2 applicable history fields answered", { timeout: 10000 });
  await expect(page.locator("#hist-h_movement_persistence_4w-unknown")).toBeChecked();
  for (const field of ["h_exposure_duration_3m", "h_exposure_1m_if_60plus", "h_medication_timeline_documented", "h_onset_timing", "h_trial_adequacy_prior", "h_prior_response", "h_monitoring_baseline", "h_functional_impact", "h_alternative_cause_considered", "h_falls_or_limitation"]) {
    await expect(page.locator(`[name="hist-${field}"]`)).toHaveCount(0);
    await expect(page.locator(`#history-still-needed a[href*="${field}"]`)).toHaveCount(0);
  }
  await expect(page.getByRole("heading", { name: "Prior treatment", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Prior treatment", exact: true })).toHaveCount(0);
  const saved = await (await request.get(`/api/v1/encounters/${draftId}/history`)).json();
  expect(saved.values.h_exposure_dopamine_blocker).toBe("unknown");
  expect(saved.provenance.h_exposure_dopamine_blocker).toBeUndefined();
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-provenance")).toContainText("Not yet recorded");
});

test("history answers save and keep applicable completeness after reload and re-login", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ehistresume");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  await page.locator("#hist-h_exposure_dopamine_blocker-yes").check();
  await page.locator("#hist-h_onset_timing-unknown").check();
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 2)", {
    timeout: 15000,
  });

  const verdict = page.locator("#history-preview-status");
  await expect(verdict).toContainText("Complete — all 4 applicable history fields answered", { timeout: 10000 });
  await expect(page.locator("#history-still-needed")).toHaveCount(0);

  await page.reload();
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-yes")).toBeChecked();
  await expect(page.locator("#hist-h_onset_timing-unknown")).toBeChecked();
  await expect(page.locator("#history-preview-status")).toContainText("Complete", {
    timeout: 10000,
  });

  await signOut(page);
  await loginAs(page, "physician", physician.username, physician.password);
  await acknowledgeWarning(page);
  await gotoEncounter(page, draftId);
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-yes")).toBeChecked();
  await expect(page.locator("#history-preview-status")).toContainText("Complete", {
    timeout: 10000,
  });
});

test("complete history via UI keeps unknown distinct from false", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ehistfull");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  const choices: Record<string, string> = {
    h_exposure_dopamine_blocker: "no",
    h_movement_persistence_4w: "yes",
    h_alternative_cause_considered: "unknown",
    h_falls_or_limitation: "no",
  };
  for (const [field, value] of Object.entries(choices)) {
    await page.locator(`#hist-${field}-${value}`).check();
  }

  const verdict = page.locator("#history-preview-status");
  await expect(verdict).toContainText("Complete — all 4 applicable history fields answered", {
    timeout: 25000,
  });
  await expect(page.locator(".xi-history-summary-note")).toContainText(
    "Unknown and Not assessed are recorded separately from No",
  );
  await expect(page.locator("#history-still-needed")).toHaveCount(0);
  await expect(page.locator("#hist-h_alternative_cause_considered-unknown")).toBeChecked();
});

test("exposure and treatment transitions clear hidden answers and provenance while preserving workflow context", async ({ page, request }) => {
  test.setTimeout(90000);
  const physician = await loginPhysician(page, request, "e2ehistconditional");
  const { draftId } = await createPatientWithDraft(request, physician);
  const csrf = await physicianSession(request, physician);
  const seeded = await request.post(`/api/v1/encounters/${draftId}/history`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: { values: { h_exposure_dopamine_blocker: "yes", h_onset_timing: "other", h_trial_adequacy_prior: "inadequate", h_prior_response: "response" } },
  });
  expect(seeded.status(), await seeded.text()).toBe(200);
  const inventory = await seeded.json();
  expect(inventory.provenance.h_prior_response.source).toBe("clinician_entry");
  const workflow = inventory.workflow_fields[0];
  await gotoEncounter(page, draftId);
  await expect(page.locator(".xi-diagnosis-overview")).toContainText("5 / 5 responses");
  const trial = page.getByRole("group", { name: "Adequacy of prior treatment trial", exact: true });
  const response = page.getByRole("group", { name: "Response to prior treatment", exact: true });
  await expect(trial.getByRole("radio")).toHaveCount(3);
  for (const label of ["Adequate", "Inadequate", "Unknown"]) await expect(trial.getByRole("radio", { name: label, exact: true })).toBeVisible();
  await expect(response.getByRole("radio")).toHaveCount(3);
  for (const label of ["Some level of response", "No response", "Unknown"]) await expect(response.getByRole("radio", { name: label, exact: true })).toBeVisible();
  await page.locator(`[id="hist-${workflow.id}"]`).selectOption(workflow.permitted[0]);
  const savedHistory = async () => (await (await request.get(`/api/v1/encounters/${draftId}/history`)).json());
  await expect.poll(async () => (await savedHistory()).values[workflow.id]).toBe(workflow.permitted[0]);

  for (const adequacy of ["adequate", "unknown"]) {
    await page.locator(`#hist-h_trial_adequacy_prior-${adequacy}`).check();
    await expect(response).toHaveCount(0);
    await expect(page.locator(".xi-diagnosis-overview")).toContainText("4 / 4 responses");
    await expect.poll(async () => {
      const history = await savedHistory();
      return [history.values.h_trial_adequacy_prior, history.values.h_prior_response ?? null, history.provenance.h_prior_response ?? null];
    }).toEqual([adequacy, null, null]);
    await page.reload();
    await page.locator("#hist-h_trial_adequacy_prior-inadequate").check();
    await expect(page.locator("#hist-h_prior_response-unknown")).toBeChecked();
    await page.locator("#hist-h_prior_response-no_response").check();
    await expect.poll(async () => (await savedHistory()).values.h_prior_response).toBe("no_response");
  }
  for (const exposure of ["no", "unknown"]) {
    await page.locator(`#hist-h_exposure_dopamine_blocker-${exposure}`).check();
    await expect(page.locator("#history-group-treatment")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Prior treatment", exact: true })).toHaveCount(0);
    await expect(page.locator(".xi-diagnosis-overview")).toContainText("2 / 2 responses");
    await expect.poll(async () => {
      const history = await savedHistory();
      return [history.values.h_exposure_dopamine_blocker,
        ...["h_onset_timing", "h_trial_adequacy_prior", "h_prior_response"].flatMap((id) => [history.values[id] ?? null, history.provenance[id] ?? null])];
    }).toEqual([exposure, null, null, null, null, null, null]);
    await page.reload();
    await expect(page.locator(`#hist-h_exposure_dopamine_blocker-${exposure}`)).toBeChecked();
    await page.locator("#hist-h_exposure_dopamine_blocker-yes").check();
    await expect(page.locator("#hist-h_onset_timing-unknown")).toBeChecked();
    await expect(page.locator("#hist-h_trial_adequacy_prior-unknown")).toBeChecked();
    await expect(page.locator('[name="hist-h_prior_response"]')).toHaveCount(0);
    await page.locator("#hist-h_trial_adequacy_prior-inadequate").check();
    await expect(page.locator("#hist-h_prior_response-unknown")).toBeChecked();
    await expect.poll(async () => (await savedHistory()).values.h_trial_adequacy_prior).toBe("inadequate");
    expect((await savedHistory()).values[workflow.id]).toBe(workflow.permitted[0]);
  }
});

test("movement and established treatment reveal only applicable controls and clear hidden saved provenance", async ({ page, request }) => {
  test.setTimeout(120000);
  const physician = await loginPhysician(page, request, "e2ehistmovement");
  const { draftId } = await createPatientWithDraft(request, physician);
  const prefix = "candidate/history/pharmacotherapy.v2/";
  const context = prefix + "h_antipsychotic_treatment_context";
  const csrf = await physicianSession(request, physician);
  const seeded = await request.post(`/api/v1/encounters/${draftId}/history`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: { values: { h_exposure_dopamine_blocker: "yes", h_movement_persistence_4w: "yes",
      h_trial_adequacy_prior: "inadequate", h_alternative_cause_considered: "yes", h_falls_or_limitation: "no", [context]: "Aripiprazole" } },
  });
  expect(seeded.status(), await seeded.text()).toBe(200);
  const read = async () => (await (await request.get(`/api/v1/encounters/${draftId}/history`)).json());
  expect((await read()).provenance.h_alternative_cause_considered.source).toBe("clinician_entry");
  expect((await read()).provenance[context].source).toBe("clinician_entry");
  await gotoEncounter(page, draftId);
  await expect(page.locator(".xi-diagnosis-overview")).toContainText("7 / 7 responses");
  for (const label of ["Involuntary movements persist over at least 4 weeks", "Alternative explanations ruled out", "Falls or functional limitation from parkinsonian signs"]) {
    const group = page.getByRole("group", { name: label, exact: true });
    await expect(group.getByRole("radio")).toHaveCount(3);
    for (const name of ["Yes", "No", "Unknown"]) await expect(group.getByRole("radio", { name, exact: true })).toBeVisible();
  }
  const established = page.getByLabel("Explicit established antipsychotic", { exact: true });
  await expect(established).toHaveValue("Aripiprazole");
  const options = await established.locator("option").evaluateAll((nodes) => nodes.map((node) => (node as HTMLOptionElement).value));
  expect(options).toHaveLength(23);
  expect(options).toContain("unknown");
  for (const value of ["", "not_assessed", "NoEstablishedTreatment"]) expect(options).not.toContain(value);
  for (const value of ["no", "unknown"]) {
    await page.locator(`#hist-h_movement_persistence_4w-${value}`).check();
    for (const id of ["h_alternative_cause_considered", "h_falls_or_limitation"]) {
      await expect(page.locator(`[name="hist-${id}"]`)).toHaveCount(0);
      await expect(page.locator(`#history-still-needed a[href*="${id}"]`)).toHaveCount(0);
    }
    await expect.poll(async () => {
      const h = await read();
      return [h.values.h_movement_persistence_4w, ...["h_alternative_cause_considered", "h_falls_or_limitation"].flatMap((id) => [h.values[id] ?? null, h.provenance[id] ?? null])];
    }).toEqual([value, null, null, null, null]);
    await page.reload();
    await page.locator("#hist-h_movement_persistence_4w-yes").check();
    for (const id of ["h_alternative_cause_considered", "h_falls_or_limitation"]) await expect(page.locator(`#hist-${id}-unknown`)).toBeChecked();
    await expect.poll(async () => (await read()).values.h_movement_persistence_4w).toBe("yes");
    await page.reload();
    await expect(page.locator("#hist-h_alternative_cause_considered-unknown")).toBeChecked();
    await page.locator("#hist-h_alternative_cause_considered-no").check();
    await expect.poll(async () => (await read()).values.h_alternative_cause_considered).toBe("no");
  }
  for (const value of ["no", "unknown"]) {
    await page.locator(`#hist-h_exposure_dopamine_blocker-${value}`).check();
    await expect(established).toHaveCount(0);
    await expect.poll(async () => {
      const h = await read(); return [h.values.h_exposure_dopamine_blocker, h.values[context] ?? null, h.provenance[context] ?? null];
    }).toEqual([value, null, null]);
    await page.reload();
    await expect(established).toHaveCount(0);
    await page.locator("#hist-h_exposure_dopamine_blocker-yes").check();
    await expect(established).toHaveValue("unknown");
    await established.selectOption("Aripiprazole");
    await expect.poll(async () => (await read()).values[context]).toBe("Aripiprazole");
  }
});

test("pharmacotherapy tri-state defaults persist and retired controls never appear in readiness", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ehistpharm");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoEncounter(page, draftId);
  const prefix = "candidate/history/pharmacotherapy.v2/";
  for (const field of ["h_physical_vulnerability", "h_substance_contribution"]) {
    const select = page.locator(`[id="hist-${prefix}${field}"]`);
    await expect(select).toHaveValue("unknown");
    expect(await select.locator("option").evaluateAll((nodes) => nodes.map((node) => (node as HTMLOptionElement).value))).toEqual(["yes", "no", "unknown"]);
    await select.selectOption("no");
  }
  await expect.poll(async () => (await (await request.get(`/api/v1/encounters/${draftId}/history`)).json()).values[prefix + "h_substance_contribution"]).toBe("no");
  await page.reload();
  for (const field of ["h_physical_vulnerability", "h_substance_contribution"]) await expect(page.locator(`[id="hist-${prefix}${field}"]`)).toHaveValue("no");
  const retired = ["h_prior_treatment_difficulty", "h_preference_barrier", "h_exposure_modifier", "h_trial_duration_review", "h_urgent_safety_concern", "h_monitoring_gap"];
  for (const id of retired) {
    await expect(page.locator(`[id="hist-${prefix}${id}"]`)).toHaveCount(0);
    await expect(page.locator(`a[href*="${encodeURIComponent(prefix + id)}"]`)).toHaveCount(0);
  }
  const readiness = await (await request.get(`/api/v1/encounters/${draftId}/proposal-readiness`)).json();
  for (const id of retired) expect(readiness.blockers.map((b: { source_path: string }) => b.source_path)).not.toContain(prefix + id);
  // A similarly named question in another package remains independently assessed.
  await expect(page.locator('[id="hist-candidate/history/high_suicide_clozapine.v1/h_urgent_safety_concern"]')).toBeVisible();
});

test("all five onset choices persist through autosave and reload", async ({ page, request }) => {
  test.setTimeout(60000);
  const physician = await loginPhysician(page, request, "e2ehistonset");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoEncounter(page, draftId);
  await page.locator("#hist-h_exposure_dopamine_blocker-yes").check();
  await expect(page.locator("#hist-h_onset_timing-unknown")).toBeChecked();
  await expect(page.locator("#hist-h_trial_adequacy_prior-unknown")).toBeChecked();
  const onset = page.getByRole("group", { name: "Onset timing relative to exposure or withdrawal", exact: true });
  await expect(onset.getByRole("radio")).toHaveCount(5);
  for (const label of ["During exposure", "Within 4 weeks after oral withdrawal", "Within 8 weeks after LAI withdrawal", "Other", "Unknown"]) {
    await expect(onset.getByRole("radio", { name: label, exact: true })).toBeVisible();
  }
  for (const value of ["during_exposure", "within_4w_oral_withdrawal", "within_8w_lai_withdrawal", "other", "unknown"]) {
    await page.locator(`#hist-h_onset_timing-${value}`).check();
    await expect.poll(async () => (await (await request.get(`/api/v1/encounters/${draftId}/history`)).json()).values.h_onset_timing).toBe(value);
    await page.reload();
    await expect(page.locator(`#hist-h_onset_timing-${value}`)).toBeChecked();
  }
});

test("fresh effects are unrecorded with null severity and no completion demand", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eefffresh");
  const draftId = await createFollowupDraft(request, physician);

  await gotoEncounter(page, draftId, "effects");

  await expect(page.locator("#effects-heading")).toBeVisible();
  await expect(page.locator('input[name^="effect-"]:checked')).toHaveCount(0);
  for (const id of [
    "#effect-akathisia-present",
    "#effect-akathisia-absent",
    "#effect-parkinsonism-not_assessed",
    "#effect-acute_dystonia-present",
  ]) {
    await expect(page.locator(id)).toBeVisible();
  }

  const verdict = page.locator("#effects-preview-status");
  await expect(verdict).toContainText("not recorded", { timeout: 10000 });
  // No severity selects until an effect is present; no questionnaires yet.
  await expect(page.locator("#effect-akathisia-severity")).toHaveCount(0);
  await expect(page.locator("#effect-parkinsonism-severity")).toHaveCount(0);
});

test("akathisia present via UI needs complete BARS and matching global severity", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eeffbars");
  const draftId = await createFollowupDraft(request, physician);

  await gotoEncounter(page, draftId, "effects");

  await page.locator("#effect-akathisia-present").check();
  // Questionnaire appears only for present; severity starts unchosen.
  await expect(page.locator("#bars-bars_global-3")).toBeVisible();
  await expect(page.locator("#effect-akathisia-severity")).toBeVisible();

  await page.locator("#bars-bars_objective-2").check();
  await page.locator("#bars-bars_awareness-2").check();
  await page.locator("#bars-bars_distress-2").check();
  await page.locator("#bars-bars_global-3").check();
  await page.locator("#effect-akathisia-severity").selectOption("3");

  await expect(page.locator("#draft-save-status")).toContainText("Saved", { timeout: 15000 });
  const verdict = page.locator("#effects-preview-status");
  await expect(verdict).toContainText("BARS complete, global 3", { timeout: 15000 });

  const results = page.locator("#effect-akathisia-results");
  await expect(results).toBeVisible();
  await expect(results).toContainText("Global /5 (principal severity)");
});

test("switching present to absent clears obsolete severity in the same edit", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eeffstale");
  const draftId = await createFollowupDraft(request, physician);

  await gotoEncounter(page, draftId, "effects");

  await page.locator("#effect-akathisia-present").check();
  await page.locator("#bars-bars_objective-2").check();
  await page.locator("#bars-bars_awareness-2").check();
  await page.locator("#bars-bars_distress-2").check();
  await page.locator("#bars-bars_global-3").check();
  await page.locator("#effect-akathisia-severity").selectOption("3");
  await expect(page.locator("#draft-save-status")).toContainText("Saved", { timeout: 15000 });
  await expect(page.locator("#effects-preview-status")).toContainText("BARS complete, global 3", {
    timeout: 15000,
  });

  // Explicit clear: absent carries null severity and hides the questionnaire.
  await page.locator("#effect-akathisia-absent").check();
  await expect(page.locator("#effect-akathisia-severity")).toHaveCount(0);
  await expect(page.locator("#draft-save-status")).toContainText("Saved", { timeout: 15000 });
  await expect(page.locator("#effects-preview-status")).toContainText(
    "Akathisia (BARS): absent (severity null, no questionnaire required)",
    { timeout: 15000 },
  );

  await page.reload();
  await expect(page.locator("#effect-akathisia-absent")).toBeChecked();
  await expect(page.locator("#effect-akathisia-severity")).toHaveCount(0);
  await expect(page.locator("#effects-preview-status")).toContainText("severity null", {
    timeout: 10000,
  });
});

test("urgent airway alert shows before checklist completion and persists", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eeffurgent");
  const draftId = await createFollowupDraft(request, physician);

  await gotoEncounter(page, draftId, "effects");

  await page.locator("#effect-acute_dystonia-present").check();
  await expect(page.locator("#acute-addx_urgent_airway-yes")).toBeVisible();
  await page.locator("#acute-addx_urgent_airway-yes").check();

  // Urgent routes independently of the remaining four criteria.
  const urgent = page.locator("#effect-acute_dystonia-urgent");
  await expect(urgent).toBeVisible({ timeout: 5000 });
  await expect(urgent).toHaveAttribute("role", "alert");
  await expect(urgent).toContainText("emergency assessment");
  await expect(page.locator("#effects-preview-status")).toContainText("incomplete", {
    timeout: 15000,
  });

  await page.locator("#acute-addx_sustained_posture-yes").check();
  await page.locator("#acute-addx_medication_timeline-yes").check();
  await page.locator("#acute-addx_distribution_persistence-yes").check();
  await page.locator("#acute-addx_exclusions-yes").check();
  await expect(page.locator("#effect-acute_dystonia-urgent")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#effects-preview-status")).toContainText("urgent yes", {
    timeout: 15000,
  });
});

test("history 412 keeps edits and reconciles via Reload/Retry", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ehiststale");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);
  const status = page.locator("#draft-save-status");
  await expect(status).toContainText("Saved (revision 1)");

  await page.locator("#hist-h_exposure_dopamine_blocker-yes").check();
  await expect(status).toContainText("Saved (revision 2)", { timeout: 15000 });

  // Another tab wins the race through the real API (revision 2 -> 3).
  const winner = await patchDraftApi(request, physician, draftId, { working_note: "server-winner" }, 2);
  expect(winner).toBe(3);

  // This tab is stale on revision 2: the autosave 412s and keeps the edit.
  await page.locator("#hist-h_movement_persistence_4w-no").check();
  const conflict = page.getByRole("heading", { name: "Another tab saved first" });
  await expect(conflict).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#hist-h_movement_persistence_4w-no")).toBeChecked();
  await expect(status).toContainText("Conflict");

  await page.getByRole("button", { name: "Keep my edits and retry" }).click();
  await expect(status).toContainText("Saved (revision 4)", { timeout: 15000 });

  await page.reload();
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-yes")).toBeChecked();
  await expect(page.locator("#hist-h_movement_persistence_4w-no")).toBeChecked();
});

test("failed history save never shows Saved; Retry recovers", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ehistfail");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);
  const status = page.locator("#draft-save-status");
  await expect(status).toContainText("Saved (revision 1)");

  await page.route("**/api/v1/encounters/*", (route) => {
    if (route.request().method() === "PATCH") {
      return route.abort();
    }
    return route.continue();
  });
  try {
    await page.locator("#history-phone-update").fill("+43 699 999");
    await expect(status).toContainText("Save failed", { timeout: 15000 });
    await expect(status).not.toContainText("Saved (revision 2)");
    await expect(page.locator("#history-phone-update")).toHaveValue("+43 699 999");
  } finally {
    await page.unroute("**/api/v1/encounters/*");
  }

  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(status).toContainText("Saved (revision 2)", { timeout: 15000 });
});

test("history controls wait for the saved field inventory before accepting edits", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ehistload");
  const { draftId } = await createPatientWithDraft(request, physician);
  let release!: () => void;
  const loaded = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/api/v1/encounters/${draftId}/history`, async (route) => {
    await loaded;
    return route.continue();
  });
  await gotoEncounter(page, draftId);
  await expect(page.locator("#history-phone-update")).toBeDisabled();
  await expect(page.locator("#history-reconciliation-status")).toBeDisabled();
  await expect(page.locator("#history-reconciliation-baseline")).toBeDisabled();
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-yes")).toBeDisabled();
  release();
  await expect(page.locator("#history-phone-update")).toBeEnabled();
  await page.locator("#history-reconciliation-status").selectOption("pending");
  await page.locator("#history-reconciliation-baseline").fill("synthetic-baseline");
  await page.locator("#history-phone-update").fill("+43 699 222");
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 2)", { timeout: 15000 });
  await page.reload();
  await expect(page.locator("#history-reconciliation-status")).toHaveValue("pending");
  await expect(page.locator("#history-reconciliation-baseline")).toHaveValue("synthetic-baseline");
  await expect(page.locator("#history-phone-update")).toHaveValue("+43 699 222");
});

test("reconciliation and phone update save and survive reload", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ehistrecon");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  await page.locator("#history-reconciliation-status").selectOption("pending");
  await page.locator("#history-reconciliation-baseline").fill("baseline-enc-001");
  await page.locator("#history-phone-update").fill("+43 699 111");
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 2)", {
    timeout: 15000,
  });

  await page.reload();
  await expect(page.locator("#history-reconciliation-status")).toHaveValue("pending");
  await expect(page.locator("#history-reconciliation-baseline")).toHaveValue("baseline-enc-001");
  await expect(page.locator("#history-phone-update")).toHaveValue("+43 699 111");
});
