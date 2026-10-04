import { test, expect } from "@playwright/test";
import {
  uniqueName,
  uniqueLetters,
  uniquePatientId,
  ensurePhysician,
  ensurePatient,
  physicianSession,
  loginAs,
  acknowledgeWarning,
} from "./helpers";

/* S07 draft autosave journeys (seam T9 browser; backend covered by
 * BT/http/test_drafts.py and not duplicated here). Selectors used below
 * are the handoff contract for the test agent:
 * - heading "Encounter draft"
 * - textarea labelled "Draft working note" (#draft-working-note)
 * - save status #draft-save-status (role=status): Saving… / Unsaved changes…
 *   / Saved (revision N) / Save failed: … / Conflict: …
 * - conflict panel: heading "Another tab saved first — your edits are kept",
 *   buttons "Reload server version", "Use server version",
 *   "Keep my edits and retry"
 * - failed-save retry: button "Retry save"
 * - discard: button "Discard draft", dialog "Discard this draft?",
 *   checkbox "I understand this discards the draft but keeps the patient.",
 *   buttons "Confirm discard" / "Cancel"
 * - directory: per-row "Start follow-up" (aria-label includes identifier),
 *   generic 409 alert "An open draft already exists for this patient."
 * - registration success: link "Open registration draft" to #/encounters/:id
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

/** Create a patient through the real API and return patient + draft ids. */
async function createPatientWithDraft(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  overrides: Record<string, unknown> = {},
): Promise<{ patientId: string; draftId: string; identifier: string }> {
  const csrf = await physicianSession(request, physician);
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const identifier = (overrides["identifier"] as string) ?? uniquePatientId();
  const payload = {
    given_name: uniqueLetters("given"),
    family_name: uniqueLetters("family"),
    sex: "F",
    age: 30,
    clinical_status: "first_time",
    ...overrides,
    identifier,
  };
  const create = await request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": key },
    data: payload,
  });
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return {
    patientId: body.patient.id as string,
    draftId: body.draft.id as string,
    identifier: body.patient.identifier as string,
  };
}

async function patchDraftApi(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  draftData: Record<string, unknown>,
  revision: number,
): Promise<import("@playwright/test").APIResponse> {
  const csrf = await physicianSession(request, physician);
  return request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": `"${revision}"` },
    data: { draft_data: draftData },
  });
}

test("autosaved note shows Saved only after 2xx and survives reload", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eautosave");
  const { draftId } = await createPatientWithDraft(request, physician);

  await page.goto(`/#/encounters/${draftId}`);
  await expect(
    page.getByRole("heading", { name: "Encounter draft" }),
  ).toBeVisible();
  const note = page.getByLabel("Draft working note");
  await expect(note).toBeVisible();
  const status = page.locator("#draft-save-status");
  await expect(status).toContainText("Saved (revision 1)");

  await note.fill("lives alone, morning visits");
  await expect(status).toContainText("Saved (revision 2)", { timeout: 10000 });

  await page.reload();
  await expect(page.getByLabel("Draft working note")).toHaveValue(
    "lives alone, morning visits",
  );
  await expect(page.locator("#draft-save-status")).toContainText(
    "Saved (revision 2)",
  );
});

test("stale-tab 412 keeps edits and reconciles via Reload/Retry", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2estale");
  const { draftId } = await createPatientWithDraft(request, physician);

  await page.goto(`/#/encounters/${draftId}`);
  const note = page.getByLabel("Draft working note");
  const status = page.locator("#draft-save-status");
  await expect(status).toContainText("Saved (revision 1)");

  await note.fill("tab-A first save");
  await expect(status).toContainText("Saved (revision 2)", { timeout: 10000 });

  // Another tab wins the race through the real API (revision 2 -> 3).
  const winner = await patchDraftApi(request, physician, draftId, {
    working_note: "server-winner",
  }, 2);
  expect(winner.status(), await winner.text()).toBe(200);

  // This tab is stale on revision 2: the autosave 412s, keeps every
  // keystroke, and offers reconcile instead of overwriting.
  await note.fill("tab-B stale edits");
  const conflict = page.getByRole("heading", {
    name: "Another tab saved first",
  });
  await expect(conflict).toBeVisible({ timeout: 10000 });
  await expect(note).toHaveValue("tab-B stale edits");
  await expect(status).toContainText("Conflict");

  // Explicit retry saves the kept edits onto the fresh revision.
  await page
    .getByRole("button", { name: "Keep my edits and retry" })
    .click();
  await expect(status).toContainText("Saved (revision 4)", { timeout: 10000 });

  await page.reload();
  await expect(page.getByLabel("Draft working note")).toHaveValue(
    "tab-B stale edits",
  );
});

test("failed network save never shows Saved; Retry recovers", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2enetfail");
  const { draftId } = await createPatientWithDraft(request, physician);

  await page.goto(`/#/encounters/${draftId}`);
  const note = page.getByLabel("Draft working note");
  const status = page.locator("#draft-save-status");
  await expect(status).toContainText("Saved (revision 1)");

  await page.route("**/api/v1/encounters/*", (route) => {
    if (route.request().method() === "PATCH") {
      return route.abort();
    }
    return route.continue();
  });
  try {
    await note.fill("offline edits stay local");
    await expect(status).toContainText("Save failed", { timeout: 10000 });
    // The stale Saved marker for the new revision must never appear.
    await expect(status).not.toContainText("Saved (revision 2)");
    await expect(note).toHaveValue("offline edits stay local");
  } finally {
    await page.unroute("**/api/v1/encounters/*");
  }

  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(status).toContainText("Saved (revision 2)", { timeout: 10000 });
});

test("navigation warns while unsaved edits remain", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2enavguard");
  const { draftId } = await createPatientWithDraft(request, physician);

  await page.goto(`/#/encounters/${draftId}`);
  const status = page.locator("#draft-save-status");
  await expect(status).toContainText("Saved (revision 1)");

  // Type and navigate before the ~1s debounce can save: the in-app guard
  // must warn instead of silently dropping the keystrokes.
  await page.getByLabel("Draft working note").fill("unsaved navigation probe");

  let dialogMessage: string | null = null;
  page.on("dialog", async (dialog) => {
    dialogMessage = dialog.message();
    await dialog.dismiss();
  });
  await page.getByRole("link", { name: "Patients" }).first().click();
  await expect
    .poll(() => dialogMessage, { timeout: 5000 })
    .toContain("unsaved draft changes");
  // Dismissing stays on the draft with edits intact.
  await expect(page.getByLabel("Draft working note")).toHaveValue(
    "unsaved navigation probe",
  );

  // Accepting leaves the draft.
  page.removeAllListeners("dialog");
  page.on("dialog", async (dialog) => {
    await dialog.accept();
  });
  await page.getByRole("link", { name: "Patients" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Patients" }),
  ).toBeVisible({ timeout: 5000 });
});

test("discard needs explicit confirmation, frees the slot, keeps the patient", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ediscard");
  const { draftId, identifier } = await createPatientWithDraft(
    request,
    physician,
  );

  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.locator("#draft-save-status")).toContainText(
    "Saved (revision 1)",
  );

  await page.getByRole("button", { name: "Discard draft" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Discard this draft?");
  const confirm = page.getByRole("button", { name: "Confirm discard" });
  // Explicit confirmation: the button stays disabled until the checkbox.
  await expect(confirm).toBeDisabled();
  await page
    .getByText("I understand this discards the draft but keeps the patient.")
    .click();
  await expect(confirm).toBeEnabled();
  await confirm.click();

  await expect(page.getByRole("status")).toContainText("Draft discarded");
  await expect(page.getByRole("status")).toContainText("patient record was kept");

  // The patient survives; the slot is free for a new follow-up draft.
  await page.goto("/#/patients");
  await page.getByLabel("Search by name or ID").fill(identifier);
  await expect(page.getByRole("table", { name: "Patients" })).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Patients" }).getByText(identifier),
  ).toBeVisible();
  await page
    .getByRole("button", { name: `Start follow-up draft for ${identifier}` })
    .click();
  await expect(
    page.getByRole("heading", { name: "Encounter draft" }),
  ).toBeVisible({ timeout: 10000 });
  await expect(page.locator("#draft-save-status")).toContainText(
    "Saved (revision 1)",
  );
});

test("occupied slot shows a generic conflict without content", async ({
  page,
  request,
}) => {
  const author = uniqueName("e2eslota");
  const reader = uniqueName("e2eslotb");
  await ensurePhysician(request, author, "secret123");
  await ensurePhysician(request, reader, "secret123");
  const authorCreds = { username: author, password: "secret123" };
  const secret = `secret${uniqueLetters("zz")}`;
  const created = await createPatientWithDraft(request, authorCreds);
  const saved = await patchDraftApi(request, authorCreds, created.draftId, {
    working_note: secret,
  }, 1);
  expect(saved.status(), await saved.text()).toBe(200);

  await loginAs(page, "physician", reader, "secret123");
  await acknowledgeWarning(page);
  await page.goto("/#/patients");
  await page.getByLabel("Search by name or ID").fill(created.identifier);
  await expect(
    page.getByRole("table", { name: "Patients" }).getByText(created.identifier),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: `Start follow-up draft for ${created.identifier}`,
    })
    .click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("already exists");
  await expect(page.locator("body")).not.toContainText(secret);
});

test("strangers see an author-only denial without content", async ({
  page,
  request,
}) => {
  const author = uniqueName("e2eprivatea");
  const stranger = uniqueName("e2eprivateb");
  await ensurePhysician(request, author, "secret123");
  await ensurePhysician(request, stranger, "secret123");
  const authorCreds = { username: author, password: "secret123" };
  const secret = `priv${uniqueLetters("qq")}`;
  const created = await createPatientWithDraft(request, authorCreds);
  const saved = await patchDraftApi(request, authorCreds, created.draftId, {
    working_note: secret,
  }, 1);
  expect(saved.status(), await saved.text()).toBe(200);

  await loginAs(page, "physician", stranger, "secret123");
  await acknowledgeWarning(page);
  await page.goto(`/#/encounters/${created.draftId}`);
  await expect(page.getByRole("alert")).toContainText(
    "Only the draft author can access this draft.",
  );
  await expect(page.locator("body")).not.toContainText(secret);
  await expect(page.getByLabel("Draft working note")).toHaveCount(0);
});

test("registration form stays local-only until POST succeeds", async ({
  page,
  request,
}) => {
  await loginPhysician(page, request, "e2elocalonly");
  await page.goto("/#/patients/new");
  await expect(page.getByRole("button", { name: "Register patient" })).toBeDisabled();

  await page.getByLabel("Given name").fill(uniqueLetters("anna"));
  await page.getByLabel("Family name").fill(uniqueLetters("novak"));
  await page.getByLabel("Patient ID").fill(uniquePatientId());
  await page.getByRole("radio", { name: "M", exact: true }).check();
  await page.getByLabel("Age").fill("30");
  await page.getByRole("radio", { name: "First-time" }).check();
  await expect(page.getByRole("button", { name: "Register patient" })).toBeEnabled();
  // Pre-creation demographics are local only: never advertised as durable.
  await expect(page.locator("body")).not.toContainText("Saved (revision");
});
