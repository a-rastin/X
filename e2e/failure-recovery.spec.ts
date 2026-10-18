import { test, expect } from "@playwright/test";
import {
  uniqueName,
  uniqueLetters,
  uniquePatientId,
  acknowledgeWarning,
  loginAs,
} from "./helpers";

// Behavior under test needs no waits, but setup does real provisioning
// round-trips (logins, draft PATCH, plan/note saves) that stretch under a
// contended host — allow headroom per test instead of the 30s default.
test.setTimeout(120000);

/* S60 cross-user, failure, and security acceptance — frontend slice (T1/T9).
 *
 * Backend owns the races/recovery/adversarial matrix
 * (`BT/http/test_crosscutting_acceptance.py`, 5 tests: two-physician
 * isolation while one run fails, mixed-exhaustion stage resume, lease/
 * crash/archive fencing, payload/forgery denial, sign-denial flow). This
 * spec guards only the OBSERVABLE UI states over the planned public seams
 * (no DB rows, no private helpers, no worker harness):
 *
 * - §1 wrong-author draft reads render the denied view without
 *   content leak; shared directory/chart reads stay available.
 * - §1 a mocked failed provider run keeps draft saves responsive,
 *   acknowledged revisions survive reload, and no fake successful proposal
 *   or enabled sign appears (honest unavailable + blocked sign + retry).
 * - §§2+4 a stale run fences acceptance/signing with exact text reasons
 *   (batch banner + per-question ◍ + disabled accept).
 * - §§3-4 physician markup renders literally (React text, never elements)
 *   and the client blocks manual sign without acceptance, preserving plan text.
 *
 * Conventions follow e2e/proposal-review.spec.ts + probability-review: real
 * auth/draft/notes/plan via T1; failed/stale/complete batch + review
 * payloads via controlled page.route mocks (deterministic, no sleeps);
 * localStorage `xi.proposal.<encounterId>` seeds the recorded batch so the
 * panel resumes via GET without a new POST. Retry POST-to-server and real
 * provider runs are NOT exercised here (backend S60 + S48 §2+4 cover them).
 *
 * Setup fails on the first unexpected HTTP response. No sleeps, write
 * retries, or session-maturation waits hide commit-visibility failures.
 *
 * Selector contract (added for this slice; see panel headers):
 * - denied `data-testid="encounter-denied"`, load error `encounter-error`
 * - honest unavailable `data-testid="proposal-unavailable"`
 * - stale banner `data-testid="proposal-stale-banner"`
 * - reused: proposal-review, proposal-status-<key>, proposal-retry-<key>,
 *   proposal-freshness-<key>, proposal-sign-blocked, cpt-panel/toggle/
 *   accept/retry-<key>, secondary-plan-textarea/save/status,
 *   plan-compare-proposal/plan, sign-panel/button,
 *   notes-<page>-input/add/list, chart-heading.
 *
 * Run with --workers=1 to avoid unnecessary load on the shared dev stack.
 * This is not concurrent two-physician or process-restart coverage.
 */

function freshKey(prefix: string): string {
  return `${prefix}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

async function loginAdmin(
  request: import("@playwright/test").APIRequestContext,
): Promise<string> {
  const login = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(login.ok()).toBeTruthy();
  const { csrf_token } = await login.json();
  return csrf_token as string;
}

/** Strict setup login: an acknowledged account must be usable immediately. */
async function physicianSessionLocal(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
): Promise<string> {
  const login = await request.post("/api/v1/auth/login", {
    data: { username: physician.username, password: physician.password, role: "physician" },
  });
  expect(login.status(), await login.text()).toBe(200);
  return ((await login.json()) as { csrf_token: string }).csrf_token;
}

/** Strict provisioning avoids the legacy shared helper's write retry. */
async function ensurePhysicianLocal(
  request: import("@playwright/test").APIRequestContext,
  username: string,
  password: string,
): Promise<void> {
  const csrf = await loginAdmin(request);
  const create = await request.post("/api/v1/physicians", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": freshKey("e2es60") },
    data: { username, password },
  });
  expect(create.status(), await create.text()).toBe(201);
}

async function loginPhysician(
  page: import("@playwright/test").Page,
  request: import("@playwright/test").APIRequestContext,
  prefix: string,
): Promise<{ username: string; password: string }> {
  const username = uniqueName(prefix);
  const password = "secret123";
  await ensurePhysicianLocal(request, username, password);
  await loginAs(page, "physician", username, password);
  await acknowledgeWarning(page);
  return { username, password };
}

async function createPatientWithDraft(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
): Promise<{ patientId: string; draftId: string; givenName: string; revision: number }> {
  const givenName = uniqueLetters("sgiven");
  const payload = {
    identifier: uniquePatientId(),
    given_name: givenName,
    family_name: uniqueLetters("sfamily"),
    sex: "F",
    age: 30,
    clinical_status: "first_time",
  };
  const post = (csrfToken: string, idempotencyKey: string) =>
    request.post("/api/v1/patients", {
      headers: { "X-CSRF-Token": csrfToken, "Idempotency-Key": idempotencyKey },
      data: payload,
    });
  const csrf = await physicianSessionLocal(request, physician);
  const create = await post(csrf, freshKey("e2es60"));
  expect(create.status(), await create.text()).toBe(201);
  const parsed = (await create.json()) as {
    patient: { id: string; given_name?: string };
    draft: { id: string };
  };
  return {
    patientId: parsed.patient.id as string,
    draftId: parsed.draft.id as string,
    givenName,
    revision: 1,
  };
}

async function patchDraft(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  draftData: Record<string, unknown>,
  revision: number,
): Promise<number> {
  const csrf = await physicianSessionLocal(request, physician);
  const attempt = async () =>
    request.patch(`/api/v1/encounters/${draftId}`, {
      headers: { "X-CSRF-Token": csrf, "If-Match": `"${revision}"` },
      data: { draft_data: draftData },
    });
  const response = await attempt();
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as { revision: number }).revision;
}

async function seedStoredProposal(
  page: import("@playwright/test").Page,
  encounterId: string,
  value: { batchId: string; packages: unknown; sourceRevision: number },
): Promise<void> {
  await page.evaluate(
    ({ encounterId: id, stored }) => {
      localStorage.setItem(`xi.proposal.${id}`, JSON.stringify(stored));
    },
    { encounterId, stored: value },
  );
  const check = await page.evaluate(
    (id) => localStorage.getItem(`xi.proposal.${id}`),
    encounterId,
  );
  expect(check).not.toBeNull();
}

async function gotoEncounter(
  page: import("@playwright/test").Page,
  draftId: string,
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}#proposal-review`);
  await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
    timeout: 15000,
  });
}

// --- Minimal failed/stale/complete fixtures (two-node literals only) ---

function mockTables() {
  return [
    {
      node_id: "A",
      parent_ids: [],
      states: ["no", "yes"],
      rows: [{ parent_states: [], percentages: ["80", "20"] }],
    },
    {
      node_id: "B",
      parent_ids: ["A"],
      states: ["no", "yes"],
      rows: [
        { parent_states: ["no"], percentages: ["90", "10"] },
        { parent_states: ["yes"], percentages: ["30", "70"] },
      ],
    },
  ];
}

function mockRun(runId: string, batchId: string, questionKey: string) {
  return {
    id: runId,
    batch_id: batchId,
    question_key: questionKey,
    status: "ready",
    gate_reason: "gate true",
    projection: { variables: [] },
    projection_hash: "c".repeat(64),
    fingerprint: "fp-s60",
    created_at: "2026-10-11T00:00:00Z",
  };
}

function mockQueue() {
  return {
    busy: false,
    leased_count: 0,
    queued_count: 0,
    max_provider_slots: 2,
    max_queued_runs: 100,
    queue_position: null,
  };
}

function mockFailedBatch(batchId: string, draftId: string, run: ReturnType<typeof mockRun>) {
  return {
    batch: {
      id: batchId,
      encounter_id: draftId,
      author_id: "author-s60",
      source_revision: 2,
      fingerprint: "fp-s60",
      status: "ready",
      pinned_bundle: {},
      created_at: "2026-10-11T00:00:00Z",
    },
    question_runs: [run],
    freshness: { stale: false, reason: "current", current_fingerprint: "fp-s60" },
    job: null,
    jobs: [
      {
        id: "job-s60-failed",
        batch_id: batchId,
        question_run_id: run.id,
        job_class: "generation",
        status: "failed",
        attempt_index: 3,
        max_attempts: 3,
        lease_deadline: null,
        last_heartbeat: null,
        next_eligible_at: null,
        result: { error_code: "PROVIDER_429" },
        created_at: "2026-10-11T00:00:00Z",
        updated_at: "2026-10-11T00:00:00Z",
      },
    ],
    attempts: 3,
    queue: mockQueue(),
    baseline: null,
    transparency: null,
    baselines: [
      {
        question_run_id: run.id,
        question_key: run.question_key,
        position: 0,
        status: "ready",
        baseline: null,
        transparency: null,
      },
    ],
    proposal: null,
    workflow: {
      complete: false,
      status: "incomplete",
      pending_question_keys: [run.question_key],
      needs_clarification: [],
      skipped: [],
      coverage_warnings: [],
      ddi_status: "valid",
    },
  };
}

function mockBaseline(
  baselineId: string,
  runId: string,
  batchId: string,
  questionKey: string,
  sectionText: string,
) {
  const posteriors = [
    { node_id: "A", states: ["no", "yes"], probabilities: [0.8, 0.2] },
    { node_id: "B", states: ["no", "yes"], probabilities: [0.78, 0.22] },
  ];
  return {
    id: baselineId,
    question_run_id: runId,
    batch_id: batchId,
    source_hash: "a".repeat(64),
    effective_hash: "b".repeat(64),
    effective_xml: "<BIF>mock effective</BIF>",
    raw_response: { network_hash: "a".repeat(64), tables: mockTables() },
    validated_tables: mockTables(),
    query_nodes: ["A", "B"],
    posteriors,
    section_text: sectionText,
    template_version: "v1",
    prompt_version: "v1",
    network_version: "s60-test-v1",
    provider_model: "mock-model",
    projection_hash: "c".repeat(64),
    provenance: { question_key: questionKey, provider_model: "mock-model" },
    created_at: "2026-10-11T00:00:00Z",
  };
}

function mockTransparency(questionKey: string, sectionText: string) {
  return {
    question_key: questionKey,
    network_version: "s60-test-v1",
    saved_patient_inputs: [],
    returned_cpt_percentages: mockTables(),
    deterministic_result: {
      posteriors: [
        { node_id: "A", states: ["no", "yes"], probabilities: [0.8, 0.2] },
        { node_id: "B", states: ["no", "yes"], probabilities: [0.78, 0.22] },
      ],
      section_text: sectionText,
      query_nodes: ["A", "B"],
      effective_hash: "b".repeat(64),
    },
  };
}

function mockBatchFromBaselines(
  batchId: string,
  draftId: string,
  run: ReturnType<typeof mockRun>,
  baseline: ReturnType<typeof mockBaseline>,
  transparency: ReturnType<typeof mockTransparency>,
  opts: { stale?: boolean } = {},
) {
  const stale = opts.stale ?? false;
  const proposal = {
    id: "prop-s60",
    batch_id: batchId,
    fingerprint: "fp-s60",
    sections: [
      {
        question_run_id: run.id,
        question_key: run.question_key,
        position: 0,
        baseline_id: baseline.id,
        section_text: baseline.section_text,
        posteriors: baseline.posteriors,
        query_nodes: ["A", "B"],
        effective_hash: baseline.effective_hash,
        template_version: "v1",
        network_version: "s60-test-v1",
      },
    ],
    skipped: [],
    coverage_warnings: [],
    ddi_report: {
      dataset_version: "ddi-mock/1",
      catalog_version: "cat-mock/1",
      medication_fingerprint: "fp-mock",
      resolved_medications: [],
      coverage_unavailable_medications: [],
      pairs: [],
      limitations: [],
      generated_at: "2026-10-11T00:00:00Z",
    },
    created_at: "2026-10-11T00:00:00Z",
  };
  return {
    batch: {
      id: batchId,
      encounter_id: draftId,
      author_id: "author-s60",
      source_revision: 2,
      fingerprint: "fp-s60",
      status: "ready",
      pinned_bundle: {},
      created_at: "2026-10-11T00:00:00Z",
    },
    question_runs: [run],
    freshness: stale
      ? {
          stale: true,
          reason: "analysis facts changed since freeze",
          current_fingerprint: "fp-new",
        }
      : { stale: false, reason: "current", current_fingerprint: "fp-s60" },
    job: null,
    jobs: [
      {
        id: "job-s60-ok",
        batch_id: batchId,
        question_run_id: run.id,
        job_class: "generation",
        status: "succeeded",
        attempt_index: 1,
        max_attempts: 3,
        lease_deadline: null,
        last_heartbeat: null,
        next_eligible_at: null,
        result: null,
        created_at: "2026-10-11T00:00:00Z",
        updated_at: "2026-10-11T00:00:00Z",
      },
    ],
    attempts: 1,
    queue: mockQueue(),
    baseline,
    transparency,
    baselines: [
      {
        question_run_id: run.id,
        question_key: run.question_key,
        position: 0,
        status: "ready",
        baseline,
        transparency,
      },
    ],
    proposal,
    workflow: {
      complete: true,
      status: "complete",
      pending_question_keys: [],
      needs_clarification: [],
      skipped: [],
      coverage_warnings: [],
      ddi_status: "valid",
    },
  };
}

function mockReview(opts: {
  run: ReturnType<typeof mockRun>;
  batch: Record<string, unknown>;
  baseline: ReturnType<typeof mockBaseline> | null;
  transparency: ReturnType<typeof mockTransparency> | null;
  calculationState: "unchanged" | "recalculating" | "successfully_recalculated" | "failed";
  stale?: boolean;
}) {
  const stale = opts.stale ?? false;
  const fresh = stale
    ? {
        stale: true,
        reason: "analysis facts changed since freeze",
        current_fingerprint: "fp-new",
      }
    : { stale: false, reason: "current", current_fingerprint: "fp-s60" };
  return {
    question_run: opts.run,
    batch: opts.batch,
    baseline: opts.baseline,
    adjustable: opts.baseline !== null,
    transparency: opts.transparency,
    freshness: fresh,
    input_freshness: fresh,
    job: null,
    attempts: 1,
    queue: mockQueue(),
    original_tables: opts.baseline !== null ? mockTables() : null,
    current_tables: opts.baseline !== null ? mockTables() : null,
    current_cpt_revision_id: null,
    displayed_result_revision_id: null,
    current_result_matches: true,
    calculation_state: opts.calculationState,
    calculation_result: null,
    displayed_result: null,
    calculation_results: [],
    local_jobs: [],
    local_job: null,
    review_revision: 1,
    revisions: [],
    cpt_hash: null,
    outputs_read_only: true,
    acceptance: null,
    is_accepted: false,
    acceptances: [],
  };
}

// S60 §1 (T1, no mocks): wrong-author draft denied without leak, shared reads stay.
test("S60 §1 wrong-author draft shows denied view without leak, shared reads stay available", async ({
  page,
  request,
}) => {
  const author = { username: uniqueName("e2es60au"), password: "secret123" };
  await ensurePhysicianLocal(request, author.username, author.password);
  const { patientId, draftId, givenName } = await createPatientWithDraft(request, author);
  const sentinel = `SENTINEL-S60-${Date.now().toString(36)}`;
  await patchDraft(request, author, draftId, { working_note: sentinel }, 1);

  await loginPhysician(page, request, "e2es60st");
  await page.goto(`/#/encounters/${draftId}`);
  const denied = page.getByTestId("encounter-denied");
  await expect(denied).toContainText("Only the draft author", { timeout: 15000 });
  await expect(denied).toHaveAttribute("role", "alert");
  // No clinical content leaks through the denied surface (keyboard/text cues kept).
  await expect(page.locator("body")).not.toContainText(sentinel);
  const back = page.getByRole("link", { name: "Back to directory" });
  await expect(back).toBeVisible();
  await back.click();
  await expect(page).toHaveURL(/#\/patients/, { timeout: 15000 });

  // Shared demographics + signed chart stay readable to the other physician.
  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("body")).toContainText(givenName);
  await expect(page.locator("body")).not.toContainText(sentinel);
});

// S60 §1 (T1 draft + mocked failed run): saves stay responsive, acknowledged
// revisions survive reload, no fake proposal or enabled sign.
test("S60 §1 failed run keeps saves responsive and revisions durable with honest unavailable", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es60fl");
  const { draftId } = await createPatientWithDraft(request, physician);
  await patchDraft(
    request,
    physician,
    draftId,
    { history: { values: { h_s60_a: "yes", h_s60_b: "no" } }, gate: "true" },
    1,
  );

  const batchId = "s60-batch-failed-1111-4111-8111-111111111111";
  const run = mockRun("s60-run-failed-2222-4222-8222-222222222222", batchId, "s60_q1");
  const batchPayload = mockFailedBatch(batchId, draftId, run);
  const review = mockReview({
    run,
    batch: batchPayload.batch,
    baseline: null,
    transparency: null,
    calculationState: "unchanged",
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(review) }),
  );
  try {
    await seedStoredProposal(page, draftId, {
      batchId,
      packages: [{ question_key: "s60_q1" }],
      sourceRevision: 2,
    });
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });

    // Failed question: glyph + text (never color alone), exact retry, no baseline.
    const status = page.getByTestId("proposal-status-s60_q1");
    await expect(status).toContainText("Failed", { timeout: 15000 });
    await expect(status).toContainText("✕");
    await expect(status).toContainText("Failed after 3 of 3 attempts");
    await expect(page.getByTestId("proposal-retry-s60_q1")).toBeVisible();
    // No fake successful proposal: honest unavailable + blocked sign with reason.
    await expect(page.getByTestId("proposal-unavailable")).toContainText("Proposal unavailable");
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toContainText("Signing is blocked");
    await expect(blocked).toContainText("s60_q1");
    await expect(blocked).toContainText("failed");

    // Working note is on Register patient. Autosave there stays responsive
    // while the failed run is unchanged.
    await page.locator("#wizard-step-register").click();
    const note = page.getByLabel("Draft working note");
    await note.fill("s60 acknowledged edit while failed");
    // Generous auto-wait (not a sleep): the contended-host PATCH can take
    // seconds; Playwright returns as soon as Saved lands.
    await expect(page.locator("#draft-save-status")).toContainText("Saved (revision", {
      timeout: 30000,
    });

    // Acknowledgement must mean immediately readable, not eventually visible.
    const read = await request.get(`/api/v1/encounters/${draftId}`);
    expect(read.status(), await read.text()).toBe(200);
    expect((await read.json()).draft_data.working_note).toBe(
      "s60 acknowledged edit while failed",
    );

    await page.locator("#wizard-step-proposal-review").click();
    // Failed originals expose no adjustable baseline and block acceptance.
    await page.getByTestId("cpt-toggle-s60_q1").click();
    const panel = page.getByTestId("cpt-panel-s60_q1");
    await expect(panel).toContainText("No adjustable baseline", { timeout: 15000 });
    await expect(panel).toContainText("Acceptance blocked");
    await expect(panel.getByTestId("cpt-accept-s60_q1")).toBeDisabled();

    // Acknowledged revisions survive reload; failure states stay honest.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.locator("#draft-save-status")).toContainText("Saved (revision", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-status-s60_q1")).toContainText("Failed", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-unavailable")).toContainText("Proposal unavailable", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-sign-blocked")).toContainText("Signing is blocked");
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S60 §§2+4 (mocked stale run): batch banner + per-question ◍ fence accept/sign.
test("S60 §§2+4 stale run fences acceptance and sign-off with exact reasons", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es60sl");
  const { draftId } = await createPatientWithDraft(request, physician);
  await patchDraft(
    request,
    physician,
    draftId,
    { history: { values: { h_s60_a: "yes", h_s60_b: "no" } }, gate: "true" },
    1,
  );

  const batchId = "s60-batch-stale-1111-4111-8111-111111111111";
  const run = mockRun("s60-run-stale-2222-4222-8222-222222222222", batchId, "s60_q1");
  const section = "s60_q1 absent: B no 78.00% outcome.";
  const baseline = mockBaseline(
    "s60-base-stale-1111-4111-8111-111111111111",
    run.id,
    batchId,
    "s60_q1",
    section,
  );
  const transparency = mockTransparency("s60_q1", section);
  const batchPayload = mockBatchFromBaselines(batchId, draftId, run, baseline, transparency, {
    stale: true,
  });
  const review = mockReview({
    run,
    batch: batchPayload.batch,
    baseline,
    transparency,
    calculationState: "unchanged",
    stale: true,
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(review) }),
  );
  try {
    await seedStoredProposal(page, draftId, {
      batchId,
      packages: [{ question_key: "s60_q1" }],
      sourceRevision: 2,
    });
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });

    // Whole-batch stale banner with an explicit new-run affordance (text + action).
    const banner = page.getByTestId("proposal-stale-banner");
    await expect(banner).toContainText("stale", { timeout: 15000 });
    await expect(banner.getByRole("button", { name: /start new run/i })).toBeVisible();
    // Only the affected question is marked out of date ◍ (never color alone).
    const fresh = page.getByTestId("proposal-freshness-s60_q1");
    await expect(fresh).toContainText("Out of date", { timeout: 15000 });
    await expect(fresh).toContainText("◍");
    // Stale current values cannot be accepted or signed.
    await page.getByTestId("cpt-toggle-s60_q1").click();
    const panel = page.getByTestId("cpt-panel-s60_q1");
    await expect(panel).toContainText("Out of date", { timeout: 15000 });
    await expect(panel).toContainText("Acceptance blocked");
    await expect(panel.getByTestId("cpt-accept-s60_q1")).toBeDisabled();
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toContainText("Signing is blocked");
    await expect(blocked).toContainText("stale");
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S60 §§3-4 (mocked proposal + real plan/notes): markup renders literally,
// client preflight blocks manual sign without acceptance; no sign POST.
test("S60 §§3-4 markup renders literally and manual sign is denied with plan kept", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es60mk");
  const { draftId } = await createPatientWithDraft(request, physician);
  await patchDraft(
    request,
    physician,
    draftId,
    { history: { values: { h_s60_a: "yes", h_s60_b: "no" } }, gate: "true" },
    1,
  );

  const batchId = "s60-batch-mark-1111-4111-8111-111111111111";
  const run = mockRun("s60-run-mark-2222-4222-8222-222222222222", batchId, "s60_q1");
  const section = "s60_q1 present: <b>care</b> & follow =SUM(A1:A2) outcome.";
  const baseline = mockBaseline(
    "s60-base-mark-1111-4111-8111-111111111111",
    run.id,
    batchId,
    "s60_q1",
    section,
  );
  const transparency = mockTransparency("s60_q1", section);
  const batchPayload = mockBatchFromBaselines(batchId, draftId, run, baseline, transparency);
  const review = mockReview({
    run,
    batch: batchPayload.batch,
    baseline,
    transparency,
    calculationState: "unchanged",
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(review) }),
  );
  try {
    await seedStoredProposal(page, draftId, {
      batchId,
      packages: [{ question_key: "s60_q1" }],
      sourceRevision: 2,
    });
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s60_q1")).toContainText("Completed", {
      timeout: 15000,
    });

    // Exact current original is accept-ready (gating visible, no server roundtrip here).
    await page.getByTestId("cpt-toggle-s60_q1").click();
    const panel = page.getByTestId("cpt-panel-s60_q1");
    await expect(panel).toContainText("Accept the exact current original baseline", {
      timeout: 15000,
    });
    await expect(panel.getByTestId("cpt-accept-s60_q1")).toBeEnabled();

    // Real secondary plan with markup: saved, compared literally, never elements.
    const planText = "Plan <b>markup</b> & follow =SUM(B1:B2)";
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    // Multi-round-trip save (GET truth + PATCH); generous auto-wait only.
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision", {
      timeout: 30000,
    });
    const proposalCompare = page.getByTestId("plan-compare-proposal");
    await expect(proposalCompare).toContainText("<b>care</b>");
    expect(await proposalCompare.locator("b").count()).toBe(0);
    const planCompare = page.getByTestId("plan-compare-plan");
    await expect(planCompare).toContainText("<b>markup</b>");
    expect(await planCompare.locator("b").count()).toBe(0);

    // Real page note with markup renders literally too.
    const noteText = "<i>s60 note</i> & check =SUM(C1:C2)";
    await page.locator("#notes-proposal-input").fill(noteText);
    await page.locator("#notes-proposal-add").click();
    const noteList = page.locator("#notes-proposal-list");
    // Multi-round-trip note save (flush + list + POST + list); auto-wait only.
    await expect(noteList).toContainText("<i>s60 note</i>", { timeout: 30000 });
    expect(await noteList.locator("i").count()).toBe(0);

    // Client preflight blocks sign without acceptance; no backend denial proof.
    await page.getByTestId("sign-button").click();
    const signPanel = page.getByTestId("sign-panel");
    await expect(signPanel).toContainText("no current acceptance", { timeout: 15000 });
    await expect(signPanel).toContainText("nothing was signed");
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});
