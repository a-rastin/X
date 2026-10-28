import { test, expect } from "@playwright/test";
import {
  uniqueName,
  uniqueLetters,
  uniquePatientId,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
} from "./helpers";

/* S09 diagnosis threshold journeys (seam T9 browser; evaluator
 * and HTTP enforcement covered by BT/assessments/test_diagnosis.py and not
 * duplicated here). The verdict always comes from the server preview — the
 * browser never computes criteria itself. Registration Diagnosis Continue
 * (`#wizard-continue`, an action button on that step only) saves first,
 * then navigates directly only for a complete criteria-satisfied server
 * evaluation; every other state opens a confirmation dialog ("The current
 * assessment does not confirm the diagnosis. Do you want to continue to
 * PANSS anyway?") where only "Continue to PANSS anyway" navigates.
 * Completion and acknowledgment govern proposal generation, not page
 * navigation: confirming never calls bypass/acknowledgment and never
 * writes a skip sentinel. Follow-up Diagnosis Continue keeps the plain
 * pager link (no check).
 *
 * Selectors used below are the handoff contract for the test agent:
 * - section heading "Diagnosis (step 2)"
 * - radios `#diag-{itemId}-yes|no|unknown` (item ids from the released v1:
 *   a_delusions, a_hallucinations, a_disorganized_speech,
 *   a_disorganized_behavior, a_negative_symptoms, b_functional_decline,
 *   c_six_months, c_active_month, c_shortened_by_intervention,
 *   d_mood_exclusion, e_substance_medical_exclusion, f_autism_present,
 *   f_prominent_psychosis)
 * - verdict `#diagnosis-preview-status` (aria-live polite, deliberately
 *   not role=status so the S07 save status keeps the page's single status
 *   role)
 * - criteria table `#diagnosis-criteria` (A–F rows, met/not_met/unknown)
 * - warning panel text "Completed below threshold", button
 *   "Acknowledge below-threshold result"
 * - legacy bypass state text "Diagnosis bypassed" (status only, seeded via
 *   API; no Bypass button exists)
 * - continue dialog text "The current assessment does not confirm the
 *   diagnosis", buttons "Return to diagnosis" / "Continue to PANSS anyway"
 */

const REQUIRED_ITEMS = [
  "a_delusions",
  "a_hallucinations",
  "a_disorganized_speech",
  "a_disorganized_behavior",
  "a_negative_symptoms",
  "b_functional_decline",
  "c_six_months",
  "c_active_month",
  "c_shortened_by_intervention",
  "d_mood_exclusion",
  "e_substance_medical_exclusion",
  "f_autism_present",
  "f_prominent_psychosis",
];

/** Source-derived qualifying case: every required criterion met. */
function qualifyingAnswers(): Record<string, string> {
  const answers: Record<string, string> = {};
  for (const id of REQUIRED_ITEMS) {
    answers[id] = "no";
  }
  answers["a_delusions"] = "yes";
  answers["a_hallucinations"] = "yes";
  answers["b_functional_decline"] = "yes";
  answers["c_six_months"] = "yes";
  answers["c_active_month"] = "yes";
  answers["d_mood_exclusion"] = "yes";
  answers["e_substance_medical_exclusion"] = "yes";
  return answers;
}

/** Symptom count alone: Criterion A met but B–F fail → below threshold. */
function symptomOnlyAnswers(): Record<string, string> {
  const answers: Record<string, string> = {};
  for (const id of REQUIRED_ITEMS) {
    answers[id] = "no";
  }
  answers["a_delusions"] = "yes";
  answers["a_hallucinations"] = "yes";
  answers["a_disorganized_speech"] = "yes";
  return answers;
}

/** Unknowns with no failure: complete but indeterminate (not proceedable). */
function indeterminateAnswers(): Record<string, string> {
  const answers = qualifyingAnswers();
  answers["a_delusions"] = "unknown";
  answers["a_disorganized_speech"] = "unknown";
  return answers;
}

/** Explicit all-Unknown: complete but indeterminate, never below threshold. */
function allUnknownAnswers(): Record<string, string> {
  const answers: Record<string, string> = {};
  for (const id of REQUIRED_ITEMS) {
    answers[id] = "unknown";
  }
  return answers;
}

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
  const csrf = await physicianSession(request, physician);
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const create = await request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": key },
    data: {
      identifier: uniquePatientId(),
      given_name: uniqueLetters("given"),
      family_name: uniqueLetters("family"),
      sex: "F",
      age: 30,
      clinical_status: "first_time",
    },
  });
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return { draftId: body.draft.id as string, patientId: body.patient.id as string };
}

async function seedAnswersApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  answers: Record<string, string>,
  revision = 1,
): Promise<void> {
  const csrf = await physicianSession(request, physician);
  const saved = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"${revision}"` },
    data: { draft_data: { diagnosis: { answers } } },
  });
  expect(saved.status(), await saved.text()).toBe(200);
}

async function answerAll(
  page: import("@playwright/test").Page,
  answers: Record<string, string>,
): Promise<void> {
  for (const [itemId, value] of Object.entries(answers)) {
    await page.locator(`#diag-${itemId}-${value}`).check();
  }
}

async function readDraft(request: import("@playwright/test").APIRequestContext, draftId: string) {
  const response = await request.get(`/api/v1/encounters/${draftId}`);
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}

async function seedDraft(request: import("@playwright/test").APIRequestContext, physician: { username: string; password: string }, draftId: string, data: Record<string, unknown>) {
  const csrf = await physicianSession(request, physician);
  const response = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' }, data: { draft_data: data },
  });
  expect(response.status(), await response.text()).toBe(200);
}

test("initialization preserves invalid values, extra keys and draft metadata", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ediagpreserve");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedDraft(request, physician, draftId, {
    working_note: "preserve note",
    diagnosis: { answers: { a_delusions: "invalid", extra_key: "kept", f_prominent_psychosis: "yes" }, extra_metadata: "kept" },
  });
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.locator("#diag-a_hallucinations-unknown")).toBeChecked();
  await expect.poll(async () => (await readDraft(request, draftId)).revision).toBe(3);
  const draft = await readDraft(request, draftId);
  expect(draft.draft_data.working_note).toBe("preserve note");
  expect(draft.draft_data.diagnosis.extra_metadata).toBe("kept");
  expect(draft.draft_data.diagnosis.answers).toEqual({ ...allUnknownAnswers(), a_delusions: "invalid", extra_key: "kept", f_prominent_psychosis: "yes" });
  await page.locator("#diag-f_autism_present-yes").check();
  await expect(page.locator("#diag-f_prominent_psychosis-yes")).toBeChecked();
  await page.locator("#diag-f_autism_present-no").check();
  await expect(page.locator("#diag-f_prominent_psychosis-yes")).toBeChecked();
});

for (const sentinel of ["__bypass", "__skipped"]) {
  test(`legacy ${sentinel} answers do not trigger initialization`, async ({ page, request }) => {
    const physician = await loginPhysician(page, request, "e2ediagsentinel");
    const { draftId } = await createPatientWithDraft(request, physician);
    await seedAnswersApi(request, physician, draftId, { [sentinel]: "yes" });
    await page.goto(`/#/encounters/${draftId}`);
    await expect(page.locator("#wizard-continue")).toBeEnabled();
    await page.reload();
    await expect(page.locator("#wizard-continue")).toBeEnabled();
    const draft = await readDraft(request, draftId);
    expect(draft.revision).toBe(2);
    expect(draft.draft_data.diagnosis.answers).toEqual({ [sentinel]: "yes" });
  });
}

test("qualifying case shows every required criterion satisfied and can proceed", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagqual");
  const { draftId } = await createPatientWithDraft(request, physician);

  await page.goto(`/#/encounters/${draftId}`);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();

  await answerAll(page, qualifyingAnswers());

  const verdict = page.locator("#diagnosis-preview-status");
  await expect(verdict).toContainText("Every required criterion satisfied", {
    timeout: 15000,
  });
  const criteria = page.locator("#diagnosis-criteria");
  await expect(criteria).toBeVisible();
  for (const criterion of ["A", "B", "C", "D", "E", "F"]) {
    await expect(criteria.getByRole("row", { name: new RegExp(`^${criterion} · .+ Met$`) })).toBeVisible();
  }
  // No warning, no acknowledgment CTA on a satisfied result.
  await expect(page.getByText("Completed below threshold")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Acknowledge below-threshold result" }),
  ).toHaveCount(0);
});

test("symptom-count-only case does not satisfy and requests acknowledgment", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagsym");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, symptomOnlyAnswers());

  await page.goto(`/#/encounters/${draftId}`);
  const verdict = page.locator("#diagnosis-preview-status");
  await expect(verdict).toContainText("Completed below threshold", {
    timeout: 10000,
  });
  await expect(
    page.getByRole("button", { name: "Acknowledge below-threshold result" }),
  ).toBeVisible();
  // Criterion A met, but the full document is not satisfied.
  const criteria = page.locator("#diagnosis-criteria");
  await expect(criteria.getByRole("row", { name: /^A · .+ Met$/ })).toBeVisible();
  await expect(verdict).not.toContainText("Every required criterion satisfied");
});

test("partial saved answers retain selections and initialize missing values", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagpart");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, {
    a_delusions: "yes",
    a_hallucinations: "yes",
  });

  await page.goto(`/#/encounters/${draftId}`);
  const verdict = page.locator("#diagnosis-preview-status");
  await expect(verdict).toContainText("Indeterminate", { timeout: 10000 });
  await expect(page.locator("#diag-a_delusions-yes")).toBeChecked();
  await expect(page.locator("#diag-f_prominent_psychosis-unknown")).toBeChecked();
  await expect(page.getByText("Completed below threshold")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Acknowledge below-threshold result" }),
  ).toHaveCount(0);
  const preview = await readDiagnosisApi(request, physician, draftId);
  expect(preview["answers"]).toEqual({ ...allUnknownAnswers(), a_delusions: "yes", a_hallucinations: "yes" });
});

test("indeterminate answers neither proceed nor request acknowledgment", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagind");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, indeterminateAnswers());

  await page.goto(`/#/encounters/${draftId}`);
  const verdict = page.locator("#diagnosis-preview-status");
  await expect(verdict).toContainText("Indeterminate", { timeout: 10000 });
  await expect(verdict).toContainText("needs no acknowledgment");
  await expect(
    page.getByRole("button", { name: "Acknowledge below-threshold result" }),
  ).toHaveCount(0);
});

test("below-threshold acknowledgment is recorded; relevant edit invalidates", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagack");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, symptomOnlyAnswers());

  await page.goto(`/#/encounters/${draftId}`);
  const verdict = page.locator("#diagnosis-preview-status");
  await expect(verdict).toContainText("Completed below threshold", {
    timeout: 10000,
  });

  await page
    .getByRole("button", { name: "Acknowledge below-threshold result" })
    .click();
  await expect(verdict).toContainText("acknowledged", { timeout: 10000 });
  await expect(page.getByText(/Acknowledged by author/)).toBeVisible();

  // A relevant edit flips one answer: the stored acknowledgment no longer
  // describes the assessment, so the warning returns.
  await page.locator("#diag-b_functional_decline-yes").check();
  await expect(verdict).toContainText("Completed below threshold", {
    timeout: 15000,
  });
  await expect(
    page.getByRole("button", { name: "Acknowledge below-threshold result" }),
  ).toBeVisible();
});

async function seedBypassApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  revision = 1,
): Promise<void> {
  const csrf = await physicianSession(request, physician);
  const response = await request.post(
    `/api/v1/encounters/${draftId}/diagnosis/bypass`,
    {
      headers: {
        "X-CSRF-Token": csrf,
        "If-Match": `"${revision}"`,
        "Idempotency-Key": `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`,
      },
      data: {},
    },
  );
  expect(response.status(), await response.text()).toBe(200);
}

async function seedAcknowledgmentApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  revision: number,
): Promise<void> {
  const csrf = await physicianSession(request, physician);
  const response = await request.post(
    `/api/v1/encounters/${draftId}/diagnosis/acknowledgment`,
    {
      headers: {
        "X-CSRF-Token": csrf,
        "If-Match": `"${revision}"`,
        "Idempotency-Key": `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`,
      },
      data: {},
    },
  );
  expect(response.status(), await response.text()).toBe(200);
}

async function readDiagnosisApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
): Promise<Record<string, unknown>> {
  const csrf = await physicianSession(request, physician);
  const response = await request.get(`/api/v1/encounters/${draftId}/diagnosis`, {
    headers: { "X-CSRF-Token": csrf },
  });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as Record<string, unknown>;
}

/** Registration occupies the one-open-draft slot: discard it, then open a
 * follow-up draft for the same patient. */
async function createFollowupDraft(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  patientId: string,
  draftId: string,
): Promise<string> {
  let csrf = await physicianSession(request, physician);
  const discard = await request.post(`/api/v1/encounters/${draftId}/discard`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"1"` },
    data: { confirm: true },
  });
  expect(discard.status(), await discard.text()).toBe(200);
  csrf = await physicianSession(request, physician);
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

const CONTINUE_DIALOG_TEXT =
  "The current assessment does not confirm the diagnosis. Do you want to continue to PANSS anyway?";

async function expectContinueDialog(
  page: import("@playwright/test").Page,
): Promise<void> {
  await expect(page.getByText(CONTINUE_DIALOG_TEXT)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Return to diagnosis" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Continue to PANSS anyway" }),
  ).toBeVisible();
}

test("partial answers require confirmation; cancel/escape stay, confirm navigates", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagcont");
  const { draftId } = await createPatientWithDraft(request, physician);

  await page.goto(`/#/encounters/${draftId}`);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();

  // Partial answers, then Continue with no acknowledgment or bypass.
  await page.locator("#diag-a_delusions-yes").check();
  await page.locator("#diag-a_hallucinations-yes").check();
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Indeterminate",
    { timeout: 15000 },
  );

  // The warning appears and the page stays on Diagnosis.
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();

  // Return dismisses the dialog, stays, and restores Continue focus.
  await page.getByRole("button", { name: "Return to diagnosis" }).click();
  await expect(page.getByText(CONTINUE_DIALOG_TEXT)).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();
  await expect(page.locator("#wizard-continue")).toBeFocused();

  // Escape also dismisses without navigating.
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await page.keyboard.press("Escape");
  await expect(page.getByText(CONTINUE_DIALOG_TEXT)).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();

  // Only explicit confirmation navigates.
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await page.getByRole("button", { name: "Continue to PANSS anyway" }).click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();

  // Back keeps the answers; visiting never created a bypass or acknowledgment.
  await page.locator("#wizard-back").click();
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();
  await expect(page.locator("#diag-a_delusions-yes")).toBeChecked();
  await expect(page.locator("#diag-a_hallucinations-yes")).toBeChecked();
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Indeterminate",
    { timeout: 15000 },
  );
  await expect(
    page.locator("#diagnosis-preview-status"),
  ).not.toContainText("Diagnosis bypassed");
  await expect(
    page.getByRole("button", { name: "Bypass diagnosis" }),
  ).toHaveCount(0);
  const preview = await readDiagnosisApi(request, physician, draftId);
  expect(preview["acknowledgment"]).toBeNull();
  expect(preview["bypass"]).toBeNull();
  expect(preview["acknowledgment_valid"]).toBe(false);
  expect(preview["bypass_valid"]).toBe(false);

  // Confirmation is transient: continuing again rechecks and asks again.
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
});

test("untouched diagnosis requires confirmation; cancel stays, confirm navigates", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagfresh");
  const { draftId } = await createPatientWithDraft(request, physician);

  // Fresh draft: missing values are initialized and saved.
  await page.goto(`/#/encounters/${draftId}`);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Indeterminate",
    { timeout: 15000 },
  );

  for (const id of REQUIRED_ITEMS) {
    await expect(page.locator(`#diag-${id}-unknown`)).toBeChecked();
  }
  const initialized = await readDiagnosisApi(request, physician, draftId);
  expect(initialized["answers"]).toEqual(allUnknownAnswers());
  const revision = initialized["revision"];
  await page.reload();
  await expect(page.locator("#diag-f_prominent_psychosis-unknown")).toBeChecked();
  await expect(page.locator("#diagnosis-preview-status")).toContainText("Indeterminate");
  expect((await readDiagnosisApi(request, physician, draftId))["revision"]).toBe(revision);

  // The warning appears and the page stays on Diagnosis.
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();

  // Return dismisses, stays, and restores Continue focus.
  await page.getByRole("button", { name: "Return to diagnosis" }).click();
  await expect(page.getByText(CONTINUE_DIALOG_TEXT)).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();
  await expect(page.locator("#wizard-continue")).toBeFocused();

  // Only explicit confirmation navigates.
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await page.getByRole("button", { name: "Continue to PANSS anyway" }).click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();

  // Confirming wrote no bypass, skip, or clinical acknowledgment.
  const preview = await readDiagnosisApi(request, physician, draftId);
  expect(preview["acknowledgment"]).toBeNull();
  expect(preview["bypass"]).toBeNull();
  expect(preview["acknowledgment_valid"]).toBe(false);
  expect(preview["bypass_valid"]).toBe(false);
});

test("an immediate answer edit keeps every default without repeated initialization", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ediagquick");
  const { draftId } = await createPatientWithDraft(request, physician);
  let patches = 0;
  await page.route(`**/api/v1/encounters/${draftId}`, async (route) => {
    if (route.request().method() === "PATCH") patches += 1;
    await route.continue();
  });
  await page.goto(`/#/encounters/${draftId}`);
  await page.locator("#diag-a_delusions-yes").check();
  const expected = { ...allUnknownAnswers(), a_delusions: "yes" };
  await expect.poll(async () => (await readDiagnosisApi(request, physician, draftId)).answers).toEqual(expected);
  await page.reload();
  await expect(page.locator("#diag-a_delusions-yes")).toBeChecked();
  await expect(page.locator("#diag-f_prominent_psychosis-unknown")).toBeChecked();
  await page.waitForTimeout(1500);
  expect(patches).toBe(1);
});

test("Continue waits for the definition and initialized answers", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ediagwait");
  const { draftId } = await createPatientWithDraft(request, physician);
  let release!: () => void;
  let started!: () => void;
  const waiting = new Promise<void>((resolve) => { release = resolve; });
  const requested = new Promise<void>((resolve) => { started = resolve; });
  await page.route("**/api/v1/content/assessments/diagnosis", async (route) => {
    started();
    await waiting;
    await route.continue();
  });
  await page.goto(`/#/encounters/${draftId}`);
  await requested;
  await expect(page.locator("#wizard-continue")).toBeDisabled();
  release();
  await expect(page.locator("#wizard-continue")).toBeEnabled();
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  const preview = await readDiagnosisApi(request, physician, draftId);
  expect(preview["answers"]).toEqual(allUnknownAnswers());
});

for (const failure of [500, 412]) {
  test(`initialization retains defaults after save failure ${failure} until explicit retry`, async ({ page, request }) => {
    const physician = await loginPhysician(page, request, "e2ediagsavefail");
    const { draftId } = await createPatientWithDraft(request, physician);
    let attempts = 0;
    await page.route(`**/api/v1/encounters/${draftId}`, async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      attempts += 1;
      if (attempts === 1) return route.fulfill({ status: failure, contentType: "application/json", body: JSON.stringify({ message: "Synthetic save failure" }) });
      return route.continue();
    });
    await page.goto(`/#/encounters/${draftId}`);
    await expect(page.locator("#draft-save-status")).toContainText(failure === 412 ? "Conflict" : "Save failed", { timeout: 15000 });
    await expect(page.locator("#diag-a_delusions-unknown")).toBeChecked();
    await page.waitForTimeout(1500);
    expect(attempts).toBe(1);
    await page.getByRole("button", { name: failure === 412 ? "Keep my edits and retry" : "Retry save" }).click();
    await expect.poll(async () => (await readDiagnosisApi(request, physician, draftId)).answers).toEqual(allUnknownAnswers());
    await expect(page.locator("#draft-save-status")).toHaveCount(0);
  });
}

test("diagnosis check failure stays with retry, no navigation", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagchkfail");
  const { draftId } = await createPatientWithDraft(request, physician);

  await page.goto(`/#/encounters/${draftId}`);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Indeterminate",
    { timeout: 15000 },
  );

  // Fail the guarded Continue's preview read after the save barrier passes.
  await page.route(
    `**/api/v1/encounters/${draftId}/diagnosis`,
    (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ message: "Synthetic diagnosis failure" }),
      }),
  );
  try {
    await page.locator("#wizard-continue").click();
    // Stays on Diagnosis with a retryable error: no dialog, no navigation.
    await expect(
      page.getByRole("heading", { name: "Diagnosis (step 2)" }),
    ).toBeVisible();
    await expect(page.getByText(/Synthetic diagnosis failure/)).toBeVisible({
      timeout: 15000,
    });
    await expect(
      page.getByRole("button", { name: "Retry" }),
    ).toBeVisible();
    await expect(page.getByText(CONTINUE_DIALOG_TEXT)).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "PANSS (step 3)" }),
    ).toHaveCount(0);
  } finally {
    await page.unroute(`**/api/v1/encounters/${draftId}/diagnosis`);
  }

  // Retry after recovery rechecks and asks again (still nonconfirming).
  await page.getByRole("button", { name: "Retry" }).click();
  await expectContinueDialog(page);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();
});

test("satisfied result continues directly without confirmation", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagsat");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, qualifyingAnswers());

  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Every required criterion satisfied",
    { timeout: 15000 },
  );

  await page.locator("#wizard-continue").click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();
  await expect(page.getByText(CONTINUE_DIALOG_TEXT)).toHaveCount(0);
});

test("explicit all-Unknown answers require confirmation", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagunk");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, allUnknownAnswers());

  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Indeterminate",
    { timeout: 15000 },
  );

  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await page.getByRole("button", { name: "Continue to PANSS anyway" }).click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();
});

test("indeterminate answers require confirmation", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagindc");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, indeterminateAnswers());

  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Indeterminate",
    { timeout: 15000 },
  );

  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await page.getByRole("button", { name: "Continue to PANSS anyway" }).click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();
});

test("completed below-threshold answers require confirmation", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagbel");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, symptomOnlyAnswers());

  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Completed below threshold",
    { timeout: 15000 },
  );

  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  // Cancel stays on Diagnosis; the clinical acknowledgment CTA is unchanged.
  await page.getByRole("button", { name: "Return to diagnosis" }).click();
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 2)" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Acknowledge below-threshold result" }),
  ).toBeVisible();
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await page.getByRole("button", { name: "Continue to PANSS anyway" }).click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();
  // Confirming wrote no clinical acknowledgment on its own.
  const preview = await readDiagnosisApi(request, physician, draftId);
  expect(preview["acknowledgment"]).toBeNull();
  expect(preview["acknowledgment_valid"]).toBe(false);
});

test("acknowledged below-threshold answers still require confirmation", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagackc");
  const { draftId } = await createPatientWithDraft(request, physician);
  // Seeded answers save as revision 2; acknowledgment fences that revision.
  await seedAnswersApi(request, physician, draftId, symptomOnlyAnswers());
  await seedAcknowledgmentApi(request, physician, draftId, 2);

  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "acknowledged",
    { timeout: 15000 },
  );

  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await page.getByRole("button", { name: "Continue to PANSS anyway" }).click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();
});

test("initialization preserves an acknowledgment but lets the server invalidate it", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ediagoldack");
  const { draftId } = await createPatientWithDraft(request, physician);
  const answers = symptomOnlyAnswers();
  delete answers.f_prominent_psychosis;
  await seedAnswersApi(request, physician, draftId, answers);
  await seedAcknowledgmentApi(request, physician, draftId, 2);
  const before = await readDiagnosisApi(request, physician, draftId);
  expect(before.acknowledgment_valid).toBe(true);
  await page.goto(`/#/encounters/${draftId}`);
  await expect.poll(async () => (await readDiagnosisApi(request, physician, draftId)).revision).toBe(4);
  const after = await readDiagnosisApi(request, physician, draftId);
  expect(after.acknowledgment).toEqual(before.acknowledgment);
  expect(after.acknowledgment_valid).toBe(false);
  expect(after.requires_acknowledgment).toBe(true);
  await expect(page.getByRole("button", { name: "Acknowledge below-threshold result" })).toBeVisible();
});

test("legacy bypass record shows status without a new bypass action", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediaglegbyp");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedBypassApi(request, physician, draftId);
  const bypassRevision = (await readDiagnosisApi(request, physician, draftId)).revision;

  await page.goto(`/#/encounters/${draftId}`);
  const verdict = page.locator("#diagnosis-preview-status");
  await expect(verdict).toContainText("Diagnosis bypassed", { timeout: 10000 });
  // Status only: no control encourages a new bypass.
  await expect(
    page.getByRole("button", { name: "Bypass diagnosis" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Bypass instead of completing" }),
  ).toHaveCount(0);
  // Bypassed, not completed: no criteria table, no warning CTA.
  await expect(page.locator("#diagnosis-criteria")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Acknowledge below-threshold result" }),
  ).toHaveCount(0);

  // Reload keeps the record; Continue still asks for confirmation — a
  // legacy bypass never silently skips the warning.
  await page.reload();
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Diagnosis bypassed",
    { timeout: 10000 },
  );
  expect((await readDiagnosisApi(request, physician, draftId)).revision).toBe(bypassRevision);
  await page.locator("#wizard-continue").click();
  await expectContinueDialog(page);
  await page.getByRole("button", { name: "Continue to PANSS anyway" }).click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();
});

test("follow-up Diagnosis Continue keeps the plain pager link", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediagfol");
  const { draftId, patientId } = await createPatientWithDraft(request, physician);
  const followupId = await createFollowupDraft(request, physician, patientId, draftId);
  await seedAnswersApi(request, physician, followupId, {
    a_delusions: "yes",
    a_hallucinations: "yes",
  });

  await page.goto(`/#/encounters/${followupId}`);
  await expect(
    page.getByRole("heading", { name: "Diagnosis (step 1)" }),
  ).toBeVisible();
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Indeterminate",
    { timeout: 15000 },
  );

  // No confirmation on follow-up: partial answers continue directly.
  await page.locator("#wizard-continue").click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 2)" }),
  ).toBeVisible({ timeout: 15000 });
  await expect(page.getByText(CONTINUE_DIALOG_TEXT)).toHaveCount(0);
});
