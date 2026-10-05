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
 * - effects heading "Adverse effects (step 6)" (#effects-heading)
 * - status radios `#effect-{key}-{present|absent|not_assessed}`
 * - BARS radios `#bars-{item}-{n}` (e.g. `#bars-bars_global-3`)
 * - SAS radios `#sas-{item}-{n}` (e.g. `#sas-sas_gait-1`)
 * - Acute radios `#acute-{item}-{yes|no|unknown}` (e.g. `#acute-addx_urgent_airway-yes`)
 * - severity selects `#effect-{key}-severity` (akathisia: `#effect-akathisia-severity`)
 * - verdict `#effects-preview-status` (aria-live polite)
 * - per-effect results `#effect-{key}-results`, still-needed `#effect-{key}-still-needed`
 * - urgent panel `#effect-acute_dystonia-urgent` (role=alert)
 */

const HISTORY_FIELDS: { id: string; values: string[] }[] = [
  { id: "h_exposure_dopamine_blocker", values: ["yes", "no", "unknown", "not_assessed"] },
  { id: "h_exposure_duration_3m", values: ["yes", "no", "unknown", "not_assessed"] },
  { id: "h_exposure_1m_if_60plus", values: ["yes", "no", "unknown", "not_assessed"] },
  { id: "h_movement_persistence_4w", values: ["yes", "no", "unknown", "not_assessed"] },
  {
    id: "h_onset_timing",
    values: ["during_exposure", "within_4w_oral_withdrawal", "within_8w_lai_withdrawal", "unknown", "not_assessed"],
  },
  { id: "h_trial_adequacy_prior", values: ["adequate", "inadequate", "unknown", "not_assessed"] },
  { id: "h_prior_response", values: ["response", "no_response", "unknown", "not_assessed"] },
  { id: "h_monitoring_baseline", values: ["yes", "no", "unknown", "not_assessed"] },
  { id: "h_alternative_cause_considered", values: ["yes", "no", "unknown", "not_assessed"] },
  { id: "h_functional_impact", values: ["yes", "no", "unknown", "not_assessed"] },
  { id: "h_falls_or_limitation", values: ["yes", "no", "unknown", "not_assessed"] },
  { id: "h_medication_timeline_documented", values: ["yes", "no", "unknown", "not_assessed"] },
];

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
): Promise<{ draftId: string }> {
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
  return { draftId: body.draft.id as string };
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
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("heading", { name: "History (step 5)" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Adverse effects (step 6)" })).toBeVisible();
}

test("fresh history form has 12 unanswered fields, no pre-selected radios", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ehistfresh");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  await expect(page.locator("#history-heading")).toBeVisible();
  await expect(page.locator('input[name^="hist-"]:checked')).toHaveCount(0);
  for (const id of [
    "#hist-h_exposure_dopamine_blocker-yes",
    "#hist-h_exposure_dopamine_blocker-not_assessed",
    "#hist-h_onset_timing-during_exposure",
    "#hist-h_onset_timing-not_assessed",
  ]) {
    await expect(page.locator(id)).toBeVisible();
    await expect(page.locator(id)).not.toBeChecked();
  }

  const verdict = page.locator("#history-preview-status");
  await expect(verdict).toContainText("No answers yet", { timeout: 10000 });
  await expect(verdict).toContainText("12 fields with nothing selected");
  await expect(verdict).not.toContainText("Complete");
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-provenance")).toContainText(
    "Not yet recorded",
  );
});

test("history answers save, survive reload and re-login with still-needed guidance", async ({
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
  await expect(verdict).toContainText("Incomplete", { timeout: 10000 });
  await expect(verdict).toContainText("10 of 12 fields still need");
  await expect(page.locator("#history-still-needed")).toBeVisible();
  await expect(page.locator("#history-still-needed")).toContainText("Still needed:");

  await page.reload();
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-yes")).toBeChecked();
  await expect(page.locator("#hist-h_onset_timing-unknown")).toBeChecked();
  await expect(page.locator("#history-preview-status")).toContainText("Incomplete", {
    timeout: 10000,
  });

  await signOut(page);
  await loginAs(page, "physician", physician.username, physician.password);
  await acknowledgeWarning(page);
  await gotoEncounter(page, draftId);
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-yes")).toBeChecked();
  await expect(page.locator("#history-preview-status")).toContainText("Incomplete", {
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
    h_exposure_duration_3m: "no",
    h_exposure_1m_if_60plus: "no",
    h_movement_persistence_4w: "no",
    h_onset_timing: "not_assessed",
    h_trial_adequacy_prior: "not_assessed",
    h_prior_response: "not_assessed",
    h_monitoring_baseline: "no",
    h_alternative_cause_considered: "unknown",
    h_functional_impact: "no",
    h_falls_or_limitation: "no",
    h_medication_timeline_documented: "no",
  };
  for (const [field, value] of Object.entries(choices)) {
    await page.locator(`#hist-${field}-${value}`).check();
  }

  const verdict = page.locator("#history-preview-status");
  await expect(verdict).toContainText("Complete — all 12 history fields answered", {
    timeout: 25000,
  });
  await expect(verdict).toContainText("unknown and not assessed stay distinct from false");
  await expect(page.locator("#history-still-needed")).toHaveCount(0);
  await expect(page.locator("#hist-h_alternative_cause_considered-unknown")).toBeChecked();
});

test("fresh effects are unrecorded with null severity and no completion demand", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eefffresh");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

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
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

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
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

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
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

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
  await page.locator("#hist-h_exposure_duration_3m-no").check();
  const conflict = page.getByRole("heading", { name: "Another tab saved first" });
  await expect(conflict).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#hist-h_exposure_duration_3m-no")).toBeChecked();
  await expect(status).toContainText("Conflict");

  await page.getByRole("button", { name: "Keep my edits and retry" }).click();
  await expect(status).toContainText("Saved (revision 4)", { timeout: 15000 });

  await page.reload();
  await expect(page.locator("#hist-h_exposure_dopamine_blocker-yes")).toBeChecked();
  await expect(page.locator("#hist-h_exposure_duration_3m-no")).toBeChecked();
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
