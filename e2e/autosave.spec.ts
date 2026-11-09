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
 * - heading "Add New Patient" on `#/encounters/:id#register` (working note home)
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
 * - registration success: `#/patients/new` opens #/encounters/:id#diagnosis
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

  await page.goto(`/#/encounters/${draftId}#register`);
  await expect(
    page.getByRole("heading", { name: "Add New Patient" }),
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

  await page.goto(`/#/encounters/${draftId}#register`);
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

  await page.goto(`/#/encounters/${draftId}#register`);
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

test("navigation waits for the latest draft edit and preserves it on reload", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2enavguard");
  const { draftId } = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${draftId}#register`);
  const note = page.getByLabel("Draft working note");
  await expect(note).toBeVisible();
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let started!: () => void;
  const patchStarted = new Promise<void>((resolve) => { started = resolve; });
  await page.route("**/api/v1/encounters/*", async (route) => {
    if (route.request().method() === "PATCH") {
      started();
      await held;
    }
    await route.continue();
  });
  await note.fill("navigation barrier probe");
  await page.getByRole("link", { name: "Patients" }).first().click();
  await patchStarted;
  await expect(note).toHaveValue("navigation barrier probe");
  release();
  await expect(page.getByRole("heading", { name: "Patients" })).toBeVisible();
  await page.unroute("**/api/v1/encounters/*");
  await page.goto(`/#/encounters/${draftId}#register`);
  await expect(page.getByLabel("Draft working note")).toHaveValue("navigation barrier probe");
});


test("Continue waits for the latest draft edit and preserves it on reload", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2enavguard");
  const { draftId } = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${draftId}#register`);
  const note = page.getByLabel("Draft working note");
  await expect(note).toBeVisible();
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let started!: () => void;
  const patchStarted = new Promise<void>((resolve) => { started = resolve; });
  await page.route("**/api/v1/encounters/*", async (route) => {
    if (route.request().method() === "PATCH") {
      started();
      await held;
    }
    await route.continue();
  });
  await note.fill("navigation barrier probe");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await patchStarted;
  await expect(note).toHaveValue("navigation barrier probe");
  await expect(page.getByRole("heading", { name: "Diagnosis (step 2)" })).toHaveCount(0);
  release();
  await expect(page).toHaveURL(/#diagnosis$/);
  await page.unroute("**/api/v1/encounters/*");
  await page.goto(`/#/encounters/${draftId}#register`);
  await expect(page.getByLabel("Draft working note")).toHaveValue("navigation barrier probe");
});

test("edits during a save remain unsaved until the newer revision is acknowledged", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2einflight");
  const { draftId } = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${draftId}#register`);
  const note = page.getByLabel("Draft working note");
  const status = page.locator("#draft-save-status");
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let started!: () => void;
  const patchStarted = new Promise<void>((resolve) => { started = resolve; });
  let first = true;
  await page.route("**/api/v1/encounters/*", async (route) => {
    if (route.request().method() === "PATCH" && first) {
      first = false;
      started();
      await held;
    }
    await route.continue();
  });
  await note.fill("first edit");
  await patchStarted;
  await note.fill("latest edit");
  await expect(status).not.toContainText("Saved");
  release();
  await expect(status).toContainText("Saved (revision 3)");
  await page.unroute("**/api/v1/encounters/*");
  await page.reload();
  await expect(note).toHaveValue("latest edit");
});

test("failed navigation keeps the editor and retry permits departure", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2enavfail");
  const { draftId } = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${draftId}#register`);
  const note = page.getByLabel("Draft working note");
  await page.route("**/api/v1/encounters/*", (route) => route.request().method() === "PATCH" ? route.abort() : route.continue());
  await note.fill("keep this edit");
  await page.getByRole("link", { name: "Patients" }).first().click();
  await expect(page.locator("#draft-save-status")).toContainText("Save failed");
  await expect(note).toHaveValue("keep this edit");
  await page.unroute("**/api/v1/encounters/*");
  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 2)");
  await page.getByRole("link", { name: "Patients" }).first().click();
  await expect(page.getByRole("heading", { name: "Patients" })).toBeVisible();
});

test("assessment steps expose the shared draft save status", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2estatus");
  const { draftId } = await createPatientWithDraft(request, physician);
  for (const step of ["diagnosis", "cssrs", "history"]) {
    await page.goto(`/#/encounters/${draftId}#${step}`);
    await expect(page.locator("#draft-save-status")).toBeVisible();
  }
});

test("an edit arriving as navigation is released is saved before leaving", async ({ page, request }) => {
  await page.addInitScript(() => {
    let patientEvents = 0;
    window.addEventListener("hashchange", () => {
      if (window.location.hash !== "#/patients") return;
      patientEvents += 1;
      if (patientEvents !== 2) return;
      const note = document.querySelector<HTMLTextAreaElement>("#draft-working-note");
      if (note === null) return;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(note, "edit after save");
      note.dispatchEvent(new Event("input", { bubbles: true }));
    }, true);
  });
  const physician = await loginPhysician(page, request, "e2erelease");
  const { draftId } = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${draftId}#register`);
  await page.getByLabel("Draft working note").fill("first edit");
  await page.getByRole("link", { name: "Patients" }).first().click();
  await expect(page.getByRole("heading", { name: "Patients" })).toBeVisible();
  await page.goto(`/#/encounters/${draftId}#register`);
  await expect(page.getByLabel("Draft working note")).toHaveValue("edit after save");
});

test("explicit discard remains available after a failed save", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ediscardfail");
  const { draftId } = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${draftId}#register`);
  await page.route("**/api/v1/encounters/*", (route) => route.request().method() === "PATCH" ? route.abort() : route.continue());
  await page.getByLabel("Draft working note").fill("discard this failed edit");
  await expect(page.locator("#draft-save-status")).toContainText("Save failed");
  await page.getByRole("button", { name: "Discard draft", exact: true }).click();
  await page.getByText("I understand this discards the draft but keeps the patient.").click();
  await page.getByRole("button", { name: "Confirm discard" }).click();
  await expect(page.getByRole("status")).toContainText("Draft discarded");
});

test("retry replays the original request after a lost response before saving newer edits", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2elostresponse");
  const { draftId } = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${draftId}#register`);
  let first = true;
  await page.route("**/api/v1/encounters/*", async (route) => {
    if (route.request().method() === "PATCH" && first) {
      first = false;
      await route.fetch();
      await route.abort();
      return;
    }
    await route.continue();
  });
  const note = page.getByLabel("Draft working note");
  await note.fill("committed without response");
  await expect(page.locator("#draft-save-status")).toContainText("Save failed");
  await note.fill("newer edit");
  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 3)");
  await page.unroute("**/api/v1/encounters/*");
  await page.reload();
  await expect(note).toHaveValue("newer edit");
});

test("switching encounter IDs waits for the original draft save", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2eswitchdraft");
  const original = await createPatientWithDraft(request, physician);
  const destination = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${original.draftId}#register`);
  const note = page.getByLabel("Draft working note");
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let started!: () => void;
  const patchStarted = new Promise<void>((resolve) => { started = resolve; });
  await page.route("**/api/v1/encounters/*", async (route) => {
    if (route.request().method() === "PATCH") { started(); await held; }
    await route.continue();
  });
  await note.fill("original draft edit");
  await page.evaluate((id) => { window.location.hash = `#/encounters/${id}#register`; }, destination.draftId);
  await patchStarted;
  await expect(note).toHaveValue("original draft edit");
  release();
  await expect(note).toHaveValue("");
  await page.unroute("**/api/v1/encounters/*");
  await page.goto(`/#/encounters/${original.draftId}#register`);
  await expect(note).toHaveValue("original draft edit");
});

test("discard does not delete a newer conflicting server revision", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ediscardconflict");
  const { draftId } = await createPatientWithDraft(request, physician);
  await page.goto(`/#/encounters/${draftId}#register`);
  await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 1)");
  const winner = await patchDraftApi(request, physician, draftId, { working_note: "newer server edit" }, 1);
  expect(winner.status()).toBe(200);
  await page.getByLabel("Draft working note").fill("stale local edit");
  await expect(page.locator("#draft-save-status")).toContainText("Conflict");
  await page.getByRole("button", { name: "Discard draft", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByText("I understand this discards the draft but keeps the patient.").click();
  await dialog.getByRole("button", { name: "Confirm discard" }).click();
  await expect(dialog).toContainText("Reconcile the conflict before discarding");
  const csrf = await physicianSession(request, physician);
  const current = await request.get(`/api/v1/encounters/${draftId}`, { headers: { "X-CSRF-Token": csrf } });
  expect(current.status()).toBe(200);
  expect((await current.json()).draft_data.working_note).toBe("newer server edit");
});

test("registration steps show a standalone discard button", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2ediscardlayout");
  const { draftId } = await createPatientWithDraft(request, physician);
  for (const step of ["register", "diagnosis", "panss", "cssrs", "history", "medications", "proposal-review"]) {
    await page.goto(`/#/encounters/${draftId}#${step}`);
    await expect(page.locator(`#wizard-step-${step}`)).toHaveAttribute("aria-current", "page");
    const button = page.getByRole("button", { name: "Discard draft", exact: true });
    await expect(button).toHaveCount(1);
    await expect(button).toBeVisible();
    await expect(page.getByRole("heading", { name: "Discard draft", exact: true })).toHaveCount(0);
    await expect(button.locator("xpath=ancestor::section[@class='xi-card']")).toHaveCount(0);
  }
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
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "Discard draft" }).click();
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
  await expect(page.getByRole("button", { name: "Register", exact: true })).toBeDisabled();

  await page.getByLabel("Given name").fill(uniqueLetters("anna"));
  await page.getByLabel("Family name").fill(uniqueLetters("novak"));
  await page.getByLabel("Patient ID").fill(uniquePatientId());
  await page.getByRole("radio", { name: "M", exact: true }).check();
  await page.getByLabel("Age", { exact: true }).fill("30");
  await page.getByRole("radio", { name: "Known case" }).check();
  await expect(page.getByRole("button", { name: "Register", exact: true })).toBeEnabled();
  // Pre-creation demographics are local only: never advertised as durable.
  await expect(page.locator("body")).not.toContainText("Saved (revision");
});
