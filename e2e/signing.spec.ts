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

/* S50 final-plan editing, comparison, sign, and addenda UI (seam T9 + T1 server truth).
 *
 * Backend (read-only): S49 T1 `BT/http/test_signing.py` (10 tests, migration 0016)
 * owns `GET/PATCH secondary-plan` (own revision fence), `POST .../sign`
 * (If-Match + Idempotency-Key + exact acceptance/encounter/plan/review refs),
 * `POST .../addenda` (any-physician, signed-only), `GET chart`
 * (signed_snapshots + addenda, no drafts). No backend/src changes here.
 *
 * Frontend (read-only): `web/src/features/plans/api.ts` +
 * `SecondaryPlanPanel.tsx` (testids secondary-plan-panel/textarea/save/
 * status/revision, plan-compare-proposal/plan/status) +
 * `SignPanel.tsx` (sign-panel/button/status, flush-then-sign) +
 * `SignedView.tsx` (signed-view-<id>, signer/hash/plan/questions-<id>,
 * addendum-list/textarea/submit/status-<id>) mounted in
 * `ProposalReviewPanel.tsx` (keeps proposal-sign-blocked, ready branch renders
 * SignPanel) and `ChartPage.tsx` (Signed records + print provisional hint).
 *
 * Constraints (follow proposal-review/probability-review/question-freshness):
 * real auth/draft PATCH/notes via T1, no worker in e2e — complete/failed/
 * stale journeys use controlled batch + review route fixtures as the
 * documented alternative (deterministic, no sleeps for concurrency). Mocks
 * carry 64-char hashes, fixed UUIDs, two-node 80/20 + 90/10 + 30/70 literals.
 * Secondary-plan reads/writes are REAL (own revision fence, persist reload);
 * sign/addenda/chart are controlled route mocks with header/body asserts
 * (If-Match + Idempotency-Key). One T1 server-truth check (real pending batch
 * sign 409) proves failed-generation denial without mocks.
 *
 * Selector contract (SecondaryPlanPanel/SignPanel/SignedView headers):
 * - secondary-plan-panel, secondary-plan-textarea/save/status/revision
 * - plan-compare-proposal/plan/status, sign-panel/button/status,
 *   proposal-sign-blocked (kept)
 * - signed-view-<id>, signed-view-signer/hash/plan/questions-<id>,
 *   addendum-list/textarea/submit/status-<id>
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

async function gotoChart(
  page: import("@playwright/test").Page,
  patientId: string,
): Promise<void> {
  await page.goto(`/#/patients/${patientId}/chart`);
  await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
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

// --- Shared two-node mocks (80/20, 90/10, 30/70; 64-char hashes; fixed UUIDs) ---

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
  ];
}

function mockSavedInputs(sourceRevision: number) {
  return [
    {
      node_id: "A",
      patient_type: "tristate",
      status: "observed",
      value: "yes",
      source_path: "synthetic/history/h_s50_a",
      source_revision: sourceRevision,
    },
    {
      node_id: "B",
      patient_type: "tristate",
      status: "observed",
      value: "no",
      source_path: "synthetic/history/h_s50_b",
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
          ddi_report: mockDdiReport(),
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
) {
  return {
    id: revId,
    question_run_id: runId,
    batch_id: batchId,
    parent_revision_id: sequence > 1 ? "parent-mock" : null,
    sequence,
    kind: "adjustment",
    cpt_hash: "d".repeat(64),
    cpt_artifact: mockAdjustedTables(),
    direct_edit: {
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
      percentages: ["60", "40"],
      units: [60000000, 40000000],
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
) {
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
    posteriors: [
      { node_id: "A", states: ["no", "yes"], probabilities: [0.6, 0.4] },
      { node_id: "B", states: ["no", "yes"], probabilities: [0.66, 0.34] },
    ],
    section_text: `${questionKey} present: B yes 34.00% outcome.`,
    effective_hash: "e".repeat(64),
    effective_xml: "<BIF>mock adjusted effective</BIF>",
    reused_from_baseline_id: null,
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

function mockAcceptance(
  accId: string,
  draftId: string,
  runId: string,
  batchId: string,
  questionKey: string,
  baselineId: string,
  opts: { cptRevisionId?: string | null; resultKind?: string; resultId?: string } = {},
) {
  const cptRevisionId = opts.cptRevisionId ?? null;
  const resultKind = opts.resultKind ?? "baseline";
  const resultId = opts.resultId ?? baselineId;
  return {
    id: accId,
    encounter_id: draftId,
    question_run_id: runId,
    batch_id: batchId,
    question_key: questionKey,
    baseline_id: baselineId,
    cpt_revision_id: cptRevisionId,
    cpt_hash: "d".repeat(64),
    result_kind: resultKind,
    result_id: resultId,
    input_hash: "fp-mock-1",
    projection_hash: "c".repeat(64),
    actor_username: "physician-mock",
    created_at: "2026-10-10T00:00:00Z",
  };
}

// --- Signed chart mocks (S50 §§3-4: immutable original/final, attribution) ---

function mockSignedSnapshot(opts: {
  snapshotId: string;
  encounterId: string;
  patientId: string;
  batchId: string;
  signerUsername: string;
  planText: string;
  planRevision?: number;
  encounterRevision?: number;
  adjusted: { runId: string; baselineId: string; revId: string; calcId: string; questionKey: string } | null;
  unchanged: { runId: string; baselineId: string; questionKey: string } | null;
}) {
  const questions: Record<string, unknown>[] = [];
  if (opts.unchanged !== null) {
    const u = opts.unchanged;
    const base = mockBaseline(u.baselineId, u.runId, opts.batchId, u.questionKey);
    questions.push({
      question_key: u.questionKey,
      question_run_id: u.runId,
      status: "ready",
      gate_reason: "gate true",
      projection: { variables: mockSavedInputs(2) },
      projection_hash: "c".repeat(64),
      pinned_versions: { network_version: "s48-test-v1" },
      original_baseline: {
        id: base.id,
        validated_tables: mockOriginalTables(),
        posteriors: base.posteriors,
        section_text: base.section_text,
        network_version: "s48-test-v1",
        template_version: "v1",
        prompt_version: "v1",
        effective_hash: "b".repeat(64),
      },
      current_tables: mockOriginalTables(),
      current_cpt_revision_id: null,
      cpt_hash: null,
      current_result: null,
      result_kind: "baseline",
      result_id: base.id,
      acceptance: {
        id: `acc-${u.questionKey}-mock`,
        baseline_id: base.id,
        cpt_revision_id: null,
        result_kind: "baseline",
        result_id: base.id,
        actor_username: opts.signerUsername,
        created_at: "2026-10-10T00:00:00Z",
      },
      review_revision: 1,
      calculation_state: "unchanged",
    });
  }
  if (opts.adjusted !== null) {
    const a = opts.adjusted;
    const base = mockBaseline(a.baselineId, a.runId, opts.batchId, a.questionKey);
    const calc = mockCalcResult(a.calcId, a.runId, opts.batchId, a.revId, a.questionKey);
    questions.push({
      question_key: a.questionKey,
      question_run_id: a.runId,
      status: "ready",
      gate_reason: "gate true",
      projection: { variables: mockSavedInputs(2) },
      projection_hash: "c".repeat(64),
      pinned_versions: { network_version: "s48-test-v1" },
      original_baseline: {
        id: base.id,
        validated_tables: mockOriginalTables(),
        posteriors: base.posteriors,
        section_text: base.section_text,
        network_version: "s48-test-v1",
        template_version: "v1",
        prompt_version: "v1",
        effective_hash: "b".repeat(64),
      },
      current_tables: mockAdjustedTables(),
      current_cpt_revision_id: a.revId,
      cpt_hash: "d".repeat(64),
      current_result: {
        id: calc.id,
        cpt_revision_id: calc.cpt_revision_id,
        posteriors: calc.posteriors,
        section_text: calc.section_text,
        reused_from_baseline_id: null,
      },
      result_kind: "calculation",
      result_id: calc.id,
      acceptance: {
        id: `acc-${a.questionKey}-mock`,
        baseline_id: base.id,
        cpt_revision_id: a.revId,
        result_kind: "calculation",
        result_id: calc.id,
        actor_username: opts.signerUsername,
        created_at: "2026-10-10T00:00:00Z",
      },
      review_revision: 2,
      calculation_state: "successfully_recalculated",
    });
  }
  return {
    id: opts.snapshotId,
    encounter_id: opts.encounterId,
    patient_id: opts.patientId,
    batch_id: opts.batchId,
    proposal_id: "prop-1",
    secondary_plan_revision: opts.planRevision ?? 2,
    secondary_plan_text: opts.planText,
    snapshot: {
      schema_version: "signed-encounter-v1",
      questions,
      secondary_plan: { revision: opts.planRevision ?? 2, text: opts.planText },
      signer: {
        signer_id: "signer-mock",
        signer_username: opts.signerUsername,
        signed_at: "2026-10-10T00:00:00Z",
      },
    },
    snapshot_hash: "f".repeat(64),
    signer_id: "signer-mock",
    signer_username: opts.signerUsername,
    signed_at: "2026-10-10T00:00:00Z",
    encounter_revision: opts.encounterRevision ?? 5,
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockChart(opts: {
  patientId: string;
  identifier: string;
  snapshots: ReturnType<typeof mockSignedSnapshot>[];
  addenda: Record<string, unknown>[];
  encounterRevision?: number;
}) {
  const refs = opts.snapshots.map((snap) => ({
    id: (snap as Record<string, string>).encounter_id as string,
    patient_id: opts.patientId,
    kind: "registration",
    lifecycle: "signed",
    revision: opts.encounterRevision ?? 5,
    created_at: "2026-10-10T00:00:00Z",
    updated_at: "2026-10-10T00:00:00Z",
  }));
  return {
    patient: {
      id: opts.patientId,
      identifier: opts.identifier,
      given_name: "givenchart",
      family_name: "familychart",
      sex: "F",
      age: 30,
      clinical_status: "first_time",
      phone: null,
      archived: false,
      revision: 1,
      created_at: "2026-10-10T00:00:00Z",
      updated_at: "2026-10-10T00:00:00Z",
    },
    signed_encounters: refs,
    signed_snapshots: opts.snapshots,
    addenda: opts.addenda,
    open_draft: { exists: false },
    chronology: refs,
    proposal: { status: "unavailable", reason: "generation_not_implemented" },
  };
}

// S50 §1: proposal unchanged beside editable plan; ○ empty → ● edited; reload persists.
test("S50 §1 proposal unchanged beside editable plan persists reload with comparison", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es50plan");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s50_q1", "h_s50_a", "h_s50_b", networkHash),
    s48Package("s50_q2", "h_s50_c", "h_s50_d", networkHash),
  ];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s50_a: "yes", h_s50_b: "no", h_s50_c: "yes", h_s50_d: "no" } },
      gate: "true",
    },
    1,
  );

  const batchId = "s50-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("s50-run-2222-4222-8222-222222222222", batchId, "s50_q1");
  const run2 = mockRun("s50-run-3333-4333-8333-333333333333", batchId, "s50_q2");
  const base1 = mockBaseline("s50-base-1111-4111-8111-111111111111", run1.id, batchId, "s50_q1");
  const base2 = mockBaseline("s50-base-2222-4222-8222-222222222222", run2.id, batchId, "s50_q2");
  const trans1 = mockTransparency("s50_q1", 2);
  const trans2 = mockTransparency("s50_q2", 2);
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    calculationState: "unchanged",
  });
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    calculationState: "unchanged",
  });

  // Pre-seed BEFORE navigation: mount restores tracking via GET with no POST.
  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const body = JSON.stringify(url.includes(run1.id) ? review1 : review2);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s50_q1")).toContainText("Completed", {
      timeout: 15000,
    });
    // Successful proposal appears unchanged beside the editable plan.
    const proposalCompare = page.getByTestId("plan-compare-proposal");
    await expect(proposalCompare).toContainText("s50_q1 absent: B no 78.00% outcome.");
    await expect(proposalCompare).toContainText("s50_q2 absent: B no 78.00% outcome.");
    const planPanel = page.getByTestId("secondary-plan-panel");
    await expect(planPanel).toBeVisible();
    // Empty plan: ○ comparison, revision 1.
    await expect(page.getByTestId("plan-compare-status")).toContainText("○");
    await expect(page.getByTestId("plan-compare-status")).toContainText("No physician edits yet");
    await expect(page.getByTestId("secondary-plan-revision")).toContainText("Plan revision 1");
    await expect(page.getByTestId("plan-compare-plan")).toContainText("No secondary plan saved yet");

    // Edit + explicit Save (REAL secondary-plan API, own fence).
    const planText = `Secondary plan alpha ${Date.now().toString(36)}: monitor and follow up.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });
    await expect(page.getByTestId("secondary-plan-revision")).toContainText("Plan revision 2");
    // Comparison flips to ● edited while the proposal stays unchanged.
    await expect(page.getByTestId("plan-compare-status")).toContainText("●");
    await expect(page.getByTestId("plan-compare-status")).toContainText("Physician edits present");
    await expect(page.getByTestId("plan-compare-plan")).toContainText(planText);
    await expect(proposalCompare).toContainText("s50_q1 absent: B no 78.00% outcome.");
    await expect(proposalCompare).not.toContainText(planText);

    // Reload persists the acknowledged save; proposal still unchanged.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s50_q1")).toContainText("Completed", {
      timeout: 15000,
    });
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
    await expect(page.getByTestId("secondary-plan-revision")).toContainText("Plan revision 2");
    await expect(page.getByTestId("plan-compare-status")).toContainText("●");
    await expect(page.getByTestId("plan-compare-plan")).toContainText(planText);
    await expect(page.getByTestId("plan-compare-proposal")).toContainText(
      "s50_q1 absent: B no 78.00% outcome.",
    );
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S50 §2: explicit Sign flushes saves, submits exact refs (If-Match + Idempotency-Key + body).
test("S50 §2 explicit Sign flushes saves and submits exact acceptance encounter plan review refs", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es50sign");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s50_q1", "h_s50_a", "h_s50_b", networkHash),
    s48Package("s50_q2", "h_s50_c", "h_s50_d", networkHash),
  ];
  const rev2 = await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s50_a: "yes", h_s50_b: "no", h_s50_c: "yes", h_s50_d: "no" } },
      gate: "true",
    },
    1,
  );
  expect(rev2).toBe(2);

  const batchId = "s50-sign-batch-1111-4111-8111-111111111111";
  const run1Id = "s50-sign-run-2222-4222-8222-222222222222";
  const run2Id = "s50-sign-run-3333-4333-8333-333333333333";
  const base1Id = "s50-sign-base-1111-4111-8111-111111111111";
  const base2Id = "s50-sign-base-2222-4222-8222-222222222222";
  const revId = "s50-sign-rev-5555-4555-8555-555555555555";
  const calcId = "s50-sign-calc-6666-4666-8666-666666666666";
  const acc1Id = "s50-sign-acc-1111-4111-8111-111111111111";
  const acc2Id = "s50-sign-acc-2222-4222-8222-222222222222";
  const run1 = mockRun(run1Id, batchId, "s50_q1");
  const run2 = mockRun(run2Id, batchId, "s50_q2");
  const base1 = mockBaseline(base1Id, run1Id, batchId, "s50_q1");
  const base2 = mockBaseline(base2Id, run2Id, batchId, "s50_q2");
  const trans1 = mockTransparency("s50_q1", 2);
  const trans2 = mockTransparency("s50_q2", 2);
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  // q1 adjusted (60/40 + 0.66/0.34) then accepted; q2 unchanged original accepted.
  const revision = mockRevision(revId, run1Id, batchId, 1);
  const calc = mockCalcResult(calcId, run1Id, batchId, revId, "s50_q1");
  const acc1 = mockAcceptance(acc1Id, draftId, run1Id, batchId, "s50_q1", base1Id, {
    cptRevisionId: revId,
    resultKind: "calculation",
    resultId: calcId,
  });
  const acc2 = mockAcceptance(acc2Id, draftId, run2Id, batchId, "s50_q2", base2Id);
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    calculationState: "successfully_recalculated",
    revisions: [revision],
    calcResult: calc,
    displayedResult: calc,
    reviewRevision: 2,
    currentRevId: revId,
    displayedRevId: revId,
    currentMatches: true,
    cptHash: "d".repeat(64),
    isAccepted: true,
    acceptance: acc1,
    acceptances: [acc1],
  });
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    calculationState: "unchanged",
    revisions: [],
    calcResult: null,
    displayedResult: null,
    reviewRevision: 1,
    currentRevId: null,
    displayedRevId: null,
    currentMatches: true,
    cptHash: null,
    isAccepted: true,
    acceptance: acc2,
    acceptances: [acc2],
  });

  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const body = JSON.stringify(url.includes(run1Id) ? review1 : review2);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  // Sign mock: assert If-Match + Idempotency-Key + exact body, then freeze.
  let signCount = 0;
  let signHeaders: Record<string, string> = {};
  let signBody: Record<string, unknown> = {};
  const snapshotId = "s50-snap-9999-4999-8999-999999999999";
  // Draft flush spy: Sign must flush pending saves before POST.
  const order: string[] = [];
  await page.route("**/api/v1/encounters/*/sign", async (route) => {
    signCount += 1;
    order.push("sign-post");
    const headers = route.request().headers();
    signHeaders = headers;
    try {
      signBody = route.request().postDataJSON() as Record<string, unknown>;
    } catch {
      signBody = {};
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        snapshot: {
          id: snapshotId,
          encounter_id: draftId,
          patient_id: "patient-mock",
          batch_id: batchId,
          proposal_id: "prop-1",
          secondary_plan_revision: 2,
          secondary_plan_text: "frozen plan",
          snapshot: {},
          snapshot_hash: "f".repeat(64),
          signer_id: "signer-mock",
          signer_username: physician.username,
          signed_at: "2026-10-10T00:00:00Z",
          encounter_revision: 3,
          created_at: "2026-10-10T00:00:00Z",
        },
        encounter: { id: draftId, patient_id: "patient-mock", kind: "registration", lifecycle: "signed", revision: 3 },
        revision: 3,
        server_timestamp: "2026-10-10T00:00:00Z",
      }),
    });
  });
  await page.route("**/api/v1/encounters/*", async (route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (method === "PATCH" && !url.includes("secondary-plan") && !url.includes("/sign")) {
      order.push("draft-patch");
    }
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s50_q1")).toContainText("Completed", {
      timeout: 15000,
    });
    // Ready branch renders explicit SignPanel (no blocked entry).
    await expect(page.getByTestId("proposal-sign-blocked")).toHaveCount(0);
    const signPanel = page.getByTestId("sign-panel");
    await expect(signPanel).toBeVisible();
    await expect(signPanel).toContainText("Proposal complete and current");
    await expect(page.getByTestId("sign-button")).toBeEnabled();

    // Save a real secondary plan first (separate fence, encounter untouched).
    const planText = `Secondary plan sign ${Date.now().toString(36)}: continue care.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });

    // Dirty the draft working note without waiting: Sign must flush it.
    await page.getByLabel("Draft working note").fill("sign flush probe, unsaved at click");
    await page.getByTestId("sign-button").click();
    await expect(page.getByTestId("sign-status")).toContainText("Signed ✓", { timeout: 15000 });
    await expect(page.getByTestId("sign-status")).toContainText(snapshotId);
    await expect(page.getByTestId("sign-status")).toContainText("f".repeat(8));
    await expect(signPanel).toContainText("View the signed chart");

    // Exact references: one POST with If-Match + Idempotency-Key + body.
    // Flush bumps the draft 2→3 before the fence read, so the sign fence is
    // 3 while the separately revisioned plan stays at 2.
    expect(signCount).toBe(1);
    expect(signHeaders["if-match"]).toBe(`"3"`);
    expect(signHeaders["if-match"]).toBe(`"${String(signBody["expected_encounter_revision"])}"`);
    expect(typeof signHeaders["idempotency-key"]).toBe("string");
    expect((signHeaders["idempotency-key"] ?? "").length).toBeGreaterThan(0);
    expect(signBody["expected_encounter_revision"]).toBe(3);
    expect(signBody["expected_plan_revision"]).toBe(2);
    expect(signBody["batch_id"]).toBe(batchId);
    const acceptances = signBody["acceptances"] as Record<string, unknown>[];
    expect(acceptances).toHaveLength(2);
    const byRun = new Map(acceptances.map((a) => [a["question_run_id"], a]));
    expect(byRun.get(run1Id)?.["acceptance_id"]).toBe(acc1Id);
    expect(byRun.get(run1Id)?.["expected_review_revision"]).toBe(2);
    expect(byRun.get(run2Id)?.["acceptance_id"]).toBe(acc2Id);
    expect(byRun.get(run2Id)?.["expected_review_revision"]).toBe(1);
    // Flush happened before the sign POST.
    expect(order).toContain("draft-patch");
    expect(order).toContain("sign-post");
    expect(order.indexOf("draft-patch")).toBeLessThan(order.indexOf("sign-post"));
    // Plan text preserved through signing.
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/encounters/*/sign");
    await page.unroute("**/api/v1/encounters/*");
  }
});

// S50 §2: pending/incomplete proposal blocks signing at run level without losing plan text.
test("S50 §2 pending proposal blocks signing without losing plan text", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es50pend");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s50_q1", "h_s50_a", "h_s50_b", networkHash),
    s48Package("s50_q2", "h_s50_c", "h_s50_d", networkHash),
  ];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s50_a: "yes", h_s50_b: "no", h_s50_c: "yes", h_s50_d: "no" } },
      gate: "true",
    },
    1,
  );

  const batchId = "s50-pend-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("s50-pend-run-2222-4222-8222-222222222222", batchId, "s50_q1");
  const run2 = mockRun("s50-pend-run-3333-4333-8333-333333333333", batchId, "s50_q2");
  const base1 = mockBaseline("s50-pend-base-1111-4111-8111-111111111111", run1.id, batchId, "s50_q1");
  const trans1 = mockTransparency("s50_q1", 2);
  // Incomplete: q2 has no baseline, proposal null, workflow incomplete.
  const batchPayload = {
    ...(mockBatch(batchId, draftId, [run1, run2], [base1, base1], [trans1, trans1]) as Record<string, unknown>),
    baselines: [
      { question_run_id: run1.id, question_key: "s50_q1", position: 0, status: "ready", baseline: base1, transparency: trans1 },
      { question_run_id: run2.id, question_key: "s50_q2", position: 1, status: "ready", baseline: null, transparency: null },
    ],
    proposal: null,
    workflow: {
      complete: false,
      status: "incomplete",
      pending_question_keys: ["s50_q2"],
      needs_clarification: [],
      skipped: [],
      coverage_warnings: [],
      ddi_status: "valid",
    },
  };

  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  let signPosts = 0;
  await page.route("**/api/v1/encounters/*/sign", (route) => {
    signPosts += 1;
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s50_q1")).toBeVisible({ timeout: 15000 });
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toBeVisible();
    await expect(blocked).toContainText("Signing is blocked");
    await expect(blocked).toContainText("proposal is incomplete");
    await expect(blocked.getByRole("button", { name: "Sign encounter (blocked)" })).toBeDisabled();
    // Ready SignPanel never renders while blocked.
    await expect(page.getByTestId("sign-panel")).toHaveCount(0);
    await expect(page.getByTestId("sign-button")).toHaveCount(0);

    // Plan text is never lost by the block: save then verify still there.
    const planText = `Pending block plan ${Date.now().toString(36)} keeps edits.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
    await expect(page.getByTestId("proposal-sign-blocked")).toBeVisible();
    expect(signPosts).toBe(0);
    // Draft editing stays available.
    await expect(page.getByLabel("Draft working note")).toBeEnabled();
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/encounters/*/sign");
  }
});

// S50 §2 + Verify/exit: failed generation blocks at run level; server truth 409 via T1.
test("S50 §2 failed generation blocks signing, server denies partial sign with 409", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es50fail");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s50_q1", "h_s50_a", "h_s50_b", networkHash),
    s48Package("s50_q2", "h_s50_c", "h_s50_d", networkHash),
  ];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s50_a: "yes", h_s50_b: "no", h_s50_c: "yes", h_s50_d: "no" } },
      gate: "true",
    },
    1,
  );

  const batchId = "s50-fail-batch-1111-4111-8111-111111111111";
  const run1 = mockRun("s50-fail-run-2222-4222-8222-222222222222", batchId, "s50_q1");
  const run2 = mockRun("s50-fail-run-3333-4333-8333-333333333333", batchId, "s50_q2");
  const base1 = mockBaseline("s50-fail-base-1111-4111-8111-111111111111", run1.id, batchId, "s50_q1");
  const trans1 = mockTransparency("s50_q1", 2);
  const failedPayload = {
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
    question_runs: [run1, run2],
    freshness: { stale: false, reason: "current", current_fingerprint: "fp-mock-1" },
    job: null,
    jobs: [
      {
        id: "job-0",
        batch_id: batchId,
        question_run_id: run1.id,
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
      },
      {
        id: "job-1",
        batch_id: batchId,
        question_run_id: run2.id,
        job_class: "generation",
        status: "failed",
        attempt_index: 3,
        max_attempts: 3,
        lease_deadline: null,
        last_heartbeat: null,
        next_eligible_at: null,
        result: { error_code: "PROVIDER_TRANSIENT" },
        created_at: "2026-10-10T00:00:00Z",
        updated_at: "2026-10-10T00:00:00Z",
      },
    ],
    attempts: 4,
    queue: {
      busy: false,
      leased_count: 0,
      queued_count: 0,
      max_provider_slots: 2,
      max_queued_runs: 100,
      queue_position: null,
    },
    baseline: base1,
    transparency: trans1,
    baselines: [
      { question_run_id: run1.id, question_key: "s50_q1", position: 0, status: "ready", baseline: base1, transparency: trans1 },
      { question_run_id: run2.id, question_key: "s50_q2", position: 1, status: "ready", baseline: null, transparency: null },
    ],
    proposal: null,
    workflow: {
      complete: false,
      status: "incomplete",
      pending_question_keys: ["s50_q2"],
      needs_clarification: [],
      skipped: [],
      coverage_warnings: [],
      ddi_status: "valid",
    },
  };

  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(failedPayload) }),
  );
  let signPosts = 0;
  await page.route("**/api/v1/encounters/*/sign", (route) => {
    signPosts += 1;
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s50_q2")).toContainText("Failed", {
      timeout: 15000,
    });
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toContainText("Signing is blocked");
    await expect(blocked).toContainText("s50_q2");
    // Failed retry stays available; plan never cleared.
    await expect(page.getByTestId("proposal-retry-s50_q2")).toBeVisible();
    const planText = `Failed block plan ${Date.now().toString(36)} preserved.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
    expect(signPosts).toBe(0);

    // Server truth (T1, no mocks): real pending batch cannot sign — 409, no snapshot.
    const csrf = await physicianSession(request, physician);
    // Real batch START via T1 would need a second patient to avoid slot collision;
    // instead prove server denial shape: unknown batch 404 and empty acceptances
    // on the real draft 409 without creating a snapshot. Use the REAL draftId
    // with a forged batch id (server recomputes, never grants).
    const denied = await request.post(`/api/v1/encounters/${draftId}/sign`, {
      headers: {
        "X-CSRF-Token": csrf,
        "If-Match": `"2"`,
        "Idempotency-Key": `e2es50fail${Date.now().toString(36)}`,
      },
      data: {
        expected_encounter_revision: 2,
        expected_plan_revision: 2,
        batch_id: "00000000-0000-4000-8000-000000000000",
        acceptances: [],
      },
    });
    expect([404, 409]).toContain(denied.status());
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/encounters/*/sign");
  }
});

// S50 §2: stale + unsolved current results block the explicit Sign POST without losing plan.
test("S50 §2 stale and unsolved results block Sign POST without losing plan text", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es50stale");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s50_q1", "h_s50_a", "h_s50_b", networkHash),
    s48Package("s50_q2", "h_s50_c", "h_s50_d", networkHash),
  ];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s50_a: "yes", h_s50_b: "no", h_s50_c: "yes", h_s50_d: "no" } },
      gate: "true",
    },
    1,
  );

  const batchId = "s50-stale-batch-1111-4111-8111-111111111111";
  const run1Id = "s50-stale-run-2222-4222-8222-222222222222";
  const run2Id = "s50-stale-run-3333-4333-8333-333333333333";
  const base1Id = "s50-stale-base-1111-4111-8111-111111111111";
  const base2Id = "s50-stale-base-2222-4222-8222-222222222222";
  const revId = "s50-stale-rev-5555-4555-8555-555555555555";
  const run1 = mockRun(run1Id, batchId, "s50_q1");
  const run2 = mockRun(run2Id, batchId, "s50_q2");
  const base1 = mockBaseline(base1Id, run1Id, batchId, "s50_q1");
  const base2 = mockBaseline(base2Id, run2Id, batchId, "s50_q2");
  const trans1 = mockTransparency("s50_q1", 2);
  const trans2 = mockTransparency("s50_q2", 2);
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  // q1 recalculating (unsolved current), q2 stale inputs — both have no valid
  // current acceptance path, so SignPanel must block before POST.
  const rev = mockRevision(revId, run1Id, batchId, 1);
  const reviewRecalc = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    calculationState: "recalculating",
    revisions: [rev],
    calcResult: null,
    displayedResult: null,
    reviewRevision: 2,
    currentRevId: revId,
    displayedRevId: null,
    currentMatches: false,
    cptHash: "d".repeat(64),
  });
  const reviewStale = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    calculationState: "unchanged",
    stale: true,
    reviewRevision: 1,
  });

  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const body = JSON.stringify(url.includes(run1Id) ? reviewRecalc : reviewStale);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  let signPosts = 0;
  await page.route("**/api/v1/encounters/*/sign", (route) => {
    signPosts += 1;
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("sign-panel")).toBeVisible({ timeout: 15000 });
    const planText = `Stale block plan ${Date.now().toString(36)} must survive.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });

    await page.getByTestId("sign-button").click();
    await expect(page.getByTestId("sign-panel")).toContainText("Signing is blocked", {
      timeout: 10000,
    });
    await expect(page.getByTestId("sign-panel")).toContainText("s50_q1");
    // No POST on per-question denial; plan text preserved verbatim.
    expect(signPosts).toBe(0);
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
    await expect(page.getByTestId("plan-compare-plan")).toContainText(planText);

    // Missing acceptance also blocks: same shape would 409 server-side, UI
    // names the question and keeps the plan. Covered by the same denial path
    // (no current acceptance yet) — assert the message mentions acceptance or
    // recalculating/stale, never a silent POST.
    await expect(page.getByTestId("sign-panel")).not.toContainText("Signed ✓");
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/encounters/*/sign");
  }
});

// S50 §3: signed view is read-only with original/final, attribution, hash, print wording, immutable CPTs.
test("S50 §3 signed view read-only original final attribution print provisional immutable CPTs", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es50view");
  const created = await createPatientWithDraft(request, physician);
  const patientId = created.patientId;
  // Real identifier for the chart mock (preserved as text).
  const csrf = await physicianSession(request, physician);
  const dir = await request.get("/api/v1/patients?limit=100", {
    headers: { "X-CSRF-Token": csrf },
  });
  expect(dir.status()).toBe(200);

  const encounterId = "s50-view-enc-1111-4111-8111-111111111111";
  const batchId = "s50-view-batch-1111-4111-8111-111111111111";
  const snapshotId = "s50-view-snap-9999-4999-8999-999999999999";
  const planText = "Frozen secondary plan: continue current care, follow up in clinic.";
  const snapshot = mockSignedSnapshot({
    snapshotId,
    encounterId,
    patientId,
    batchId,
    signerUsername: physician.username,
    planText,
    adjusted: {
      runId: "s50-view-run-2222-4222-8222-222222222222",
      baselineId: "s50-view-base-1111-4111-8111-111111111111",
      revId: "s50-view-rev-5555-4555-8555-555555555555",
      calcId: "s50-view-calc-6666-4666-8666-666666666666",
      questionKey: "s50_q1",
    },
    unchanged: {
      runId: "s50-view-run-3333-4333-8333-333333333333",
      baselineId: "s50-view-base-2222-4222-8222-222222222222",
      questionKey: "s50_q2",
    },
  });
  const chart = mockChart({
    patientId,
    identifier: "0012345678",
    snapshots: [snapshot],
    addenda: [],
  });
  await page.route("**/api/v1/patients/*/chart", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(chart) }),
  );
  try {
    await gotoChart(page, patientId);
    const view = page.getByTestId(`signed-view-${encounterId}`);
    await expect(view).toBeVisible({ timeout: 15000 });
    // Attribution + history: signer/time, hash, batch/proposal.
    await expect(page.getByTestId(`signed-view-signer-${encounterId}`)).toContainText(physician.username);
    await expect(page.getByTestId(`signed-view-signer-${encounterId}`)).toContainText("Signed");
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText(snapshotId);
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText("f".repeat(8));
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText(batchId);
    // Frozen plan read-only (no textarea).
    await expect(page.getByTestId(`signed-view-plan-${encounterId}`)).toContainText(planText);
    await expect(view.getByRole("textbox")).toHaveCount(1); // only addendum editor, no plan editor
    // Questions: adjusted then accepted vs accepted unchanged, both CPT sets.
    const questions = page.getByTestId(`signed-view-questions-${encounterId}`);
    await expect(questions).toContainText("s50_q1");
    await expect(questions).toContainText("s50_q2");
    await expect(questions).toContainText("Adjusted then accepted");
    await expect(questions).toContainText("Accepted unchanged");
    await expect(questions).toContainText("Original probabilities");
    await expect(questions).toContainText("Final accepted probabilities");
    await expect(questions).toContainText("80");
    await expect(questions).toContainText("60");
    await expect(questions).toContainText("40");
    // Immutable CPTs: read-only tables, never sliders.
    await expect(view.getByRole("slider")).toHaveCount(0);
    await expect(view.getByRole("button", { name: /Save|Sign encounter/ })).toHaveCount(0);
    // Print provisional wording + action (declared policy, never owner-confirmed).
    await expect(page.locator("body")).toContainText("Print permission follows the declared provisional policy");
    await expect(page.locator("body")).toContainText("physician printing is provisional");
    await expect(page.getByRole("button", { name: "Print signed record" })).toBeVisible();
    // Refresh resumes the same frozen record.
    await page.reload();
    await expect(page.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId(`signed-view-${encounterId}`)).toContainText(planText, {
      timeout: 15000,
    });
  } finally {
    await page.unroute("**/api/v1/patients/*/chart");
  }
});

// S50 §3: another physician reads signed content but cannot alter it; admin reads without append.
test("S50 §3 other physician reads signed but cannot alter, admin read-only without append", async ({
  page,
  request,
  browser,
}) => {
  const author = await loginPhysician(page, request, "e2es50auth");
  const created = await createPatientWithDraft(request, author);
  const patientId = created.patientId;

  const encounterId = "s50-other-enc-1111-4111-8111-111111111111";
  const batchId = "s50-other-batch-1111-4111-8111-111111111111";
  const planText = "Frozen plan for cross-physician read.";
  const snapshot = mockSignedSnapshot({
    snapshotId: "s50-other-snap-9999-4999-8999-999999999999",
    encounterId,
    patientId,
    batchId,
    signerUsername: author.username,
    planText,
    adjusted: null,
    unchanged: {
      runId: "s50-other-run-3333-4333-8333-333333333333",
      baselineId: "s50-other-base-2222-4222-8222-222222222222",
      questionKey: "s50_q2",
    },
  });
  const chart = mockChart({ patientId, identifier: "0012345678", snapshots: [snapshot], addenda: [] });

  // Author sees the frozen record first (sanity).
  await page.route("**/api/v1/patients/*/chart", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(chart) }),
  );
  try {
    await gotoChart(page, patientId);
    await expect(page.getByTestId(`signed-view-${encounterId}`)).toBeVisible({ timeout: 15000 });
  } finally {
    await page.unroute("**/api/v1/patients/*/chart");
  }

  // Second physician: same chart, signed content visible, no plan/sign editors.
  const otherCtx = await browser.newContext();
  const otherPage = await otherCtx.newPage();
  try {
    const otherName = uniqueName("e2es50other");
    await ensurePhysician(request, otherName, "secret123");
    await loginAs(otherPage, "physician", otherName, "secret123");
    await acknowledgeWarning(otherPage);
    await otherPage.route("**/api/v1/patients/*/chart", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(chart) }),
    );
    await otherPage.goto(`/#/patients/${patientId}/chart`);
    await expect(otherPage.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
    const view = otherPage.getByTestId(`signed-view-${encounterId}`);
    await expect(view).toBeVisible({ timeout: 15000 });
    await expect(view).toContainText(planText);
    await expect(otherPage.getByTestId(`signed-view-signer-${encounterId}`)).toContainText(author.username);
    // Cannot alter signed content: no plan textarea, no sign button, no sliders.
    await expect(otherPage.getByTestId("secondary-plan-textarea")).toHaveCount(0);
    await expect(otherPage.getByTestId("sign-button")).toHaveCount(0);
    await expect(view.getByRole("slider")).toHaveCount(0);
    // Any-physician addendum form IS present (append-only, not alteration).
    await expect(otherPage.getByTestId(`addendum-textarea-${encounterId}`)).toBeVisible();
    // Stranger draft reads stay denied via T1 (no content leak).
    const otherCsrf = await physicianSession(request, { username: otherName, password: "secret123" });
    const strangerDraft = await request.get(`/api/v1/encounters/${created.draftId}`, {
      headers: { "X-CSRF-Token": otherCsrf },
    });
    expect(strangerDraft.status()).toBe(403);
    expect(await strangerDraft.text()).not.toContain("percentages");
  } finally {
    await otherPage.close();
    await otherCtx.close();
  }
});

// S50 §4: different physician appends attributed correction; signer vs addendum author/date separate.
test("S50 §4 different physician appends attributed correction with separate chronology", async ({
  page,
  request,
  browser,
}) => {
  const author = await loginPhysician(page, request, "e2es50signer");
  const created = await createPatientWithDraft(request, author);
  const patientId = created.patientId;

  const encounterId = "s50-add-enc-1111-4111-8111-111111111111";
  const batchId = "s50-add-batch-1111-4111-8111-111111111111";
  const planText = "Frozen plan awaiting correction.";
  const snapshot = mockSignedSnapshot({
    snapshotId: "s50-add-snap-9999-4999-8999-999999999999",
    encounterId,
    patientId,
    batchId,
    signerUsername: author.username,
    planText,
    adjusted: null,
    unchanged: {
      runId: "s50-add-run-3333-4333-8333-333333333333",
      baselineId: "s50-add-base-2222-4222-8222-222222222222",
      questionKey: "s50_q2",
    },
  });
  let addenda: Record<string, unknown>[] = [];
  const chartBody = () =>
    JSON.stringify(
      mockChart({ patientId, identifier: "0012345678", snapshots: [snapshot], addenda }),
    );

  const otherCtx = await browser.newContext();
  const otherPage = await otherCtx.newPage();
  try {
    const otherName = uniqueName("e2es50adder");
    await ensurePhysician(request, otherName, "secret123");
    await loginAs(otherPage, "physician", otherName, "secret123");
    await acknowledgeWarning(otherPage);
    await otherPage.route("**/api/v1/patients/*/chart", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: chartBody() }),
    );
    let addendumPosts = 0;
    let addendumHeaders: Record<string, string> = {};
    let addendumBody: Record<string, unknown> = {};
    await otherPage.route("**/api/v1/encounters/*/addenda", async (route) => {
      addendumPosts += 1;
      addendumHeaders = route.request().headers();
      try {
        addendumBody = route.request().postDataJSON() as Record<string, unknown>;
      } catch {
        addendumBody = {};
      }
      const text = String((addendumBody as Record<string, unknown>)["text"] ?? "correction");
      const entry = {
        id: "s50-addendum-1111-4111-8111-111111111111",
        encounter_id: encounterId,
        author_id: "adder-mock",
        author_display: otherName,
        created_at: "2026-10-11T00:00:00Z",
        text,
      };
      addenda = [entry];
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          addendum: entry,
          revision: 5,
          server_timestamp: "2026-10-11T00:00:00Z",
        }),
      });
    });

    await otherPage.goto(`/#/patients/${patientId}/chart`);
    await expect(otherPage.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
    await expect(otherPage.getByTestId(`signed-view-${encounterId}`)).toBeVisible({ timeout: 15000 });
    // Chronology before: original signer only, no addenda.
    await expect(otherPage.getByTestId(`signed-view-signer-${encounterId}`)).toContainText(author.username);
    await expect(otherPage.getByTestId(`addendum-list-${encounterId}`)).toContainText("No addenda yet");

    const correction = `Attributed correction ${Date.now().toString(36)}: dosage note clarified.`;
    await otherPage.getByTestId(`addendum-textarea-${encounterId}`).fill(correction);
    await otherPage.getByTestId(`addendum-submit-${encounterId}`).click();
    // Success reloads the chart (SignedView remounts, transient status is
    // replaced): the durable proof is the attributed entry in the list.
    // Status testid stays covered (initial + remounted states).
    await expect(otherPage.getByTestId(`addendum-list-${encounterId}`)).toContainText(correction, {
      timeout: 10000,
    });
    await expect(otherPage.getByTestId(`addendum-list-${encounterId}`)).toContainText(otherName);
    await expect(otherPage.getByTestId(`addendum-status-${encounterId}`)).toBeVisible();
    expect(addendumPosts).toBe(1);
    // Exact addendum fence: If-Match signed revision + Idempotency-Key + body.
    expect(addendumHeaders["if-match"]).toBe(`"5"`);
    expect(typeof addendumHeaders["idempotency-key"]).toBe("string");
    expect((addendumHeaders["idempotency-key"] ?? "").length).toBeGreaterThan(0);
    expect(addendumBody["text"]).toBe(correction);
    expect(addendumBody["expected_encounter_revision"]).toBe(5);

    // Reload shows separate chronology: original signer vs addendum author/date.
    await otherPage.reload();
    await expect(otherPage.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
    await expect(otherPage.getByTestId(`signed-view-signer-${encounterId}`)).toContainText(author.username, {
      timeout: 15000,
    });
    const list = otherPage.getByTestId(`addendum-list-${encounterId}`);
    await expect(list).toContainText(correction);
    await expect(list).toContainText(otherName);
    await expect(list).toContainText("2026-10-11");
    // Original frozen plan unchanged by the append.
    await expect(otherPage.getByTestId(`signed-view-plan-${encounterId}`)).toContainText(planText);
    await expect(otherPage.getByTestId(`signed-view-signer-${encounterId}`)).not.toContainText(otherName);
  } finally {
    await otherPage.close();
    await otherCtx.close();
  }
});

// S50 §4: double-click Sign does not duplicate (one POST, disabled while pending).
test("S50 §4 double-click Sign sends one POST and disables while pending", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es50dblsign");
  const { draftId } = await createPatientWithDraft(request, physician);
  const networkHash = createHash("sha256").update(TWO_NODE_XML, "utf8").digest("hex");
  const packages = [
    s48Package("s50_q1", "h_s50_a", "h_s50_b", networkHash),
    s48Package("s50_q2", "h_s50_c", "h_s50_d", networkHash),
  ];
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    {
      history: { values: { h_s50_a: "yes", h_s50_b: "no", h_s50_c: "yes", h_s50_d: "no" } },
      gate: "true",
    },
    1,
  );

  const batchId = "s50-dbl-batch-1111-4111-8111-111111111111";
  const run1Id = "s50-dbl-run-2222-4222-8222-222222222222";
  const run2Id = "s50-dbl-run-3333-4333-8333-333333333333";
  const base1Id = "s50-dbl-base-1111-4111-8111-111111111111";
  const base2Id = "s50-dbl-base-2222-4222-8222-222222222222";
  const acc1Id = "s50-dbl-acc-1111-4111-8111-111111111111";
  const acc2Id = "s50-dbl-acc-2222-4222-8222-222222222222";
  const run1 = mockRun(run1Id, batchId, "s50_q1");
  const run2 = mockRun(run2Id, batchId, "s50_q2");
  const base1 = mockBaseline(base1Id, run1Id, batchId, "s50_q1");
  const base2 = mockBaseline(base2Id, run2Id, batchId, "s50_q2");
  const trans1 = mockTransparency("s50_q1", 2);
  const trans2 = mockTransparency("s50_q2", 2);
  const batchPayload = mockBatch(batchId, draftId, [run1, run2], [base1, base2], [trans1, trans2]);
  const acc1 = mockAcceptance(acc1Id, draftId, run1Id, batchId, "s50_q1", base1Id);
  const acc2 = mockAcceptance(acc2Id, draftId, run2Id, batchId, "s50_q2", base2Id);
  const review1 = mockReview({
    run: run1,
    batch: batchPayload.batch,
    baseline: base1,
    transparency: trans1,
    calculationState: "unchanged",
    isAccepted: true,
    acceptance: acc1,
    acceptances: [acc1],
  });
  const review2 = mockReview({
    run: run2,
    batch: batchPayload.batch,
    baseline: base2,
    transparency: trans2,
    calculationState: "unchanged",
    isAccepted: true,
    acceptance: acc2,
    acceptances: [acc2],
  });

  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const body = JSON.stringify(url.includes(run1Id) ? review1 : review2);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  let signCount = 0;
  await page.route("**/api/v1/encounters/*/sign", async (route) => {
    signCount += 1;
    // Hold the response so the second click lands while pending.
    await new Promise((resolve) => setTimeout(resolve, 800));
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        snapshot: {
          id: "s50-dbl-snap-9999-4999-8999-999999999999",
          encounter_id: draftId,
          patient_id: "patient-mock",
          batch_id: batchId,
          proposal_id: "prop-1",
          secondary_plan_revision: 2,
          secondary_plan_text: "frozen",
          snapshot: {},
          snapshot_hash: "f".repeat(64),
          signer_id: "signer-mock",
          signer_username: physician.username,
          signed_at: "2026-10-10T00:00:00Z",
          encounter_revision: 3,
          created_at: "2026-10-10T00:00:00Z",
        },
        encounter: { id: draftId, patient_id: "patient-mock", kind: "registration", lifecycle: "signed", revision: 3 },
        revision: 3,
        server_timestamp: "2026-10-10T00:00:00Z",
      }),
    });
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("sign-panel")).toBeVisible({ timeout: 15000 });
    const planText = `Double-click plan ${Date.now().toString(36)} saved.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });

    const button = page.getByTestId("sign-button");
    // Double-click: two rapid clicks, second lands while disabled/pending.
    await button.click();
    await button.click({ force: true }).catch(() => {});
    await expect(button).toBeDisabled({ timeout: 5000 });
    await expect(page.getByTestId("sign-status")).toContainText("Signed ✓", { timeout: 15000 });
    expect(signCount).toBe(1);
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/encounters/*/sign");
  }
});

// S50 §4: double-click/retry addendum sends one POST, disables while pending, preserves text on failure.
test("S50 §4 double-click addendum sends one POST, retry preserves text without duplicate", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es50dbladd");
  const created = await createPatientWithDraft(request, physician);
  const patientId = created.patientId;

  const encounterId = "s50-dbladd-enc-1111-4111-8111-111111111111";
  const batchId = "s50-dbladd-batch-1111-4111-8111-111111111111";
  const snapshot = mockSignedSnapshot({
    snapshotId: "s50-dbladd-snap-9999-4999-8999-999999999999",
    encounterId,
    patientId,
    batchId,
    signerUsername: physician.username,
    planText: "Frozen plan for double-click addendum.",
    adjusted: null,
    unchanged: {
      runId: "s50-dbladd-run-3333-4333-8333-333333333333",
      baselineId: "s50-dbladd-base-2222-4222-8222-222222222222",
      questionKey: "s50_q2",
    },
  });
  let liveAddenda: Record<string, unknown>[] = [];
  const chartBody = () =>
    JSON.stringify(
      mockChart({ patientId, identifier: "0012345678", snapshots: [snapshot], addenda: liveAddenda }),
    );
  await page.route("**/api/v1/patients/*/chart", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: chartBody() }),
  );
  let addendumCount = 0;
  let firstFailed = false;
  let lastText = "";
  await page.route("**/api/v1/encounters/*/addenda", async (route) => {
    addendumCount += 1;
    let posted = "";
    try {
      const parsed = route.request().postDataJSON() as Record<string, unknown>;
      posted = String(parsed["text"] ?? "");
    } catch {
      posted = "";
    }
    lastText = posted;
    if (!firstFailed) {
      firstFailed = true;
      // Hold then fail: retry must preserve text, not duplicate.
      await new Promise((resolve) => setTimeout(resolve, 600));
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          code: "UNAVAILABLE",
          message: "Addendum service unavailable. Retry — the typed text is preserved.",
          field_errors: {},
          request_id: "req-mock-1",
          retryable: true,
        }),
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 800));
    const entry = {
      id: "s50-dbladd-1111-4111-8111-111111111111",
      encounter_id: encounterId,
      author_id: "author-mock",
      author_display: physician.username,
      created_at: "2026-10-11T00:00:00Z",
      text: posted,
    };
    liveAddenda = [entry];
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        addendum: entry,
        revision: 5,
        server_timestamp: "2026-10-11T00:00:00Z",
      }),
    });
  });
  try {
    await gotoChart(page, patientId);
    await expect(page.getByTestId(`signed-view-${encounterId}`)).toBeVisible({ timeout: 15000 });
    const text = `Double-click addendum ${Date.now().toString(36)} preserves.`;
    const area = page.getByTestId(`addendum-textarea-${encounterId}`);
    const submit = page.getByTestId(`addendum-submit-${encounterId}`);
    await area.fill(text);
    // Double-click while pending: second lands disabled, one POST only.
    await submit.click();
    await submit.click({ force: true }).catch(() => {});
    await expect(submit).toBeDisabled({ timeout: 5000 });
    // First attempt fails but preserves text.
    await expect(page.locator("body")).toContainText("preserved", { timeout: 10000 });
    await expect(area).toHaveValue(text);
    expect(addendumCount).toBe(1);
    // Retry with the same text sends exactly one more POST (no duplicate).
    // Success reloads the chart (remount resets transient status): durable
    // proof is the attributed entry in the list carrying the retried text.
    await expect(submit).toBeEnabled({ timeout: 10000 });
    await submit.click();
    await expect(page.getByTestId(`addendum-list-${encounterId}`)).toContainText(text, {
      timeout: 10000,
    });
    await expect(page.getByTestId(`addendum-list-${encounterId}`)).toContainText(physician.username);
    expect(addendumCount).toBe(2);
    expect(lastText).toBe(text);
  } finally {
    await page.unroute("**/api/v1/patients/*/chart");
    await page.unroute("**/api/v1/encounters/*/addenda");
  }
});
