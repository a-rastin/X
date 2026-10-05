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

/* S13 attributed page notes (seam T9 browser; persistence/ownership covered
 * by BT/http/test_notes.py and not duplicated here).
 *
 * Selector contract (see web/src/features/notes/NotesSection.tsx header):
 * - panel `.xi-notes-panel` (per page)
 * - heading `#notes-{page}-heading`, e.g. `#notes-diagnosis-heading`
 * - list `#notes-{page}-list` (aria-live polite; always rendered, empty list
 *   has no size so assert attached, non-empty lists are visible)
 * - empty `#notes-{page}-empty`
 * - input `#notes-{page}-input` (labelled "Page note for {page}")
 * - add `#notes-{page}-add`
 * - status `#notes-{page}-status` (role=status)
 * - conflict `#notes-{page}-conflict` (role=alert)
 * - reload `#notes-{page}-reload`, retry `#notes-{page}-retry`
 * Demographics uses #notes-demographics-list/input/add/status.
 *
 * Separation: notes render in .xi-notes-panel, never inside the history
 * analysis_visible list. Text renders as plain escaped text (literal markup
 * like <b>hello</b> shows literally). No snapshot machinery here;
 * S40/S41/S59 are the mandatory end-to-end note-noninterference checks.
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
    create = await post(await physicianSession(request, physician));
  }
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return { draftId: body.draft.id as string };
}

/** Author-only note append through the real API (seam T1 setup helper for
 * denial tests). Returns the new encounter revision. */
async function postNoteApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  pageKey: string,
  noteText: string,
  revision: number,
): Promise<number> {
  const csrf = await physicianSession(request, physician);
  const response = await request.post(`/api/v1/encounters/${draftId}/notes`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"${revision}"` },
    data: { page: pageKey, text: noteText },
  });
  expect(response.status(), await response.text()).toBe(201);
  return ((await response.json()).revision as number) ?? revision + 1;
}

async function gotoEncounter(
  page: import("@playwright/test").Page,
  draftId: string,
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible();
  await expect(page.locator("#notes-diagnosis-heading")).toBeVisible();
}

test("demographics + diagnosis + history note controls mount with stable selectors", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2enotesmount");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  for (const section of ["demographics", "diagnosis", "history"]) {
    await expect(page.locator(`#notes-${section}-heading`)).toBeVisible();
    // Stable anchor: always rendered, but an empty <ul> has no bounding box
    // (Playwright reports hidden) until the first note arrives.
    await expect(page.locator(`#notes-${section}-list`)).toBeAttached();
    await expect(page.locator(`#notes-${section}-input`)).toBeVisible();
    await expect(page.locator(`#notes-${section}-add`)).toBeVisible();
    await expect(page.locator(`#notes-${section}-status`)).toBeVisible();
  }
  await expect(page.locator(".xi-notes-panel").first()).toBeVisible();
});

test("author adds a diagnosis note and sees it after reload with author/time", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2enotesadd");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  const input = page.locator("#notes-diagnosis-input");
  const add = page.locator("#notes-diagnosis-add");
  const status = page.locator("#notes-diagnosis-status");
  const list = page.locator("#notes-diagnosis-list");

  await expect(add).toBeDisabled();
  await input.fill("consider DDx");
  await expect(add).toBeEnabled();
  await add.click();
  await expect(status).toContainText("Note saved (revision", { timeout: 15000 });
  await expect(list).toContainText("consider DDx", { timeout: 10000 });
  await expect(list).toContainText(physician.username);
  await expect(list).toContainText("diagnosis");

  await page.reload();
  await expect(page.locator("#notes-diagnosis-list")).toContainText("consider DDx", {
    timeout: 10000,
  });
});

test("demographics note after creation survives reload and re-login with author/time", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2enotesdemo");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  const input = page.locator("#notes-demographics-input");
  const add = page.locator("#notes-demographics-add");
  const status = page.locator("#notes-demographics-status");
  const list = page.locator("#notes-demographics-list");
  const probe = `lives alone ${uniqueLetters("zz")}`;

  await input.fill(probe);
  await add.click();
  await expect(status).toContainText("Note saved (revision", { timeout: 15000 });
  await expect(list).toContainText(probe, { timeout: 10000 });
  await expect(list).toContainText(physician.username);
  await expect(list).toContainText("demographics");

  await page.reload();
  await expect(page.locator("#notes-demographics-list")).toContainText(probe, {
    timeout: 10000,
  });

  await signOut(page);
  await loginAs(page, "physician", physician.username, physician.password);
  await acknowledgeWarning(page);
  await gotoEncounter(page, draftId);
  const resumed = page.locator("#notes-demographics-list");
  await expect(resumed).toContainText(probe, { timeout: 10000 });
  await expect(resumed).toContainText(physician.username);
  await expect(resumed).toContainText("demographics");
});

test("note markup renders literally and stays separate from history", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2enotessep");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  const literal = "<b>hello</b> <script>alert(1)</script>";
  await page.locator("#notes-history-input").fill(literal);
  await page.locator("#notes-history-add").click();
  await expect(page.locator("#notes-history-status")).toContainText("Note saved", {
    timeout: 15000,
  });

  const list = page.locator("#notes-history-list");
  await expect(list).toContainText("<b>hello</b>", { timeout: 10000 });
  await expect(list).toContainText("<script>alert(1)</script>");
  // No elements are created: the markup shows as plain text.
  await expect(list.locator("b")).toHaveCount(0);
  await expect(list.locator("script")).toHaveCount(0);

  // Separate channel: the notes panel and the history analysis_visible list
  // are distinct sections; the history preview never carries notes.
  await expect(page.locator("#notes-history-heading")).toBeVisible();
  await expect(page.locator("#history-heading")).toBeVisible();
  await expect(page.locator(".xi-notes-panel").first()).toBeVisible();
  await expect(page.locator("#history-preview-status")).toBeVisible();
  await expect(page.locator("#history-preview-status")).not.toContainText("<b>hello</b>");
  await expect(page.locator("#history-preview-status")).not.toContainText("alert(1)");
});

test("stale note save keeps text and retries onto the fresh revision", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2enotestale");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  // First POST gets a real-shaped 412 so the UI must keep the typed text and
  // offer reconcile; the retry below goes to the live server and must save.
  // (Glob needs the trailing /notes segment: "*" never crosses "/".)
  let mockedOnce = false;
  await page.route("**/api/v1/encounters/*/notes", async (route) => {
    if (route.request().method() === "POST" && !mockedOnce) {
      mockedOnce = true;
      return route.fulfill({
        status: 412,
        contentType: "application/json",
        body: JSON.stringify({
          code: "STALE_REVISION",
          message: "The draft changed. Reload and reconcile your edits.",
          field_errors: {},
          request_id: "e2e-notes-stale",
          retryable: false,
        }),
      });
    }
    return route.continue();
  });

  const probe = `stale probe ${uniqueLetters("qq")}`;
  const input = page.locator("#notes-diagnosis-input");
  const status = page.locator("#notes-diagnosis-status");
  await input.fill(probe);
  await page.locator("#notes-diagnosis-add").click();

  const conflict = page.locator("#notes-diagnosis-conflict");
  await expect(conflict).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByRole("heading", { name: "Another tab saved first" }),
  ).toBeVisible();
  await expect(input).toHaveValue(probe);
  await expect(status).toContainText("Note conflict");

  await page.unroute("**/api/v1/encounters/*/notes");
  await page.getByRole("button", { name: "Keep my text and retry" }).click();
  await expect(status).toContainText("Note saved (revision", { timeout: 15000 });
  await expect(page.locator("#notes-diagnosis-list")).toContainText(probe, {
    timeout: 10000,
  });
});

test("failed note save never shows Saved and retry recovers", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2enotesfail");
  const { draftId } = await createPatientWithDraft(request, physician);

  await gotoEncounter(page, draftId);

  await page.route("**/api/v1/encounters/*/notes", (route) => {
    if (route.request().method() === "POST") {
      return route.abort();
    }
    return route.continue();
  });
  try {
    const probe = `offline probe ${uniqueLetters("ww")}`;
    const input = page.locator("#notes-panss-input");
    const status = page.locator("#notes-panss-status");
    await input.fill(probe);
    await page.locator("#notes-panss-add").click();
    await expect(status).toContainText("Note save failed", { timeout: 15000 });
    await expect(status).not.toContainText("Saved (revision");
    await expect(input).toHaveValue(probe);
    await expect(page.locator("#notes-panss-list")).not.toContainText(probe);
  } finally {
    await page.unroute("**/api/v1/encounters/*/notes");
  }

  await page.locator("#notes-panss-retry").click();
  await expect(page.locator("#notes-panss-status")).toContainText("Note saved (revision", {
    timeout: 15000,
  });
});

test("stranger physician and admin see a denial without note content", async ({
  page,
  request,
}) => {
  const author = uniqueName("e2enoteown");
  const stranger = uniqueName("e2enotestr");
  await ensurePhysician(request, author, "secret123");
  await ensurePhysician(request, stranger, "secret123");
  const authorCreds = { username: author, password: "secret123" };
  const secret = `owneronly${uniqueLetters("kk")}`;
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const csrf = await physicianSession(request, authorCreds);
  const created = await request.post("/api/v1/patients", {
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
  expect(created.status(), await created.text()).toBe(201);
  const draftId = ((await created.json()).draft.id as string) ?? "";
  await postNoteApi(request, authorCreds, draftId, "effects", secret, 1);

  await loginAs(page, "physician", stranger, "secret123");
  await acknowledgeWarning(page);
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("alert")).toContainText(
    "Only the draft author can access this draft.",
  );
  await expect(page.locator("body")).not.toContainText(secret);
  await expect(page.locator("#notes-effects-list")).toHaveCount(0);

  await signOut(page);
  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("alert")).toContainText("Physician access required");
  await expect(page.locator("body")).not.toContainText(secret);
  await expect(page.locator("#notes-effects-list")).toHaveCount(0);
});
