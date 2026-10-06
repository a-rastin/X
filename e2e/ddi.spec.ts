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

/* S20 medications + DDI into encounter history (seam T9 browser; strict
 * validation, fingerprint fencing and coverage rules covered by
 * BT/http/test_medications.py and not duplicated here).
 *
 * Selector contract (see MedicationsSection.tsx / DdiReportPanel.tsx headers):
 * - section heading `#medications-heading` ("Medications (step 7)")
 * - search input `#meds-search`, results `#meds-search-results`,
 *   per-row add `#meds-add-{index}` (aria-label "Add {name}")
 * - catalog versions `#meds-catalog-info`
 * - selected list `#meds-selected-list`, empty `#meds-selected-empty`,
 *   per-row remove `#meds-remove-{index}` (aria-label "Remove {id}")
 * - save button `#meds-save`, status `#meds-save-status` (aria-live),
 *   error `#meds-save-error` (role=alert)
 * - conflict panel `#meds-conflict` + `#meds-conflict-reload`/`#meds-conflict-retry`
 * - reconciliation notice `#meds-reconcile-status` + action `#meds-reconcile-action`
 * - DDI panel `#ddi-report-panel`, pending `#ddi-pending`,
 *   error `#ddi-error` + retry `#ddi-retry`, current `#ddi-report` with
 *   `#ddi-report-status` (aria-live), pairs `#ddi-report-pairs`,
 *   per-pair `#ddi-report-pair-{index}`, uncovered `#ddi-report-uncovered`,
 *   limitations `#ddi-report-limitations`
 *
 * Synthetic e2e catalog only (seeded in dev DB as limited release e2e-s20/1):
 * E2eAlpha + E2eBeta conflict (serious vs minor, markup in raw_text),
 * E2eGamma uncovered (limited scope). Real corpus stays awaiting_review.
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
): Promise<{ patientId: string; draftId: string }> {
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
    create = await post(await physicianSession(request, physician));
  }
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return {
    patientId: body.patient.id as string,
    draftId: body.draft.id as string,
  };
}

async function gotoEncounter(
  page: import("@playwright/test").Page,
  draftId: string,
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.locator("#medications-heading")).toBeVisible({
    timeout: 15000,
  });
  await expect(page.locator("#ddi-report-panel")).toBeVisible();
}

test("covered, uncovered and conflicting severities render with HTTP readback", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eddimain");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoEncounter(page, draftId);

  // Empty list starts pending, never a stale current report.
  await expect(page.locator("#meds-selected-empty")).toBeVisible();
  await expect(page.locator("#ddi-pending")).toBeVisible({ timeout: 10000 });
  await expect(page.locator("#ddi-report")).toHaveCount(0);

  // Demo-catalog search lists the seeded e2e drugs with versions.
  await page.locator("#meds-search").fill("e2e");
  await expect(page.locator("#meds-search-results")).toBeVisible({
    timeout: 10000,
  });
  await expect(page.locator("#meds-catalog-info")).toContainText("e2e-s20/1");
  await expect(page.locator("#meds-search-results")).toContainText("E2eAlpha");
  await expect(page.locator("#meds-search-results")).toContainText("E2eGamma");

  // Add 2 covered (Alpha+Beta conflict) + 1 uncovered (Gamma).
  await page.locator("#meds-add-0").click();
  await page.locator("#meds-add-1").click();
  await page.locator("#meds-add-2").click();
  await expect(page.locator("#meds-selected-list")).toContainText(
    "catalog_e2e_alpha",
  );
  await expect(page.locator("#meds-selected-list")).toContainText(
    "catalog_e2e_gamma",
  );

  await page.locator("#meds-save").click();
  await expect(page.locator("#meds-save-status")).toContainText("Saved", {
    timeout: 15000,
  });

  // Current versioned report with evidence, spans, conflicts and uncovered.
  const report = page.locator("#ddi-report");
  await expect(report).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#ddi-report-status")).toContainText(
    "Current report",
  );
  await expect(page.locator("#ddi-report-pairs")).toBeVisible();
  const pair0 = page.locator("#ddi-report-pair-0");
  await expect(pair0).toContainText("catalog_e2e_alpha");
  await expect(pair0).toContainText("catalog_e2e_beta");
  await expect(pair0).toContainText("Interaction found");
  await expect(pair0).toContainText("serious");
  await expect(pair0).toContainText("Conflicting severities");
  await expect(pair0).toContainText("minor");
  await expect(pair0).toContainText("E2eBeta increases the level");
  await expect(pair0).toContainText("Source:");
  await expect(page.locator("#ddi-report-uncovered")).toBeVisible();
  await expect(page.locator("#ddi-report-uncovered")).toContainText(
    "coverage unavailable",
  );
  await expect(page.locator("#ddi-report-limitations")).toContainText(
    "coverage unavailable",
  );
  // Never "safe" or "no interaction" as a claim.
  await expect(report).not.toContainText("safe", { ignoreCase: true });
  await expect(report).not.toContainText("no interaction", {
    ignoreCase: true,
  });

  // Markup literals render literally, never as elements.
  await expect(report).toContainText("<b>e2e-bold</b>");
  await expect(report).toContainText("<script>alert(1)</script>");
  await expect(report.locator("b")).toHaveCount(0);
  await expect(report.locator("script")).toHaveCount(0);

  // HTTP readback of the same versioned report (author session).
  const csrf = await physicianSession(request, physician);
  void csrf;
  const api = await request.get(`/api/v1/encounters/${draftId}/ddi-report`);
  expect(api.status(), await api.text()).toBe(200);
  const payload = await api.json();
  expect(payload.report_status).toBe("current");
  expect(payload.report).not.toBeNull();
  expect(payload.report.pairs.length).toBe(3);
  const found = payload.report.pairs.find(
    (p: Record<string, unknown>) =>
      (p["drug_a"] === "catalog_e2e_alpha" &&
        p["drug_b"] === "catalog_e2e_beta") ||
      (p["drug_a"] === "catalog_e2e_beta" &&
        p["drug_b"] === "catalog_e2e_alpha"),
  );
  expect(found.status).toBe("interaction_found");
  expect(found.highest_known_severity).toBe("serious");
  expect(found.conflicts.sort()).toEqual(["minor", "serious"]);
  expect(found.evidence.length).toBe(2);
  expect(
    payload.report.coverage_unavailable_medications.length,
  ).toBeGreaterThan(0);
});

test("removing a drug shows pending without stale rows", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eddistale");
  const { draftId } = await createPatientWithDraft(request, physician);
  await gotoEncounter(page, draftId);

  await page.locator("#meds-search").fill("e2e");
  await expect(page.locator("#meds-search-results")).toBeVisible({
    timeout: 10000,
  });
  await page.locator("#meds-add-0").click();
  await page.locator("#meds-add-1").click();
  await page.locator("#meds-add-2").click();
  await page.locator("#meds-save").click();
  await expect(page.locator("#ddi-report")).toBeVisible({ timeout: 15000 });

  // Remove one drug through the shared draft: report must go pending,
  // never showing the old rows as current (fresh pin needs a strict save;
  // the stale-fingerprint pending itself is the browser assertion here).
  await page.locator("#meds-remove-0").click();
  await expect(page.locator("#ddi-pending")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#ddi-pending")).toContainText("changed", {
    ignoreCase: true,
  });
  await expect(page.locator("#ddi-report")).toHaveCount(0);

  // HTTP readback agrees: pending stale_fingerprint with no rows as current.
  const csrf = await physicianSession(request, physician);
  void csrf;
  const api = await request.get(`/api/v1/encounters/${draftId}/ddi-report`);
  expect(api.status(), await api.text()).toBe(200);
  const payload = await api.json();
  expect(payload.report_status).toBe("pending");
  expect(payload.reason).toBe("stale_fingerprint");
  expect(payload.report).toBeNull();
});

test("follow-up copied medications need explicit reconcile before current", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eddifu");
  // Registration draft discarded to free the slot, then a follow-up with
  // copied baseline meds (test-only inline snapshot, no signing).
  const followSetup = await createPatientWithDraft(request, physician);
  const followPatientId = followSetup.patientId;
  const regDraftId = followSetup.draftId;
  let csrf = await physicianSession(request, physician);
  const discard = await request.post(
    `/api/v1/encounters/${regDraftId}/discard`,
    {
      headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
      data: { confirm: true },
    },
  );
  expect(discard.status(), await discard.text()).toBe(200);

  csrf = await physicianSession(request, physician);
  const baselineId = "e2e-signed-baseline-001";
  const createFu = await request.post(
    `/api/v1/patients/${followPatientId}/encounters`,
    {
      headers: { "X-CSRF-Token": csrf },
      data: {
        kind: "follow_up",
        baseline: {
          history_values: { h_exposure_dopamine_blocker: "yes" },
          prior_scores: {},
          medications: [
            { catalog_drug_id: "catalog_e2e_alpha" },
            { catalog_drug_id: "catalog_e2e_beta" },
          ],
          provenance_note: "e2e signed baseline snapshot",
        },
        baseline_encounter_id: baselineId,
      },
    },
  );
  expect(createFu.status(), await createFu.text()).toBe(201);
  const followId = (await createFu.json()).encounter.id as string;

  await gotoEncounter(page, followId);
  await expect(page.locator("#meds-reconcile-status")).toBeVisible({
    timeout: 15000,
  });
  await expect(page.locator("#meds-reconcile-status")).toContainText(
    "reconciliation",
    { ignoreCase: true },
  );
  const reconcile = page.locator("#meds-reconcile-action");
  await expect(reconcile).toBeVisible();
  await expect(page.locator("#ddi-pending")).toBeVisible({ timeout: 10000 });
  await expect(page.locator("#ddi-pending")).toContainText(
    "Reconciliation required",
  );
  await expect(page.locator("#ddi-report")).toHaveCount(0);

  await reconcile.click();
  await expect(page.locator("#ddi-report")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#ddi-report-status")).toContainText(
    "Current report",
  );
  await expect(page.locator("#ddi-report-pair-0")).toContainText(
    "Interaction found",
  );
});
