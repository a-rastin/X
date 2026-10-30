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

async function fillValidForm(
  page: import("@playwright/test").Page,
  fields: {
    given: string;
    family: string;
    identifier: string;
    age?: string;
    phone?: string;
  },
): Promise<void> {
  await page.getByLabel("Given name").fill(fields.given);
  await page.getByLabel("Family name").fill(fields.family);
  await page.getByLabel("Patient ID").fill(fields.identifier);
  await page.getByRole("radio", { name: "M", exact: true }).check();
  await page.getByLabel("Age", { exact: true }).fill(fields.age ?? "30");
  await page.getByRole("radio", { name: "First-time" }).check();
  if (fields.phone !== undefined) {
    await page.getByLabel("Phone").fill(fields.phone);
  }
}

test("physician registers a patient; leading-zero ID round-trips with timestamp and revision", async ({
  page,
  request,
}) => {
  await loginPhysician(page, request, "e2ereg");
  // Direct hash navigation (the header-link path is covered by the identity
  // suite; goto keeps this create-flow test deterministic under parallel load).
  await page.goto("/#/patients");
  await expect(page.getByRole("heading", { name: "Patients" })).toBeVisible();
  await page.getByRole("link", { name: "Register new patient" }).click();
  await expect(
    page.getByRole("heading", { name: "Add New Patient" }),
  ).toBeVisible();
  // Seven steps, no adverse effects. Demographics and the working note
  // are step 1; later steps stay closed until a draft exists.
  await expect(page.locator("#wizard-pages a")).toHaveText([
    "1. Register patient",
    "2. Diagnosis",
    "3. PANSS",
    "4. C-SSRS",
    "5. History",
    "6. Medications",
    "7. Proposal review",
  ]);
  await expect(page.locator("#wizard-step-effects")).toHaveCount(0);
  await expect(page.locator("#wizard-page")).toHaveAttribute(
    "aria-label",
    "Register patient, step 1 of 7",
  );
  await expect(page.locator("#wizard-step-register")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.locator("#wizard-step-proposal-review")).toHaveAttribute(
    "aria-disabled",
    "true",
  );
  await expect(
    page.getByRole("heading", { name: "Page notes — demographics" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Working note (draft only)" }),
  ).toBeVisible();

  // Leading zeros are part of the identifier, not a number to normalize.
  const identifier = `00${uniquePatientId().slice(2)}`;
  const given = uniqueLetters("anna");
  const family = uniqueLetters("novak");
  await fillValidForm(page, { given, family, identifier, phone: "+43 699 123456" });

  const register = page.getByRole("button", { name: "Register patient" });
  await expect(register).toBeEnabled();
  const createRequests: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && /\/patients\/?$/.test(new URL(req.url()).pathname)) createRequests.push(req.url());
    if (req.method() === "POST" && /\/encounters\/?$/.test(new URL(req.url()).pathname)) createRequests.push(req.url());
  });
  // Keep the create in flight while the second click arrives.
  await page.route("**/api/v1/patients", async (route) => {
    if (route.request().method() === "POST") await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && /\/patients\/?$/.test(new URL(response.url()).pathname),
  );
  await register.dblclick({ delay: 50 });
  const created = await (await responsePromise).json();

  await expect(page).toHaveURL(new RegExp(`#/encounters/${created.draft.id}#diagnosis$`));
  await expect(page.locator("#wizard-pages a")).toHaveCount(7);
  await expect(page.locator("#wizard-step-effects")).toHaveCount(0);
  await expect(page.locator("#wizard-page")).toHaveAttribute(
    "aria-label",
    "Diagnosis, step 2 of 7",
  );
  await expect(page.locator("#wizard-step-diagnosis")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#diagnosis-heading")).toHaveText("Diagnosis (step 2)");
  await expect(page.getByRole("heading", { name: "Add New Patient" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Draft context" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Jump to criterion" })).toHaveCount(0);
  await expect(page.locator(".xi-diagnosis-item-hint").getByText("Answered", { exact: true })).toHaveCount(0);
  const confirmation = page.getByText(`Patient ${identifier} registered.`);
  await expect(confirmation).toHaveCount(1);
  await expect(confirmation).toContainText(created.server_timestamp);
  await expect(confirmation).toContainText(`revision ${created.revision}`);
  for (const id of [
    "a_delusions", "a_hallucinations", "a_disorganized_speech",
    "a_disorganized_behavior", "a_negative_symptoms", "b_functional_decline",
    "c_six_months", "c_active_month", "c_shortened_by_intervention",
    "d_mood_exclusion", "e_substance_medical_exclusion", "f_autism_present",
    "f_prominent_psychosis",
  ]) {
    await expect(page.locator(`#diag-${id}-unknown`)).toBeChecked();
  }
  await expect(page.locator("#draft-save-status")).toHaveCount(0);
  await expect(page.locator("#diagnosis-preview-status")).toContainText("Indeterminate", { timeout: 15000 });
  await page.getByRole("button", { name: "Continue to PANSS", exact: true }).click();
  await expect(page.getByText("Continue without a confirmed diagnosis?")).toBeVisible();
  await expect(page).toHaveURL(/#diagnosis$/);
  await page.getByRole("button", { name: "Return to diagnosis" }).click();
  await expect(createRequests).toHaveLength(1);
  await page.reload();
  await expect(page).toHaveURL(/#diagnosis$/);
  await expect(page.locator("#wizard-page")).toHaveAttribute("aria-label", "Diagnosis, step 2 of 7");
  await page.getByRole("link", { name: "Back to Register patient" }).click();
  await expect(page).toHaveURL(/#register$/);
  await expect(page.getByRole("heading", { name: "Add New Patient" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Draft context" })).toHaveCount(0);
  await expect(page.getByLabel("Given name")).toHaveValue(given);
  await expect(page.getByLabel("Family name")).toHaveValue(family);
  await expect(page.getByLabel("Patient ID")).toHaveValue(identifier);
  await expect(page.getByLabel("Draft working note")).toBeEnabled();
  await expect(page.getByRole("button", { name: "Register patient" })).toHaveCount(0);
  await page.getByRole("link", { name: "Continue to Diagnosis" }).click();
  await expect(page).toHaveURL(/#diagnosis$/);
  await expect(
    page.getByRole("link", { name: "Open registration draft" }),
  ).toHaveCount(0);
  await expect(page.locator("#wizard-step-proposal-review")).not.toHaveAttribute(
    "aria-disabled",
    "true",
  );

  // A registration draft has no effects step; an effects fragment does not open it.
  await page.goto(page.url().replace(/#diagnosis$/, "#effects"));
  await expect(page.locator("#wizard-step-effects")).toHaveCount(0);
  await expect(page.locator("#effects-heading")).toHaveCount(0);
});

test("Register stays disabled until every demographics field is valid", async ({
  page,
  request,
}) => {
  await loginPhysician(page, request, "e2egate");
  await page.goto("/#/patients/new");

  const register = page.getByRole("button", { name: "Register patient" });
  // Empty form: invalid.
  await expect(register).toBeDisabled();

  // Names + ID alone still leave sex/age/status missing.
  await page.getByLabel("Given name").fill(uniqueLetters("anna"));
  await page.getByLabel("Family name").fill(uniqueLetters("novak"));
  await page.getByLabel("Patient ID").fill(uniquePatientId());
  await expect(register).toBeDisabled();

  // Out-of-range and decimal ages never enable the button.
  await page.getByRole("radio", { name: "M", exact: true }).check();
  await page.getByLabel("Age", { exact: true }).fill("17");
  await page.getByRole("radio", { name: "First-time" }).check();
  await expect(register).toBeDisabled();
  await page.getByLabel("Age", { exact: true }).fill("30.5");
  await expect(register).toBeDisabled();
  await page.getByLabel("Age", { exact: true }).fill("30");
  await expect(register).toBeEnabled();

  // A malformed ID disables again; fixing it re-enables.
  await page.getByLabel("Patient ID").fill("123");
  await expect(register).toBeDisabled();
  await page.getByLabel("Patient ID").fill(uniquePatientId());
  await expect(register).toBeEnabled();
});

test("duplicate identifier shows a conflict without losing input", async ({
  page,
  request,
}) => {
  const credentials = await loginPhysician(page, request, "e2econflict");
  const identifier = uniquePatientId();
  await ensurePatient(request, credentials, { identifier });
  await page.goto("/#/patients/new");

  const given = uniqueLetters("anna");
  const family = uniqueLetters("novak");
  await fillValidForm(page, { given, family, identifier });
  const register = page.getByRole("button", { name: "Register patient" });
  await expect(register).toBeEnabled();
  await register.click();

  const alert = page.getByRole("alert");
  await expect(alert).toContainText("already exists");
  // Every keystroke survives the conflict.
  await expect(page.getByLabel("Patient ID")).toHaveValue(identifier);
  await expect(page.getByLabel("Given name")).toHaveValue(given);
  await expect(page.getByLabel("Family name")).toHaveValue(family);
});

test("directory search finds patients by name and ID, and status filter narrows", async ({
  page,
  request,
}) => {
  const author = uniqueName("e2esharea");
  const reader = uniqueName("e2eshareb");
  await ensurePhysician(request, author, "secret123");
  await ensurePhysician(request, reader, "secret123");
  const given = uniqueLetters("searchgiven");
  const family = uniqueLetters("searchfamily");
  const identifier = uniquePatientId();
  await ensurePatient(
    request,
    { username: author, password: "secret123" },
    {
      identifier,
      given_name: given,
      family_name: family,
      clinical_status: "established",
    },
  );

  // Results are shared across physicians: the reader finds the author's patient.
  await loginAs(page, "physician", reader, "secret123");
  await acknowledgeWarning(page);
  const table = page.getByRole("table", { name: "Patients" });
  const search = page.getByLabel("Search by name or ID");

  await search.fill(identifier.slice(0, 8));
  await expect(table.getByText(identifier)).toBeVisible();
  await search.fill(given.slice(0, 8));
  await expect(table.getByText(given)).toBeVisible();

  // Status filter narrows the shared list: an established patient hides
  // under first-time and returns under established.
  await page.getByLabel("Clinical status").selectOption("first_time");
  await expect(table.getByText(identifier)).toHaveCount(0);
  await page.getByLabel("Clinical status").selectOption("established");
  await expect(table.getByText(identifier)).toBeVisible();
});

test("directory pages through results in bounded pages", async ({
  page,
  request,
}) => {
  const credentials = await loginPhysician(page, request, "e2epage");
  // A distinctive family-name stem keeps the count exact however many
  // patients other specs have accumulated in the shared database. One
  // physician session serves all 26 creates (normal multi-operation use).
  const stem = uniqueLetters("pagefam");
  const csrf = await physicianSession(request, credentials);
  for (let i = 0; i < 26; i += 1) {
    const letter = String.fromCharCode(97 + (i % 26));
    await ensurePatient(
      request,
      credentials,
      {
        family_name: `${stem}${letter}`,
      },
      csrf,
    );
  }

  await page.getByLabel("Search by name or ID").fill(stem);
  await expect(page.getByText("Showing 1–25 of 26.")).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Showing 26–26 of 26.")).toBeVisible();
  await page.getByRole("button", { name: "Previous" }).click();
  await expect(page.getByText("Showing 1–25 of 26.")).toBeVisible();
});

test("admin reads the directory but has no registration form", async ({
  page,
  request,
}) => {
  const username = uniqueName("e2eadmseen");
  await ensurePhysician(request, username, "secret123");
  const identifier = uniquePatientId();
  await ensurePatient(request, { username, password: "secret123" }, { identifier });

  await loginAs(page, "admin", "admin", "admin");
  await expect(
    page.getByRole("heading", { name: "Administrator dashboard" }),
  ).toBeVisible();
  const table = page.getByRole("table", { name: "Patients" });
  // The shared directory pages newest-last: search to reach the row.
  await page.getByLabel("Search by name or ID").fill(identifier);
  await expect(table.getByText(identifier)).toBeVisible();
  await expect(
    page.getByText(/Read-only: administrators do not register/),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Register new patient" }),
  ).toHaveCount(0);

  // The registration route itself is denied without a create button.
  await page.goto("/#/patients/new");
  await expect(page.getByText("Physician access required.")).toBeVisible();
  await expect(
    page.getByRole("form", { name: "Register patient" }),
  ).toHaveCount(0);
});

test("directory surfaces endpoint failure with Retry and recovers", async ({
  page,
  request,
}) => {
  await loginPhysician(page, request, "e2edirfail");
  const table = page.getByRole("table", { name: "Patients" });
  const empty = page.getByText("No patients found.");
  await expect(table.or(empty)).toBeVisible();

  // Transport-level failure of the real list endpoint (no internal mocks:
  // the app, router, and server contract are untouched).
  await page.route("**/api/v1/patients*", (route) => route.abort());
  await page.reload();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText(/failed to load/i)).toBeVisible();
  const retry = page.getByRole("button", { name: "Retry" });
  await expect(retry).toBeVisible();

  await page.unroute("**/api/v1/patients*");
  await retry.click();
  await expect(table.or(page.getByText("No patients found."))).toBeVisible();
});

test("server field errors render against their labels and keep input", async ({
  page,
  request,
}) => {
  await loginPhysician(page, request, "e2e422");
  await page.goto("/#/patients/new");
  const given = uniqueLetters("anna");
  const family = uniqueLetters("novak");
  const identifier = uniquePatientId();
  await fillValidForm(page, { given, family, identifier });

  // Failure injection at the transport edge only: the app, router, and
  // server contract are untouched; the payload below mirrors the real 422
  // field_errors shape so the rendering path is exercised for real.
  await page.route("**/api/v1/patients", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          code: "VALIDATION_FAILED",
          message: "Patient demographics are invalid.",
          field_errors: {
            age: ["Must be an integer 18-99."],
            identifier: ["Must be exactly 10 ASCII digits (0-9)."],
          },
          request_id: "e2e-field-errors",
          retryable: false,
        }),
      });
    } else {
      await route.continue();
    }
  });
  try {
    await page.getByRole("button", { name: "Register patient" }).click();

    const alert = page.getByRole("alert");
    await expect(alert).toContainText("Patient demographics are invalid.");
    // Keyboard focus moves to the summary; inputs name their errors.
    await expect(alert).toBeFocused();
    await expect(page.getByLabel("Age", { exact: true })).toHaveAttribute(
      "aria-describedby",
      "age-error",
    );
    await expect(page.getByLabel("Patient ID")).toHaveAttribute(
      "aria-describedby",
      "identifier-error",
    );
    await expect(
      page.getByText("Must be an integer 18-99."),
    ).toBeVisible();
    // Nothing the physician typed is lost.
    await expect(page.getByLabel("Age", { exact: true })).toHaveValue("30");
    await expect(page.getByLabel("Patient ID")).toHaveValue(identifier);
    await expect(page.getByLabel("Given name")).toHaveValue(given);
    await expect(page.getByLabel("Family name")).toHaveValue(family);
  } finally {
    await page.unroute("**/api/v1/patients");
  }
});
