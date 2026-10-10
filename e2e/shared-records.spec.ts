import { test, expect } from "@playwright/test";
import { createHash } from "crypto";
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

/* S51 close archive, deactivation, and multi-user race cases (seams T1/T9).
 *
 * Backend (read-only): BT/http/test_record_races.py (15 T1/T8 tests) owns
 * admin-only archive/unarchive (If-Match/ETag + Idempotency-Key, no deletion),
 * shared demographics PATCH (physician-only, relevant age/sex/status stale via
 * patient-aware hash, phone/name preserve), deactivate retain/discard with
 * draft-set revision + job fencing, and concurrent sign/save/slider/reset/
 * demographic/deactivation/archive races (one serial outcome, no second
 * draft/stale signature). No backend/src changes here.
 *
 * Frontend (read-only): ChartPage.tsx (testids archive-panel/button,
 * unarchive-button, archive-confirm(-button), archive-status,
 * chart-archived-badge, demographics-form/save/status/freshness,
 * chart-draft-badge) + PhysiciansPanel.tsx (deactivate-preview-<id>,
 * retain/discard radios, DRAFT_SET_CHANGED reconfirm) + api.ts conflictHint
 * (PATIENT_ARCHIVED / OPEN_DRAFT_EXISTS / STALE_INPUTS / DRAFT_SET_CHANGED).
 *
 * Constraints (follow signing/proposal-review/question-freshness): real
 * auth/draft creation via T1 helpers, no worker in e2e — batch/review/sign
 * journeys use controlled route mocks as the documented alternative
 * (deterministic, no sleeps for concurrency). Mocks carry 64-char hashes,
 * fixed UUIDs, two-node 80/20 + 90/10 + 30/70 literals. Demographics,
 * archive, deactivation, and follow-up slot conflicts are REAL (no mocks).
 * One T1 server-truth denial (archived edit 409 + stale accept shape via
 * real demographics race) proves UI mirrors server without mocks.
 *
 * Selector contract:
 * - archive-panel, archive-button/unarchive-button, archive-confirm,
 *   archive-confirm-button, archive-status, chart-archived-badge
 * - demographics-form/save/status/freshness, chart-draft-badge
 * - deactivate-preview-<id> (role=status), Deactivate physician form
 *
 * Run with --workers=1: parallel workers contend on the shared dev DB
 * (physician/patient single-draft slots). The retain test discards its
 * retained draft at the end so the reused account is empty for the next run.
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

async function loginAdmin(page: import("@playwright/test").Page): Promise<void> {
  await loginAs(page, "admin", "admin", "admin");
}

async function adminSession(
  request: import("@playwright/test").APIRequestContext,
): Promise<string> {
  const login = await request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "admin", role: "admin" },
  });
  expect(login.ok()).toBeTruthy();
  const { csrf_token } = await login.json();
  return csrf_token as string;
}

async function createPhysicianWithId(
  request: import("@playwright/test").APIRequestContext,
  username: string,
  password: string,
): Promise<string> {
  const csrf = await adminSession(request);
  const key = `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  const create = await request.post("/api/v1/physicians", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": key },
    data: { username, password },
  });
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  return body.user.id as string;
}

async function createPatientWithDraft(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
): Promise<{ patientId: string; draftId: string; identifier: string }> {
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
    identifier: payload.identifier,
  };
}

async function gotoChart(
  page: import("@playwright/test").Page,
  patientId: string,
): Promise<void> {
  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
}

async function gotoEncounter(
  page: import("@playwright/test").Page,
  draftId: string,
): Promise<void> {
  await page.goto(`/#/encounters/${draftId}`);
  await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });
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
}

// --- Minimal two-node mocks (80/20, 90/10, 30/70; 64-char hashes; fixed UUIDs) ---

function mockOriginalTables() {
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

function mockSavedInputs(sourceRevision: number) {
  return [
    {
      node_id: "A",
      patient_type: "tristate",
      status: "observed",
      value: "yes",
      source_path: "synthetic/history/h_s51_a",
      source_revision: sourceRevision,
    },
    {
      node_id: "B",
      patient_type: "tristate",
      status: "observed",
      value: "no",
      source_path: "synthetic/history/h_s51_b",
      source_revision: sourceRevision,
    },
  ];
}

function mockBaseline(baselineId: string, runId: string, batchId: string, key: string) {
  return {
    id: baselineId,
    question_run_id: runId,
    batch_id: batchId,
    source_hash: "a".repeat(64),
    effective_hash: "b".repeat(64),
    effective_xml: "<BIF>mock effective</BIF>",
    raw_response: { network_hash: "a".repeat(64), tables: mockOriginalTables() },
    validated_tables: mockOriginalTables(),
    query_nodes: ["A", "B"],
    posteriors: [
      { node_id: "A", states: ["no", "yes"], probabilities: [0.8, 0.2] },
      { node_id: "B", states: ["no", "yes"], probabilities: [0.78, 0.22] },
    ],
    section_text: `${key} absent: B no 78.00% outcome.`,
    template_version: "v1",
    prompt_version: "v1",
    network_version: "s51-test-v1",
    provider_model: "mock-model",
    projection_hash: "c".repeat(64),
    provenance: { question_key: key, provider_model: "mock-model" },
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockTransparency(key: string, sourceRevision: number) {
  return {
    question_key: key,
    network_version: "s51-test-v1",
    saved_patient_inputs: mockSavedInputs(sourceRevision),
    returned_cpt_percentages: mockOriginalTables(),
    deterministic_result: {
      posteriors: [
        { node_id: "A", states: ["no", "yes"], probabilities: [0.8, 0.2] },
        { node_id: "B", states: ["no", "yes"], probabilities: [0.78, 0.22] },
      ],
      section_text: `${key} absent: B no 78.00% outcome.`,
      query_nodes: ["A", "B"],
      effective_hash: "b".repeat(64),
    },
  };
}

function mockRun(runId: string, batchId: string, key: string) {
  return {
    id: runId,
    batch_id: batchId,
    question_key: key,
    status: "ready",
    gate_reason: "gate true",
    projection: { variables: mockSavedInputs(2) },
    projection_hash: "c".repeat(64),
    fingerprint: "fp-mock-1",
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockBatch(
  batchId: string,
  draftId: string,
  runs: ReturnType<typeof mockRun>[],
  baselines: ReturnType<typeof mockBaseline>[],
  transparencies: ReturnType<typeof mockTransparency>[],
) {
  return {
    batch: {
      id: batchId,
      encounter_id: draftId,
      author_id: "author-mock",
      source_revision: 2,
      fingerprint: "fp-mock-1",
      status: "ready",
      pinned_bundle: {},
      created_at: "2026-10-10T00:00:00Z",
    },
    question_runs: runs,
    freshness: { stale: false, reason: "current", current_fingerprint: "fp-mock-1" },
    job: null,
    jobs: runs.map((run, index) => ({
      id: `job-${index}`,
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
      created_at: "2026-10-10T00:00:00Z",
      updated_at: "2026-10-10T00:00:00Z",
    })),
    attempts: runs.length,
    queue: {
      busy: false,
      leased_count: 0,
      queued_count: 0,
      max_provider_slots: 2,
      max_queued_runs: 100,
      queue_position: null,
    },
    baseline: baselines[0] ?? null,
    transparency: transparencies[0] ?? null,
    baselines: runs.map((run, index) => ({
      question_run_id: run.id,
      question_key: run.question_key,
      position: index,
      status: "ready",
      baseline: baselines[index] ?? null,
      transparency: transparencies[index] ?? null,
    })),
    proposal: {
      id: "prop-1",
      batch_id: batchId,
      fingerprint: "fp-mock-1",
      sections: baselines.map((base, index) => ({
        question_run_id: runs[index].id,
        question_key: runs[index].question_key,
        position: index,
        baseline_id: base.id,
        section_text: base.section_text,
        posteriors: base.posteriors,
        query_nodes: ["A", "B"],
        effective_hash: base.effective_hash,
        template_version: "v1",
        network_version: "s51-test-v1",
      })),
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
        generated_at: "2026-10-10T00:00:00Z",
      },
      created_at: "2026-10-10T00:00:00Z",
    },
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

function mockAcceptance(
  accId: string,
  draftId: string,
  runId: string,
  batchId: string,
  questionKey: string,
  baselineId: string,
) {
  return {
    id: accId,
    encounter_id: draftId,
    question_run_id: runId,
    batch_id: batchId,
    question_key: questionKey,
    baseline_id: baselineId,
    cpt_revision_id: null,
    cpt_hash: "d".repeat(64),
    result_kind: "baseline",
    result_id: baselineId,
    input_hash: "fp-mock-1",
    projection_hash: "c".repeat(64),
    actor_username: "physician-mock",
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockReview(opts: {
  run: ReturnType<typeof mockRun>;
  batch: Record<string, unknown>;
  baseline: ReturnType<typeof mockBaseline>;
  transparency: ReturnType<typeof mockTransparency>;
  acceptance?: Record<string, unknown> | null;
}) {
  const acceptance = opts.acceptance ?? null;
  return {
    question_run: opts.run,
    batch: opts.batch,
    baseline: opts.baseline,
    adjustable: true,
    transparency: opts.transparency,
    freshness: { stale: false, reason: "current", current_fingerprint: "fp-mock-1" },
    input_freshness: {
      stale: false,
      reason: "current",
      current_fingerprint: "fp-mock-1",
    },
    job: null,
    attempts: 1,
    queue: {
      busy: false,
      leased_count: 0,
      queued_count: 0,
      max_provider_slots: 2,
      max_queued_runs: 100,
      queue_position: null,
    },
    original_tables: mockOriginalTables(),
    current_tables: mockOriginalTables(),
    current_cpt_revision_id: null,
    displayed_result_revision_id: null,
    current_result_matches: true,
    calculation_state: "unchanged",
    calculation_result: null,
    displayed_result: null,
    calculation_results: [],
    local_jobs: [],
    local_job: null,
    review_revision: 1,
    revisions: [],
    cpt_hash: null,
    outputs_read_only: true,
    acceptance,
    is_accepted: acceptance !== null,
    acceptances: acceptance !== null ? [acceptance] : [],
  };
}

const TWO_NODE_XML =
  '<BIF VERSION="0.3"><NETWORK><NAME>TwoNode</NAME>' +
  "<PROPERTY>net-prop=kept</PROPERTY>" +
  "<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME>" +
  "<PROPERTY>var-prop-a=kept</PROPERTY></VARIABLE>" +
  "<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>" +
  "<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE>" +
  "<PROPERTY>def-prop-a=kept</PROPERTY></DEFINITION>" +
  "<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>" +
  "<TABLE>0.5 0.5 0.5 0.5</TABLE></DEFINITION>" +
  "</NETWORK></BIF>";

function s51Package(
  questionKey: string,
  fieldA: string,
  fieldB: string,
  networkHash: string,
): Record<string, unknown> {
  return {
    manifest: {
      schema_version: "question-package-v1",
      question_key: questionKey,
      title: `Synthetic ${questionKey}`,
      workflow: "registration",
      version: "s51-test-v1",
      review_status: "draft",
      network_file: "network.xml",
      network_hash: networkHash,
      source_refs: ["synthetic/source.md"],
      declared_node_order: ["A", "B"],
      variables: [
        {
          node_id: "A",
          kind: "nature",
          patient_value_type: "tristate",
          states: ["no", "yes"],
          ordered_parents: [],
        },
        {
          node_id: "B",
          kind: "nature",
          patient_value_type: "tristate",
          states: ["no", "yes"],
          ordered_parents: ["A"],
        },
      ],
      patient_mappings: [
        {
          node_id: "A",
          allowed_source_paths: [`synthetic/history/${fieldA}`],
          transform: "copy",
          time_window: "current_encounter",
          usage: "cpt_context",
          missing_policy: "needs_clarification",
        },
        {
          node_id: "B",
          allowed_source_paths: [`synthetic/history/${fieldB}`],
          transform: "copy",
          time_window: "current_encounter",
          usage: "cpt_context",
          missing_policy: "needs_clarification",
        },
      ],
      applicability: {
        expression: "true",
        required_fields: [`synthetic/history/${fieldA}`, `synthetic/history/${fieldB}`],
        unknown_policy: "needs_clarification",
      },
      cpt_contract: {
        nodes: [
          { node_id: "A", parent_ids: [], states: ["no", "yes"] },
          { node_id: "B", parent_ids: ["A"], states: ["no", "yes"] },
        ],
      },
      query_nodes: ["A", "B"],
      execution_evidence: {},
      prompt_version: "v1",
      template_version: "v1",
    },
    prompt: {
      version: "v1",
      text: "Estimate every CPT in percentage units for this question using only the supplied inputs. Return strict schema.",
    },
    template: {
      version: "v1",
      branches: [
        {
          when: { node: "B", state: "yes", operator: "==" },
          text: `${questionKey} present: {B} outcome.`,
        },
        {
          when: { node: "B", state: "no", operator: "==" },
          text: `${questionKey} absent: {B} outcome.`,
        },
      ],
    },
    examples: {
      numerical: [{ inputs: {}, expected: { A: { no: 0.8, yes: 0.2 } } }],
      clinical: [{ inputs: {}, expected_for_review: { B: "yes" }, note: "review candidate" }],
    },
    review: {
      reviewer: "owner",
      decision: "draft",
      date: "2026-10-04",
      source_hashes: { "network.xml": networkHash },
      assumptions: ["synthetic only"],
      source_comparison: "synthetic",
      explicit_graph: "A -> B",
      reference_table_provenance: "synthetic",
      estimation_instructions: "estimate every CPT",
      result_mapping: "B yes/no",
      numerical_examples: "two-node",
      clinical_examples: "synthetic",
      admission_measurements: "synthetic",
      open_assumptions: "synthetic",
    },
    network_xml: TWO_NODE_XML,
  };
}

// S51 §1: admin archive/unarchive confirmation flow (identifier+revision,
// archived badge, provisional wording, author resumable).
test("S51 §1 admin archives and unarchives with identifier revision confirm", async ({
  page,
  request,
}) => {
  const author = await loginPhysician(page, request, "e2es51arch");
  const { patientId, identifier } = await createPatientWithDraft(request, author);
  await signOut(page);
  await loginAdmin(page);
  await gotoChart(page, patientId);
  // Admin sees the archive panel and the active badge.
  await expect(page.getByTestId("archive-panel")).toBeVisible();
  await expect(page.getByTestId("chart-archived-badge")).toContainText("Active");
  await expect(page.getByTestId("archive-button")).toBeVisible();
  // Explicit confirmation shows identifier + revision, provisional wording.
  await page.getByTestId("archive-button").click();
  const confirm = page.getByTestId("archive-confirm");
  await expect(confirm).toBeVisible();
  await expect(confirm).toContainText(identifier);
  await expect(confirm).toContainText("rev 1");
  await expect(confirm).toContainText("provisional");
  await expect(confirm).toContainText("nothing is deleted");
  const confirmButton = page.getByTestId("archive-confirm-button");
  await expect(confirmButton).toContainText(identifier);
  await expect(confirmButton).toContainText("rev 1");
  await confirmButton.click();
  // Archive succeeds (revision 1->2): badge + retained-draft wording persist
  // across the parent reload (the transient done toast remounts away, so the
  // badge is the stable proof, not archive-status).
  await expect(page.getByTestId("chart-archived-badge")).toContainText("Archived", {
    timeout: 10000,
  });
  await expect(page.getByTestId("chart-archived-badge")).toContainText(
    "not owner-confirmed",
  );
  await expect(page.getByTestId("chart-draft-badge")).toContainText(
    "retained private draft",
  );
  // Unarchive resumes the retained slot.
  await expect(page.getByTestId("unarchive-button")).toBeVisible();
  await page.getByTestId("unarchive-button").click();
  await expect(page.getByTestId("archive-confirm")).toContainText(identifier);
  await expect(page.getByTestId("archive-confirm")).toContainText("Writes resume");
  await page.getByTestId("archive-confirm-button").click();
  await expect(page.getByTestId("chart-archived-badge")).toContainText("Active", {
    timeout: 10000,
  });
});

// S51 §1: physician denied archive buttons, blocked create messaging, author resumable.
test("S51 §1 physician sees no archive buttons and blocked follow-up hint", async ({
  page,
  request,
}) => {
  const authorCreds = {
    username: uniqueName("e2es51author"),
    password: "secret123",
  };
  await ensurePhysician(request, authorCreds.username, authorCreds.password);
  const { patientId } = await createPatientWithDraft(request, authorCreds);
  // Archive via real admin API (T1 server truth for the blocked state below).
  const adminCsrf = await adminSession(request);
  const patientGet = await request.get(`/api/v1/patients/${patientId}`);
  expect(patientGet.ok()).toBeTruthy();
  const patientBody = await patientGet.json();
  const revision = patientBody.patient.revision as number;
  const archived = await request.post(`/api/v1/patients/${patientId}/archive`, {
    headers: { "X-CSRF-Token": adminCsrf, "If-Match": `"${revision}"` },
    data: {},
  });
  expect(archived.status(), await archived.text()).toBe(200);
  // Physician sees the archived badge, no archive panel/buttons, read-only notice.
  await loginAs(page, "physician", authorCreds.username, authorCreds.password);
  await acknowledgeWarning(page);
  await gotoChart(page, patientId);
  await expect(page.getByTestId("chart-archived-badge")).toContainText(
    "provisional policy",
  );
  await expect(page.getByTestId("archive-panel")).toHaveCount(0);
  await expect(page.getByTestId("archive-button")).toHaveCount(0);
  await expect(page.getByTestId("unarchive-button")).toHaveCount(0);
  await expect(page.getByText("demographics editing is blocked")).toBeVisible();
  // Blocked follow-up create shows the canned archived hint (no draft content).
  await page.getByTestId("chart-create-followup").click();
  await expect(page.getByRole("alert")).toContainText("archived and read-only");
  await expect(page.getByRole("alert")).toContainText("provisional");
  // After real unarchive the author resumes (demographics form returns).
  const adminCsrf2 = await adminSession(request);
  const patientGet2 = await request.get(`/api/v1/patients/${patientId}`);
  const rev2 = ((await patientGet2.json()).patient.revision as number) ?? 2;
  const unarchived = await request.post(`/api/v1/patients/${patientId}/unarchive`, {
    headers: { "X-CSRF-Token": adminCsrf2, "If-Match": `"${rev2}"` },
    data: {},
  });
  expect(unarchived.status(), await unarchived.text()).toBe(200);
  await page.reload();
  await expect(page.getByTestId("chart-heading")).toBeVisible();
  await expect(page.getByTestId("demographics-form")).toBeVisible();
});

// S51 §§2+4: shared demographics phone preserves, age stales with ◍ banner.
//
// Parent reload remounts the form (ChartPage reload sets loading), so the
// transient saved/freshness toast is caught by delaying the post-save chart
// GET. Server truth (revision bump + persisted phone/age via T1 below) is
// the stable proof; the banner is asserted inside the delay window.
test("S51 §§2+4 physician phone edit preserves, age edit marks out of date", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es51demo");
  const { patientId } = await createPatientWithDraft(request, physician);
  await gotoChart(page, patientId);
  const form = page.getByTestId("demographics-form");
  await expect(form).toBeVisible();
  // Delay the post-save chart reload so the transient status is observable.
  let delayChart = false;
  await page.route("**/api/v1/patients/*/chart", async (route) => {
    if (delayChart) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    await route.continue();
  });
  try {
    // Phone-only edit preserves acceptance (no stale banner, no draft leak).
    delayChart = true;
    await page.getByLabel("Phone (blank keeps current)").fill("555-0100");
    await page.getByTestId("demographics-save").click();
    await expect(page.getByTestId("demographics-status")).toContainText(
      "Phone/name edits preserve acceptance",
      { timeout: 10000 },
    );
    await expect(page.getByTestId("demographics-freshness")).toHaveCount(0);
    await expect(page.getByTestId("demographics-status")).not.toContainText("h_s51");
    // Wait for the delayed reload to land (revision 2, phone persisted).
    await expect(page.getByText("Shared demographics (revision 2)")).toBeVisible({
      timeout: 10000,
    });
    delayChart = true;
    // Relevant age edit shows the ◍ out-of-date banner (no draft content).
    await page.getByLabel("Age").fill("31");
    await page.getByTestId("demographics-save").click();
    const freshness = page.getByTestId("demographics-freshness");
    await expect(freshness).toContainText("◍", { timeout: 10000 });
    await expect(freshness).toContainText("Out of date");
    await expect(freshness).toContainText("age, sex, or clinical status");
    await expect(freshness).not.toContainText("h_s51");
    // Server truth (T1): the acknowledged age persisted across the reload.
    const csrf = await physicianSession(request, physician);
    void csrf;
    const patientGet = await request.get(`/api/v1/patients/${patientId}`);
    expect(patientGet.ok()).toBeTruthy();
    const patientBody = await patientGet.json();
    expect(patientBody.patient.age).toBe(31);
    expect(patientBody.patient.phone).toBe("555-0100");
  } finally {
    await page.unroute("**/api/v1/patients/*/chart");
  }
  // Reload keeps the acknowledged age.
  await page.reload();
  await expect(page.getByTestId("chart-heading")).toBeVisible();
  await expect(page.getByLabel("Age")).toHaveValue("31");
});

// S51 §§2+4: sex/status stale without granting draft access (fresh patients).
test("S51 §§2+4 sex and status edits stale without draft leak", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es51sex");
  let delayChart = false;
  let first!: Awaited<ReturnType<typeof createPatientWithDraft>>;
  let second!: Awaited<ReturnType<typeof createPatientWithDraft>>;
  await page.route("**/api/v1/patients/*/chart", async (route) => {
    if (delayChart) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    await route.continue();
  });
  try {
    // Sex edit on a fresh patient.
    first = await createPatientWithDraft(request, physician);
    await gotoChart(page, first.patientId);
    delayChart = true;
    await page.getByLabel("Sex").selectOption("M");
    await page.getByTestId("demographics-save").click();
    await expect(page.getByTestId("demographics-freshness")).toContainText("◍", {
      timeout: 10000,
    });
    await expect(page.getByTestId("demographics-freshness")).not.toContainText("h_s51");
    // Status edit on a second fresh patient (first is already stale).
    second = await createPatientWithDraft(request, physician);
    delayChart = false;
    await page.goto(`/#/patients/${second.patientId}/chart`);
    await expect(page.getByTestId("chart-heading")).toBeVisible();
    delayChart = true;
    await page.getByLabel("Clinical status").selectOption("established");
    await page.getByTestId("demographics-save").click();
    await expect(page.getByTestId("demographics-freshness")).toContainText("◍", {
      timeout: 10000,
    });
  } finally {
    await page.unroute("**/api/v1/patients/*/chart");
  }
  // Stranger physician cannot read the draft via any chart affordance.
  const stranger = uniqueName("e2es51strang");
  await ensurePhysician(request, stranger, "secret123");
  const strangerCsrf = await physicianSession(request, {
    username: stranger,
    password: "secret123",
  });
  const denied = await request.get(`/api/v1/encounters/${second.draftId}`, {
    headers: { "X-CSRF-Token": strangerCsrf },
  });
  // Stranger read is denied through the real API (T1 server truth, no leak).
  expect([401, 403]).toContain(denied.status());
  expect(await denied.text()).not.toContain("h_s51");
});

// Physicians table shows only the first 100 of 2000+ accumulated rows, so a
// freshly created physician never appears (same root cause as the
// identity.spec trio). Reuse a visible row instead: pick the first active
// physician from the real first page, reset its password, and drive it as
// the target. Its row is guaranteed visible; the draft it holds is real.
async function reuseVisiblePhysicianWithZeroDrafts(
  request: import("@playwright/test").APIRequestContext,
): Promise<{ id: string; username: string; password: string; revision: number }> {
  const adminCsrf = await adminSession(request);
  const list = await request.get("/api/v1/physicians?limit=100");
  expect(list.ok()).toBeTruthy();
  const items = ((await list.json()).items as Record<string, unknown>[]) ?? [];
  const candidates = items.filter(
    (item) => item["role"] === "physician" && item["active"] === true,
  );
  expect(candidates.length).toBeGreaterThan(0);
  const password = `pw${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  for (const candidate of candidates.slice(0, 50)) {
    const id = candidate["id"] as string;
    const preview = await request.get(`/api/v1/physicians/${id}/open-drafts`, {
      headers: { "X-CSRF-Token": adminCsrf },
    });
    if (!preview.ok()) {
      continue;
    }
    const previewBody = await preview.json();
    if ((previewBody.reviewed_drafts as unknown[]).length !== 0) {
      continue;
    }
    const revision = candidate["revision"] as number;
    const patched = await request.patch(`/api/v1/physicians/${id}`, {
      headers: { "X-CSRF-Token": adminCsrf, "If-Match": `"${revision}"` },
      data: { password },
    });
    expect(patched.status(), await patched.text()).toBe(200);
    return { id, username: candidate["username"] as string, password, revision };
  }
  throw new Error("no visible physician with zero drafts in first 10");
}

// S51 §2: deactivation retain keeps the slot, reactivation never resurrects sessions.
test("S51 §2 admin retain keeps slot with preview count, reactivate messaging", async ({
  page,
  request,
}) => {
  const target = await reuseVisiblePhysicianWithZeroDrafts(request);
  const targetCreds = { username: target.username, password: target.password };
  // Target holds one open draft (real slot occupancy).
  await createPatientWithDraft(request, targetCreds);
  await loginAdmin(page);
  await page.goto("/#/physicians");
  await expect(page.getByRole("table", { name: "Physicians" })).toBeVisible({
    timeout: 15000,
  });
  const row = page
    .getByRole("table", { name: "Physicians" })
    .getByRole("row", { name: new RegExp(target.username) });
  await expect(row).toBeVisible({ timeout: 15000 });
  await row.getByRole("button", { name: "Deactivate" }).click();
  const form = page.getByRole("form", { name: "Deactivate physician" });
  // Open-draft preview shows the count + revision, identifiers only.
  const preview = page.getByTestId(`deactivate-preview-${target.id}`);
  await expect(preview).toContainText("Open drafts: 1", { timeout: 10000 });
  await expect(preview).toContainText("identifiers only");
  await form.getByLabel(/Retain/).check();
  await form.getByRole("button", { name: "Confirm deactivation" }).click();
  await expect(page.getByText(/retained.*occupying the single-draft slot/)).toBeVisible({
    timeout: 10000,
  });
  await expect(page.getByText(/queued work cancelled/)).toBeVisible();
  // Reactivation messaging names no-resurrect + new login.
  const inactiveRow = page
    .getByRole("table", { name: "Physicians" })
    .getByRole("row", { name: new RegExp(target.username) });
  await expect(inactiveRow).toBeVisible({ timeout: 15000 });
  await inactiveRow.getByRole("button", { name: "Reactivate" }).click();
  await expect(
    page.getByText(/Discarded drafts are not resurrected/),
  ).toBeVisible({ timeout: 10000 });
  // Cleanup (T1): discard the retained draft so the reused account is empty
  // for the next run (retain keeps the slot by design).
  const retainCsrf = await physicianSession(request, targetCreds);
  const retainedList = await request.get(`/api/v1/physicians/${target.id}/open-drafts`, {
    headers: { "X-CSRF-Token": await adminSession(request) },
  });
  if (retainedList.ok()) {
    const retainedBody = await retainedList.json();
    for (const entry of (retainedBody.reviewed_drafts as Record<string, unknown>[]) ?? []) {
      const encId = entry["encounter_id"] as string;
      const encGet = await request.get(`/api/v1/encounters/${encId}`, {
        headers: { "X-CSRF-Token": retainCsrf },
      });
      if (!encGet.ok()) {
        continue;
      }
      const encRev = ((await encGet.json()).revision as number) ?? 1;
      await request.post(`/api/v1/encounters/${encId}/discard`, {
        headers: { "X-CSRF-Token": retainCsrf, "If-Match": `"${encRev}"` },
        data: { confirm: true },
      });
    }
  }
  await expect(
    page.getByRole("table", { name: "Physicians" }),
  ).toBeVisible({ timeout: 15000 });
});

// S51 §2: discard needs exact revision, set change forces reconfirm, no resurrect.
test("S51 §2 discard reconfirms after DRAFT_SET_CHANGED, never resurrects", async ({
  page,
  request,
}) => {
  const target = await reuseVisiblePhysicianWithZeroDrafts(request);
  const targetCreds = { username: target.username, password: target.password };
  const first = await createPatientWithDraft(request, targetCreds);
  await loginAdmin(page);
  await page.goto("/#/physicians");
  const row = page
    .getByRole("table", { name: "Physicians" })
    .getByRole("row", { name: new RegExp(target.username) });
  await expect(row).toBeVisible({ timeout: 15000 });
  await row.getByRole("button", { name: "Deactivate" }).click();
  const form = page.getByRole("form", { name: "Deactivate physician" });
  const preview = page.getByTestId(`deactivate-preview-${target.id}`);
  await expect(preview).toContainText("Open drafts: 1", { timeout: 10000 });
  // Change the set under review via real API: author saves (bumps revision).
  const targetCsrf = await physicianSession(request, targetCreds);
  const draftGet = await request.get(`/api/v1/encounters/${first.draftId}`, {
    headers: { "X-CSRF-Token": targetCsrf },
  });
  const draftRev = ((await draftGet.json()).revision as number) ?? 1;
  const saved = await request.patch(`/api/v1/encounters/${first.draftId}`, {
    headers: { "X-CSRF-Token": targetCsrf, "If-Match": `"${draftRev}"` },
    data: { draft_data: { history: { values: { h_changed: "yes" } } } },
  });
  expect(saved.status(), await saved.text()).toBe(200);
  // Discard with the stale preview now 409s and forces re-review.
  await form.getByLabel(/Discard/).check();
  // Discard needs both explicit confirmations.
  const reviewedBox = form.getByText(/I reviewed the current set/);
  await expect(reviewedBox).toBeVisible();
  await form.locator("input[type=checkbox]").first().check();
  await form.locator("input[type=checkbox]").nth(1).check();
  await form.getByRole("button", { name: "Confirm deactivation" }).click();
  await expect(form.getByText(/draft set changed since it was reviewed/i)).toBeVisible({
    timeout: 10000,
  });
  await expect(preview).toContainText("Open drafts: 1", { timeout: 10000 });
  // Re-review the fresh set and confirm: slots release, no resurrect.
  await expect(form.getByText(/I reviewed the current set/)).toBeVisible();
  await form.locator("input[type=checkbox]").first().check();
  await form.locator("input[type=checkbox]").nth(1).check();
  await form.getByRole("button", { name: "Confirm deactivation" }).click();
  await expect(page.getByText(/discarded and the slots released/)).toBeVisible({
    timeout: 10000,
  });
  await expect(page.getByText(/does not resurrect discarded drafts/)).toBeVisible();
  // Server truth (T1): the discarded draft reads 404 after reactivation.
  const adminCsrf = await adminSession(request);
  const reactivated = await request.post(`/api/v1/physicians/${target.id}/reactivate`, {
    headers: { "X-CSRF-Token": adminCsrf },
  });
  expect(reactivated.status(), await reactivated.text()).toBe(200);
  const freshCsrf = await physicianSession(request, targetCreds);
  const reread = await request.get(`/api/v1/encounters/${first.draftId}`, {
    headers: { "X-CSRF-Token": freshCsrf },
  });
  expect(reread.status()).toBe(404);
});

// S51 §§3-4: race messaging (OPEN_DRAFT_EXISTS / PATIENT_ARCHIVED / STALE_INPUTS)
// plus single-POST double-click guards.
test("S51 §§3-4 race hints name the serial outcome, double submit posts once", async ({
  page,
  request,
}) => {
  const owner = await loginPhysician(page, request, "e2es51race");
  const { patientId } = await createPatientWithDraft(request, owner);
  // Occupied slot: second follow-up is a generic conflict without content.
  await gotoChart(page, patientId);
  await page.getByTestId("chart-create-followup").click();
  await expect(page.getByRole("alert")).toContainText("already exists", {
    timeout: 10000,
  });
  await expect(page.getByRole("alert")).toContainText("no second draft");
  // Archived: follow-up blocked under the provisional policy.
  const ownerCsrf = await physicianSession(request, owner);
  const adminCsrf = await adminSession(request);
  const patientGet = await request.get(`/api/v1/patients/${patientId}`);
  const rev = ((await patientGet.json()).patient.revision as number) ?? 1;
  void ownerCsrf;
  const archived = await request.post(`/api/v1/patients/${patientId}/archive`, {
    headers: { "X-CSRF-Token": adminCsrf, "If-Match": `"${rev}"` },
    data: {},
  });
  expect(archived.status(), await archived.text()).toBe(200);
  await page.reload();
  await expect(page.getByTestId("chart-heading")).toBeVisible();
  // Physician chart keeps the archived notice (form hidden, follow-up blocked).
  await expect(page.getByText("Follow-up creation is blocked while archived")).toBeVisible();
  // STALE_INPUTS sign denial via controlled mock (no worker): canned hint + server code.
  // Real draft PATCH to rev2 so the mocked batch sourceRevision matches (as in
  // signing.spec); accepted review makes the ready SignPanel render.
  const raceDraft = await createPatientWithDraft(request, owner);
  const draftId = raceDraft.draftId;
  const raceCsrf = await physicianSession(request, owner);
  const patchAttempt = async (csrfToken: string) =>
    request.patch(`/api/v1/encounters/${draftId}`, {
      headers: { "X-CSRF-Token": csrfToken, "If-Match": '"1"' },
      data: {
        draft_data: {
          history: { values: { h_s51_a: "yes", h_s51_b: "no" } },
          gate: "true",
        },
      },
    });
  let patched = await patchAttempt(raceCsrf);
  if (patched.status() === 401) {
    patched = await patchAttempt(await physicianSession(request, owner));
  }
  expect(patched.status(), await patched.text()).toBe(200);
  const batchId = "s51-race-batch-1111-4111-8111-111111111111";
  const runId = "s51-race-run-2222-4222-8222-222222222222";
  const baseId = "s51-race-base-3333-4333-8333-333333333333";
  const accId = "s51-race-acc-4444-4444-8444-444444444444";
  const run = mockRun(runId, batchId, "s51_q1");
  const base = mockBaseline(baseId, runId, batchId, "s51_q1");
  const transparency = mockTransparency("s51_q1", 2);
  const batchPayload = mockBatch(batchId, draftId, [run], [base], [transparency]);
  const acceptance = mockAcceptance(accId, draftId, runId, batchId, "s51_q1", baseId);
  const review = mockReview({
    run,
    batch: batchPayload.batch,
    baseline: base,
    transparency,
    acceptance,
  });
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [s51Package("s51_q1", "h_s51_a", "h_s51_b", networkHash)];
  await page.evaluate(
    ({ encounterId: id, stored }) => {
      localStorage.setItem(`xi.proposal.${id}`, JSON.stringify(stored));
    },
    { encounterId: draftId, stored: { batchId, packages, sourceRevision: 2 } },
  );
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(batchPayload),
    }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(review),
    }),
  );
  let signPosts = 0;
  await page.route("**/api/v1/encounters/*/sign", async (route) => {
    signPosts += 1;
    // Small delay so the second click lands while pending (guard = 1 POST).
    await new Promise((resolve) => setTimeout(resolve, 400));
    return route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        code: "STALE_INPUTS",
        message: "Patient inputs changed. Regenerate before signing.",
        field_errors: {},
        request_id: "00000000-0000-4000-8000-000000000000",
        retryable: false,
      }),
    });
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s51_q1")).toBeVisible({
      timeout: 15000,
    });
    // Real secondary plan save (own revision fence) so sign reaches the POST.
    const planText = `Secondary plan race ${Date.now().toString(36)}: monitor.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText(
      "Saved (plan revision 2)",
      { timeout: 10000 },
    );
    const signButton = page.getByTestId("sign-button");
    await expect(signButton).toBeEnabled();
    // Double-click while pending: single POST, stale hint, plan preserved.
    // SignPanel renders 409/412 denials in its own role=alert (sign-status
    // keeps the ready copy; history validation may add a second alert, so
    // scope to the sign panel); the canned STALE_INPUTS hint + server code
    // prove messaging.
    await signButton.click();
    await signButton.click({ force: true }).catch(() => undefined);
    const signError = page.getByTestId("sign-panel").getByRole("alert");
    await expect(signError).toContainText("out of date", { timeout: 15000 });
    await expect(signError).toContainText("STALE_INPUTS");
    await expect(signError).toContainText("Nothing was signed");
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
    expect(signPosts).toBe(1);
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/encounters/*/sign");
  }
});

// S51 Verify/exit: T1 server truth — archived edit 409 mirrors the UI hint.
test("S51 server truth archived edit denied 409 without draft content", async ({
  request,
}) => {
  const physician = {
    username: uniqueName("e2es51truth"),
    password: "secret123",
  };
  await ensurePhysician(request, physician.username, physician.password);
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
  const create = await request.post("/api/v1/patients", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": key },
    data: payload,
  });
  expect(create.status(), await create.text()).toBe(201);
  const body = await create.json();
  const patientId = body.patient.id as string;
  const draftId = body.draft.id as string;
  const adminCsrf = await adminSession(request);
  const archived = await request.post(`/api/v1/patients/${patientId}/archive`, {
    headers: { "X-CSRF-Token": adminCsrf, "If-Match": '"1"' },
    data: {},
  });
  expect(archived.status(), await archived.text()).toBe(200);
  // Real server denial (no mocks): archived draft edit 409s without content.
  const freshCsrf = await physicianSession(request, physician);
  const denied = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": freshCsrf, "If-Match": '"1"' },
    data: { draft_data: { history: { values: { h_secret: "yes" } } } },
  });
  expect(denied.status()).toBe(409);
  const deniedBody = await denied.json();
  expect(deniedBody.code).toBe("PATIENT_ARCHIVED");
  expect(await denied.text()).not.toContain("h_secret");
  // Archived follow-up create 409s the same way (slot hint stays generic).
  const deniedCreate = await request.post(`/api/v1/patients/${patientId}/encounters`, {
    headers: { "X-CSRF-Token": freshCsrf },
    data: { kind: "follow_up" },
  });
  expect(deniedCreate.status()).toBe(409);
  expect((await deniedCreate.json()).code).toBe("PATIENT_ARCHIVED");
});
