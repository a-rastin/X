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

/* Registration step 4 and follow-up step 3 share one C-SSRS section:
 * recent ideation levels 1–5 (past month), each yes / no / unknown.
 * Severity and flags come from the server preview. Unknown is an explicit
 * answer, not a negative. A higher-level yes never changes other items.
 */

const RECENT_IDS = [
  "css_i1_recent",
  "css_i2_recent",
  "css_i3_recent",
  "css_i4_recent",
  "css_i5_recent",
] as const;

const RETIRED_TEXT = [
  "Lifetime ideation — lifetime history",
  "Intensity of the most severe recent ideation",
  "Recent behavior — past 3 months",
  "Lifetime behavior — lifetime history",
  "Lethality codings",
];

const RETIRED_SELECTORS = [
  "#cssrs-intensity",
  "#cssrs-behavior",
  "#cssrs-lethality",
  '[id*="css_i"][id*="_lifetime"]',
  '[id*="css_frequency"]',
  '[id*="css_duration"]',
  '[id*="css_controllability"]',
  '[id*="css_deterrents"]',
  '[id*="css_reasons"]',
  '[id*="css_beh_"]',
  '[id*="css_leth_"]',
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
  return {
    draftId: body.draft.id as string,
    patientId: body.patient.id as string,
  };
}

async function discardDraft(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
): Promise<void> {
  const csrf = await physicianSession(request, physician);
  const response = await request.post(`/api/v1/encounters/${draftId}/discard`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: { confirm: true },
  });
  expect(response.status(), await response.text()).toBe(200);
}

async function createFollowup(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  patientId: string,
): Promise<string> {
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

async function gotoCssrs(
  page: import("@playwright/test").Page,
  draftId: string,
  heading: string,
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}#cssrs`);
  await expect(page.locator("#cssrs-heading")).toHaveText(heading);
}

async function expectRecentIdeationOnly(
  page: import("@playwright/test").Page,
): Promise<void> {
  await expect(page.locator("#cssrs-group-recent-ideation")).toHaveText(
    "Recent ideation — past month",
  );
  await expect(page.locator('input[name^="cssrs-"]')).toHaveCount(15);
  for (const id of RECENT_IDS) {
    const radios = page.locator(`input[name="cssrs-${id}"]`);
    await expect(radios).toHaveCount(3);
    for (const value of ["yes", "no", "unknown"] as const) {
      const radio = page.locator(`#cssrs-${id}-${value}`);
      await expect(radio).toHaveCount(1);
      await expect(radio).toHaveAttribute("name", `cssrs-${id}`);
    }
  }
  for (const text of RETIRED_TEXT) {
    await expect(page.getByText(text, { exact: true })).toHaveCount(0);
  }
  for (const selector of RETIRED_SELECTORS) {
    await expect(page.locator(selector)).toHaveCount(0);
  }
}

async function expectUnknownSelected(
  page: import("@playwright/test").Page,
): Promise<void> {
  for (const id of RECENT_IDS) {
    await expect(page.locator(`#cssrs-${id}-unknown`)).toBeChecked();
  }
}

async function expectRecentSeverityOnly(
  page: import("@playwright/test").Page,
  severity: string,
): Promise<void> {
  const scores = page.locator("#cssrs-scores");
  await expect(scores).toBeVisible();
  await expect(scores.locator("tbody tr")).toHaveCount(1);
  await expect(scores.locator("tbody tr")).toHaveText(
    new RegExp(`Recent ideation severity \\(past month\\)\\s*${severity}`),
  );
  await expect(scores).not.toContainText("Lifetime");
  await expect(scores).not.toContainText("Intensity");
  await expect(scores).not.toContainText("Behavior");
  await expect(scores).not.toContainText("Lethality");
}

test("fresh registration step shows five recent items, Unknown selected", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsfresh");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoCssrs(page, draftId, "C-SSRS (step 4)");
  await expectRecentIdeationOnly(page);
  await expectUnknownSelected(page);

  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — recent ideation severity 0.", {
    timeout: 15000,
  });
  await expect(verdict).not.toContainText("no positive items");
  await expect(page.locator("#cssrs-high-risk-alert")).toHaveCount(0);
  await expect(page.locator("#cssrs-still-needed")).toHaveCount(0);
  await expectRecentSeverityOnly(page, "0");

  await page.locator("#cssrs-css_i1_recent-yes").focus();
  await page.keyboard.press(" ");
  await expect(page.locator("#cssrs-css_i1_recent-yes")).toBeChecked();
  for (const id of RECENT_IDS.slice(1)) {
    await expect(page.locator(`#cssrs-${id}-unknown`)).toBeChecked();
  }
});

test("follow-up C-SSRS step shows the same recent-ideation section", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsfu");
  const created = await createPatientWithDraft(request, physician);
  await discardDraft(request, physician, created.draftId);
  const followId = await createFollowup(request, physician, created.patientId);

  await gotoCssrs(page, followId, "C-SSRS (step 3)");
  await expectRecentIdeationOnly(page);
  await expectUnknownSelected(page);
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "Complete — recent ideation severity 0.",
    { timeout: 15000 },
  );
  await expectRecentSeverityOnly(page, "0");
});

test("a higher-level yes does not change other items and is not high-risk below 4", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrslvl");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoCssrs(page, draftId, "C-SSRS (step 4)");

  await page.locator("#cssrs-css_i1_recent-no").check();
  await page.locator("#cssrs-css_i3_recent-yes").check();

  await expect(page.locator("#cssrs-css_i1_recent-no")).toBeChecked();
  await expect(page.locator("#cssrs-css_i2_recent-unknown")).toBeChecked();
  await expect(page.locator("#cssrs-css_i3_recent-yes")).toBeChecked();
  await expect(page.locator("#cssrs-css_i4_recent-unknown")).toBeChecked();
  await expect(page.locator("#cssrs-css_i5_recent-unknown")).toBeChecked();

  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — recent ideation severity 3.", {
    timeout: 15000,
  });
  await expect(verdict).not.toContainText("no positive items");
  await expect(page.locator("#cssrs-clinical-review")).toBeVisible();
  await expect(page.locator("#cssrs-flags")).toBeVisible();
  await expect(page.locator("#cssrs-high-risk-alert")).toHaveCount(0);
  await expectRecentSeverityOnly(page, "3");
});

test("all explicit No yields the no-positive-items result", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsnone");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoCssrs(page, draftId, "C-SSRS (step 4)");

  for (const id of RECENT_IDS) {
    await page.locator(`#cssrs-${id}-no`).check();
  }

  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText(
    "Complete — no positive items (recent ideation severity 0).",
    { timeout: 15000 },
  );
  await expect(verdict).toContainText(
    "A negative screen does not prove absence of risk.",
  );
  await expect(page.locator("#cssrs-flags")).toContainText("no_positive_items");
  await expect(page.locator("#cssrs-high-risk-alert")).toHaveCount(0);
  await expect(page.locator("#cssrs-clinical-review")).toHaveCount(0);
  await expectRecentSeverityOnly(page, "0");
});

test("yes on ideation 4 or 5 shows the high-risk alert and recent severity only", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsurg");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoCssrs(page, draftId, "C-SSRS (step 4)");
  await expectRecentIdeationOnly(page);

  await page.locator("#cssrs-css_i4_recent-yes").check();
  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — recent ideation severity 4.", {
    timeout: 15000,
  });
  const urgent = page.locator("#cssrs-high-risk-alert");
  await expect(urgent).toBeVisible();
  await expect(urgent).toHaveAttribute("role", "alert");
  await expect(urgent).toContainText("high_risk_alert");
  await expect(page.locator("#cssrs-flags")).toBeVisible();
  await expect(page.locator("#cssrs-clinical-review")).toBeVisible();
  await expectRecentSeverityOnly(page, "4");
  for (const id of RECENT_IDS) {
    if (id === "css_i4_recent") {
      continue;
    }
    await expect(page.locator(`#cssrs-${id}-unknown`)).toBeChecked();
  }

  await page.locator("#cssrs-css_i4_recent-no").check();
  await page.locator("#cssrs-css_i5_recent-yes").check();
  await expect(verdict).toContainText("Complete — recent ideation severity 5.", {
    timeout: 15000,
  });
  await expect(page.locator("#cssrs-high-risk-alert")).toBeVisible();
  await expect(page.locator("#cssrs-css_i4_recent-no")).toBeChecked();
  await expect(page.locator("#cssrs-css_i5_recent-yes")).toBeChecked();
  await expect(page.locator("#cssrs-css_i1_recent-unknown")).toBeChecked();
  await expectRecentSeverityOnly(page, "5");
  await expectRecentIdeationOnly(page);
});

test("skip is not assessed and resume returns every item to Unknown", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsskip");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoCssrs(page, draftId, "C-SSRS (step 4)");
  await page.locator("#cssrs-css_i1_recent-yes").check();
  await expect(page.locator("#draft-save-status")).toContainText("Saved", {
    timeout: 15000,
  });

  await page.getByRole("button", { name: "Skip C-SSRS (not assessed)" }).click();
  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("not assessed", { timeout: 15000 });
  await expect(verdict).toContainText("No severity is reported");
  await expect(page.locator("#cssrs-scores")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Resume C-SSRS (clear skip)" }),
  ).toBeVisible();

  await page.reload();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "not assessed",
    { timeout: 15000 },
  );

  await page.getByRole("button", { name: "Resume C-SSRS (clear skip)" }).click();
  await expectUnknownSelected(page);
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "Complete — recent ideation severity 0.",
    { timeout: 15000 },
  );
  await expect(page.locator("#cssrs-preview-status")).not.toContainText(
    "no positive items",
  );
  await expect(
    page.getByRole("button", { name: "Skip C-SSRS (not assessed)" }),
  ).toBeVisible();
});

test("leaving the page keeps answers and does not turn skip into a negative screen", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsswitch");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoCssrs(page, draftId, "C-SSRS (step 4)");

  await page.locator("#cssrs-css_i1_recent-no").check();
  await page.locator("#cssrs-css_i2_recent-yes").check();
  await expect(page.locator("#draft-save-status")).toContainText("Saved", {
    timeout: 15000,
  });
  await page.getByRole("link", { name: "Back to directory" }).click();
  await expect(page).toHaveURL(/#\/patients/);

  await gotoCssrs(page, draftId, "C-SSRS (step 4)");
  await expect(page.locator("#cssrs-css_i1_recent-no")).toBeChecked();
  await expect(page.locator("#cssrs-css_i2_recent-yes")).toBeChecked();
  await expect(page.locator("#cssrs-css_i3_recent-unknown")).toBeChecked();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "Complete — recent ideation severity 2.",
    { timeout: 15000 },
  );

  await page.getByRole("button", { name: "Skip C-SSRS (not assessed)" }).click();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "not assessed",
    { timeout: 15000 },
  );
  await page.getByRole("link", { name: "Back to directory" }).click();
  await expect(page).toHaveURL(/#\/patients/);

  await gotoCssrs(page, draftId, "C-SSRS (step 4)");
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "not assessed",
    { timeout: 15000 },
  );
  await expect(page.locator("#cssrs-scores")).toHaveCount(0);
  await expect(page.locator("#cssrs-preview-status")).not.toContainText(
    "no positive items",
  );
  await expect(
    page.getByRole("button", { name: "Resume C-SSRS (clear skip)" }),
  ).toBeVisible();
});

test("recent severity 3 survives reload and re-login", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecssrsresume");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoCssrs(page, draftId, "C-SSRS (step 4)");

  for (const id of RECENT_IDS) {
    await page.locator(`#cssrs-${id}-no`).check();
  }
  await page.locator("#cssrs-css_i3_recent-yes").check();
  const verdict = page.locator("#cssrs-preview-status");
  await expect(verdict).toContainText("Complete — recent ideation severity 3.", {
    timeout: 15000,
  });
  await expect(page.locator("#cssrs-high-risk-alert")).toHaveCount(0);
  await expectRecentSeverityOnly(page, "3");

  await page.reload();
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "Complete — recent ideation severity 3.",
    { timeout: 15000 },
  );
  await expect(page.locator("#cssrs-css_i3_recent-yes")).toBeChecked();
  await expect(page.locator("#cssrs-css_i1_recent-no")).toBeChecked();

  await signOut(page);
  await loginAs(page, "physician", physician.username, physician.password);
  await acknowledgeWarning(page);
  await gotoCssrs(page, draftId, "C-SSRS (step 4)");
  await expect(page.locator("#cssrs-preview-status")).toContainText(
    "Complete — recent ideation severity 3.",
    { timeout: 15000 },
  );
  await expectRecentSeverityOnly(page, "3");
  await expect(page.locator("#cssrs-clinical-review")).toBeVisible();
  await expect(page.locator("#cssrs-high-risk-alert")).toHaveCount(0);
});
