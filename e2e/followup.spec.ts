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

/* S14 shared chart + follow-up draft entry (seam T9 browser; HTTP
 * enforcement covered by BT/http/test_followup.py and not duplicated here).
 *
 * Selector contract (see ChartPage.tsx / FollowupBaselinePanel.tsx headers):
 * - heading `data-testid="chart-heading"` (`#chart-heading`)
 * - draft badge `data-testid="chart-draft-badge"` (`#chart-draft-badge`, role=status)
 * - proposal `data-testid="chart-proposal-unavailable"` (`#chart-proposal-unavailable`)
 * - create button `data-testid="chart-create-followup"` (`#chart-create-followup`)
 * - directory button `data-testid="directory-create-followup"`
 * - baseline panel `data-testid="followup-baseline"` (`#followup-baseline`)
 * - reconciliation `#followup-baseline-status` (role=status)
 * - prior scores `data-testid="followup-prior-scores"` (`#followup-prior-scores`)
 * Wizard pages (see EncounterPage/HistorySection/EffectsSection headers):
 * - history `#history-heading`, phone `#history-phone-update`
 * - effects `#effects-heading`, panss `#panss-heading`, cssrs `#cssrs-heading`
 *
 * Test-only baseline fixtures travel inline (S49 is not a dependency); no
 * bypass-sign production route exists.
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

async function createPatientAndFreeSlot(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
): Promise<{ patientId: string; identifier: string; givenName: string }> {
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const payload = {
    identifier: uniquePatientId(),
    given_name: uniqueLetters("given"),
    family_name: uniqueLetters("family"),
    sex: "F",
    age: 30,
    clinical_status: "first_time",
  };
  const csrf = await physicianSession(request, physician);
  let create = await request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": key },
    data: payload,
  });
  if (create.status() === 401) {
    create = await request.post("/api/v1/patients", {
      headers: {
        "X-CSRF-Token": await physicianSession(request, physician),
        "Idempotency-Key": key,
      },
      data: payload,
    });
  }
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  await discardDraftApi(request, physician, body.draft.id as string, 1);
  return {
    patientId: body.patient.id as string,
    identifier: payload.identifier,
    givenName: payload.given_name,
  };
}

async function createFollowupApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  patientId: string,
  baseline?: Record<string, unknown>,
  baselineEncounterId?: string,
): Promise<string> {
  const csrf = await physicianSession(request, physician);
  const data: Record<string, unknown> = { kind: "follow_up" };
  if (baseline !== undefined) {
    data["baseline"] = baseline;
  }
  if (baselineEncounterId !== undefined) {
    data["baseline_encounter_id"] = baselineEncounterId;
  }
  const response = await request.post(`/api/v1/patients/${patientId}/encounters`, {
    headers: {
      "X-CSRF-Token": csrf,
      "Idempotency-Key": `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`,
    },
    data,
  });
  expect(response.status(), await response.text()).toBe(201);
  return ((await response.json()).encounter.id as string) ?? "";
}

// Test-only signed baseline snapshot (inline; S49 is not a dependency).
function testBaseline() {
  return {
    history_values: {
      h_exposure_dopamine_blocker: "yes",
      h_monitoring_baseline: "no",
      h_onset_timing: "unknown",
    },
    prior_scores: { panss_total: 68, cssrs_severity: 3 },
    medications: [{ catalog_drug_id: "demo-aspirin" }],
    provenance_note: "e2e signed baseline snapshot",
  };
}
const TEST_BASELINE_ID = "e2e-signed-baseline-001";

test("follow-up creation from chart navigates to draft with phone/history/effect pages", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2efucreate");
  const { patientId } = await createPatientAndFreeSlot(request, physician);

  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible();
  await expect(page.getByTestId("chart-draft-badge")).toContainText("No open draft");
  await expect(page.getByTestId("chart-proposal-unavailable")).toContainText(
    "generation_not_implemented",
  );

  await page.getByTestId("chart-create-followup").click();
  await expect(page).toHaveURL(/#\/encounters\/.+/, { timeout: 15000 });
  const encounterId = page.url().split("#/encounters/")[1]?.split(/[?#]/)[0] ?? "";
  expect(encounterId).not.toBe("");

  await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
    timeout: 15000,
  });
  // Follow-up baseline panel (empty shell) plus every wizard page mounts.
  await expect(page.getByTestId("followup-baseline")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#followup-baseline-status")).toContainText("not_required");
  await expect(page.locator("#history-heading")).toBeVisible();
  await expect(page.locator("#history-phone-update")).toBeVisible();
  await expect(page.locator("#effects-heading")).toBeVisible();
  await expect(page.locator("#panss-heading")).toBeVisible();
  await expect(page.locator("#cssrs-heading")).toBeVisible();

  // Resume: reload keeps the same draft with no fake proposal.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByTestId("followup-baseline")).toBeVisible({ timeout: 15000 });
});

test("shared-read journey: stranger sees demographics and badge but not draft content", async ({
  page,
  request,
}) => {
  const authorName = uniqueName("e2efuown");
  const strangerName = uniqueName("e2efustr");
  await ensurePhysician(request, authorName, "secret123");
  await ensurePhysician(request, strangerName, "secret123");
  const author = { username: authorName, password: "secret123" };
  const { patientId, identifier, givenName } = await createPatientAndFreeSlot(
    request,
    author,
  );
  const secret = `owneronly${uniqueLetters("kk")}`;
  const draftId = await createFollowupApi(request, author, patientId);
  const csrf = await physicianSession(request, author);
  const patched = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: { draft_data: { working_note: secret } },
  });
  expect(patched.status(), await patched.text()).toBe(200);

  await loginAs(page, "physician", strangerName, "secret123");
  await acknowledgeWarning(page);

  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("body")).toContainText(givenName);
  await expect(page.locator("body")).toContainText(identifier);
  await expect(page.getByTestId("chart-draft-badge")).toContainText("Open draft exists", {
    timeout: 10000,
  });
  await expect(page.getByTestId("chart-proposal-unavailable")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(secret);

  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("alert")).toContainText(
    "Only the draft author can access this draft.",
    { timeout: 15000 },
  );
  await expect(page.locator("body")).not.toContainText(secret);
  await expect(page.getByTestId("followup-baseline")).toHaveCount(0);

  // Administrator reads the same shared chart but cannot start or view drafts.
  await signOut(page);
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible({ timeout: 15000 });
  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("body")).toContainText(identifier);
  await expect(page.getByTestId("chart-create-followup")).toHaveCount(0);
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("alert")).toContainText("Physician access required", {
    timeout: 15000,
  });
  await expect(page.locator("body")).not.toContainText(secret);
});

test("baseline provenance, pending reconciliation, and historical prior scores render", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2efubase");
  const { patientId } = await createPatientAndFreeSlot(request, physician);
  const draftId = await createFollowupApi(
    request,
    physician,
    patientId,
    testBaseline(),
    TEST_BASELINE_ID,
  );

  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
    timeout: 15000,
  });
  const panel = page.getByTestId("followup-baseline");
  await expect(panel).toBeVisible({ timeout: 15000 });
  await expect(panel).toContainText("copied_baseline");
  await expect(panel).toContainText("h_exposure_dopamine_blocker");
  await expect(panel).toContainText("demo-aspirin");
  await expect(page.locator("#followup-baseline-status")).toContainText("pending");
  await expect(page.locator("#followup-baseline-status")).toContainText(TEST_BASELINE_ID);

  const prior = page.getByTestId("followup-prior-scores");
  await expect(prior).toBeVisible();
  await expect(prior).toContainText("PANSS total");
  await expect(prior).toContainText("68");
  await expect(prior).toContainText("historical — not a current answer");
  await expect(prior).toContainText("C-SSRS severity");

  // Prior scores never fill the fresh forms: PANSS/C-SSRS stay unanswered.
  await expect(page.locator("#panss-preview-status")).toContainText("unanswered", {
    timeout: 15000,
  });
  await expect(page.locator("#cssrs-heading")).toBeVisible();
});

test("occupied slot shows a generic conflict; different patients stay independent", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2efuconf");
  const first = await createPatientAndFreeSlot(request, physician);
  await createFollowupApi(request, physician, first.patientId);

  await page.goto(`/#/patients/${first.patientId}/chart`);
  await expect(page.getByTestId("chart-draft-badge")).toContainText("Open draft exists", {
    timeout: 15000,
  });
  await page.getByTestId("chart-create-followup").click();
  await expect(page.getByRole("alert")).toContainText(
    "An open draft already exists for this patient.",
    { timeout: 15000 },
  );
  // Generic conflict: no author, no clinical content, still on the chart.
  await expect(page.getByTestId("chart-heading")).toBeVisible();
  expect(page.url()).toContain("/chart");

  // A second patient keeps an independent slot via the directory entry point.
  // The directory pages server-side (many e2e patients exist), so search for
  // the new identifier before clicking its row action.
  const second = await createPatientAndFreeSlot(request, physician);
  await page.goto("/#/patients");
  await expect(page.getByRole("heading", { name: /Patient directory/ })).toBeVisible({
    timeout: 15000,
  });
  await page.getByLabel("Search by name or ID").fill(second.identifier);
  const startButton = page.getByRole("button", {
    name: `Start follow-up draft for ${second.identifier}`,
  });
  await expect(startButton).toBeVisible({ timeout: 15000 });
  await startButton.click();
  await expect(page).toHaveURL(/#\/encounters\/.+/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
    timeout: 15000,
  });
});

test("proposal stays honestly unavailable with chronology and no fake success", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2efuhonest");
  const { patientId, identifier } = await createPatientAndFreeSlot(request, physician);

  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("body")).toContainText(identifier);
  const proposal = page.getByTestId("chart-proposal-unavailable");
  await expect(proposal).toContainText("unavailable");
  await expect(proposal).toContainText("generation_not_implemented");
  // Honest unavailable: the panel states no proposal is shown and claims no
  // successful result — never a fabricated generated/ready proposal.
  await expect(proposal).toContainText("no proposal is shown");
  await expect(proposal).not.toContainText("Proposal generated");
  await expect(proposal).not.toContainText("Proposal ready");
  await expect(page.locator("body")).toContainText("No signed encounters yet");
  await expect(page.locator("body")).toContainText("Review navigation");

  // Navigation to review: the author reaches the same draft from the chart
  // action and lands on the encounter wizard (review opens there once
  // generation exists — until then the honest unavailable state stands).
  await page.getByTestId("chart-create-followup").click();
  await expect(page).toHaveURL(/#\/encounters\/.+/, { timeout: 15000 });
  await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
    timeout: 15000,
  });
});
