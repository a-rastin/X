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
 * browser never computes criteria itself. Continue (`#wizard-continue`)
 * navigates regardless of completion; completion and acknowledgment govern
 * proposal generation, not page navigation.
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
  return { draftId: body.draft.id as string };
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

test("partial answers save as incomplete, never below threshold", async ({
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
  await expect(verdict).toContainText("Incomplete", { timeout: 10000 });
  await expect(verdict).toContainText("not a below-threshold result");
  await expect(page.getByText("Completed below threshold")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Acknowledge below-threshold result" }),
  ).toHaveCount(0);
  await expect(page.getByText("Still needed:")).toBeVisible();
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

test("Continue navigates without completing; partial answers survive", async ({
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
    "Incomplete",
    { timeout: 15000 },
  );

  await page.locator("#wizard-continue").click();
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
    "Incomplete",
    { timeout: 15000 },
  );
  await expect(
    page.locator("#diagnosis-preview-status"),
  ).not.toContainText("Diagnosis bypassed");
  await expect(
    page.getByRole("button", { name: "Bypass diagnosis" }),
  ).toHaveCount(0);
});

test("legacy bypass record shows status without a new bypass action", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediaglegbyp");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedBypassApi(request, physician, draftId);

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

  // Reload keeps the record; Continue still navigates.
  await page.reload();
  await expect(page.locator("#diagnosis-preview-status")).toContainText(
    "Diagnosis bypassed",
    { timeout: 10000 },
  );
  await page.locator("#wizard-continue").click();
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();
});
