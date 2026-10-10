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

/* S48c complete CPT review, comparison, and acceptance UI (seams T1/T9).
 *
 * Backend (read-only): POST /question-runs/{id}/acceptance + GET review
 * acceptance/is_accepted/acceptances + revision/result/freshness fields
 * (S48c-backend, 13 T1 tests). Frontend (read-only):
 * web/src/features/reasoning/api.ts (buildAcceptanceBody, acceptBlockedReason,
 * postCptAdjustment/postCptReset/postRetryCalculation/postAcceptance) +
 * CptReviewPanel.tsx (testids cpt-panel/toggle/accept/reset/retry-<key>).
 * No backend/frontend implementation changes here (TESTS ONLY).
 *
 * Constraints: real auth/draft via T1, no worker harness — completed/
 * failed/stale journeys use controlled batch + review route mocks as the
 * documented S48 alternative (deterministic, no sleeps for concurrency).
 * Real pending batch (T1) proves mount-restore + honest empty without mocks
 * for the review payload. Sentinel note-leakage asserts in payloads + DOM.
 *
 * Selector contract (CptReviewPanel header):
 * - panel root data-testid="cpt-panel-<key>" (#cpt-panel-<key>)
 * - toggle data-testid="cpt-toggle-<key>" (lazy GET review on expand)
 * - accept data-testid="cpt-accept-<key>" (blocked disabled vs enabled)
 * - reset data-testid="cpt-reset-<key>" (one panel only)
 * - retry data-testid="cpt-retry-<key>" (failed only)
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
      admission_measurements: "synthetic",
      open_assumptions: "synthetic",
    },
    network_xml: TWO_NODE_XML,
  };
}

async function seedHistoryAndStartTwoQuestionBatch(
  request: import("@playwright/test").APIRequestContext,
  physician: { username: string; password: string },
  draftId: string,
): Promise<{ batchId: string; packages: Record<string, unknown>[]; revision: number }> {
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s48_q1", "h_s48_a", "h_s48_b", networkHash),
    s48Package("s48_q2", "h_s48_c", "h_s48_d", networkHash),
  ];
  let csrf = await physicianSession(request, physician);
  const patched = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: {
      draft_data: {
        history: {
          values: { h_s48_a: "yes", h_s48_b: "no", h_s48_c: "yes", h_s48_d: "no" },
        },
        gate: "true",
      },
    },
  });
  expect(patched.status(), await patched.text()).toBe(200);
  const revision = (await patched.json()).revision as number;
  csrf = await physicianSession(request, physician);
  const started = await request.post(`/api/v1/encounters/${draftId}/generation-batches`, {
    headers: {
      "X-CSRF-Token": csrf,
      "If-Match": `"${revision}"`,
      "Idempotency-Key": `e2e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`,
    },
    data: { packages },
  });
  expect(started.status(), await started.text()).toBe(202);
  const body = await started.json();
  return { batchId: body.batch.id as string, packages, revision };
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

// --- Shared CPT mocks (S48c §§1-2: exact strings, 6-decimal precision) ---

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
    {
      node_id: "S",
      parent_ids: [],
      states: ["only"],
      rows: [{ parent_states: [], percentages: ["100"] }],
    },
  ];
}

function mockAdjustedTables() {
  return [
    {
      node_id: "A",
      parent_ids: [],
      states: ["no", "yes"],
      rows: [{ parent_states: [], percentages: ["60", "40"] }],
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
    {
      node_id: "S",
      parent_ids: [],
      states: ["only"],
      rows: [{ parent_states: [], percentages: ["100"] }],
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
      source_path: "synthetic/history/h_s48_a",
      source_revision: sourceRevision,
    },
    {
      node_id: "B",
      patient_type: "tristate",
      status: "observed",
      value: "no",
      source_path: "synthetic/history/h_s48_b",
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

function mockTransparency(questionKey: string, sourceRevision: number) {
  return {
    question_key: questionKey,
    network_version: "s48-test-v1",
    saved_patient_inputs: mockSavedInputs(sourceRevision),
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

function mockRun(runId: string, batchId: string, questionKey: string) {
  return {
    id: runId,
    batch_id: batchId,
    question_key: questionKey,
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
  opts: { stale?: boolean; complete?: boolean } = {},
) {
  const stale = opts.stale ?? false;
  const complete = opts.complete ?? true;
  const baselineEntries = runs.map((run, index) => ({
    question_run_id: run.id,
    question_key: run.question_key,
    position: index,
    status: "ready",
    baseline: baselines[index] ?? null,
    transparency: transparencies[index] ?? null,
  }));
  const jobs = runs.map((run, index) => ({
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
    baselines: baselineEntries,
    proposal: complete
      ? {
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
            network_version: "s48-test-v1",
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
        }
      : null,
    workflow: complete
      ? {
          complete: true,
          status: "complete",
          pending_question_keys: [],
          needs_clarification: [],
          skipped: [],
          coverage_warnings: [],
          ddi_status: "valid",
        }
      : {
          complete: false,
          status: "incomplete",
          pending_question_keys: runs.map((r) => r.question_key),
          needs_clarification: [],
          skipped: [],
          coverage_warnings: [],
          ddi_status: "valid",
        },
  };
}

function mockRevision(
  revId: string,
  runId: string,
  batchId: string,
  sequence = 1,
  kind = "adjustment",
) {
  const artifact =
    kind === "reset" ? mockOriginalTables() : mockAdjustedTables();
  return {
    id: revId,
    question_run_id: runId,
    batch_id: batchId,
    parent_revision_id: sequence > 1 ? "parent-mock" : null,
    sequence,
    kind,
    cpt_hash: "d".repeat(64),
    cpt_artifact: artifact,
    direct_edit:
      kind === "reset"
        ? { reset: true, baseline_id: "baseline-mock" }
        : {
            node_id: "A",
            parent_states: [],
            state: "yes",
            target_percentage: "40",
            target_units: 40000000,
          },
    before_row: {
      node_id: "A",
      parent_states: [],
      percentages: ["80", "20"],
      units: [80000000, 20000000],
    },
    after_row: {
      node_id: "A",
      parent_states: [],
      percentages: kind === "reset" ? ["80", "20"] : ["60", "40"],
      units: kind === "reset" ? [80000000, 20000000] : [60000000, 40000000],
    },
    actor_username: "physician-mock",
    redistribution_version: "redistribution-v1",
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockCalcResult(
  calcId: string,
  runId: string,
  batchId: string,
  revId: string,
  questionKey: string,
  opts: { reusedFrom?: string | null; wordingUnchanged?: boolean } = {},
) {
  const unchanged = opts.wordingUnchanged ?? true;
  return {
    id: calcId,
    question_run_id: runId,
    batch_id: batchId,
    cpt_revision_id: revId,
    cpt_hash: "d".repeat(64),
    network_hash: "a".repeat(64),
    network_version: "s48-test-v1",
    template_version: "v1",
    query_nodes: ["A", "B"],
    posteriors:
      opts.reusedFrom !== undefined && opts.reusedFrom !== null
        ? [
            { node_id: "A", states: ["no", "yes"], probabilities: [0.8, 0.2] },
            { node_id: "B", states: ["no", "yes"], probabilities: [0.78, 0.22] },
          ]
        : [
            { node_id: "A", states: ["no", "yes"], probabilities: [0.6, 0.4] },
            { node_id: "B", states: ["no", "yes"], probabilities: [0.66, 0.34] },
          ],
    section_text: unchanged
      ? `${questionKey} absent: B no 78.00% outcome.`
      : `${questionKey} present: B yes 34.00% outcome.`,
    effective_hash: "e".repeat(64),
    effective_xml: "<BIF>mock adjusted effective</BIF>",
    reused_from_baseline_id: opts.reusedFrom ?? null,
    provenance: { question_key: questionKey },
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockReview(opts: {
  run: ReturnType<typeof mockRun>;
  batch: Record<string, unknown>;
  baseline: ReturnType<typeof mockBaseline> | null;
  transparency: ReturnType<typeof mockTransparency> | null;
  calculationState:
    | "unchanged"
    | "recalculating"
    | "successfully_recalculated"
    | "failed";
  stale?: boolean;
  revisions?: ReturnType<typeof mockRevision>[];
  calcResult?: ReturnType<typeof mockCalcResult> | null;
  displayedResult?: ReturnType<typeof mockCalcResult> | null;
  reviewRevision?: number;
  currentRevId?: string | null;
  displayedRevId?: string | null;
  currentMatches?: boolean;
  cptHash?: string | null;
  isAccepted?: boolean;
  acceptance?: Record<string, unknown> | null;
  acceptances?: Record<string, unknown>[];
}) {
  const revisions = opts.revisions ?? [];
  const stale = opts.stale ?? false;
  const reviewRevision = opts.reviewRevision ?? (revisions.length > 0 ? 2 : 1);
  const currentTables =
    revisions.length > 0
      ? (revisions[revisions.length - 1].cpt_artifact as ReturnType<
          typeof mockOriginalTables
        >)
      : opts.baseline !== null
        ? mockOriginalTables()
        : null;
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
    current_tables: currentTables,
    current_cpt_revision_id: opts.currentRevId ?? (revisions.length > 0 ? revisions[revisions.length - 1].id : null),
    displayed_result_revision_id:
      opts.displayedRevId ??
      (opts.calcResult !== undefined && opts.calcResult !== null
        ? opts.calcResult.cpt_revision_id
        : null),
    current_result_matches: opts.currentMatches ?? (opts.calculationState === "unchanged" || opts.calculationState === "successfully_recalculated"),
    calculation_state: opts.calculationState,
    calculation_result: opts.calcResult ?? null,
    displayed_result: opts.displayedResult ?? opts.calcResult ?? null,
    calculation_results:
      opts.calcResult !== undefined && opts.calcResult !== null ? [opts.calcResult] : [],
    local_jobs: [],
    local_job: null,
    review_revision: reviewRevision,
    revisions,
    cpt_hash: opts.cptHash ?? (revisions.length > 0 ? "d".repeat(64) : null),
    outputs_read_only: true,
    acceptance: opts.acceptance ?? null,
    is_accepted: opts.isAccepted ?? false,
    acceptances: opts.acceptances ?? [],
  };
}

// S48c §1 (T1/T9): real pending run exposes honest empty CPT, stranger denied.
test("S48c §1 real pending run shows honest empty CPT, no sliders, stranger denied", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecptreal");
  const { draftId } = await createPatientWithDraft(request, physician);
  const { batchId, packages } = await seedHistoryAndStartTwoQuestionBatch(
    request,
    physician,
    draftId,
  );

  // Wrong-author denied via real T1 (no page session switch): a second
  // physician created via the API cannot read the owner's batch.
  const strangerName = uniqueName("e2ecptstrang");
  await ensurePhysician(request, strangerName, "secret123");
  const strangerCsrf = await physicianSession(request, {
    username: strangerName,
    password: "secret123",
  });
  const strangerBatch = await request.get(`/api/v1/generation-batches/${batchId}`, {
    headers: { "X-CSRF-Token": strangerCsrf },
  });
  expect(strangerBatch.status()).toBe(403);
  expect(await strangerBatch.text()).not.toContain("percentages");

  let adjustPosts = 0;
  let resetPosts = 0;
  let retryPosts = 0;
  let acceptPosts = 0;
  await page.route("**/api/v1/question-runs/*/cpt-adjustments", (route) => {
    adjustPosts += 1;
    return route.continue();
  });
  await page.route("**/api/v1/question-runs/*/reset", (route) => {
    resetPosts += 1;
    return route.continue();
  });
  await page.route("**/api/v1/question-runs/*/retry-calculation", (route) => {
    retryPosts += 1;
    return route.continue();
  });
  await page.route("**/api/v1/question-runs/*/acceptance", (route) => {
    acceptPosts += 1;
    return route.continue();
  });
  try {
    await seedStoredProposal(page, draftId, {
      batchId,
      packages,
      sourceRevision: 2,
    });
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Questions (2)", { timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s48_q1")).toBeVisible({
      timeout: 10000,
    });

    // Honest empty via real review: expand CPT for q1 — real baseline is
    // pending (no worker), so no sliders.
    const toggle = page.getByTestId("cpt-toggle-s48_q1");
    await expect(toggle).toBeVisible();
    await toggle.click();
    const cptPanel = page.getByTestId("cpt-panel-s48_q1");
    await expect(cptPanel).toContainText("No adjustable baseline", {
      timeout: 15000,
    });
    await expect(cptPanel.getByRole("slider")).toHaveCount(0);
    // No mutation POSTs from a pending honest-empty panel.
    expect(adjustPosts).toBe(0);
    expect(resetPosts).toBe(0);
    expect(retryPosts).toBe(0);
    expect(acceptPosts).toBe(0);
  } finally {
    await page.unroute("**/api/v1/question-runs/*/cpt-adjustments");
    await page.unroute("**/api/v1/question-runs/*/reset");
    await page.unroute("**/api/v1/question-runs/*/retry-calculation");
    await page.unroute("**/api/v1/question-runs/*/acceptance");
  }
});

// S48c §1 (T9): completed CPT rows reachable with exact values/totals/diffs.
test("S48c §1 completed CPT exposes roots, totals, diffs, badges, single-state, search", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecptreach");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [s48Package("s48_q1", "h_s48_a", "h_s48_b", networkHash)];
  const rev2 = await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s48_a: "yes", h_s48_b: "no" } },
      gate: "true",
    },
    1,
  );
  const sentinel = `cptsentinel${Date.now().toString(36)}`;
  await postNoteWithRetry(request, physician, draftId, "proposal", sentinel, rev2);

  const batchId = "cpt-batch-1111-4111-8111-111111111111";
  const runId = "cpt-run-2222-4222-8222-222222222222";
  const baselineId = "cpt-base-4444-4444-8444-444444444444";
  const revId = "cpt-rev-5555-4555-8555-555555555555";
  const calcId = "cpt-calc-6666-4666-8666-666666666666";
  const run = mockRun(runId, batchId, "s48_q1");
  const baseline = mockBaseline(baselineId, runId, batchId, "s48_q1");
  const transparency = mockTransparency("s48_q1", 2);
  const batchPayload = mockBatch(batchId, draftId, [run], [baseline], [transparency]);
  const revision = mockRevision(revId, runId, batchId, 1, "adjustment");
  const calc = mockCalcResult(calcId, runId, batchId, revId, "s48_q1", {
    wordingUnchanged: true,
  });
  const reviewPayload = mockReview({
    run,
    batch: batchPayload.batch,
    baseline,
    transparency,
    calculationState: "successfully_recalculated",
    revisions: [revision],
    calcResult: calc,
    displayedResult: calc,
    reviewRevision: 2,
    currentRevId: revId,
    displayedRevId: revId,
    currentMatches: true,
    cptHash: "d".repeat(64),
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
  await page.route("**/api/v1/generation-batches/*", (route) => {
    const body = JSON.stringify(batchPayload);
    expect(body).not.toContain(sentinel);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const body = JSON.stringify(reviewPayload);
    expect(body).not.toContain(sentinel);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Questions (1)", { timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed");

    const toggle = page.getByTestId("cpt-toggle-s48_q1");
    await toggle.click();
    const cpt = page.getByTestId("cpt-panel-s48_q1");
    await expect(cpt).toContainText("CPT values (exact percentages)", { timeout: 15000 });

    // Root + conditional exact values, totals, diffs, badges.
    await expect(cpt).toContainText("80");
    await expect(cpt).toContainText("20");
    await expect(cpt).toContainText("60");
    await expect(cpt).toContainText("40");
    await expect(cpt).toContainText("Row total");
    await expect(cpt).toContainText("100%");
    await expect(cpt).toContainText("direct");
    await expect(cpt).toContainText("redistributed");
    await expect(cpt).toContainText("LLM-estimated");
    await expect(cpt).toContainText("physician adjustments");
    // Root grouping + conditional parent states.
    await expect(cpt).toContainText("root distribution");
    await expect(cpt).toContainText("parents A");
    // Outputs read-only (comparison section, no sliders there).
    await expect(cpt).toContainText("Outputs and recommendations (read-only)");
    // Single-state constraint explained.
    await expect(cpt).toContainText("Single-state row");
    await expect(cpt).toContainText("remains at 100%");
    // Search keeps every row reachable with X-of-Y + clear.
    await expect(cpt).toContainText("Showing 4 of 4 rows");
    const search = cpt.getByLabel("Search CPT rows");
    await search.fill("B");
    await expect(cpt).toContainText("Showing 2 of 4 rows");
    await expect(cpt.getByRole("button", { name: "Clear search" })).toBeVisible();
    await cpt.getByRole("button", { name: "Clear search" }).click();
    await expect(cpt).toContainText("Showing 4 of 4 rows");
    // Folding keeps rows reachable (collapse then expand preserves content).
    const firstNode = cpt.locator("details.xi-cpt-node").first();
    await firstNode.locator("summary").click();
    await firstNode.locator("summary").click();
    await expect(cpt).toContainText("Showing 4 of 4 rows");

    // Sentinel: notes never leak into CPT DOM.
    await expect(page.locator("#notes-proposal-list")).toContainText(sentinel, {
      timeout: 10000,
    });
    await expect(page.getByTestId("proposal-review")).not.toContainText(sentinel);
    await expect(cpt).not.toContainText(sentinel);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48c §1 (T9): keyboard sliders with readable labels work in both themes.
test("S48c §1 keyboard adjusts slider with labels in light and dark themes", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecptkbd");
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

  const batchId = "kbd-batch-1111-4111-8111-111111111111";
  const runId = "kbd-run-2222-4222-8222-222222222222";
  const baselineId = "kbd-base-4444-4444-8444-444444444444";
  const revId = "kbd-rev-5555-4555-8555-555555555555";
  const run = mockRun(runId, batchId, "s48_q1");
  const baseline = mockBaseline(baselineId, runId, batchId, "s48_q1");
  const transparency = mockTransparency("s48_q1", 2);
  const batchPayload = mockBatch(batchId, draftId, [run], [baseline], [transparency]);
  const unchangedReview = mockReview({
    run,
    batch: batchPayload.batch,
    baseline,
    transparency,
    calculationState: "unchanged",
    revisions: [],
    calcResult: null,
    displayedResult: null,
    reviewRevision: 1,
    currentRevId: null,
    displayedRevId: null,
    currentMatches: true,
    cptHash: null,
  });
  const revision = mockRevision(revId, runId, batchId, 1, "adjustment");
  const recalcReview = mockReview({
    run,
    batch: batchPayload.batch,
    baseline,
    transparency,
    calculationState: "recalculating",
    revisions: [revision],
    calcResult: null,
    displayedResult: null,
    reviewRevision: 2,
    currentRevId: revId,
    displayedRevId: null,
    currentMatches: false,
    cptHash: "d".repeat(64),
  });

  let adjustBodies: Record<string, unknown>[] = [];
  let adjusted = false;
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
      body: JSON.stringify(adjusted ? recalcReview : unchangedReview),
    }),
  );
  await page.route("**/api/v1/question-runs/*/cpt-adjustments", async (route) => {
    try {
      adjustBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    } catch {
      adjustBodies.push({});
    }
    adjusted = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        revision,
        review_state: { review_revision: 2, current_revision_id: revId },
        current_cpt_revision_id: revId,
        review_revision: 2,
        cpt_hash: "d".repeat(64),
        current_tables: mockAdjustedTables(),
      }),
    });
  });
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", {
      timeout: 15000,
    });

    // Keyboard-only toggle: Tab to the toggle then Enter expands.
    const toggle = page.getByTestId("cpt-toggle-s48_q1");
    await toggle.focus();
    await expect(toggle).toBeFocused();
    await page.keyboard.press("Enter");
    const cpt = page.getByTestId("cpt-panel-s48_q1");
    await expect(cpt).toContainText("CPT values (exact percentages)", { timeout: 15000 });

    // Accessible labels carry node/parents/state + original percentage.
    const slider = cpt.getByLabel(/A.*yes.*original 20/i);
    await expect(slider).toBeVisible();
    await expect(slider).toBeEnabled();
    await slider.focus();
    await expect(slider).toBeFocused();
    // Focus-visible: global rule gives a non-zero outline.
    const outline = await slider.evaluate((el) => getComputedStyle(el).outlineWidth);
    expect(outline).not.toBe("0px");

    // Arrow keys adjust the draft readout, then debounced autosave POSTs.
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await expect(cpt).toContainText("Unsaved changes", { timeout: 5000 });
    await expect
      .poll(() => adjustBodies.length, { timeout: 10000 })
      .toBeGreaterThanOrEqual(1);
    const sent = adjustBodies[0];
    expect(sent["node_id"]).toBe("A");
    expect(sent["state"]).toBe("yes");
    expect(typeof sent["target_percentage"]).toBe("string");
    expect(sent["expected_review_revision"]).toBe(1);
    // Autosave acknowledged + recalculating keeps sliders responsive.
    await expect(cpt).toContainText("Saved", { timeout: 10000 });
    await expect(cpt).toContainText("Recalculating", { timeout: 10000 });
    await expect(slider).toBeEnabled();

    // Readable in dark theme too: toggle theme, panel + labels persist.
    const themeToggle = page.getByRole("button", { name: /switch to/i });
    await expect(themeToggle).toBeVisible();
    await themeToggle.click();
    await expect.poll(
      () => page.evaluate(() => document.documentElement.getAttribute("data-theme")),
      { timeout: 10000 },
    ).toBe("dark");
    await expect(cpt).toContainText("CPT values (exact percentages)");
    await expect(slider).toBeVisible();
    await expect(slider).toBeEnabled();
    // Restore light for stable state for other specs.
    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await expect.poll(
      () => page.evaluate(() => document.documentElement.getAttribute("data-theme")),
      { timeout: 10000 },
    ).toBe("light");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/question-runs/*/cpt-adjustments");
  }
});

// S48c §2 (T9): original vs latest-successful side-by-side, wording unchanged.
test("S48c §2 comparison shows original vs latest-successful even when wording unchanged", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecptcmp");
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

  const batchId = "cmp-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("cmp-run-2222-4222-8222-222222222222", batchId, "s48_q1");
  const run2 = mockRun("cmp-run-3333-4333-8333-333333333333", batchId, "s48_q2");
  const base1 = mockBaseline("cmp-base-1111-4111-8111-111111111111", run1.id, batchId, "s48_q1");
  const base2 = mockBaseline("cmp-base-2222-4222-8222-222222222222", run2.id, batchId, "s48_q2");
  const trans1 = mockTransparency("s48_q1", 2);
  const trans2 = mockTransparency("s48_q2", 2);
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  const revId = "cmp-rev-5555-4555-8555-555555555555";
  const calcId = "cmp-calc-6666-4666-8666-666666666666";
  const revision = mockRevision(revId, run2.id, batchId, 1, "adjustment");
  const calc = mockCalcResult(calcId, run2.id, batchId, revId, "s48_q2", {
    wordingUnchanged: true,
  });
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    calculationState: "unchanged",
    revisions: [],
    calcResult: null,
    displayedResult: null,
    reviewRevision: 1,
    currentRevId: null,
    displayedRevId: null,
    currentMatches: true,
    cptHash: null,
  });
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    calculationState: "successfully_recalculated",
    revisions: [revision],
    calcResult: calc,
    displayedResult: calc,
    reviewRevision: 2,
    currentRevId: revId,
    displayedRevId: revId,
    currentMatches: true,
    cptHash: "d".repeat(64),
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
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", {
      timeout: 15000,
    });

    // q1 unchanged: state + honest "original is current" comparison.
    await page.getByTestId("cpt-toggle-s48_q1").click();
    const cpt1 = page.getByTestId("cpt-panel-s48_q1");
    await expect(cpt1).toContainText("Unchanged", { timeout: 15000 });
    await expect(cpt1).toContainText("●");
    await expect(cpt1).toContainText("Original result");
    await expect(cpt1).toContainText("s48_q1 absent: B no 78.00% outcome.");
    await expect(cpt1).toContainText("no successful adjusted result yet");
    await expect(cpt1).toContainText("Current inputs");

    // q2 successfully recalculated: side-by-side even when wording unchanged.
    await page.getByTestId("cpt-toggle-s48_q2").click();
    const cpt2 = page.getByTestId("cpt-panel-s48_q2");
    await expect(cpt2).toContainText("Successfully recalculated", { timeout: 15000 });
    await expect(cpt2).toContainText("Original result");
    await expect(cpt2).toContainText("Latest successful result");
    await expect(cpt2).toContainText("s48_q2 absent: B no 78.00% outcome.");
    await expect(cpt2).toContainText("Recommendation wording is unchanged");
    await expect(cpt2).toContainText("both sides stay visible");
    // Changed probabilities still differ (60/40 vs 80/20) with provenance.
    await expect(cpt2).toContainText("60");
    await expect(cpt2.getByText("verified reuse")).toHaveCount(0);
    // Latest result carries revision + result ids (explicit references for S49).
    await expect(cpt2).toContainText(revId);
    await expect(cpt2).toContainText(calcId);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48c §§2-3 (T9): recalculating/failed/stale + earlier-revision + blocked accept.
test("S48c §§2-3 recalculating keeps sliders live, failed/stale block accept with earlier label", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecptstate");
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

  const batchId = "st-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("st-run-2222-4222-8222-222222222222", batchId, "s48_q1");
  const run2 = mockRun("st-run-3333-4333-8333-333333333333", batchId, "s48_q2");
  const base1 = mockBaseline("st-base-1111-4111-8111-111111111111", run1.id, batchId, "s48_q1");
  const base2 = mockBaseline("st-base-2222-4222-8222-222222222222", run2.id, batchId, "s48_q2");
  const trans1 = mockTransparency("s48_q1", 2);
  const trans2 = mockTransparency("s48_q2", 2);
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  // q1 recalculating: rev1 solved (displayed earlier), rev2 unsolved current.
  const rev1a = mockRevision("st-rev-1111-4111-8111-111111111111", run1.id, batchId, 1, "adjustment");
  const rev2a = mockRevision("st-rev-2222-4222-8222-222222222222", run1.id, batchId, 2, "adjustment");
  rev2a.parent_revision_id = rev1a.id;
  const calc1 = mockCalcResult(
    "st-calc-1111-4111-8111-111111111111",
    run1.id,
    batchId,
    rev1a.id,
    "s48_q1",
    { wordingUnchanged: true },
  );
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    calculationState: "recalculating",
    revisions: [rev1a, rev2a],
    calcResult: null,
    displayedResult: calc1,
    reviewRevision: 3,
    currentRevId: rev2a.id,
    displayedRevId: rev1a.id,
    currentMatches: false,
    cptHash: "d".repeat(64),
  });
  // q2 failed + stale: rev1 solved earlier, rev2 failed, inputs out of date.
  const rev1b = mockRevision("st-rev-3333-4333-8333-333333333333", run2.id, batchId, 1, "adjustment");
  const rev2b = mockRevision("st-rev-4444-4444-8444-444444444444", run2.id, batchId, 2, "adjustment");
  rev2b.parent_revision_id = rev1b.id;
  const calc2 = mockCalcResult(
    "st-calc-2222-4222-8222-222222222222",
    run2.id,
    batchId,
    rev1b.id,
    "s48_q2",
    { wordingUnchanged: false },
  );
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    calculationState: "failed",
    stale: true,
    revisions: [rev1b, rev2b],
    calcResult: null,
    displayedResult: calc2,
    reviewRevision: 3,
    currentRevId: rev2b.id,
    displayedRevId: rev1b.id,
    currentMatches: false,
    cptHash: "d".repeat(64),
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
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", {
      timeout: 15000,
    });

    // q1 recalculating: sliders stay enabled, accept blocked, earlier labeled.
    await page.getByTestId("cpt-toggle-s48_q1").click();
    const cpt1 = page.getByTestId("cpt-panel-s48_q1");
    await expect(cpt1).toContainText("Recalculating", { timeout: 15000 });
    await expect(cpt1).toContainText("◐");
    await expect(cpt1).toContainText("sliders stay enabled");
    await expect(cpt1).toContainText("earlier revision");
    await expect(cpt1).toContainText(rev1a.id);
    await expect(cpt1.getByLabel(/A.*yes/i).first()).toBeEnabled();
    await expect(cpt1).toContainText("Acceptance blocked");
    await expect(cpt1).toContainText("Recalculating");
    await expect(cpt1.getByTestId("cpt-accept-s48_q1")).toBeDisabled();

    // q2 failed + stale: separate out-of-date indicator, retry visible, accept blocked.
    await page.getByTestId("cpt-toggle-s48_q2").click();
    const cpt2 = page.getByTestId("cpt-panel-s48_q2");
    await expect(cpt2).toContainText("Failed", { timeout: 15000 });
    await expect(cpt2).toContainText("✕");
    await expect(cpt2).toContainText("Out of date");
    await expect(cpt2).toContainText("◍");
    await expect(cpt2).toContainText("earlier revision");
    await expect(cpt2).toContainText("Retry local calculation");
    await expect(cpt2.getByTestId("cpt-retry-s48_q2")).toBeVisible();
    await expect(cpt2).toContainText("Acceptance blocked");
    await expect(cpt2.getByTestId("cpt-accept-s48_q2")).toBeDisabled();
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48c §3 (T9): stale 412 shows reload without overwrite, then autosave + resume.
test("S48c §3 stale save shows reload, then autosave succeeds and resume restores", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecptsave");
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

  const batchId = "save-batch-1111-4111-8111-111111111111";
  const runId = "save-run-2222-4222-8222-222222222222";
  const baselineId = "save-base-4444-4444-8444-444444444444";
  const revId = "save-rev-5555-4555-8555-555555555555";
  const run = mockRun(runId, batchId, "s48_q1");
  const baseline = mockBaseline(baselineId, runId, batchId, "s48_q1");
  const transparency = mockTransparency("s48_q1", 2);
  const batchPayload = mockBatch(batchId, draftId, [run], [baseline], [transparency]);
  const unchangedReview = mockReview({
    run,
    batch: batchPayload.batch,
    baseline,
    transparency,
    calculationState: "unchanged",
    revisions: [],
    calcResult: null,
    displayedResult: null,
    reviewRevision: 1,
    currentRevId: null,
    displayedRevId: null,
    currentMatches: true,
    cptHash: null,
  });
  const revision = mockRevision(revId, runId, batchId, 1, "adjustment");
  const recalcReview = mockReview({
    run,
    batch: batchPayload.batch,
    baseline,
    transparency,
    calculationState: "recalculating",
    revisions: [revision],
    calcResult: null,
    displayedResult: null,
    reviewRevision: 2,
    currentRevId: revId,
    displayedRevId: null,
    currentMatches: false,
    cptHash: "d".repeat(64),
  });

  let adjustCalls = 0;
  let saved = false;
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
      body: JSON.stringify(saved ? recalcReview : unchangedReview),
    }),
  );
  await page.route("**/api/v1/question-runs/*/cpt-adjustments", async (route) => {
    adjustCalls += 1;
    if (adjustCalls === 1) {
      // First completed edit races a newer tab: stale pointer, nothing overwritten.
      return route.fulfill({
        status: 412,
        contentType: "application/json",
        body: JSON.stringify({
          code: "STALE_REVISION",
          message: "The probability review changed. Reload and reconcile your edits.",
          field_errors: {},
          request_id: "req-stale-1",
          retryable: false,
        }),
      });
    }
    saved = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        revision,
        review_state: { review_revision: 2, current_revision_id: revId },
        current_cpt_revision_id: revId,
        review_revision: 2,
        cpt_hash: "d".repeat(64),
        current_tables: mockAdjustedTables(),
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
    await expect(cpt).toContainText("CPT values (exact percentages)", { timeout: 15000 });

    // First edit hits a stale pointer: reload offered, nothing overwritten.
    const slider = cpt.getByLabel(/A.*yes.*original 20/i);
    await slider.focus();
    await page.keyboard.press("ArrowRight");
    await expect(cpt).toContainText("changed elsewhere", { timeout: 15000 });
    await expect(
      cpt.getByRole("button", { name: "Reload current values" }).first(),
    ).toBeVisible();
    expect(adjustCalls).toBe(1);
    // Stale values not clobbered: originals still exact.
    await expect(cpt).toContainText("80");

    // Reload then retry: second edit autosaves and stays responsive.
    await cpt.getByRole("button", { name: "Reload current values" }).first().click();
    await expect(cpt).toContainText("CPT values (exact percentages)");
    await slider.focus();
    await page.keyboard.press("ArrowRight");
    await expect(cpt).toContainText("Saved", { timeout: 15000 });
    await expect(cpt).toContainText("Recalculating", { timeout: 10000 });
    expect(adjustCalls).toBe(2);

    // Refresh/resume restores committed values/state (no localStorage for CPT).
    await page.reload();
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", {
      timeout: 15000,
    });
    await page.getByTestId("cpt-toggle-s48_q1").click();
    const resumed = page.getByTestId("cpt-panel-s48_q1");
    await expect(resumed).toContainText("Recalculating", { timeout: 15000 });
    await expect(resumed).toContainText("60");
    await expect(resumed).toContainText("40");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/question-runs/*/cpt-adjustments");
  }
});

// S48c §3 (T9): reset/retry affect one panel only and retain history.
test("S48c §3 reset affects one panel only and retains revision history", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecptreset");
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

  const batchId = "rst-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("rst-run-2222-4222-8222-222222222222", batchId, "s48_q1");
  const run2 = mockRun("rst-run-3333-4333-8333-333333333333", batchId, "s48_q2");
  const base1 = mockBaseline("rst-base-1111-4111-8111-111111111111", run1.id, batchId, "s48_q1");
  const base2 = mockBaseline("rst-base-2222-4222-8222-222222222222", run2.id, batchId, "s48_q2");
  const trans1 = mockTransparency("s48_q1", 2);
  const trans2 = mockTransparency("s48_q2", 2);
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  const rev1a = mockRevision("rst-rev-1111-4111-8111-111111111111", run1.id, batchId, 1, "adjustment");
  const calc1 = mockCalcResult(
    "rst-calc-1111-4111-8111-111111111111",
    run1.id,
    batchId,
    rev1a.id,
    "s48_q1",
    { wordingUnchanged: true },
  );
  const rev1b = mockRevision("rst-rev-2222-4222-8222-222222222222", run2.id, batchId, 1, "adjustment");
  const calc2 = mockCalcResult(
    "rst-calc-2222-4222-8222-222222222222",
    run2.id,
    batchId,
    rev1b.id,
    "s48_q2",
    { wordingUnchanged: true },
  );
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    calculationState: "successfully_recalculated",
    revisions: [rev1a],
    calcResult: calc1,
    displayedResult: calc1,
    reviewRevision: 2,
    currentRevId: rev1a.id,
    displayedRevId: rev1a.id,
    currentMatches: true,
    cptHash: "d".repeat(64),
  });
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    calculationState: "successfully_recalculated",
    revisions: [rev1b],
    calcResult: calc2,
    displayedResult: calc2,
    reviewRevision: 2,
    currentRevId: rev1b.id,
    displayedRevId: rev1b.id,
    currentMatches: true,
    cptHash: "d".repeat(64),
  });
  // After reset: q1 baseline-equal reuse (history grows to 2), q2 untouched.
  const resetRev = mockRevision("rst-rev-3333-4333-8333-333333333333", run1.id, batchId, 2, "reset");
  resetRev.parent_revision_id = rev1a.id;
  const reuseCalc = mockCalcResult(
    "rst-calc-3333-4333-8333-333333333333",
    run1.id,
    batchId,
    resetRev.id,
    "s48_q1",
    { reusedFrom: base1.id, wordingUnchanged: true },
  );
  const review1Reset = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    calculationState: "successfully_recalculated",
    revisions: [rev1a, resetRev],
    calcResult: reuseCalc,
    displayedResult: reuseCalc,
    reviewRevision: 3,
    currentRevId: resetRev.id,
    displayedRevId: resetRev.id,
    currentMatches: true,
    cptHash: "d".repeat(64),
  });
  let resetDone = false;

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
    if (url.includes(run1.id)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(resetDone ? review1Reset : review1),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(review2),
    });
  });
  await page.route("**/api/v1/question-runs/*/reset", async (route) => {
    resetDone = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        revision: resetRev,
        review_state: { review_revision: 3, current_revision_id: resetRev.id },
        current_cpt_revision_id: resetRev.id,
        review_revision: 3,
        cpt_hash: "d".repeat(64),
        current_tables: mockOriginalTables(),
        calculation_result: reuseCalc,
        reused_from_baseline_id: base1.id,
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
    await page.getByTestId("cpt-toggle-s48_q2").click();
    const cpt1 = page.getByTestId("cpt-panel-s48_q1");
    const cpt2 = page.getByTestId("cpt-panel-s48_q2");
    await expect(cpt1).toContainText("60", { timeout: 15000 });
    await expect(cpt2).toContainText("60", { timeout: 10000 });
    await expect(cpt1).toContainText("1 revisions");
    await expect(cpt2).toContainText("1 revisions");

    await cpt1.getByTestId("cpt-reset-s48_q1").click();
    await expect(cpt1).toContainText("80", { timeout: 15000 });
    await expect(cpt1).toContainText("2 revisions");
    await expect(cpt1).toContainText("verified reuse");
    await expect(cpt1).toContainText(base1.id);
    // Other panel unchanged: still adjusted 60/40 with its own single revision.
    await expect(cpt2).toContainText("60");
    await expect(cpt2).toContainText("1 revisions");
    await expect(cpt2).not.toContainText("verified reuse");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/question-runs/*/reset");
  }
});

// S48c §4 (T1/T9): accept exact current, forged 409, retry one panel, edit invalidates.
test("S48c §4 accept exact current, forged rejected, retry isolates, edit invalidates", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2ecptaccept");
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

  const batchId = "acc-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("acc-run-2222-4222-8222-222222222222", batchId, "s48_q1");
  const run2 = mockRun("acc-run-3333-4333-8333-333333333333", batchId, "s48_q2");
  const base1 = mockBaseline("acc-base-1111-4111-8111-111111111111", run1.id, batchId, "s48_q1");
  const base2 = mockBaseline("acc-base-2222-4222-8222-222222222222", run2.id, batchId, "s48_q2");
  const trans1 = mockTransparency("s48_q1", 2);
  const trans2 = mockTransparency("s48_q2", 2);
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  const rev1 = mockRevision("acc-rev-1111-4111-8111-111111111111", run1.id, batchId, 1, "adjustment");
  const calc1 = mockCalcResult(
    "acc-calc-1111-4111-8111-111111111111",
    run1.id,
    batchId,
    rev1.id,
    "s48_q1",
    { wordingUnchanged: true },
  );
  const acc1 = {
    id: "acc-acc-1111-4111-8111-111111111111",
    encounter_id: draftId,
    question_run_id: run1.id,
    batch_id: batchId,
    question_key: "s48_q1",
    baseline_id: base1.id,
    cpt_revision_id: rev1.id,
    cpt_hash: "d".repeat(64),
    result_kind: "calculation",
    result_id: calc1.id,
    input_hash: "fp-mock-1",
    projection_hash: "c".repeat(64),
    actor_username: "physician-mock",
    created_at: "2026-10-10T00:00:00Z",
  };
  const rev1b = mockRevision("acc-rev-2222-4222-8222-222222222222", run2.id, batchId, 1, "adjustment");
  const rev2b = mockRevision("acc-rev-3333-4333-8333-333333333333", run2.id, batchId, 2, "adjustment");
  rev2b.parent_revision_id = rev1b.id;
  const calcEarlier = mockCalcResult(
    "acc-calc-2222-4222-8222-222222222222",
    run2.id,
    batchId,
    rev1b.id,
    "s48_q2",
    { wordingUnchanged: false },
  );
  const rev2Edit = mockRevision("acc-rev-4444-4444-8444-444444444444", run1.id, batchId, 2, "adjustment");
  rev2Edit.parent_revision_id = rev1.id;

  let q1Accepted = false;
  let q1Edited = false;
  let q2Retried = false;
  const review1Base = () =>
    mockReview({
      run: run1,
      batch: batchPayload.batch,
      baseline: base1,
      transparency: trans1,
      calculationState: q1Edited ? "recalculating" : "successfully_recalculated",
      revisions: q1Edited ? [rev1, rev2Edit] : [rev1],
      calcResult: q1Edited ? null : calc1,
      displayedResult: q1Edited ? calc1 : calc1,
      reviewRevision: q1Edited ? 3 : 2,
      currentRevId: q1Edited ? rev2Edit.id : rev1.id,
      displayedRevId: rev1.id,
      currentMatches: !q1Edited,
      cptHash: "d".repeat(64),
      isAccepted: q1Accepted && !q1Edited,
      acceptance: q1Accepted && !q1Edited ? acc1 : null,
      acceptances: q1Accepted ? [acc1] : [],
    });
  const review2Base = () =>
    mockReview({
      run: run2,
      batch: batchPayload.batch,
      baseline: base2,
      transparency: trans2,
      calculationState: q2Retried ? "recalculating" : "failed",
      revisions: [rev1b, rev2b],
      calcResult: null,
      displayedResult: calcEarlier,
      reviewRevision: 3,
      currentRevId: rev2b.id,
      displayedRevId: rev1b.id,
      currentMatches: false,
      cptHash: "d".repeat(64),
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
    const body = JSON.stringify(url.includes(run1.id) ? review1Base() : review2Base());
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  await page.route("**/api/v1/question-runs/*/acceptance", async (route) => {
    const url = route.request().url();
    let body: Record<string, unknown> = {};
    try {
      body = route.request().postDataJSON() as Record<string, unknown>;
    } catch {
      body = {};
    }
    // Only q1 exact current matches; everything else is a forged mismatch.
    const exact =
      url.includes(run1.id) &&
      body["baseline_id"] === base1.id &&
      body["current_cpt_revision_id"] === rev1.id &&
      body["cpt_hash"] === "d".repeat(64) &&
      body["result_id"] === calc1.id &&
      body["input_hash"] === "fp-mock-1" &&
      body["expected_review_revision"] === 2;
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
    q1Accepted = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        acceptance: acc1,
        is_accepted: true,
        current_cpt_revision_id: rev1.id,
        review_revision: 2,
        cpt_hash: "d".repeat(64),
      }),
    });
  });
  let retryBodies: Record<string, unknown>[] = [];
  await page.route("**/api/v1/question-runs/*/retry-calculation", async (route) => {
    try {
      retryBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    } catch {
      retryBodies.push({});
    }
    q2Retried = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job: {
          id: "job-retry-1",
          batch_id: batchId,
          question_run_id: run2.id,
          job_class: "local_calculation",
          status: "queued",
          attempt_index: 0,
          max_attempts: 3,
          lease_deadline: null,
          last_heartbeat: null,
          next_eligible_at: null,
          result: null,
          created_at: "2026-10-10T00:00:00Z",
          updated_at: "2026-10-10T00:00:00Z",
        },
        current_cpt_revision_id: rev2b.id,
        review_revision: 3,
        cpt_hash: "d".repeat(64),
      }),
    });
  });
  await page.route("**/api/v1/question-runs/*/cpt-adjustments", async (route) => {
    q1Edited = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        revision: rev2Edit,
        review_state: { review_revision: 3, current_revision_id: rev2Edit.id },
        current_cpt_revision_id: rev2Edit.id,
        review_revision: 3,
        cpt_hash: "d".repeat(64),
        current_tables: mockAdjustedTables(),
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
    await page.getByTestId("cpt-toggle-s48_q2").click();
    const cpt1 = page.getByTestId("cpt-panel-s48_q1");
    const cpt2 = page.getByTestId("cpt-panel-s48_q2");
    await expect(cpt1).toContainText("Successfully recalculated", { timeout: 15000 });
    await expect(cpt2).toContainText("Failed", { timeout: 10000 });

    // Forged acceptance via browser fetch (wrong hash) is 409, no state change.
    const forgedStatus = await page.evaluate(
      async ({ runId: id }: { runId: string }) => {
        const res = await fetch(`/api/v1/question-runs/${id}/acceptance`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            baseline_id: "00000000-0000-4000-8000-000000000000",
            current_cpt_revision_id: "00000000-0000-4000-8000-000000000001",
            cpt_hash: "0".repeat(64),
            result_id: "00000000-0000-4000-8000-000000000002",
            input_hash: "0".repeat(64),
            expected_review_revision: 2,
          }),
        });
        return res.status;
      },
      { runId: run1.id },
    );
    expect(forgedStatus).toBe(409);
    await expect(cpt1).toContainText("Not accepted");

    // Exact current acceptance succeeds and exposes explicit references (S49 input).
    await cpt1.getByTestId("cpt-accept-s48_q1").click();
    await expect(cpt1).toContainText("Accepted", { timeout: 15000 });
    await expect(cpt1).toContainText(acc1.id);
    await expect(cpt1).toContainText(rev1.id);
    await expect(cpt1).toContainText(calc1.id);
    await expect(cpt1).toContainText("1 acceptances");

    // Retry targets exactly the failed panel's current revision, q1 stays accepted.
    await cpt2.getByTestId("cpt-retry-s48_q2").click();
    await expect(cpt2).toContainText("Recalculating", { timeout: 15000 });
    expect(retryBodies.length).toBeGreaterThanOrEqual(1);
    expect(retryBodies[0]["expected_review_revision"]).toBe(3);
    await expect(cpt1).toContainText("Accepted");

    // Editing q1 after acceptance invalidates it but retains history.
    const slider = cpt1.getByLabel(/A.*yes/i).first();
    await slider.focus();
    await page.keyboard.press("ArrowRight");
    await expect(cpt1).toContainText("Recalculating", { timeout: 15000 });
    await expect(cpt1).toContainText("Not accepted");
    await expect(cpt1).toContainText("1 acceptances");
    // Sign UI consumes explicit references (S49 owns enforcement — no signing here).
    await expect(cpt1).toContainText(acc1.id);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/question-runs/*/acceptance");
    await page.unroute("**/api/v1/question-runs/*/retry-calculation");
    await page.unroute("**/api/v1/question-runs/*/cpt-adjustments");
  }
});
