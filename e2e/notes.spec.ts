import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import {
  uniqueName,
  uniqueLetters,
  uniquePatientId,
  ensurePhysician,
  physicianSession,
  loginAs,
  acknowledgeWarning,
} from "./helpers";

// T9: page notes are retired throughout both wizard variants. Working-note
// autosave, failure/retry, conflict, and navigation guards also run in autosave.spec.ts.
async function setup(page: Page, request: APIRequestContext) {
  const physician = { username: uniqueName("e2eretirednotes"), password: "secret123" };
  await ensurePhysician(request, physician.username, physician.password);
  await loginAs(page, "physician", physician.username, physician.password);
  await acknowledgeWarning(page);
  return physician;
}

function watchRetiredRequests(page: Page): string[] {
  const requests: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/encounters\/[^/]+\/notes(?:\?|$)/.test(request.url())) {
      requests.push(`${request.method()} ${request.url()}`);
    }
  });
  return requests;
}

async function expectNoPageNotes(page: Page) {
  await expect(page.locator(".xi-notes-panel, [id^='notes-']")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: /page note/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /add page note/i })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /page notes/i })).toHaveCount(0);
}

async function createDraft(request: APIRequestContext, physician: { username: string; password: string }) {
  const csrf = await physicianSession(request, physician);
  const response = await request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": uniqueName("create") },
    data: {
      identifier: uniquePatientId(),
      given_name: uniqueLetters("given"),
      family_name: uniqueLetters("family"),
      sex: "F",
      age: 30,
      clinical_status: "first_time",
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return response.json();
}

test("new-patient entry keeps the disabled working note without a page-note placeholder", async ({ page, request }) => {
  await setup(page, request);
  const requests = watchRetiredRequests(page);
  await page.goto("/#/patients/new");
  await expect(page.getByRole("heading", { name: "Register patient", exact: true })).toBeVisible();
  await expectNoPageNotes(page);
  await expect(page.getByLabel("Draft working note")).toBeDisabled();
  expect(requests).toEqual([]);
});

test("every registration step omits page notes; working-note step navigation saves and reloads", async ({ page, request }) => {
  const physician = await setup(page, request);
  const created = await createDraft(request, physician);
  const requests = watchRetiredRequests(page);
  await page.goto(`/#/encounters/${created.draft.id}#register`);
  await expect(page.getByLabel("Draft working note")).toBeEnabled();
  const note = "<b>private working note</b> & review next visit";
  await page.getByLabel("Draft working note").fill(note);
  // Leave before the debounce fires: the step transition must flush the edit.
  await page.locator("#wizard-step-diagnosis").click();
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 2)");
  for (const step of ["register", "diagnosis", "panss", "cssrs", "history", "medications", "proposal-review"]) {
    await page.locator(`#wizard-step-${step}`).click();
    await expect(page.locator(`#wizard-step-${step}`)).toHaveAttribute("aria-current", "page");
    await expectNoPageNotes(page);
  }
  await page.locator("#wizard-step-register").click();
  await page.reload();
  await expect(page.getByLabel("Draft working note")).toHaveValue(note);
  await expect(page.locator("#wizard-page b")).toHaveCount(0);
  expect(requests).toEqual([]);
});

test("every follow-up step omits page-note controls and retired API requests", async ({ page, request }) => {
  const physician = await setup(page, request);
  const created = await createDraft(request, physician);
  const csrf = await physicianSession(request, physician);
  const discarded = await request.post(`/api/v1/encounters/${created.draft.id}/discard`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: { confirm: true },
  });
  expect(discarded.status(), await discarded.text()).toBe(200);
  const followup = await request.post(`/api/v1/patients/${created.patient.id}/encounters`, {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": uniqueName("followup") },
    data: { kind: "follow_up" },
  });
  expect(followup.status(), await followup.text()).toBe(201);
  const draftId = (await followup.json()).encounter.id;
  const requests = watchRetiredRequests(page);
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("heading", { name: "Encounter draft", exact: true })).toBeVisible();
  for (const step of ["diagnosis", "panss", "cssrs", "history", "effects", "medications", "proposal-review"]) {
    await page.locator(`#wizard-step-${step}`).click();
    await expect(page.locator(`#wizard-step-${step}`)).toHaveAttribute("aria-current", "page");
    await expectNoPageNotes(page);
  }
  expect(requests).toEqual([]);
});
