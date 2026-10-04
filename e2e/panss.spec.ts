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

/* S10 PANSS journeys without implicit minimum answers (seam T9 browser;
 * evaluator and HTTP enforcement covered by
 * BT/assessments/test_panss.py and not duplicated here). Every score comes
 * from the server preview — the browser never sums subscales or totals.
 *
 * Selector contract (see PanssSection.tsx header):
 * - section heading "PANSS (step 3)" (#panss-heading)
 * - radios `#panss-{P1..P7,N1..N7,G1..G16}-{1..7}` (name `panss-{itemId}`)
 * - verdict `#panss-preview-status` (aria-live polite)
 * - scores `#panss-scores`
 * - skip button "Skip PANSS (not assessed)"
 * - resume button "Resume PANSS (clear skip)"
 * - still-needed list `#panss-still-needed`
 */

const POSITIVE_IDS = ["P1", "P2", "P3", "P4", "P5", "P6", "P7"];
const NEGATIVE_IDS = ["N1", "N2", "N3", "N4", "N5", "N6", "N7"];
const GENERAL_IDS = [
  "G1",
  "G2",
  "G3",
  "G4",
  "G5",
  "G6",
  "G7",
  "G8",
  "G9",
  "G10",
  "G11",
  "G12",
  "G13",
  "G14",
  "G15",
  "G16",
];
const ALL_IDS = [...POSITIVE_IDS, ...NEGATIVE_IDS, ...GENERAL_IDS];

function allOnes(): Record<string, number> {
  return Object.fromEntries(ALL_IDS.map((id) => [id, 1]));
}

function allSevens(): Record<string, number> {
  return Object.fromEntries(ALL_IDS.map((id) => [id, 7]));
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
    data: { draft_data: { panss: { answers } } },
  });
  expect(saved.status(), await saved.text()).toBe(200);
  return (await saved.json()).revision as number;
}

async function gotoPanss(
  page: import("@playwright/test").Page,
  draftId: string,
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}`);
  await expect(
    page.getByRole("heading", { name: "PANSS (step 3)" }),
  ).toBeVisible();
}

test("fresh form has 30 unanswered items and null total, no pre-filled 1s", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2epanssfresh");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoPanss(page, draftId);

  // No radio is pre-selected anywhere in the 30-item form.
  await expect(page.locator("#panss-heading")).toBeVisible();
  await expect(
    page.locator('input[name^="panss-"]:checked'),
  ).toHaveCount(0);
  // Spot-check the selector contract edges: first/last item, low/high value.
  for (const id of ["#panss-P1-1", "#panss-P1-7", "#panss-G16-1", "#panss-G16-7"]) {
    await expect(page.locator(id)).toBeVisible();
    await expect(page.locator(id)).not.toBeChecked();
  }

  const verdict = page.locator("#panss-preview-status");
  await expect(verdict).toContainText("No answers yet", { timeout: 10000 });
  await expect(verdict).toContainText("30 items with nothing selected");
  await expect(verdict).not.toContainText("Complete");

  // Null scores render as em dashes, never zeros or ones.
  const scores = page.locator("#panss-scores");
  await expect(scores).toBeVisible();
  await expect(scores).toContainText("—");
  await expect(scores).not.toContainText("Total bands are");
});

test("skip persists not_assessed across reload; resume clears it", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2epanssskip");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoPanss(page, draftId);
  await page
    .getByRole("button", { name: "Skip PANSS (not assessed)" })
    .click();

  const verdict = page.locator("#panss-preview-status");
  await expect(verdict).toContainText("not assessed", { timeout: 15000 });
  await expect(
    page.getByRole("button", { name: "Resume PANSS (clear skip)" }),
  ).toBeVisible();

  // Skip survives a full reload via the saved draft_data body.
  await page.reload();
  await expect(page.locator("#panss-preview-status")).toContainText(
    "not assessed",
    { timeout: 10000 },
  );

  await page
    .getByRole("button", { name: "Resume PANSS (clear skip)" })
    .click();
  await expect(page.locator("#panss-preview-status")).toContainText(
    "No answers yet",
    { timeout: 15000 },
  );
  await expect(
    page.getByRole("button", { name: "Skip PANSS (not assessed)" }),
  ).toBeVisible();
});

test("one missing item suppresses the total with Still needed guidance", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2epansspart");
  const { draftId } = await createPatientWithDraft(request, physician);
  const answers = allOnes();
  delete answers["G16"];
  await seedAnswersApi(request, physician, draftId, answers);

  await gotoPanss(page, draftId);

  const verdict = page.locator("#panss-preview-status");
  await expect(verdict).toContainText("Incomplete", { timeout: 10000 });
  await expect(verdict).toContainText("1 required item");
  await expect(verdict).toContainText("suppressed to null");

  // Complete subscales still show; the incomplete General and Total do not.
  const scores = page.locator("#panss-scores");
  await expect(scores).toContainText("7");
  await expect(scores).toContainText("—");

  const stillNeeded = page.locator("#panss-still-needed");
  await expect(stillNeeded).toBeVisible();
  await expect(stillNeeded).toContainText("Still needed:");
});

test("all-1 completion via UI yields 7/7/16/30", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2epanssone");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoPanss(page, draftId);

  for (const id of ALL_IDS) {
    await page.locator(`#panss-${id}-1`).check();
  }

  const verdict = page.locator("#panss-preview-status");
  await expect(verdict).toContainText("Complete — Positive 7", {
    timeout: 25000,
  });
  await expect(verdict).toContainText("Negative 7");
  await expect(verdict).toContainText("General 16");
  await expect(verdict).toContainText("Total 30");

  const scores = page.locator("#panss-scores");
  await expect(scores).toContainText("Positive (P1–P7)");
  await expect(scores).toContainText("Total (30 items)");
  // Informational band only — never a treatment gate.
  await expect(page.getByText("Informational only")).toBeVisible();
  await expect(page.getByText("not a treatment gate")).toBeVisible();
});

test("all-7 maximum yields 49/49/112/210", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2epanssseven");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, allSevens());

  await gotoPanss(page, draftId);

  const verdict = page.locator("#panss-preview-status");
  await expect(verdict).toContainText("Complete — Positive 49", {
    timeout: 10000,
  });
  await expect(verdict).toContainText("Negative 49");
  await expect(verdict).toContainText("General 112");
  await expect(verdict).toContainText("Total 210");

  // Every seeded answer is reflected as a checked radio (server truth).
  await expect(page.locator("#panss-P1-7")).toBeChecked();
  await expect(page.locator("#panss-N7-7")).toBeChecked();
  await expect(page.locator("#panss-G16-7")).toBeChecked();
  await expect(
    page.locator('input[name^="panss-"]:checked'),
  ).toHaveCount(30);
});

test("forged invalid values are rejected server-side, never zero-filled", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2epanssinv");
  const { draftId } = await createPatientWithDraft(request, physician);

  // Forge an out-of-range value straight through the autosave PATCH path.
  const csrf = await physicianSession(request, physician);
  const forged = { ...allOnes(), P1: 8 };
  const saved = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"1"` },
    data: { draft_data: { panss: { answers: forged } } },
  });
  expect(saved.status(), await saved.text()).toBe(200);

  const preview = await request.get(`/api/v1/encounters/${draftId}/panss`);
  expect(preview.status(), await preview.text()).toBe(200);
  const body = await preview.json();
  expect(body.evaluation.item_errors.P1).toBeDefined();
  expect(body.evaluation.scores.total).toBeNull();

  // The browser surfaces the server rejection instead of a zero total.
  await gotoPanss(page, draftId);
  const verdict = page.locator("#panss-preview-status");
  await expect(verdict).toContainText("Incomplete", { timeout: 10000 });
  await expect(verdict).toContainText("suppressed to null");
  await expect(
    page.getByText("rejected (never zero-filled)"),
  ).toBeVisible();
});

test("resume preserves completeness after reload and re-login", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2epanssresume");
  const { draftId } = await createPatientWithDraft(request, physician);
  await seedAnswersApi(request, physician, draftId, allOnes());

  await gotoPanss(page, draftId);
  const verdict = page.locator("#panss-preview-status");
  await expect(verdict).toContainText("Complete — Positive 7", {
    timeout: 10000,
  });
  await expect(verdict).toContainText("Total 30");

  // Reload keeps the completed state from the same draft_data body.
  await page.reload();
  await expect(page.locator("#panss-preview-status")).toContainText(
    "Complete — Positive 7",
    { timeout: 10000 },
  );
  await expect(page.locator("#panss-preview-status")).toContainText(
    "Total 30",
  );

  // Re-login as the same author sees the same complete evaluation.
  await signOut(page);
  await loginAs(page, "physician", physician.username, physician.password);
  await acknowledgeWarning(page);
  await gotoPanss(page, draftId);
  await expect(page.locator("#panss-preview-status")).toContainText(
    "Complete — Positive 7",
    { timeout: 10000 },
  );
  await expect(page.locator("#panss-scores")).toContainText("Total (30 items)");
});
