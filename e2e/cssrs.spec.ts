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

/* S11 C-SSRS journeys with distinct results (seam T9 browser; evaluator and
 * HTTP enforcement covered by BT/assessments/test_cssrs.py and not
 * duplicated here). Every severity/flag comes from the server preview — the
 * browser never computes severity or flags and never shows a composite risk
 * score.
 *
 * Selector contract (see CssrsSection.tsx header):
 * - section heading "C-SSRS (step 4)" (#cssrs-heading)
 * - yes/no radios `#cssrs-{itemId}-yes|no` (name `cssrs-{itemId}`)
 * - numeric radios `#cssrs-{itemId}-{n}` (name `cssrs-{itemId}`), e.g.
 *   `#cssrs-css_frequency-2`, `#cssrs-css_leth_actual_recent-0`
 * - verdict `#cssrs-preview-status` (aria-live polite)
 * - scores `#cssrs-scores` (em dashes for null, never zeros)
 * - flags container `#cssrs-flags`; urgent panel `#cssrs-high-risk-alert`
 *   (role=alert); review panel `#cssrs-clinical-review`
 * - dimension panels `#cssrs-intensity`, `#cssrs-behavior`,
 *   `#cssrs-lethality` (separate, never combined)
 * - skip button "Skip C-SSRS (not assessed)"
 * - resume button "Resume C-SSRS (clear skip)"
 * - still-needed list `#cssrs-still-needed`
 * - item errors `#cssrs-{id}-error` (role=alert)
 */

const IDEATION_IDS = [
  "css_i1_recent",
  "css_i2_recent",
  "css_i3_recent",
  "css_i4_recent",
  "css_i5_recent",
  "css_i1_lifetime",
  "css_i2_lifetime",
  "css_i3_lifetime",
  "css_i4_lifetime",
  "css_i5_lifetime",
];
const BEHAVIOR_IDS = [
  "css_beh_actual_recent",
  "css_beh_interrupted_recent",
  "css_beh_aborted_recent",
  "css_beh_preparatory_recent",
  "css_beh_nssi_recent",
  "css_beh_actual_lifetime",
  "css_beh_interrupted_lifetime",
  "css_beh_aborted_lifetime",
  "css_beh_preparatory_lifetime",
  "css_beh_nssi_lifetime",
];

/** Source-derived all-negative screen: no ideation, no behavior. */
function allNo(): Record<string, unknown> {
  return {
    ...Object.fromEntries(IDEATION_IDS.map((id) => [id, "no"])),
    ...Object.fromEntries(BEHAVIOR_IDS.map((id) => [id, "no"])),
  };
}

/** Source-derived level-3 worked example: method without intent, recent. */
function level3Complete(): Record<string, unknown> {
  return {
    ...allNo(),
    css_i3_recent: "yes",
    css_frequency: 2,
    css_duration: 2,
    css_controllability: 3,
    css_deterrents: 1,
    css_reasons: 4,
  };
}

/** Recent level-4 endorsement: urgent flag expected. */
function highRiskRecent(): Record<string, unknown> {
  return {
    ...allNo(),
    css_i4_recent: "yes",
    css_frequency: 4,
    css_duration: 3,
    css_controllability: 4,
    css_deterrents: 5,
    css_reasons: 5,
  };
}

/** Historical actual attempt with lethality: review, no urgent flag. */
function lifetimeBehaviorReview(): Record<string, unknown> {
  return {
    ...allNo(),
    css_beh_actual_lifetime: "yes",
    css_leth_actual_lifetime: 2,
  };
}

/** NSSI alone: review, never high-risk. */
function nssiRecentOnly(): Record<string, unknown> {
  return { ...allNo(), css_beh_nssi_recent: "yes" };
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
  answers: Record<string, unknown>,
  revision = 1,
): Promise<number> {
  const csrf = await physicianSession(request, physician);
  const saved = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"${revision}"` },
    data: { draft_data: { cssrs: { answers } } },
  });
  expect(saved.status(), await saved.text()).toBe(200);
  return (await saved.json()).revision as number;
}

async function gotoCssrs(
  page: import("@playwright/test").Page,
  draftId: string,
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}`);
  await expect(
    page.getByRole("heading", { name: "C-SSRS (step 4)" }),
  ).toBeVisible();
}

test("fresh form has 29 unanswered items and null severity, keyboard accessible", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsfresh");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoCssrs(page, draftId);

  // No radio is pre-selected anywhere in the 29-item form.
  await expect(page.locator("#cssrs-heading")).toBeVisible();
  await expect(
    page.locator('input[name^="cssrs-"]:checked'),
  ).toHaveCount(0);
  // Spot-check the selector contract edges: ideation yes/no, intensity
  // numeric, lethality zero-anchored codings.
  for (const id of [
    "#cssrs-css_i1_recent-yes",
    "#cssrs-css_i1_recent-no",
    "#cssrs-css_i5_lifetime-yes",
    "#cssrs-css_frequency-1",
    "#cssrs-css_frequency-5",
    "#cssrs-css_leth_actual_recent-0",
    "#cssrs-css_leth_actual_recent-5",
  ]) {
    await expect(page.locator(id)).toBeVisible();
    await expect(page.locator(id)).not.toBeChecked();
  }

  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("No answers yet", { timeout: 10000 });
  await expect(verdict).toContainText("29 items with nothing selected");
  await expect(verdict).not.toContainText("Complete");

  // Null severities render as em dashes, never zeros.
  const scores = page.locator("#cssrs-scores");
  await expect(scores).toBeVisible();
  await expect(scores).toContainText("—");

  // Keyboard access: focusing a radio and pressing Space selects it, and the
  // server preview follows without any mouse.
  await page.locator("#cssrs-css_i1_recent-yes").focus();
  await page.keyboard.press(" ");
  await expect(page.locator("#cssrs-css_i1_recent-yes")).toBeChecked();
  await expect(verdict).toContainText("Incomplete", { timeout: 15000 });
  await expect(verdict).toContainText("suppressed to null");
});

test("skip persists not_assessed across reload; resume clears it", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsskip");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoCssrs(page, draftId);
  await page
    .getByRole("button", { name: "Skip C-SSRS (not assessed)" })
    .click();

  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("not assessed", { timeout: 15000 });
  await expect(
    page.getByRole("button", { name: "Resume C-SSRS (clear skip)" }),
  ).toBeVisible();

  // Skip survives a full reload via the saved draft_data body.
  await page.reload();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "not assessed",
    { timeout: 10000 },
  );

  await page
    .getByRole("button", { name: "Resume C-SSRS (clear skip)" })
    .click();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "No answers yet",
    { timeout: 15000 },
  );
  await expect(
    page.getByRole("button", { name: "Skip C-SSRS (not assessed)" }),
  ).toBeVisible();
});

test("one missing item suppresses severity with Still needed guidance", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrspart");
  const { draftId } = await createPatientWithDraft(request, physician);
  const answers = allNo();
  delete answers["css_beh_nssi_lifetime"];
  await seedAnswersApi(request, physician, draftId, answers);

  await gotoCssrs(page, draftId);

  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Incomplete", { timeout: 10000 });
  await expect(verdict).toContainText("1 required item");
  await expect(verdict).toContainText("suppressed to null");

  // Null severities render as em dashes, never zeros or guessed negatives.
  const scores = page.locator("#cssrs-scores");
  await expect(scores).toContainText("—");

  const stillNeeded = page.locator("#cssrs-still-needed");
  await expect(stillNeeded).toBeVisible();
  await expect(stillNeeded).toContainText("Still needed:");
});

test("level-3 yes never auto-fills lower levels", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrslvl");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoCssrs(page, draftId);

  // Endorsing level 3 leaves lower levels explicitly unanswered.
  await page.locator("#cssrs-css_i3_recent-yes").check();
  await expect(page.locator("#cssrs-css_i3_recent-yes")).toBeChecked();
  for (const id of [
    "#cssrs-css_i1_recent-yes",
    "#cssrs-css_i1_recent-no",
    "#cssrs-css_i2_recent-yes",
    "#cssrs-css_i2_recent-no",
  ]) {
    await expect(page.locator(id)).not.toBeChecked();
  }

  // Partial work reports what is still needed, never a severity.
  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Incomplete", { timeout: 15000 });
  await expect(verdict).toContainText("suppressed to null");
  await expect(page.locator("#cssrs-still-needed")).toBeVisible();
});

test("level-3 completion via seeded answers yields severity 3, dimensions separate", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsev");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, level3Complete());

  await gotoCssrs(page, draftId);

  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — Recent severity 3", {
    timeout: 10000,
  });
  await expect(verdict).toContainText("Lifetime severity 0");
  await expect(verdict).toContainText("Max 3");

  // Every seeded answer is reflected as a checked radio (server truth):
  // 20 ideation/behavior answers plus 5 intensity dimensions.
  await expect(page.locator("#cssrs-css_i3_recent-yes")).toBeChecked();
  await expect(page.locator("#cssrs-css_i1_recent-no")).toBeChecked();
  await expect(page.locator("#cssrs-css_frequency-2")).toBeChecked();
  await expect(
    page.locator('input[name^="cssrs-"]:checked'),
  ).toHaveCount(25);

  // Intensity, behavior, and lethality render as separate panels with no
  // composite risk score anywhere.
  await expect(page.locator("#cssrs-intensity")).toContainText("frequency 2");
  await expect(page.locator("#cssrs-behavior")).toBeVisible();
  await expect(page.locator("#cssrs-lethality")).toBeVisible();
  await expect(page.getByText("No composite risk score")).toBeVisible();
});

test("recent level-4 shows a persistent urgent alert with text access", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsurg");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, highRiskRecent());

  await gotoCssrs(page, draftId);

  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — Recent severity 4", {
    timeout: 10000,
  });

  // The urgent panel is persistent text with a heading (never color-alone)
  // and an alert role; the review panel accompanies it.
  const urgent = page.locator("#cssrs-high-risk-alert");
  await expect(urgent).toBeVisible();
  await expect(urgent).toHaveAttribute("role", "alert");
  await expect(
    page.getByRole("heading", { name: /Urgent/ }),
  ).toBeVisible();
  await expect(urgent).toContainText("high_risk_alert");
  await expect(urgent).toContainText("persists");
  await expect(page.locator("#cssrs-flags")).toBeVisible();
  await expect(page.locator("#cssrs-clinical-review")).toBeVisible();

  // Radios stay keyboard-focusable beside the alert.
  await page.locator("#cssrs-css_i5_recent-no").focus();
  await expect(page.locator("#cssrs-css_i5_recent-no")).toBeFocused();
});

test("historical findings and NSSI alone flag review without the urgent alert", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsrev");
  const first = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, first.draftId, lifetimeBehaviorReview());

  await gotoCssrs(page, first.draftId);

  let verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — Recent severity 0", {
    timeout: 10000,
  });
  const review = page.locator("#cssrs-clinical-review");
  await expect(review).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Clinical review/ }),
  ).toBeVisible();
  await expect(page.locator("#cssrs-high-risk-alert")).toHaveCount(0);

  // NSSI alone contributes to review but never triggers high-risk by itself.
  const second = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, second.draftId, nssiRecentOnly());
  await gotoCssrs(page, second.draftId);

  verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — Recent severity 0", {
    timeout: 10000,
  });
  await expect(verdict).toContainText("Max 0");
  await expect(page.locator("#cssrs-clinical-review")).toBeVisible();
  await expect(page.locator("#cssrs-high-risk-alert")).toHaveCount(0);
});

test("forged invalid values are rejected server-side, never zero-filled", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsinv");
  const { draftId } = await createPatientWithDraft(request, physician);

  // Forge an out-of-range intensity value straight through the PATCH path.
  const csrf = await physicianSession(request, physician);
  const forged = { ...level3Complete(), css_frequency: 9 };
  const saved = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"1"` },
    data: { draft_data: { cssrs: { answers: forged } } },
  });
  expect(saved.status(), await saved.text()).toBe(200);

  const preview = await request.get(`/api/v1/encounters/${draftId}/cssrs`);
  expect(preview.status(), await preview.text()).toBe(200);
  const body = await preview.json();
  expect(body.evaluation.item_errors.css_frequency).toBeDefined();
  expect(body.evaluation.scores.ideation_severity_max).toBeNull();

  // The browser surfaces the server rejection instead of a zero severity.
  await gotoCssrs(page, draftId);
  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Incomplete", { timeout: 10000 });
  await expect(verdict).toContainText("suppressed to null");
  await expect(
    page.getByText("rejected (never zero-filled)"),
  ).toBeVisible();
  await expect(page.locator("#cssrs-css_frequency-error")).toBeVisible();
});

test("resume preserves severity 3 after reload and re-login", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsresume");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, level3Complete());

  await gotoCssrs(page, draftId);
  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — Recent severity 3", {
    timeout: 10000,
  });
  await expect(verdict).toContainText("Max 3");

  // Reload keeps the completed state from the same draft_data body.
  await page.reload();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "Complete — Recent severity 3",
    { timeout: 10000 },
  );

  // Re-login as the same author sees the same complete evaluation.
  await signOut(page);
  await loginAs(page, "physician", physician.username, physician.password);
  await acknowledgeWarning(page);
  await gotoCssrs(page, draftId);
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "Complete — Recent severity 3",
    { timeout: 10000 },
  );
  await expect(page.locator("#cssrs-scores")).toContainText(
    "Maximum endorsed severity",
  );
});

test("switching pages never erases answers and never turns skip into zero", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsswitch");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoCssrs(page, draftId);

  // Answer two items, wait for the acknowledged save, then leave and return.
  await page.locator("#cssrs-css_i1_recent-no").check();
  await page.locator("#cssrs-css_beh_nssi_recent-no").check();
  await expect(page.locator("#draft-save-status")).toContainText("Saved", {
    timeout: 15000,
  });
  await page.getByRole("link", { name: "Back to directory" }).click();
  await expect(page).toHaveURL(/#\/patients/);
  await expect(
    page.getByRole("heading", { name: "Patients" }),
  ).toBeVisible();

  await gotoCssrs(page, draftId);
  await expect(page.locator("#cssrs-css_i1_recent-no")).toBeChecked();
  await expect(page.locator("#cssrs-css_beh_nssi_recent-no")).toBeChecked();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "Incomplete",
    { timeout: 10000 },
  );

  // A skip also survives the round trip: still not assessed, never zero.
  await page
    .getByRole("button", { name: "Skip C-SSRS (not assessed)" })
    .click();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "not assessed",
    { timeout: 15000 },
  );
  await page.getByRole("link", { name: "Back to directory" }).click();
  await expect(page).toHaveURL(/#\/patients/);

  await gotoCssrs(page, draftId);
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "not assessed",
    { timeout: 10000 },
  );
  await expect(
    page.getByRole("button", { name: "Resume C-SSRS (clear skip)" }),
  ).toBeVisible();
  await expect(page.locator("#cssrs-scores")).toContainText("—");
});
