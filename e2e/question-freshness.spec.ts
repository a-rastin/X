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
} from "./helpers";

/* S48d regenerate only questions affected by patient-data changes (seams T1/T9).
 *
 * Backend (read-only): per-question input_freshness + carry + STALE_INPUTS
 * (BT/worker/test_question_freshness.py, 5 T1/T8 tests). Frontend (read-only):
 * web/src/features/reasoning/api.ts getQuestionInputFreshness +
 * ProposalReviewPanel.tsx proposal-freshness-<key> labels + CptReviewPanel.tsx
 * stale messaging. No backend/frontend implementation changes here (TESTS ONLY).
 *
 * Constraints: real auth/draft via T1, no worker harness — stale/fresh/
 * skipped/carried journeys use controlled batch + review route mocks as the
 * documented S48 alternative (deterministic, no sleeps for concurrency).
 * No DB-row asserts, no internal mocks, no private-helper mirror tests.
 * Synthetic two-node A->B (80/20, 90/10, 30/70) literals; mocks carry
 * 64-char hashes, fixed UUIDs, fp-mock fingerprints.
 *
 * Selector contract (ProposalReviewPanel + CptReviewPanel headers):
 * - panel data-testid="proposal-review"
 * - per-question data-testid="proposal-status-<key>"
 * - per-question freshness data-testid="proposal-freshness-<key>"
 *   (stale batches only: Out of date ◍ vs Current inputs ●)
 * - DDI data-testid="proposal-ddi"
 * - sign blocked data-testid="proposal-sign-blocked"
 * - CPT panel data-testid="cpt-panel-<key>" with cpt-toggle/cpt-accept/
 *   cpt-reset/cpt-retry-<key>
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
): Promise<{ patientId: string; draftId: string; revision: number }> {
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
    revision: 1,
  };
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

async function patchDraftWithRetry(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  draftData: Record<string, unknown>,
  revision: number,
): Promise<number> {
  const attempt = async (csrfToken: string) =>
    request.patch(`/api/v1/encounters/${draftId}`, {
      headers: { "X-CSRF-Token": csrfToken, "If-Match": `"${revision}"` },
      data: { draft_data: draftData },
    });
  let response = await attempt(await physicianSession(request, physician));
  if (response.status() === 401) {
    response = await attempt(await physicianSession(request, physician));
  }
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()).revision as number) ?? revision + 1;
}

async function postNoteWithRetry(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
  pageKey: string,
  text: string,
  revision: number,
): Promise<number> {
  const attempt = async (csrfToken: string) =>
    request.post(`/api/v1/encounters/${draftId}/notes`, {
      headers: { "X-CSRF-Token": csrfToken, "If-Match": `"${revision}"` },
      data: { page: pageKey, text },
    });
  let response = await attempt(await physicianSession(request, physician));
  if (response.status() === 401) {
    response = await attempt(await physicianSession(request, physician));
  }
  expect(response.status(), await response.text()).toBe(201);
  return ((await response.json()).revision as number) ?? revision + 1;
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

function s48Package(
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
      version: "s48-test-v1",
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
      open_assumptions: "synthetic",
    },
    network_xml: TWO_NODE_XML,
  };
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

// --- Shared two-node mocks (80/20, 90/10, 30/70; 64-char hashes; fp-mock) ---

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

function mockSavedInputs(sourceRevision: number, prefix: string) {
  return [
    {
      node_id: "A",
      patient_type: "tristate",
      status: "observed",
      value: "yes",
      source_path: `synthetic/history/${prefix}_a`,
      source_revision: sourceRevision,
    },
    {
      node_id: "B",
      patient_type: "tristate",
      status: "observed",
      value: "no",
      source_path: `synthetic/history/${prefix}_b`,
      source_revision: sourceRevision,
    },
  ];
}

function mockBaseline(
  baselineId: string,
  runId: string,
  batchId: string,
  questionKey: string,
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
    raw_response: { network_hash: "a".repeat(64), tables: mockOriginalTables() },
    validated_tables: mockOriginalTables(),
    query_nodes: ["A", "B"],
    posteriors,
    section_text: `${questionKey} absent: B no 78.00% outcome.`,
    template_version: "v1",
    prompt_version: "v1",
    network_version: "s48-test-v1",
    provider_model: "mock-model",
    projection_hash: "c".repeat(64),
    provenance: { question_key: questionKey, provider_model: "mock-model" },
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockTransparency(questionKey: string, sourceRevision: number, prefix: string) {
  return {
    question_key: questionKey,
    network_version: "s48-test-v1",
    saved_patient_inputs: mockSavedInputs(sourceRevision, prefix),
    returned_cpt_percentages: mockOriginalTables(),
    deterministic_result: {
      posteriors: [
        { node_id: "A", states: ["no", "yes"], probabilities: [0.8, 0.2] },
        { node_id: "B", states: ["no", "yes"], probabilities: [0.78, 0.22] },
      ],
      section_text: `${questionKey} absent: B no 78.00% outcome.`,
      query_nodes: ["A", "B"],
      effective_hash: "b".repeat(64),
    },
  };
}

function mockRun(
  runId: string,
  batchId: string,
  questionKey: string,
  opts: { status?: string; gateReason?: string } = {},
) {
  return {
    id: runId,
    batch_id: batchId,
    question_key: questionKey,
    status: opts.status ?? "ready",
    gate_reason: opts.gateReason ?? "gate true",
    projection: { variables: [] },
    projection_hash: "c".repeat(64),
    fingerprint: "fp-mock-1",
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockDdiReport() {
  return {
    dataset_version: "ddi-mock/1",
    catalog_version: "cat-mock/1",
    medication_fingerprint: "fp-mock",
    resolved_medications: [],
    coverage_unavailable_medications: [],
    pairs: [],
    limitations: [],
    generated_at: "2026-10-10T00:00:00Z",
  };
}

function mockBatch(
  batchId: string,
  draftId: string,
  runs: ReturnType<typeof mockRun>[],
  baselines: (ReturnType<typeof mockBaseline> | null)[],
  transparencies: (ReturnType<typeof mockTransparency> | null)[],
  opts: { stale?: boolean } = {},
) {
  const stale = opts.stale ?? false;
  const baselineEntries = runs.map((run, index) => ({
    question_run_id: run.id,
    question_key: run.question_key,
    position: index,
    status: run.status,
    baseline: baselines[index] ?? null,
    transparency: transparencies[index] ?? null,
  }));
  const jobs = runs
    .filter((run) => run.status === "ready")
    .map((run, index) => ({
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
    }));
  const hasBaseline = baselines.some((b) => b !== null);
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
    freshness: stale
      ? {
          stale: true,
          reason: "analysis facts changed since freeze",
          current_fingerprint: "fp-new",
        }
      : { stale: false, reason: "current", current_fingerprint: "fp-mock-1" },
    job: null,
    jobs,
    attempts: jobs.length,
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
    baselines: baselineEntries,
    proposal: hasBaseline
      ? {
          id: "prop-1",
          batch_id: batchId,
          fingerprint: "fp-mock-1",
          sections: baselines.flatMap((base, index) =>
            base !== null
              ? [
                  {
                    question_run_id: runs[index].id,
                    question_key: runs[index].question_key,
                    position: index,
                    baseline_id: base.id,
                    section_text: base.section_text,
                    posteriors: base.posteriors,
                    query_nodes: ["A", "B"],
                    effective_hash: base.effective_hash,
                    template_version: "v1",
                    network_version: "s48-test-v1",
                  },
                ]
              : [],
          ),
          skipped: runs.flatMap((run, index) =>
            run.status === "not_applicable"
              ? [{ question_key: run.question_key, position: index, reason: run.gate_reason }]
              : [],
          ),
          coverage_warnings: [],
          ddi_report: mockDdiReport(),
          created_at: "2026-10-10T00:00:00Z",
        }
      : null,
    workflow: {
      complete: hasBaseline,
      status: hasBaseline ? "complete" : "incomplete",
      pending_question_keys: hasBaseline ? [] : runs.map((r) => r.question_key),
      needs_clarification: [],
      skipped: runs.flatMap((run, index) =>
        run.status === "not_applicable"
          ? [{ question_key: run.question_key, position: index, reason: run.gate_reason }]
          : [],
      ),
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
  stale?: boolean;
  isAccepted?: boolean;
  acceptance?: Record<string, unknown> | null;
  acceptances?: Record<string, unknown>[];
}) {
  const stale = opts.stale ?? false;
  return {
    question_run: opts.run,
    batch: opts.batch,
    baseline: opts.baseline,
    adjustable: opts.baseline !== null,
    transparency: opts.transparency,
    freshness: stale
      ? {
          stale: true,
          reason: "analysis facts changed since freeze",
          current_fingerprint: "fp-new",
        }
      : { stale: false, reason: "current", current_fingerprint: "fp-mock-1" },
    input_freshness: stale
      ? {
          stale: true,
          reason: "analysis facts changed since freeze",
          current_fingerprint: "fp-new",
        }
      : { stale: false, reason: "current", current_fingerprint: "fp-mock-1" },
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
    original_tables: opts.baseline !== null ? mockOriginalTables() : null,
    current_tables: opts.baseline !== null ? mockOriginalTables() : null,
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
    acceptance: opts.acceptance ?? null,
    is_accepted: opts.isAccepted ?? false,
    acceptances: opts.acceptances ?? [],
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

// S48d §1 (T9): stale batch marks only the affected question out of date.
test("S48d §1 stale batch shows affected-only Out of date vs Current inputs", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eqfreshone");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s48_q1", "h_s48_a", "h_s48_b", networkHash),
    s48Package("s48_q2", "h_s48_c", "h_s48_d", networkHash),
  ];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s48_a: "yes", h_s48_b: "no", h_s48_c: "yes", h_s48_d: "no" } },
      gate: "true",
    },
    1,
  );

  const batchId = "qfr-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("qfr-run-2222-4222-8222-222222222222", batchId, "s48_q1");
  const run2 = mockRun("qfr-run-3333-4333-8333-333333333333", batchId, "s48_q2");
  const base1 = mockBaseline("qfr-base-1111-4111-8111-111111111111", run1.id, batchId, "s48_q1");
  const base2 = mockBaseline("qfr-base-2222-4222-8222-222222222222", run2.id, batchId, "s48_q2");
  const trans1 = mockTransparency("s48_q1", 2, "h_s48");
  const trans2 = mockTransparency("s48_q2", 2, "h_s48");
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2], {
    stale: true,
  });
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    stale: true,
  });
  const acc2 = mockAcceptance(
    "qfr-acc-2222-4222-8222-222222222222",
    draftId,
    run2.id,
    batchId,
    "s48_q2",
    base2.id,
  );
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    stale: false,
    isAccepted: true,
    acceptance: acc2,
    acceptances: [acc2],
  });

  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: batchPayload.batch, question_runs: batchPayload.question_runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const body = JSON.stringify(url.includes(run1.id) ? review1 : review2);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Questions (2)", { timeout: 15000 });

    // Stale batch banner names affected-only carry-forward.
    await expect(panel).toContainText("This run is stale", { timeout: 10000 });
    await expect(panel).toContainText("unaffected questions stay valid and carry forward");

    // Affected q1: Out of date ◍ with regenerate affordance.
    const fresh1 = page.getByTestId("proposal-freshness-s48_q1");
    await expect(fresh1).toContainText("Out of date", { timeout: 15000 });
    await expect(fresh1).toContainText("◍");
    await expect(fresh1).toContainText("Only this question needs regeneration");

    // Unaffected q2: Current inputs ● carried with acceptance standing.
    const fresh2 = page.getByTestId("proposal-freshness-s48_q2");
    await expect(fresh2).toContainText("Current inputs", { timeout: 10000 });
    await expect(fresh2).toContainText("●");
    await expect(fresh2).toContainText("carried into the next run without recalculation");
    await expect(fresh2).toContainText("acceptance stands");

    // Sign stays blocked on the stale batch.
    await expect(page.getByTestId("proposal-sign-blocked")).toContainText("Signing is blocked");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48d §1 (T9): refresh/resume preserves affected-only labels without new POST.
test("S48d §1 refresh and resume preserve affected-only labels", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2eqfreshtwo");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s48_q1", "h_s48_a", "h_s48_b", networkHash),
    s48Package("s48_q2", "h_s48_c", "h_s48_d", networkHash),
  ];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s48_a: "yes", h_s48_b: "no", h_s48_c: "yes", h_s48_d: "no" } },
      gate: "true",
    },
    1,
  );

  const batchId = "qfr-rs-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("qfr-rs-run-2222-4222-8222-222222222222", batchId, "s48_q1");
  const run2 = mockRun("qfr-rs-run-3333-4333-8333-333333333333", batchId, "s48_q2");
  const base1 = mockBaseline("qfr-rs-base-1111-4111-8111-111111111111", run1.id, batchId, "s48_q1");
  const base2 = mockBaseline("qfr-rs-base-2222-4222-8222-222222222222", run2.id, batchId, "s48_q2");
  const trans1 = mockTransparency("s48_q1", 2, "h_s48");
  const trans2 = mockTransparency("s48_q2", 2, "h_s48");
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2], {
    stale: true,
  });
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    stale: true,
  });
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    stale: false,
  });

  let postCount = 0;
  await page.route("**/api/v1/encounters/*/generation-batches", (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const body = JSON.stringify(url.includes(run1.id) ? review1 : review2);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-freshness-s48_q1")).toContainText("Out of date", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-freshness-s48_q2")).toContainText("Current inputs", {
      timeout: 10000,
    });
    expect(postCount).toBe(0);

    // Resume after reload restores the same truthful labels without a POST.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("proposal-freshness-s48_q1")).toContainText("Out of date", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-freshness-s48_q2")).toContainText("Current inputs", {
      timeout: 10000,
    });
    expect(postCount).toBe(0);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48d §3 (T9): applicability transition refreshes the set; DDI carried without recalculation.
test("S48d §3 applicability keeps gate text, DDI carried shows Completed without spinners", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eqfreshthree");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s48_q1", "h_s48_a", "h_s48_b", networkHash),
    s48Package("s48_q2", "h_s48_c", "h_s48_d", networkHash),
  ];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s48_a: "yes", h_s48_b: "no", h_s48_c: "yes", h_s48_d: "no" } },
      gate: "true",
    },
    1,
  );

  const batchId = "qfr-ap-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("qfr-ap-run-2222-4222-8222-222222222222", batchId, "s48_q1");
  const run2 = mockRun("qfr-ap-run-3333-4333-8333-333333333333", batchId, "s48_q2", {
    status: "not_applicable",
    gateReason: "gate false; expression is not satisfied",
  });
  const base1 = mockBaseline("qfr-ap-base-1111-4111-8111-111111111111", run1.id, batchId, "s48_q1");
  const trans1 = mockTransparency("s48_q1", 2, "h_s48");
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, null], [trans1, null]);

  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: batchPayload.batch, question_runs: batchPayload.question_runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Questions (2)", { timeout: 15000 });

    // Carried applicable question renders Completed immediately, no recalculation.
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", {
      timeout: 10000,
    });
    await expect(panel).not.toContainText("Recalculating");
    await expect(panel).not.toContainText("Checking which questions are affected");

    // Applicability transition: skipped keeps gate text only, no freshness label.
    const skipped = page.getByTestId("proposal-status-s48_q2");
    await expect(skipped).toContainText("Skipped");
    await expect(skipped).toContainText("■");
    await expect(skipped).toContainText("gate false");
    await expect(page.getByTestId("proposal-freshness-s48_q2")).toHaveCount(0);
    // Fresh batch issues zero per-question freshness requests.
    await expect(page.getByTestId("proposal-freshness-s48_q1")).toHaveCount(0);

    // DDI-only carried proposal stays visible with its pinned report.
    await expect(panel).toContainText("s48_q1 absent: B no 78.00% outcome.");
    await expect(panel).toContainText("Skipped questions");
    const ddi = page.getByTestId("proposal-ddi");
    await expect(ddi).toBeVisible();
    await expect(ddi).toContainText("DDI coverage (pinned report)");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
  }
});

// S48d §4 (T9): note edit causes no spinners and preserves acceptance.
test("S48d §4 note edit shows no spinners and preserves acceptance", async ({ page, request }) => {
  const physician = await loginPhysician(page, request, "e2eqfreshfour");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s48_q1", "h_s48_a", "h_s48_b", networkHash),
    s48Package("s48_q2", "h_s48_c", "h_s48_d", networkHash),
  ];
  const rev2 = await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s48_a: "yes", h_s48_b: "no", h_s48_c: "yes", h_s48_d: "no" } },
      gate: "true",
    },
    1,
  );
  const sentinel = `freshnote${Date.now().toString(36)}`;
  await postNoteWithRetry(request, physician, draftId, "proposal", sentinel, rev2);

  const batchId = "qfr-nt-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("qfr-nt-run-2222-4222-8222-222222222222", batchId, "s48_q1");
  const run2 = mockRun("qfr-nt-run-3333-4333-8333-333333333333", batchId, "s48_q2");
  const base1 = mockBaseline("qfr-nt-base-1111-4111-8111-111111111111", run1.id, batchId, "s48_q1");
  const base2 = mockBaseline("qfr-nt-base-2222-4222-8222-222222222222", run2.id, batchId, "s48_q2");
  const trans1 = mockTransparency("s48_q1", 2, "h_s48");
  const trans2 = mockTransparency("s48_q2", 2, "h_s48");
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  const acc1 = mockAcceptance(
    "qfr-nt-acc-1111-4111-8111-111111111111",
    draftId,
    run1.id,
    batchId,
    "s48_q1",
    base1.id,
  );
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    stale: false,
    isAccepted: true,
    acceptance: acc1,
    acceptances: [acc1],
  });
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    stale: false,
  });

  let postCount = 0;
  await page.route("**/api/v1/encounters/*/generation-batches", (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) => {
    const body = JSON.stringify(batchPayload);
    expect(body).not.toContain(sentinel);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const payload = url.includes(run1.id) ? review1 : review2;
    const body = JSON.stringify(payload);
    expect(body).not.toContain(sentinel);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Questions (2)", { timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", {
      timeout: 10000,
    });

    // Terminal fresh batch: no spinners, no freshness labels.
    await expect(panel).not.toContainText("Recalculating");
    await expect(panel).not.toContainText("Checking which questions are affected");
    await expect(page.getByTestId("proposal-freshness-s48_q1")).toHaveCount(0);

    // Acceptance preserved through the note edit.
    await page.getByTestId("cpt-toggle-s48_q1").click();
    const cpt = page.getByTestId("cpt-panel-s48_q1");
    await expect(cpt).toContainText("Current inputs", { timeout: 15000 });
    await expect(cpt).toContainText("●");
    await expect(cpt).toContainText("Accepted");
    await expect(cpt).toContainText("✓");
    await expect(cpt).toContainText(acc1.id);

    // Notes never leak into proposal/CPT DOM; typing a working note starts no run.
    await expect(page.locator("#notes-proposal-list")).toContainText(sentinel, { timeout: 10000 });
    await expect(panel).not.toContainText(sentinel);
    await expect(cpt).not.toContainText(sentinel);
    await page.getByLabel("Draft working note").fill("freshness probe, no new run");
    await expect(page.locator("#draft-save-status")).toContainText("Saved (revision", {
      timeout: 10000,
    });
    expect(postCount).toBe(0);
    await expect(cpt).toContainText("Accepted");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48d §4 (T9): stale reset/retry stay out of date and block acceptance.
test("S48d §4 stale CPT shows reset/retry stay out of date and acceptance blocked", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eqfreshfive");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [s48Package("s48_q1", "h_s48_a", "h_s48_b", networkHash)];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: { h_s48_a: "yes", h_s48_b: "no" } }, gate: "true" },
    1,
  );

  const batchId = "qfr-st-batch-1111-4111-8111-111111111111";
  const runId = "qfr-st-run-2222-4222-8222-222222222222";
  const baselineId = "qfr-st-base-4444-4444-8444-444444444444";
  const run = mockRun(runId, batchId, "s48_q1");
  const baseline = mockBaseline(baselineId, runId, batchId, "s48_q1");
  const transparency = mockTransparency("s48_q1", 2, "h_s48");
  const batchPayload = mockBatch(batchId, draftId, [run], [baseline], [transparency], {
    stale: true,
  });
  const review = mockReview({
    run,
    batch: batchPayload.batch,
    baseline,
    transparency,
    stale: true,
  });

  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: batchPayload.batch, question_runs: batchPayload.question_runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(review) }),
  );
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-freshness-s48_q1")).toContainText("Out of date", {
      timeout: 15000,
    });

    await page.getByTestId("cpt-toggle-s48_q1").click();
    const cpt = page.getByTestId("cpt-panel-s48_q1");
    await expect(cpt).toContainText("Out of date", { timeout: 15000 });
    await expect(cpt).toContainText("◍");
    await expect(cpt).toContainText("Regeneration with current inputs is required");
    await expect(cpt).toContainText("reset cannot make stale inputs current");
    await expect(cpt).toContainText("reset and retry apply to superseded inputs and stay");
    await expect(cpt).toContainText("out of date");
    await expect(cpt).toContainText("regenerated original needs explicit review before it can be accepted");
    await expect(cpt).toContainText("Acceptance blocked");
    await expect(cpt).toContainText("Out of date");
    await expect(cpt.getByTestId("cpt-accept-s48_q1")).toBeDisabled();
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48d §§2/4 (T9): regenerated original starts unaccepted and needs exact review.
test("S48d §§2/4 regenerated original needs exact review before accept", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eqfreshsix");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [s48Package("s48_q1", "h_s48_a", "h_s48_b", networkHash)];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: { h_s48_a: "yes", h_s48_b: "no" } }, gate: "true" },
    1,
  );

  const batchId = "qfr-rg-batch-1111-4111-8111-111111111111";
  const runId = "qfr-rg-run-2222-4222-8222-222222222222";
  const baselineId = "qfr-rg-base-4444-4444-8444-444444444444";
  const accId = "qfr-rg-acc-1111-4111-8111-111111111111";
  const run = mockRun(runId, batchId, "s48_q1");
  const baseline = mockBaseline(baselineId, runId, batchId, "s48_q1");
  const transparency = mockTransparency("s48_q1", 2, "h_s48");
  const batchPayload = mockBatch(batchId, draftId, [run], [baseline], [transparency]);
  let accepted = false;
  const reviewBefore = () =>
    mockReview({
      run,
      batch: batchPayload.batch,
      baseline,
      transparency,
      stale: false,
      isAccepted: accepted,
      acceptance: accepted
        ? mockAcceptance(accId, draftId, runId, batchId, "s48_q1", baselineId)
        : null,
      acceptances: accepted
        ? [mockAcceptance(accId, draftId, runId, batchId, "s48_q1", baselineId)]
        : [],
    });

  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: batchPayload.batch, question_runs: batchPayload.question_runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(reviewBefore()),
    }),
  );
  await page.route("**/api/v1/question-runs/*/acceptance", async (route) => {
    let body: Record<string, unknown> = {};
    try {
      body = route.request().postDataJSON() as Record<string, unknown>;
    } catch {
      body = {};
    }
    const exact =
      body["baseline_id"] === baselineId &&
      body["current_cpt_revision_id"] === null &&
      body["result_id"] === baselineId &&
      body["input_hash"] === "fp-mock-1" &&
      body["expected_review_revision"] === 1;
    if (!exact) {
      return route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "REFERENCE_MISMATCH",
          message: "Acceptance references do not match the current result.",
          field_errors: {},
          request_id: "req-forge-1",
          retryable: false,
        }),
      });
    }
    accepted = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        acceptance: mockAcceptance(accId, draftId, runId, batchId, "s48_q1", baselineId),
        is_accepted: true,
        current_cpt_revision_id: null,
        review_revision: 1,
        cpt_hash: "d".repeat(64),
      }),
    });
  });
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", {
      timeout: 15000,
    });

    await page.getByTestId("cpt-toggle-s48_q1").click();
    const cpt = page.getByTestId("cpt-panel-s48_q1");
    await expect(cpt).toContainText("Current inputs", { timeout: 15000 });
    await expect(cpt).toContainText("Not accepted");

    // Old input hash cannot accept the regenerated run.
    const forgedStatus = await page.evaluate(
      async ({ id }: { id: string }) => {
        const res = await fetch(`/api/v1/question-runs/${id}/acceptance`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            baseline_id: "qfr-rg-base-4444-4444-8444-444444444444",
            current_cpt_revision_id: null,
            cpt_hash: "d".repeat(64),
            result_id: "qfr-rg-base-4444-4444-8444-444444444444",
            input_hash: "fp-stale-old",
            expected_review_revision: 1,
          }),
        });
        return res.status;
      },
      { id: runId },
    );
    expect(forgedStatus).toBe(409);
    await expect(cpt).toContainText("Not accepted");

    // Exact review of the regenerated original accepts.
    await cpt.getByTestId("cpt-accept-s48_q1").click();
    await expect(cpt).toContainText("Accepted", { timeout: 15000 });
    await expect(cpt).toContainText(accId);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/question-runs/*/acceptance");
  }
});
