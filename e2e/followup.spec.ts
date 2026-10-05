import { test, expect } from "@playwright/test";
import type {
  APIRequestContext,
  APIResponse,
  Page,
} from "@playwright/test";
import type { PlaywrightWorkerArgs } from "@playwright/test";
type PlaywrightApi = PlaywrightWorkerArgs["playwright"];
import {
  uniqueName,
  uniqueLetters,
  uniquePatientId,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
} from "./helpers";
/* S14 shared chart + follow-up entry journeys (seam T9 browser; HTTP
 * enforcement covered by BT/http/test_followup.py and not duplicated here).
 * Every clinical value comes from the real endpoints — the browser never
 * invents proposals, scores, or chronology rows.
 *
 * Selector contract (see chart/api.ts, ChartPage.tsx,
 * FollowupBaselinePanel.tsx, DirectoryPage.tsx):
 * - chart heading (#chart-heading), demographics (#chart-demographics),
 *   chronology (#chart-chronology / #chart-chronology-empty),
 *   draft badge (#chart-draft-badge), proposal (#chart-proposal-status),
 *   error (#chart-error) + retry (#chart-retry)
 * - start follow-up (#followup-start), conflict (#followup-error, role=alert)
 * - back navigation (#chart-back), author resume (#chart-open-draft)
 * - directory chart link (#chart-link-<uuid>),
 *   directory start (#start-followup-<uuid>)
 * - baseline panel (#followup-baseline, #followup-prior-scores,
 *   #followup-reconciliation-status, #followup-copy-note)
 */

interface Creds {
  username: string;
  password: string;
}

interface CreatedPatient {
  patientId: string;
  draftId: string;
  identifier: string;
  givenName: string;
  familyName: string;
}

async function loginPhysician(page: Page, request: APIRequestContext, prefix: string): Promise<Creds> {
  const username = uniqueName(prefix);
  const password = "secret123";
  await ensurePhysician(request, username, password);
  await loginAs(page, "physician", username, password);
  await acknowledgeWarning(page);
  return { username, password };
}

interface ApiSetup {
  context: APIRequestContext;
  csrf: string;
}

async function physicianApi(playwrightApi: PlaywrightApi, baseURL: string | undefined, creds: Creds): Promise<ApiSetup> {
  const context = await playwrightApi.request.newContext({
    baseURL: baseURL ?? "http://localhost:5173",
  });
  const csrf = await physicianSession(context, creds);
  return { context, csrf };
}

async function apiCreatePatient(setup: ApiSetup, overrides: Record<string, unknown> = {}): Promise<CreatedPatient> {
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const payload = {
    identifier: uniquePatientId(),
    given_name: uniqueLetters("given"),
    family_name: uniqueLetters("family"),
    sex: "F",
    age: 30,
    clinical_status: "first_time",
    ...overrides,
  };
  const create = await setup.context.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": setup.csrf, "Idempotency-Key": key },
    data: payload,
  });
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return {
    patientId: body.patient.id as string,
    draftId: body.draft.id as string,
    identifier: body.patient.identifier as string,
    givenName: body.patient.given_name as string,
    familyName: body.patient.family_name as string,
  };
}

async function apiDiscardDraft(setup: ApiSetup, encounterId: string, revision = 1): Promise<void> {
  const discard = await setup.context.post(`/api/v1/encounters/${encounterId}/discard`, {
    headers: { "X-CSRF-Token": setup.csrf, "If-Match": `"${revision}"` },
    data: { confirm: true },
  });
  expect(discard.status(), await discard.text()).toBe(200);
}

async function apiCreateFollowup(setup: ApiSetup, patientId: string, baseline?: Record<string, unknown>, key?: string): Promise<APIResponse> {
  const headers: Record<string, string> = { "X-CSRF-Token": setup.csrf };
  if (key !== undefined) {
    headers["Idempotency-Key"] = key;
  }
  const body: Record<string, unknown> = { kind: "follow_up" };
  if (baseline !== undefined) {
    body.baseline = baseline;
  }
  return setup.context.post(`/api/v1/patients/${patientId}/encounters`, {
    headers,
    data: body,
  });
}

function followupBaseline(): Record<string, unknown> {
  return {
    history_values: {
      h_exposure_dopamine_blocker: "unknown",
      h_exposure_duration_3m: "unknown",
      h_onset_timing: "during_exposure",
    },
    prior_scores: { panss_total: 82, cssrs_severity: 3 },
    medications: [{ catalog_drug_id: "demo-haloperidol" }],
    provenance_note: "copied from signed baseline",
  };
}

test("physician B reads physician A's patient chart without draft content", async ({
  page,
  request,
  playwright,
  baseURL,
}) => {
  const physA: Creds = { username: uniqueName("e2efuowner"), password: "secret123" };
  await ensurePhysician(request, physA.username, physA.password);
  await loginPhysician(page, request, "e2efureader");

  const setupA = await physicianApi(playwright, baseURL, physA);
  try {
    const created = await apiCreatePatient(setupA);
    const secret = "owner-only clinical content";
    const patched = await setupA.context.patch(`/api/v1/encounters/${created.draftId}`, {
      headers: { "X-CSRF-Token": setupA.csrf, "If-Match": '"1"' },
      data: { draft_data: { working_note: secret } },
    });
    expect(patched.status(), await patched.text()).toBe(200);

  await page.goto(`/#/patients/${created.patientId}/chart`);
  await expect(page.locator("#chart-heading")).toBeVisible();
  await expect(page.locator("#chart-demographics")).toContainText(
    created.familyName,
  );
  await expect(page.locator("#chart-demographics")).toContainText(
    created.identifier,
  );
  await expect(page.locator("#chart-draft-badge")).toContainText(
    "Open draft exists",
  );
  await expect(page.locator("#chart-chronology-empty")).toContainText(
    "No signed encounters yet",
  );
  await expect(page.locator("#chart-proposal-status")).toContainText(
    "generation not implemented",
  );
  await expect(page.locator("body")).not.toContainText(secret);
  await expect(page.locator("body")).not.toContainText("working_note");
  // Directory links into the same chart without leaking draft content.
  await page.goto("/#/patients");
  await page.getByLabel("Search by name or ID").fill(created.identifier);
  const chartLink = page.locator(`#chart-link-${created.patientId}`);
  await expect(chartLink).toBeVisible();
  await expect(chartLink).toHaveAttribute(
    "href",
    `#/patients/${created.patientId}/chart`,
  );
  } finally {
    await setupA.context.dispose();
  }
});

test("start follow-up from chart creates a plain draft with unanswered scales", async ({
  page,
  request,
  playwright,
  baseURL,
}) => {
  const physician = await loginPhysician(page, request, "e2efustart");
  const setup = await physicianApi(playwright, baseURL, physician);
  try {
    const created = await apiCreatePatient(setup);
    await apiDiscardDraft(setup, created.draftId);

  await page.goto(`/#/patients/${created.patientId}/chart`);
  await expect(page.locator("#chart-draft-badge")).toContainText("No open draft");
  await page.locator("#followup-start").click();

  await expect(page).toHaveURL(/#\/encounters\/.+/);
  const encounterId = (page.url().split("/encounters/")[1] ?? "").split(/[?#]/)[0] ?? "";
  expect(encounterId.length).toBeGreaterThan(0);

  await expect(page.locator("#followup-baseline")).toBeVisible();
  await expect(page.locator("#followup-baseline")).toContainText("empty");
  await expect(page.locator("#panss-P1-1")).toBeVisible();
  await expect(page.locator('input[name^="panss-"]:checked')).toHaveCount(0);
  await expect(page.locator("#cssrs-css_i1_recent-yes")).toBeVisible();
  await expect(page.locator('input[name^="cssrs-"]:checked')).toHaveCount(0);
  await expect(page.locator("#followup-error")).toHaveCount(0);

  // Back on the chart, the author gets a resume link to their own draft.
  await page.goto(`/#/patients/${created.patientId}/chart`);
  await expect(page.locator("#chart-draft-badge")).toContainText(
    "Open draft exists",
  );
  const openDraft = page.locator("#chart-open-draft");
  await expect(openDraft).toBeVisible();
  await expect(openDraft).toHaveAttribute("href", `#/encounters/${encounterId}`);
  } finally {
    await setup.context.dispose();
  }
});

test("follow-up with baseline shows historical scores; scales stay unanswered", async ({
  page,
  request,
  playwright,
  baseURL,
}) => {
  const physician = await loginPhysician(page, request, "e2efubase");
  const setup = await physicianApi(playwright, baseURL, physician);
  try {
    const created = await apiCreatePatient(setup);
    await apiDiscardDraft(setup, created.draftId);
    const response = await apiCreateFollowup(setup, created.patientId, followupBaseline());
    expect(response.status(), await response.text()).toBe(201);
    const encounterId = (await response.json()).encounter.id as string;

  await page.goto(`/#/encounters/${encounterId}`);
  await expect(page.locator("#followup-baseline")).toBeVisible();
  await expect(page.locator("#followup-prior-scores")).toContainText(
    "Historical scores (not current answers)",
  );
  await expect(page.locator("#followup-prior-scores")).toContainText("82");
  await expect(page.locator("#followup-copy-note")).toContainText(
    "Copied from baseline",
  );
  await expect(page.locator("#followup-reconciliation-status")).toContainText(
    "pending",
  );
  await expect(page.locator("#panss-P1-1")).toBeVisible();
  await expect(page.locator('input[name^="panss-"]:checked')).toHaveCount(0);
  await expect(page.locator("#cssrs-css_i1_recent-yes")).toBeVisible();
  await expect(page.locator('input[name^="cssrs-"]:checked')).toHaveCount(0);
  } finally {
    await setup.context.dispose();
  }
});

test("two physicians racing follow-up creation get one draft and one generic conflict", async ({
  request,
  playwright,
  baseURL,
}) => {
  const physA: Creds = { username: uniqueName("e2efuracea"), password: "secret123" };
  const physB: Creds = { username: uniqueName("e2efuraceb"), password: "secret123" };
  await ensurePhysician(request, physA.username, physA.password);
  await ensurePhysician(request, physB.username, physB.password);
  const setupA = await physicianApi(playwright, baseURL, physA);
  const setupB = await physicianApi(playwright, baseURL, physB);
  try {
    const created = await apiCreatePatient(setupA);
    await apiDiscardDraft(setupA, created.draftId);

    const stem = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    const [resA, resB] = await Promise.all([
      apiCreateFollowup(setupA, created.patientId, undefined, `${stem}a`),
      apiCreateFollowup(setupB, created.patientId, undefined, `${stem}b`),
    ]);
    expect([resA.status(), resB.status()].sort()).toEqual([201, 409]);
    const loser = resA.status() === 409 ? resA : resB;
    const raw = await loser.text();
    expect(raw).toContain("OPEN_DRAFT_EXISTS");
    expect(raw).not.toContain("draft_data");
    expect(raw).not.toContain("author_id");
  } finally {
    await setupA.context.dispose();
    await setupB.context.dispose();
  }
});

test("different patients accept independent follow-up drafts", async ({
  page,
  request,
  playwright,
  baseURL,
}) => {
  const physician = await loginPhysician(page, request, "e2efuindep");
  const setup = await physicianApi(playwright, baseURL, physician);
  try {
    const first = await apiCreatePatient(setup);
    const second = await apiCreatePatient(setup);
    await apiDiscardDraft(setup, first.draftId);
    await apiDiscardDraft(setup, second.draftId);

    const resFirst = await apiCreateFollowup(setup, first.patientId);
    const resSecond = await apiCreateFollowup(setup, second.patientId);
    expect(resFirst.status(), await resFirst.text()).toBe(201);
    expect(resSecond.status(), await resSecond.text()).toBe(201);
    const firstId = (await resFirst.json()).encounter.id as string;
    const secondId = (await resSecond.json()).encounter.id as string;
    expect(firstId).not.toBe(secondId);
  } finally {
    await setup.context.dispose();
  }
});

test("admin chart is forbidden and anonymous callers see sign-in", async ({
  page,
  request,
  browser,
  playwright,
  baseURL,
}) => {
  const physA: Creds = { username: uniqueName("e2efuadmown"), password: "secret123" };
  await ensurePhysician(request, physA.username, physA.password);
  const setup = await physicianApi(playwright, baseURL, physA);
  let created: CreatedPatient;
  try {
    created = await apiCreatePatient(setup);
  } finally {
    await setup.context.dispose();
  }

  await loginAs(page, "admin", "admin", "admin");
  await page.goto(`/#/patients/${created.patientId}/chart`);
  await expect(page.locator("#chart-error")).toContainText("Only physicians");

  const anonCtx = await browser.newContext();
  try {
    const anonPage = await anonCtx.newPage();
    await anonPage.goto(`${baseURL ?? "http://localhost:5173"}/#/patients/${created.patientId}/chart`);
    await expect(
      anonPage.getByRole("button", { name: "Sign in" }),
    ).toBeVisible();
  } finally {
    await anonCtx.close();
  }
});
