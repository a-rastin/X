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

/* S59 complete registration and follow-up with CPT review (seam T9 over T1).
 *
 * Backend server truth (read-only): BT/http/test_complete_workflows.py
 * (3 T1/T8 tests green) proves the five-question registration shape
 * (4 ready + 1 false-gate skip), unchanged/adjusted/reset/skip cases,
 * acceptance, sign, snapshots, report data, affected-only regen
 * (1 captured provider call), note noninterference, pending/stale sign
 * denial, stranger-403, restart resume, and follow-up prior preservation.
 * This spec covers the same §§1-4 through the browser; it never re-proves
 * provider call counts (unobservable in e2e) and never claims
 * live-clinical verification.
 *
 * Frontend (read-only): no web/src changes. Encounter wizard sections
 * (diagnosis/panss/cssrs/history/medications/effects headings,
 * meds search plus catalog info, DDI report panel), ProposalReviewPanel
 * (proposal-review, per-question status/freshness/retry/transparency,
 * proposal-ddi, proposal-sign-blocked),
 * CptReviewPanel (per-question cpt panel/toggle/accept/reset/retry),
 * SecondaryPlanPanel (secondary plan panel plus compare proposal/plan/status),
 * SignPanel (sign panel/button/status), SignedView (per-encounter signed
 * view with signer/hash/plan/questions plus addendum list/editor/submit/
 * status), ChartPage (chart heading, create-followup, report link),
 * ReportPage (report heading/research-label/frame/print/status).
 *
 * Constraints (follow signing/proposal-review/probability-review/
 * question-freshness): real auth/draft PATCH/notes/secondary-plan/follow-up
 * via T1; controlled batch/review/cpt/sign/chart/report route fixtures
 * only; no page.route mocks for app shell/auth; no full-page snapshots;
 * synthetic two-node A->B (80/20, 90/10, 30/70) literals with 64-char
 * hashes, fixed UUIDs, fp-mock fingerprints. One T1 real-denial check
 * (forged sign POST) proves server fencing without mocks.
 *
 * Named blockers (not worked around): S39.b approved packages fail closed
 * at projection (unknown_source_path), no bundle activation, no real DDI
 * dataset — every fixture here is synthetic and this spec MUST NOT be read
 * as live-clinical verification.
 */

const NETWORK_HASH = createHash("sha256")
  .update(
    '<BIF VERSION="0.3"><NETWORK><NAME>TwoNode</NAME>' +
      "<PROPERTY>net-prop=kept</PROPERTY>" +
      "<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME>" +
      "<PROPERTY>var-prop-a=kept</PROPERTY></VARIABLE>" +
      "<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>" +
      "<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE>" +
      "<PROPERTY>def-prop-a=kept</PROPERTY></DEFINITION>" +
      "<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>" +
      "<TABLE>0.5 0.5 0.5 0.5</TABLE></DEFINITION>" +
      "</NETWORK></BIF>",
    "utf8",
  )
  .digest("hex");

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

// Five-question registration shape (backend REG_KEYS/REG_FIELDS/REG_GATES):
// s59_r1..r4 applicable, s59_r5 carries a false gate (skipped).
const REG_KEYS = ["s59_r1", "s59_r2", "s59_r3", "s59_r4", "s59_r5"] as const;
const REG_FIELDS: Record<string, [string, string]> = {
  s59_r1: ["h_r1_a", "h_r1_b"],
  s59_r2: ["h_r2_a", "h_r2_b"],
  s59_r3: ["h_r3_a", "h_r3_b"],
  s59_r4: ["h_r4_a", "h_r4_b"],
  s59_r5: ["h_r5_a", "h_r5_b"],
};
const REG_GATES: Record<string, string> = {
  s59_r1: "true",
  s59_r2: "true",
  s59_r3: "true",
  s59_r4: "true",
  s59_r5: "gate == 'false'",
};

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
  await expect(page.getByRole("heading", { name: "Administrator dashboard" })).toBeVisible({
    timeout: 15000,
  });
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
  await page.goto(`/#/encounters/${draftId}#proposal-review`);
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

function s59Package(
  questionKey: string,
  fieldA: string,
  fieldB: string,
  gateExpression = "true",
): Record<string, unknown> {
  return {
    manifest: {
      schema_version: "question-package-v1",
      question_key: questionKey,
      title: `Synthetic ${questionKey}`,
      workflow: "registration",
      version: "s59-test-v1",
      review_status: "draft",
      network_file: "network.xml",
      network_hash: NETWORK_HASH,
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
        expression: gateExpression,
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
      source_hashes: { "network.xml": NETWORK_HASH },
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

function regPackages(): Record<string, unknown>[] {
  return REG_KEYS.map((key) => s59Package(key, REG_FIELDS[key][0], REG_FIELDS[key][1], REG_GATES[key]));
}

function regHistoryValues(): Record<string, string> {
  const values: Record<string, string> = {};
  for (const key of REG_KEYS) {
    values[REG_FIELDS[key][0]] = "yes";
    values[REG_FIELDS[key][1]] = "no";
  }
  return values;
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

function mockSavedInputs(sourceRevision: number, questionKey: string) {
  const fields = REG_FIELDS[questionKey] ?? ["h_x_a", "h_x_b"];
  return [
    {
      node_id: "A",
      patient_type: "tristate",
      status: "observed",
      value: "yes",
      source_path: `synthetic/history/${fields[0]}`,
      source_revision: sourceRevision,
    },
    {
      node_id: "B",
      patient_type: "tristate",
      status: "observed",
      value: "no",
      source_path: `synthetic/history/${fields[1]}`,
      source_revision: sourceRevision,
    },
  ];
}

function mockBaseline(baselineId: string, runId: string, batchId: string, questionKey: string) {
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
    network_version: "s59-test-v1",
    provider_model: "mock-model",
    projection_hash: "c".repeat(64),
    provenance: { question_key: questionKey, provider_model: "mock-model" },
    created_at: "2026-10-10T00:00:00Z",
  };
}

function mockTransparency(questionKey: string, sourceRevision: number) {
  return {
    question_key: questionKey,
    network_version: "s59-test-v1",
    saved_patient_inputs: mockSavedInputs(sourceRevision, questionKey),
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
    projection: { variables: mockSavedInputs(2, questionKey) },
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
    resolved_medications: [
      { catalog_drug_id: "catalog_e2e_alpha", concept_id: "alpha", canonical_name: "E2eAlpha", concept_type: "ingredient" },
    ],
    coverage_unavailable_medications: [
      { catalog_drug_id: "catalog_e2e_gamma", concept_id: "gamma", canonical_name: "E2eGamma", concept_type: "ingredient" },
    ],
    pairs: [
      {
        drug_a: "catalog_e2e_alpha",
        drug_b: "catalog_e2e_beta",
        status: "interaction_found",
        highest_known_severity: "serious",
        has_unknown_severity: false,
        conflicts: ["minor", "serious"],
        evidence: [
          {
            source_severity: "serious",
            direction: "unknown",
            raw_text: "E2eBeta increases the level of E2eAlpha.",
            management: "Avoid combination.",
            source_path: "Alpha.txt",
            span_start: 0,
            span_end: 10,
            checksum: "x",
            source_hash: "y",
          },
        ],
        coverage_basis: { scope: "mock" },
      },
      {
        drug_a: "catalog_e2e_alpha",
        drug_b: "catalog_e2e_gamma",
        status: "coverage_unavailable",
        highest_known_severity: null,
        has_unknown_severity: false,
        conflicts: [],
        evidence: [],
        coverage_basis: { scope: "mock-limited" },
      },
    ],
    limitations: ["mock limited coverage: E2eGamma uncovered"],
    generated_at: "2026-10-10T00:00:00Z",
  };
}

interface BatchSpec {
  run: ReturnType<typeof mockRun>;
  baseline: ReturnType<typeof mockBaseline> | null;
  transparency: ReturnType<typeof mockTransparency> | null;
  jobStatus: "succeeded" | "queued" | "leased" | "failed" | null;
}

function mockBatch(
  batchId: string,
  draftId: string,
  specs: BatchSpec[],
  opts: { stale?: boolean; complete?: boolean } = {},
) {
  const stale = opts.stale ?? false;
  const complete = opts.complete ?? true;
  const runs = specs.map((s) => s.run);
  const baselineEntries = specs.map((s, index) => ({
    question_run_id: s.run.id,
    question_key: s.run.question_key,
    position: index,
    status: s.run.status,
    baseline: s.baseline,
    transparency: s.transparency,
  }));
  const jobs = specs.flatMap((s, index) =>
    s.jobStatus === null
      ? []
      : [
          {
            id: `job-${index}`,
            batch_id: batchId,
            question_run_id: s.run.id,
            job_class: "generation",
            status: s.jobStatus,
            attempt_index: s.jobStatus === "failed" ? 3 : s.jobStatus === "queued" ? 0 : 1,
            max_attempts: 3,
            lease_deadline:
              s.jobStatus === "leased" ? new Date(Date.now() + 60000).toISOString() : null,
            last_heartbeat: s.jobStatus === "leased" ? new Date().toISOString() : null,
            next_eligible_at: null,
            result: s.jobStatus === "failed" ? { error_code: "PROVIDER_TRANSIENT" } : null,
            created_at: "2026-10-10T00:00:00Z",
            updated_at: "2026-10-10T00:00:00Z",
          },
        ],
  );
  const pendingKeys = specs
    .filter((s) => s.baseline === null && s.run.status === "ready")
    .map((s) => s.run.question_key);
  const skipped = specs.flatMap((s, index) =>
    s.run.status === "not_applicable"
      ? [{ question_key: s.run.question_key, position: index, reason: s.run.gate_reason }]
      : [],
  );
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
    baseline: specs[0]?.baseline ?? null,
    transparency: specs[0]?.transparency ?? null,
    baselines: baselineEntries,
    proposal: complete
      ? {
          id: "prop-1",
          batch_id: batchId,
          fingerprint: "fp-mock-1",
          sections: specs.flatMap((s, index) =>
            s.baseline !== null
              ? [
                  {
                    question_run_id: s.run.id,
                    question_key: s.run.question_key,
                    position: index,
                    baseline_id: s.baseline.id,
                    section_text: s.baseline.section_text,
                    posteriors: s.baseline.posteriors,
                    query_nodes: ["A", "B"],
                    effective_hash: s.baseline.effective_hash,
                    template_version: "v1",
                    network_version: "s59-test-v1",
                  },
                ]
              : [],
          ),
          skipped,
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
          skipped,
          coverage_warnings: [],
          ddi_status: "valid",
        }
      : {
          complete: false,
          status: "incomplete",
          pending_question_keys: pendingKeys,
          needs_clarification: [],
          skipped,
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
  const artifact = kind === "reset" ? mockOriginalTables() : mockAdjustedTables();
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
  const reused = opts.reusedFrom !== undefined && opts.reusedFrom !== null;
  return {
    id: calcId,
    question_run_id: runId,
    batch_id: batchId,
    cpt_revision_id: revId,
    cpt_hash: "d".repeat(64),
    network_hash: "a".repeat(64),
    network_version: "s59-test-v1",
    template_version: "v1",
    query_nodes: ["A", "B"],
    posteriors: reused
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

// --- Signed chart mocks (S59 §2: immutable original/final, attribution) ---

interface SnapshotQuestion {
  key: string;
  runId: string;
  baselineId: string;
  mode: "unchanged" | "adjusted" | "reset" | "skipped";
  revId?: string;
  calcId?: string;
  wordingUnchanged?: boolean;
}

function mockSignedSnapshot(opts: {
  snapshotId: string;
  encounterId: string;
  patientId: string;
  batchId: string;
  signerUsername: string;
  planText: string;
  planRevision?: number;
  encounterRevision?: number;
  questions: SnapshotQuestion[];
}) {
  const frozen = opts.questions.map((q) => {
    if (q.mode === "skipped") {
      return {
        question_key: q.key,
        question_run_id: q.runId,
        status: "not_applicable",
        gate_reason: "gate false; expression is not satisfied",
        projection: { variables: [] as unknown[] },
        projection_hash: "c".repeat(64),
        pinned_versions: { network_version: "s59-test-v1", network_hash: NETWORK_HASH },
        original_baseline: null,
        current_tables: null,
        current_cpt_revision_id: null,
        cpt_hash: null,
        current_result: null,
        result_kind: null,
        result_id: null,
        acceptance: null,
        review_revision: 1,
        calculation_state: "not_applicable",
      };
    }
    const base = mockBaseline(q.baselineId, q.runId, opts.batchId, q.key);
    const currentTables = q.mode === "unchanged" ? mockOriginalTables() : mockAdjustedTables();
    const finalTables = q.mode === "reset" ? mockOriginalTables() : currentTables;
    const calc =
      q.mode === "unchanged"
        ? null
        : mockCalcResult(q.calcId ?? "calc-mock", q.runId, opts.batchId, q.revId ?? "rev-mock", q.key, {
            reusedFrom: q.mode === "reset" ? q.baselineId : null,
            wordingUnchanged: q.wordingUnchanged ?? false,
          });
    return {
      question_key: q.key,
      question_run_id: q.runId,
      status: "ready",
      gate_reason: "gate true",
      projection: { variables: mockSavedInputs(2, q.key) },
      projection_hash: "c".repeat(64),
      pinned_versions: { network_version: "s59-test-v1", network_hash: NETWORK_HASH },
      original_baseline: {
        id: base.id,
        validated_tables: mockOriginalTables(),
        posteriors: base.posteriors,
        section_text: base.section_text,
        network_version: "s59-test-v1",
        template_version: "v1",
        prompt_version: "v1",
        effective_hash: "b".repeat(64),
      },
      current_tables: finalTables,
      current_cpt_revision_id: q.mode === "unchanged" ? null : (q.revId ?? "rev-mock"),
      cpt_hash: q.mode === "unchanged" ? null : "d".repeat(64),
      current_result:
        calc === null
          ? null
          : {
              id: calc.id,
              cpt_revision_id: calc.cpt_revision_id,
              posteriors: calc.posteriors,
              section_text: calc.section_text,
              reused_from_baseline_id: calc.reused_from_baseline_id,
            },
      result_kind: q.mode === "unchanged" ? "baseline" : "calculation",
      result_id: q.mode === "unchanged" ? base.id : (calc?.id ?? base.id),
      acceptance: {
        id: `acc-${q.key}-mock`,
        baseline_id: base.id,
        cpt_revision_id: q.mode === "unchanged" ? null : (q.revId ?? "rev-mock"),
        result_kind: q.mode === "unchanged" ? "baseline" : "calculation",
        result_id: q.mode === "unchanged" ? base.id : (calc?.id ?? base.id),
        actor_username: opts.signerUsername,
        created_at: "2026-10-10T00:00:00Z",
      },
      review_revision: q.mode === "reset" ? 3 : q.mode === "unchanged" ? 1 : 2,
      calculation_state: q.mode === "unchanged" ? "unchanged" : "successfully_recalculated",
    };
  });
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
      questions: frozen,
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
    id: snap.encounter_id,
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

function mockReportHtml(opts: {
  encounterId: string;
  signerUsername: string;
  planText: string;
}) {
  const rows = REG_KEYS.map((key) => {
    const indicator =
      key === "s59_r1"
        ? "Unchanged original"
        : key === "s59_r2"
          ? "Physician-adjusted"
          : key === "s59_r3"
            ? "Adjusted then reset (Final equals original)"
            : key === "s59_r4"
              ? "Physician-adjusted"
              : "Not applicable";
    return `<section><h2>${key}</h2><p>${indicator}</p><p>80</p><p>60</p></section>`;
  }).join("\n");
  return (
    `<!doctype html><html><head><meta charset="utf-8"><style>@media print{.encounter{page-break-before:always}}</style></head>` +
    `<body><p>Research prototype — signed records only.</p>` +
    `<h1>Signed chronology</h1><p>Signed encounter ${opts.encounterId}</p>` +
    rows +
    `<p>Accepted by ${opts.signerUsername}</p><p>${NETWORK_HASH}</p>` +
    `<p>${opts.planText}</p><h2>Addenda</h2></body></html>`
  );
}

// S59 §1: registration journey — assessment pages, catalog meds + DDI panel,
// five-question sequential progress, per-question CPT inspection.
test("S59 §1 registration journey renders assessments meds DDI and five-question progress", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es59jrny");
  const { draftId } = await createPatientWithDraft(request, physician);
  const packages = regPackages();
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: regHistoryValues() }, gate: "true" },
    1,
  );

  const batchId = "s59-jrny-batch-1111-4111-8111-111111111111";
  const runIds = [
    "s59-jrny-run-1111-4111-8111-111111111111",
    "s59-jrny-run-2222-4222-8222-222222222222",
    "s59-jrny-run-3333-4333-8333-333333333333",
    "s59-jrny-run-4444-4444-8444-444444444444",
    "s59-jrny-run-5555-4555-8555-555555555555",
  ];
  const runs = REG_KEYS.map((key, i) =>
    key === "s59_r5"
      ? mockRun(runIds[i], batchId, key, {
          status: "not_applicable",
          gateReason: "gate false; expression is not satisfied",
        })
      : mockRun(runIds[i], batchId, key),
  );
  const specs: BatchSpec[] = runs.map((run, i) => {
    const key = REG_KEYS[i];
    if (key === "s59_r5") {
      return { run, baseline: null, transparency: null, jobStatus: null };
    }
    return {
      run,
      baseline: mockBaseline(`s59-jrny-base-${i}-4111-8111-111111111111`, run.id, batchId, key),
      transparency: mockTransparency(key, 2),
      jobStatus: "succeeded",
    };
  });
  const batchPayload = mockBatch(batchId, draftId, specs);
  const reviewForJourney = (index: number) => {
    if (index === 4) {
      return mockReview({
        run: runs[4],
        batch: batchPayload.batch,
        baseline: null,
        transparency: null,
        calculationState: "unchanged",
      });
    }
    return mockReview({
      run: runs[index],
      batch: batchPayload.batch,
      baseline: specs[index].baseline,
      transparency: specs[index].transparency,
      calculationState: "unchanged",
    });
  };
  const skippedReview = reviewForJourney(4);

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
    const index = runIds.findIndex((id) => url.includes(id));
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(reviewForJourney(index < 0 ? 4 : index)),
    });
  });
  try {
    // Pre-seed BEFORE navigation: mount restores tracking via GET with no POST.
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);

    // Each assessment page is its own step, not stacked on the draft.
    for (const [step, heading] of [
      ["diagnosis", "#diagnosis-heading"],
      ["panss", "#panss-heading"],
      ["cssrs", "#cssrs-heading"],
      ["history", "#history-heading"],
      ["effects", "#effects-heading"],
    ] as const) {
      await page.locator(`#wizard-step-${step}`).click();
      await expect(page.locator(heading)).toBeVisible();
    }

    // Catalog medications + DDI panel render (seed-independent structure;
    // selection mechanics are owned by e2e/ddi.spec.ts).
    await page.locator("#wizard-step-medications").click();
    await expect(page.locator("#medications-heading")).toBeVisible();
    await expect(page.locator("#meds-search")).toBeVisible();
    await expect(page.locator("#meds-catalog-info")).toBeVisible();
    await expect(page.locator("#meds-selected-empty")).toBeVisible();
    await expect(page.locator("#ddi-report-panel")).toBeVisible();
    await expect(page.locator("#ddi-pending")).toBeVisible();

    await page.locator("#wizard-step-proposal-review").click();
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });

    // Five-question sequential progress: position-ordered statuses.
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Questions (5)", { timeout: 15000 });
    for (const key of ["s59_r1", "s59_r2", "s59_r3", "s59_r4"]) {
      const el = page.getByTestId(`proposal-status-${key}`);
      await expect(el).toContainText("Completed");
      await expect(el).toContainText("●");
    }
    const skipped = page.getByTestId("proposal-status-s59_r5");
    await expect(skipped).toContainText("Skipped");
    await expect(skipped).toContainText("■");
    await expect(skipped).toContainText("gate false");
    const tops: number[] = [];
    for (const key of REG_KEYS) {
      tops.push(
        await page.getByTestId(`proposal-status-${key}`).evaluate((el) => el.getBoundingClientRect().top),
      );
    }
    for (let i = 1; i < tops.length; i += 1) {
      expect(tops[i]).toBeGreaterThan(tops[i - 1]);
    }

    // Proposal sections + skipped tail + pinned DDI report.
    await expect(panel).toContainText("s59_r1 absent: B no 78.00% outcome.");
    await expect(panel).toContainText("Skipped questions");
    await expect(panel).toContainText("s59_r5");
    const ddi = page.getByTestId("proposal-ddi");
    await expect(ddi).toBeVisible();
    await expect(ddi).toContainText("DDI coverage (pinned report)");
    await expect(ddi).toContainText("ddi-mock/1");
    await expect(ddi).toContainText("Coverage unavailable");
    await expect(ddi).toContainText("catalog_e2e_gamma");

    // All-CPT inspection affordance per question; keyboard opens the first.
    for (const key of REG_KEYS) {
      await expect(page.getByTestId(`cpt-toggle-${key}`)).toBeVisible();
    }
    const toggle = page.getByTestId("cpt-toggle-s59_r1");
    await toggle.focus();
    await expect(toggle).toBeFocused();
    await page.keyboard.press("Enter");
    const cpt = page.getByTestId("cpt-panel-s59_r1");
    await expect(cpt).toContainText("CPT values (exact percentages)", { timeout: 15000 });
    await expect(cpt).toContainText("Current inputs");
    await expect(cpt).toContainText("●");
    const slider = cpt.getByLabel(/A.*yes.*original 20/i);
    await expect(slider).toBeVisible();
    await expect(slider).toBeEnabled();
    await slider.focus();
    await expect(slider).toBeFocused();

    // Transparency shows the five persisted fields for the question.
    await panel.getByRole("button", { name: "Show transparency for s59_r1" }).click();
    const transparency = page.getByTestId("proposal-transparency-s59_r1");
    await expect(transparency).toContainText("s59_r1", { timeout: 15000 });
    await expect(transparency).toContainText("s59-test-v1");

    // Restored tracking issues no new generation run.
    expect(postCount).toBe(0);

    // Both themes where cheap: dark keeps every status readable.
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.getAttribute("data-theme"))).toBe("dark");
    await expect(page.getByTestId("proposal-status-s59_r1")).toContainText("Completed");
    await expect(page.getByTestId("proposal-status-s59_r5")).toContainText("Skipped");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S59 §§1-2: unchanged accept, real slider adjustment + comparison + accept,
// real reset (final==original, history retained) + accept, wording-unchanged
// accept; skipped gate carries no acceptance.
test("S59 §§1-2 four cases accept exact current with comparison and retained history", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es59case");
  const { draftId } = await createPatientWithDraft(request, physician);
  const packages = regPackages();
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: regHistoryValues() }, gate: "true" },
    1,
  );

  const batchId = "s59-case-batch-1111-4111-8111-111111111111";
  const runIds = [
    "s59-case-run-1111-4111-8111-111111111111",
    "s59-case-run-2222-4222-8222-222222222222",
    "s59-case-run-3333-4333-8333-333333333333",
    "s59-case-run-4444-4444-8444-444444444444",
    "s59-case-run-5555-4555-8555-555555555555",
  ];
  const baseIds = [
    "s59-case-base-1111-4111-8111-111111111111",
    "s59-case-base-2222-4222-8222-222222222222",
    "s59-case-base-3333-4333-8333-333333333333",
    "s59-case-base-4444-4444-8444-444444444444",
  ];
  const runs = REG_KEYS.map((key, i) =>
    key === "s59_r5"
      ? mockRun(runIds[i], batchId, key, {
          status: "not_applicable",
          gateReason: "gate false; expression is not satisfied",
        })
      : mockRun(runIds[i], batchId, key),
  );
  const bases = [0, 1, 2, 3].map((i) => mockBaseline(baseIds[i], runIds[i], batchId, REG_KEYS[i]));
  const transes = ["s59_r1", "s59_r2", "s59_r3", "s59_r4"].map((key) => mockTransparency(key, 2));
  const specs: BatchSpec[] = runs.map((run, i) =>
    REG_KEYS[i] === "s59_r5"
      ? { run, baseline: null, transparency: null, jobStatus: null }
      : { run, baseline: bases[i], transparency: transes[i], jobStatus: "succeeded" },
  );
  const batchPayload = mockBatch(batchId, draftId, specs);

  // r2 starts clean; a real keyboard slider edit adjusts it.
  const revR2 = "s59-case-rev-2222-4222-8222-222222222222";
  const calcR2 = "s59-case-calc-2222-4222-8222-222222222222";
  const revisionR2 = mockRevision(revR2, runIds[1], batchId, 1);
  const calcResultR2 = mockCalcResult(calcR2, runIds[1], batchId, revR2, "s59_r2", {
    wordingUnchanged: false,
  });
  let adjustedR2 = false;
  const reviewR2clean = () =>
    mockReview({
      run: runs[1],
      batch: batchPayload.batch,
      baseline: bases[1],
      transparency: transes[1],
      calculationState: "unchanged",
    });
  const reviewR2adjusted = () =>
    mockReview({
      run: runs[1],
      batch: batchPayload.batch,
      baseline: bases[1],
      transparency: transes[1],
      calculationState: "successfully_recalculated",
      revisions: [revisionR2],
      calcResult: calcResultR2,
      displayedResult: calcResultR2,
      reviewRevision: 2,
      currentRevId: revR2,
      displayedRevId: revR2,
      currentMatches: true,
      cptHash: "d".repeat(64),
    });

  // r3 starts adjusted; a real Reset returns it to original with history.
  const revR3a = "s59-case-rev-3333-4333-8333-333333333333";
  const revR3b = "s59-case-revr-3333-4333-8333-333333333333";
  const calcR3reuse = "s59-case-calcr-3333-4333-8333-333333333333";
  const revisionR3a = mockRevision(revR3a, runIds[2], batchId, 1);
  const calcR3a = mockCalcResult("s59-case-calc-3333-4333-8333-333333333333", runIds[2], batchId, revR3a, "s59_r3", {
    wordingUnchanged: false,
  });
  const revisionR3b = mockRevision(revR3b, runIds[2], batchId, 2, "reset");
  revisionR3b.parent_revision_id = revR3a;
  const calcResultR3reuse = mockCalcResult(calcR3reuse, runIds[2], batchId, revR3b, "s59_r3", {
    reusedFrom: baseIds[2],
    wordingUnchanged: true,
  });
  let resetR3 = false;
  const reviewR3adjusted = () =>
    mockReview({
      run: runs[2],
      batch: batchPayload.batch,
      baseline: bases[2],
      transparency: transes[2],
      calculationState: "successfully_recalculated",
      revisions: [revisionR3a],
      calcResult: calcR3a,
      displayedResult: calcR3a,
      reviewRevision: 2,
      currentRevId: revR3a,
      displayedRevId: revR3a,
      currentMatches: true,
      cptHash: "d".repeat(64),
    });
  const reviewR3reset = () =>
    mockReview({
      run: runs[2],
      batch: batchPayload.batch,
      baseline: bases[2],
      transparency: transes[2],
      calculationState: "successfully_recalculated",
      revisions: [revisionR3a, revisionR3b],
      calcResult: calcResultR3reuse,
      displayedResult: calcResultR3reuse,
      reviewRevision: 3,
      currentRevId: revR3b,
      displayedRevId: revR3b,
      currentMatches: true,
      cptHash: "d".repeat(64),
    });

  // r4 adjusted with unchanged recommendation wording.
  const revR4 = "s59-case-rev-4444-4444-8444-444444444444";
  const calcR4 = "s59-case-calc-4444-4444-8444-444444444444";
  const revisionR4 = mockRevision(revR4, runIds[3], batchId, 1);
  const calcResultR4 = mockCalcResult(calcR4, runIds[3], batchId, revR4, "s59_r4", {
    wordingUnchanged: true,
  });
  const reviewR4 = () =>
    mockReview({
      run: runs[3],
      batch: batchPayload.batch,
      baseline: bases[3],
      transparency: transes[3],
      calculationState: "successfully_recalculated",
      revisions: [revisionR4],
      calcResult: calcResultR4,
      displayedResult: calcResultR4,
      reviewRevision: 2,
      currentRevId: revR4,
      displayedRevId: revR4,
      currentMatches: true,
      cptHash: "d".repeat(64),
    });

  const accepted = new Set<string>();
  const accIds = [
    "s59-case-acc-1111-4111-8111-111111111111",
    "s59-case-acc-2222-4222-8222-222222222222",
    "s59-case-acc-3333-4333-8333-333333333333",
    "s59-case-acc-4444-4444-8444-444444444444",
  ];
  const reviewFor = (index: number) => {
    if (index === 0) {
      const on = accepted.has(runIds[0]);
      return mockReview({
        run: runs[0],
        batch: batchPayload.batch,
        baseline: bases[0],
        transparency: transes[0],
        calculationState: "unchanged",
        isAccepted: on,
        acceptance: on ? mockAcceptance(accIds[0], draftId, runIds[0], batchId, "s59_r1", baseIds[0]) : null,
        acceptances: on ? [mockAcceptance(accIds[0], draftId, runIds[0], batchId, "s59_r1", baseIds[0])] : [],
      });
    }
    if (index === 1) {
      const base = adjustedR2 ? reviewR2adjusted() : reviewR2clean();
      const on = accepted.has(runIds[1]);
      return {
        ...base,
        is_accepted: on,
        acceptance: on
          ? mockAcceptance(accIds[1], draftId, runIds[1], batchId, "s59_r2", baseIds[1], {
              cptRevisionId: revR2,
              resultKind: "calculation",
              resultId: calcR2,
            })
          : null,
        acceptances: on
          ? [
              mockAcceptance(accIds[1], draftId, runIds[1], batchId, "s59_r2", baseIds[1], {
                cptRevisionId: revR2,
                resultKind: "calculation",
                resultId: calcR2,
              }),
            ]
          : [],
      };
    }
    if (index === 2) {
      const base = resetR3 ? reviewR3reset() : reviewR3adjusted();
      const on = accepted.has(runIds[2]);
      return {
        ...base,
        is_accepted: on,
        acceptance: on
          ? mockAcceptance(accIds[2], draftId, runIds[2], batchId, "s59_r3", baseIds[2], {
              cptRevisionId: revR3b,
              resultKind: "calculation",
              resultId: calcR3reuse,
            })
          : null,
        acceptances: on
          ? [
              mockAcceptance(accIds[2], draftId, runIds[2], batchId, "s59_r3", baseIds[2], {
                cptRevisionId: revR3b,
                resultKind: "calculation",
                resultId: calcR3reuse,
              }),
            ]
          : [],
      };
    }
    const on = accepted.has(runIds[3]);
    const base = reviewR4();
    return {
      ...base,
      is_accepted: on,
      acceptance: on
        ? mockAcceptance(accIds[3], draftId, runIds[3], batchId, "s59_r4", baseIds[3], {
            cptRevisionId: revR4,
            resultKind: "calculation",
            resultId: calcR4,
          })
        : null,
      acceptances: on
        ? [
            mockAcceptance(accIds[3], draftId, runIds[3], batchId, "s59_r4", baseIds[3], {
              cptRevisionId: revR4,
              resultKind: "calculation",
              resultId: calcR4,
            }),
          ]
        : [],
    };
  };
  const skippedReview = mockReview({
    run: runs[4],
    batch: batchPayload.batch,
    baseline: null,
    transparency: null,
    calculationState: "unchanged",
  });

  const adjustBodies: Record<string, unknown>[] = [];
  const acceptBodies: Record<string, unknown>[] = [];
  const acceptKeys: Record<string, string> = {};
  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const index = runIds.findIndex((id) => url.includes(id));
    const body = JSON.stringify(index >= 0 && index < 4 ? reviewFor(index) : skippedReview);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  await page.route("**/api/v1/question-runs/*/cpt-adjustments", async (route) => {
    const headers = route.request().headers();
    let body: Record<string, unknown> = {};
    try {
      body = route.request().postDataJSON() as Record<string, unknown>;
    } catch {
      body = {};
    }
    adjustBodies.push(body);
    expect(headers["if-match"]).toBe(`"${String(body["expected_review_revision"])}"`);
    expect((headers["idempotency-key"] ?? "").length).toBeGreaterThan(0);
    adjustedR2 = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        revision: revisionR2,
        review_state: { review_revision: 2, current_revision_id: revR2 },
        current_cpt_revision_id: revR2,
        review_revision: 2,
        cpt_hash: "d".repeat(64),
        current_tables: mockAdjustedTables(),
      }),
    });
  });
  await page.route("**/api/v1/question-runs/*/reset", async (route) => {
    const headers = route.request().headers();
    let body: Record<string, unknown> = {};
    try {
      body = route.request().postDataJSON() as Record<string, unknown>;
    } catch {
      body = {};
    }
    expect(body["expected_review_revision"]).toBe(2);
    expect(headers["if-match"]).toBe('"2"');
    expect((headers["idempotency-key"] ?? "").length).toBeGreaterThan(0);
    resetR3 = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        revision: revisionR3b,
        review_state: { review_revision: 3, current_revision_id: revR3b },
        current_cpt_revision_id: revR3b,
        review_revision: 3,
        cpt_hash: "d".repeat(64),
        current_tables: mockOriginalTables(),
        calculation_result: calcResultR3reuse,
        reused_from_baseline_id: baseIds[2],
      }),
    });
  });
  await page.route("**/api/v1/question-runs/*/acceptance", async (route) => {
    const url = route.request().url();
    const headers = route.request().headers();
    let body: Record<string, unknown> = {};
    try {
      body = route.request().postDataJSON() as Record<string, unknown>;
    } catch {
      body = {};
    }
    const index = runIds.findIndex((id) => url.includes(id));
    acceptBodies.push(body);
    acceptKeys[runIds[index]] = REG_KEYS[index];
    expect((headers["idempotency-key"] ?? "").length).toBeGreaterThan(0);
    expect(headers["if-match"]).toBe(`"${String(body["expected_review_revision"])}"`);
    accepted.add(runIds[index]);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        acceptance: reviewFor(index).acceptance,
        is_accepted: true,
        current_cpt_revision_id: reviewFor(index).current_cpt_revision_id,
        review_revision: reviewFor(index).review_revision,
        cpt_hash: "d".repeat(64),
      }),
    });
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s59_r1")).toContainText("Completed", {
      timeout: 15000,
    });

    // Case 1 — unchanged: exact original baseline accepted as-is.
    await page.getByTestId("cpt-toggle-s59_r1").click();
    const cpt1 = page.getByTestId("cpt-panel-s59_r1");
    await expect(cpt1).toContainText("CPT values (exact percentages)", { timeout: 15000 });
    await expect(cpt1).toContainText("Accept the exact current original baseline");
    await expect(cpt1).toContainText("80");
    await cpt1.getByTestId("cpt-accept-s59_r1").click();
    await expect(cpt1).toContainText("Accepted ✓", { timeout: 15000 });
    await expect(cpt1).toContainText(accIds[0]);

    // Case 2 — adjusted: real keyboard slider edit, comparison, exact accept.
    await page.getByTestId("cpt-toggle-s59_r2").click();
    const cpt2 = page.getByTestId("cpt-panel-s59_r2");
    await expect(cpt2).toContainText("CPT values (exact percentages)", { timeout: 15000 });
    const slider = cpt2.getByLabel(/A.*yes.*original 20/i);
    await slider.focus();
    await expect(slider).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(slider).toBeEnabled();
    await slider.fill("40");
    await expect(cpt2).toContainText("Saved", { timeout: 15000 });
    await expect(cpt2).toContainText("40", { timeout: 15000 });
    await expect(cpt2).toContainText("direct");
    await expect(cpt2).toContainText("redistributed");
    await expect(cpt2).toContainText("Original result");
    await expect(cpt2).toContainText("Latest successful result");
    await expect(cpt2).toContainText("s59_r2 present: B yes 34.00% outcome.");
    await expect(cpt2).toContainText("Accept the exact current adjusted revision");
    await cpt2.getByTestId("cpt-accept-s59_r2").click();
    await expect(cpt2).toContainText("Accepted ✓", { timeout: 15000 });
    await expect(cpt2).toContainText(accIds[1]);
    expect(adjustBodies.length).toBeGreaterThanOrEqual(1);
    const lastAdjust = adjustBodies[adjustBodies.length - 1];
    expect(lastAdjust["node_id"]).toBe("A");
    expect(lastAdjust["state"]).toBe("yes");
    expect(lastAdjust["target_percentage"]).toBe("40");
    expect(lastAdjust["expected_review_revision"]).toBe(1);

    // Case 3 — adjusted then reset: real Reset, final==original, history kept.
    await page.getByTestId("cpt-toggle-s59_r3").click();
    const cpt3 = page.getByTestId("cpt-panel-s59_r3");
    await expect(cpt3).toContainText("60", { timeout: 15000 });
    await expect(cpt3).toContainText("1 revisions");
    await cpt3.getByTestId("cpt-reset-s59_r3").click();
    await expect(cpt3).toContainText("80", { timeout: 15000 });
    await expect(cpt3).toContainText("2 revisions");
    await expect(cpt3).toContainText("verified reuse");
    await expect(cpt3).toContainText(baseIds[2]);
    await expect(cpt3).toContainText("Reset creates an audited baseline-equal revision");
    await cpt3.getByTestId("cpt-accept-s59_r3").click();
    await expect(cpt3).toContainText("Accepted ✓", { timeout: 15000 });
    await expect(cpt3).toContainText(accIds[2]);

    // Case 4 — unchanged recommendation: adjusted CPTs, identical wording.
    await page.getByTestId("cpt-toggle-s59_r4").click();
    const cpt4 = page.getByTestId("cpt-panel-s59_r4");
    await expect(cpt4).toContainText("60", { timeout: 15000 });
    await expect(cpt4).toContainText("Recommendation wording is unchanged");
    await cpt4.getByTestId("cpt-accept-s59_r4").click();
    await expect(cpt4).toContainText("Accepted ✓", { timeout: 15000 });
    await expect(cpt4).toContainText(accIds[3]);

    // Exact acceptance references per question.
    expect(acceptBodies).toHaveLength(4);
    const byKey = new Map(acceptBodies.map((b) => [acceptKeys[b["result_id"] as string] ?? "", b]));
    void byKey;
    const findBy = (baselineId: string) => acceptBodies.find((b) => b["baseline_id"] === baselineId) as Record<string, unknown>;
    const a1 = findBy(baseIds[0]);
    expect(a1["current_cpt_revision_id"]).toBeNull();
    expect(a1["result_id"]).toBe(baseIds[0]);
    expect(a1["input_hash"]).toBe("fp-mock-1");
    expect(a1["expected_review_revision"]).toBe(1);
    const a2 = findBy(baseIds[1]);
    expect(a2["current_cpt_revision_id"]).toBe(revR2);
    expect(a2["result_id"]).toBe(calcR2);
    expect(a2["cpt_hash"]).toBe("d".repeat(64));
    expect(a2["input_hash"]).toBe("fp-mock-1");
    expect(a2["expected_review_revision"]).toBe(2);
    const a3 = findBy(baseIds[2]);
    expect(a3["current_cpt_revision_id"]).toBe(revR3b);
    expect(a3["result_id"]).toBe(calcR3reuse);
    expect(a3["expected_review_revision"]).toBe(3);
    const a4 = findBy(baseIds[3]);
    expect(a4["current_cpt_revision_id"]).toBe(revR4);
    expect(a4["result_id"]).toBe(calcR4);
    expect(a4["expected_review_revision"]).toBe(2);

    // Skipped gate carries no acceptance affordance hassle: gate text only.
    await expect(page.getByTestId("proposal-status-s59_r5")).toContainText("Skipped");

    // Resume: reload shows the ready SignPanel with an enabled explicit Sign.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByTestId("sign-panel")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("sign-panel")).toContainText("Proposal complete and current");
    await expect(page.getByTestId("sign-button")).toBeEnabled();
    await expect(page.getByTestId("proposal-sign-blocked")).toHaveCount(0);
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/question-runs/*/cpt-adjustments");
    await page.unroute("**/api/v1/question-runs/*/reset");
    await page.unroute("**/api/v1/question-runs/*/acceptance");
  }
});

// S59 §2: explicit Sign freezes the five-question snapshot; the signed chart
// shows original + final CPTs/results with versions/inputs/attribution, and
// the admin printable report shows every question with adjustment
// indicators, versions, and attribution.
test("S59 §2 sign freezes snapshot and admin report shows indicators versions attribution", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es59sign");
  const created = await createPatientWithDraft(request, physician);
  const { patientId, draftId } = created;
  const packages = regPackages();
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: regHistoryValues() }, gate: "true" },
    1,
  );

  const batchId = "s59-sign-batch-1111-4111-8111-111111111111";
  const runIds = [
    "s59-sign-run-1111-4111-8111-111111111111",
    "s59-sign-run-2222-4222-8222-222222222222",
    "s59-sign-run-3333-4333-8333-333333333333",
    "s59-sign-run-4444-4444-8444-444444444444",
    "s59-sign-run-5555-4555-8555-555555555555",
  ];
  const baseIds = [
    "s59-sign-base-1111-4111-8111-111111111111",
    "s59-sign-base-2222-4222-8222-222222222222",
    "s59-sign-base-3333-4333-8333-333333333333",
    "s59-sign-base-4444-4444-8444-444444444444",
  ];
  const runs = REG_KEYS.map((key, i) =>
    key === "s59_r5"
      ? mockRun(runIds[i], batchId, key, {
          status: "not_applicable",
          gateReason: "gate false; expression is not satisfied",
        })
      : mockRun(runIds[i], batchId, key),
  );
  const bases = [0, 1, 2, 3].map((i) => mockBaseline(baseIds[i], runIds[i], batchId, REG_KEYS[i]));
  const transes = ["s59_r1", "s59_r2", "s59_r3", "s59_r4"].map((key) => mockTransparency(key, 2));
  const revIds = [
    "s59-sign-rev-2222-4222-8222-222222222222",
    "s59-sign-revr-3333-4333-8333-333333333333",
    "s59-sign-rev-4444-4444-8444-444444444444",
  ];
  const calcIds = [
    "s59-sign-calc-2222-4222-8222-222222222222",
    "s59-sign-calcr-3333-4333-8333-333333333333",
    "s59-sign-calc-4444-4444-8444-444444444444",
  ];
  const specs: BatchSpec[] = runs.map((run, i) =>
    REG_KEYS[i] === "s59_r5"
      ? { run, baseline: null, transparency: null, jobStatus: null }
      : { run, baseline: bases[i], transparency: transes[i], jobStatus: "succeeded" },
  );
  const batchPayload = mockBatch(batchId, draftId, specs);

  const accIds = [
    "s59-sign-acc-1111-4111-8111-111111111111",
    "s59-sign-acc-2222-4222-8222-222222222222",
    "s59-sign-acc-3333-4333-8333-333333333333",
    "s59-sign-acc-4444-4444-8444-444444444444",
  ];
  const expectedRevs = [1, 2, 3, 2];
  const reviewFor = (index: number) => {
    if (index >= 4) {
      return mockReview({
        run: runs[4],
        batch: batchPayload.batch,
        baseline: null,
        transparency: null,
        calculationState: "unchanged",
      });
    }
    const revisions =
      index === 0
        ? []
        : index === 2
          ? [mockRevision("s59-sign-reva-3333-4333-8333-333333333333", runIds[2], batchId, 1), mockRevision(revIds[1], runIds[2], batchId, 2, "reset")]
          : [mockRevision(revIds[index === 1 ? 0 : 2], runIds[index], batchId, 1)];
    if (index === 2) {
      revisions[1].parent_revision_id = revisions[0].id;
    }
    const calc =
      index === 0
        ? null
        : mockCalcResult(calcIds[index - 1], runIds[index], batchId, revIds[index - 1], REG_KEYS[index], {
            reusedFrom: index === 2 ? baseIds[2] : null,
            wordingUnchanged: index !== 1,
          });
    const acc = mockAcceptance(accIds[index], draftId, runIds[index], batchId, REG_KEYS[index], baseIds[index], {
      cptRevisionId: index === 0 ? null : revIds[index - 1],
      resultKind: index === 0 ? "baseline" : "calculation",
      resultId: index === 0 ? baseIds[index] : calcIds[index - 1],
    });
    return mockReview({
      run: runs[index],
      batch: batchPayload.batch,
      baseline: bases[index],
      transparency: transes[index],
      calculationState: index === 0 ? "unchanged" : "successfully_recalculated",
      revisions,
      calcResult: calc,
      displayedResult: calc,
      reviewRevision: expectedRevs[index],
      currentRevId: index === 0 ? null : revIds[index - 1],
      displayedRevId: index === 0 ? null : revIds[index - 1],
      currentMatches: true,
      cptHash: index === 0 ? null : "d".repeat(64),
      isAccepted: true,
      acceptance: acc,
      acceptances: [acc],
    });
  };

  const encounterId = "s59-sign-enc-1111-4111-8111-111111111111";
  const snapshotId = "s59-sign-snap-9999-4999-8999-999999999999";
  const planText = "Secondary plan with reviewed probabilities for all five questions.";
  const snapshot = mockSignedSnapshot({
    snapshotId,
    encounterId,
    patientId,
    batchId,
    signerUsername: physician.username,
    planText,
    questions: [
      { key: "s59_r1", runId: runIds[0], baselineId: baseIds[0], mode: "unchanged" },
      { key: "s59_r2", runId: runIds[1], baselineId: baseIds[1], mode: "adjusted", revId: revIds[0], calcId: calcIds[0], wordingUnchanged: false },
      { key: "s59_r3", runId: runIds[2], baselineId: baseIds[2], mode: "reset", revId: revIds[1], calcId: calcIds[1], wordingUnchanged: true },
      { key: "s59_r4", runId: runIds[3], baselineId: baseIds[3], mode: "adjusted", revId: revIds[2], calcId: calcIds[2], wordingUnchanged: true },
      { key: "s59_r5", runId: runIds[4], baselineId: "unused", mode: "skipped" },
    ],
  });
  const chart = mockChart({ patientId, identifier: "0012345678", snapshots: [snapshot], addenda: [] });

  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const index = runIds.findIndex((id) => url.includes(id));
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(reviewFor(index < 0 ? 4 : index)),
    });
  });
  let signCount = 0;
  let signHeaders: Record<string, string> = {};
  let signBody: Record<string, unknown> = {};
  await page.route("**/api/v1/encounters/*/sign", async (route) => {
    signCount += 1;
    signHeaders = route.request().headers();
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
          secondary_plan_text: planText,
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
  await page.route("**/api/v1/patients/*/chart", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(chart) }),
  );
  await page.route("**/api/v1/patients/*/report", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: mockReportHtml({ encounterId, signerUsername: physician.username, planText }),
    }),
  );
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s59_r5")).toContainText("Skipped", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-sign-blocked")).toHaveCount(0);
    await expect(page.getByTestId("sign-panel")).toContainText("Proposal complete and current");
    await expect(page.getByTestId("sign-button")).toBeEnabled();

    // Secondary plan is a separately revisioned real edit beside the proposal.
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });
    await expect(page.getByTestId("plan-compare-status")).toContainText("●");

    await page.getByTestId("sign-button").click();
    await expect(page.getByTestId("sign-status")).toContainText("Signed ✓", { timeout: 15000 });
    await expect(page.getByTestId("sign-status")).toContainText(snapshotId);
    await expect(page.getByTestId("sign-status")).toContainText("f".repeat(8));

    // Exact sign references: one POST with If-Match + Idempotency-Key + body.
    expect(signCount).toBe(1);
    expect(signHeaders["if-match"]).toBe('"2"');
    expect(signHeaders["if-match"]).toBe(`"${String(signBody["expected_encounter_revision"])}"`);
    expect((signHeaders["idempotency-key"] ?? "").length).toBeGreaterThan(0);
    expect(signBody["expected_encounter_revision"]).toBe(2);
    expect(signBody["expected_plan_revision"]).toBe(2);
    expect(signBody["batch_id"]).toBe(batchId);
    const acceptances = signBody["acceptances"] as Record<string, unknown>[];
    expect(acceptances).toHaveLength(4);
    const byRun = new Map(acceptances.map((a) => [a["question_run_id"], a]));
    for (let i = 0; i < 4; i += 1) {
      expect(byRun.get(runIds[i])?.["acceptance_id"]).toBe(accIds[i]);
      expect(byRun.get(runIds[i])?.["expected_review_revision"]).toBe(expectedRevs[i]);
    }

    // Signed chart: original + final CPTs/results, versions, inputs, attribution.
    await gotoChart(page, patientId);
    const view = page.getByTestId(`signed-view-${encounterId}`);
    await expect(view).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId(`signed-view-signer-${encounterId}`)).toContainText(physician.username);
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText(snapshotId);
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText("f".repeat(8));
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText(batchId);
    await expect(page.getByTestId(`signed-view-plan-${encounterId}`)).toContainText(planText);
    await expect(view.getByRole("textbox")).toHaveCount(1);
    const questions = page.getByTestId(`signed-view-questions-${encounterId}`);
    for (const key of REG_KEYS) {
      await expect(questions).toContainText(key);
    }
    await expect(questions).toContainText("Accepted unchanged");
    await expect(questions).toContainText("Adjusted then accepted");
    await expect(questions).toContainText("■ Skipped");
    await expect(questions).toContainText("gate false");
    await expect(questions).toContainText("Original probabilities");
    await expect(questions).toContainText("Final accepted probabilities");
    await expect(questions).toContainText("80");
    await expect(questions).toContainText("60");
    await expect(questions).toContainText("adjusted then reset — final equality shown, history retained above");
    await expect(questions).toContainText("s59-test-v1");
    await expect(questions).toContainText("Accepted by");
    await expect(questions).toContainText(physician.username);
    await expect(view.getByRole("slider")).toHaveCount(0);

    // Admin printable report: every question with indicators and attribution.
    await signOut(page);
    await loginAdmin(page);
    await expect(page.getByTestId("chart-report-link")).toHaveCount(0); // not on dashboard
    await page.goto(`/#/patients/${patientId}/report`);
    await expect(page.getByTestId("report-heading")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("report-research-label")).toContainText("research prototype");
    await expect(page.getByTestId("report-status")).toContainText("Signed-only printable report loaded.", {
      timeout: 15000,
    });
    await expect(page.getByTestId("report-frame")).toBeVisible();
    const frame = page.frameLocator("#report-frame");
    const bodyText = (await frame.locator("body").textContent({ timeout: 15000 })) ?? "";
    for (const key of REG_KEYS) {
      expect(bodyText, key).toContain(key);
    }
    expect(bodyText).toContain("Unchanged original");
    expect(bodyText).toContain("Physician-adjusted");
    expect(bodyText).toContain("Adjusted then reset");
    expect(bodyText).toContain("Final equals original");
    expect(bodyText).toContain("Not applicable");
    expect(bodyText).toContain("Accepted by");
    expect(bodyText).toContain(physician.username);
    expect(bodyText).toContain(NETWORK_HASH);
    expect(bodyText).toContain("80");
    expect(bodyText).toContain("60");
    expect(bodyText).toContain(planText);
  } finally {
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/encounters/*/sign");
    await page.unroute("**/api/v1/patients/*/chart");
    await page.unroute("**/api/v1/patients/*/report");
  }
});

// S59 §3: relevant change stales only the affected question (◍ vs ●),
// regen establishes a new original without transferring the old adjustment,
// and the regenerated original needs explicit re-review before sign.
test("S59 §3 affected-only stale then regen needs re-review without adjustment transfer", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es59stale");
  const { draftId } = await createPatientWithDraft(request, physician);
  const packages = regPackages();
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: regHistoryValues() }, gate: "true" },
    1,
  );

  const batchId = "s59-stale-batch-1111-4111-8111-111111111111";
  const runIds = [
    "s59-stale-run-1111-4111-8111-111111111111",
    "s59-stale-run-2222-4222-8222-222222222222",
    "s59-stale-run-3333-4333-8333-333333333333",
    "s59-stale-run-4444-4444-8444-444444444444",
    "s59-stale-run-5555-4555-8555-555555555555",
  ];
  const baseIds = [
    "s59-stale-base-1111-4111-8111-111111111111",
    "s59-stale-base-2222-4222-8222-222222222222",
    "s59-stale-base-3333-4333-8333-333333333333",
    "s59-stale-base-4444-4444-8444-444444444444",
  ];
  const runs = REG_KEYS.map((key, i) =>
    key === "s59_r5"
      ? mockRun(runIds[i], batchId, key, {
          status: "not_applicable",
          gateReason: "gate false; expression is not satisfied",
        })
      : mockRun(runIds[i], batchId, key),
  );
  const bases = [0, 1, 2, 3].map((i) => mockBaseline(baseIds[i], runIds[i], batchId, REG_KEYS[i]));
  const transes = ["s59_r1", "s59_r2", "s59_r3", "s59_r4"].map((key) => mockTransparency(key, 2));
  const staleSpecs: BatchSpec[] = runs.map((run, i) =>
    REG_KEYS[i] === "s59_r5"
      ? { run, baseline: null, transparency: null, jobStatus: null }
      : { run, baseline: bases[i], transparency: transes[i], jobStatus: "succeeded" },
  );
  const stalePayload = mockBatch(batchId, draftId, staleSpecs, { stale: true });

  // r2 was adjusted before going stale; the rest are accepted and current.
  const revR2old = "s59-stale-rev-2222-4222-8222-222222222222";
  const calcR2old = "s59-stale-calc-2222-4222-8222-222222222222";
  const revisionR2old = mockRevision(revR2old, runIds[1], batchId, 1);
  const calcResultR2old = mockCalcResult(calcR2old, runIds[1], batchId, revR2old, "s59_r2", {
    wordingUnchanged: false,
  });
  const accR1 = mockAcceptance(
    "s59-stale-acc-1111-4111-8111-111111111111",
    draftId,
    runIds[0],
    batchId,
    "s59_r1",
    baseIds[0],
  );
  const staleReviewFor = (index: number) => {
    if (index === 1) {
      return mockReview({
        run: runs[1],
        batch: stalePayload.batch,
        baseline: bases[1],
        transparency: transes[1],
        calculationState: "successfully_recalculated",
        stale: true,
        revisions: [revisionR2old],
        calcResult: calcResultR2old,
        displayedResult: calcResultR2old,
        reviewRevision: 2,
        currentRevId: revR2old,
        displayedRevId: revR2old,
        currentMatches: true,
        cptHash: "d".repeat(64),
      });
    }
    if (index === 4) {
      return mockReview({
        run: runs[4],
        batch: stalePayload.batch,
        baseline: null,
        transparency: null,
        calculationState: "unchanged",
      });
    }
    const acc = index === 0 ? accR1 : mockAcceptance(
      `s59-stale-acc-${index}-4111-8111-111111111111`,
      draftId,
      runIds[index],
      batchId,
      REG_KEYS[index],
      baseIds[index],
    );
    return mockReview({
      run: runs[index],
      batch: stalePayload.batch,
      baseline: bases[index],
      transparency: transes[index],
      calculationState: "unchanged",
      stale: false,
      isAccepted: true,
      acceptance: acc,
      acceptances: [acc],
    });
  };

  // Regen: new batch, new baselines; r2 carries NO adjustment forward.
  const batchId2 = "s59-regen-batch-2222-4222-8222-222222222222";
  const runIds2 = [
    "s59-regen-run-1111-4111-8111-111111111111",
    "s59-regen-run-2222-4222-8222-222222222222",
    "s59-regen-run-3333-4333-8333-333333333333",
    "s59-regen-run-4444-4444-8444-444444444444",
    "s59-regen-run-5555-4555-8555-555555555555",
  ];
  const baseIds2 = [
    "s59-regen-base-1111-4111-8111-111111111111",
    "s59-regen-base-2222-4222-8222-222222222222",
    "s59-regen-base-3333-4333-8333-333333333333",
    "s59-regen-base-4444-4444-8444-444444444444",
  ];
  const runs2 = REG_KEYS.map((key, i) =>
    key === "s59_r5"
      ? mockRun(runIds2[i], batchId2, key, {
          status: "not_applicable",
          gateReason: "gate false; expression is not satisfied",
        })
      : mockRun(runIds2[i], batchId2, key),
  );
  const bases2 = [0, 1, 2, 3].map((i) => mockBaseline(baseIds2[i], runIds2[i], batchId2, REG_KEYS[i]));
  const transes2 = ["s59_r1", "s59_r2", "s59_r3", "s59_r4"].map((key) => mockTransparency(key, 3));
  const regenSpecs: BatchSpec[] = runs2.map((run, i) =>
    REG_KEYS[i] === "s59_r5"
      ? { run, baseline: null, transparency: null, jobStatus: null }
      : { run, baseline: bases2[i], transparency: transes2[i], jobStatus: "succeeded" },
  );
  const regenPayload = mockBatch(batchId2, draftId, regenSpecs);
  let regenAcceptedR2 = false;
  const regenReviewFor = (index: number) => {
    if (index === 4) {
      return mockReview({
        run: runs2[4],
        batch: regenPayload.batch,
        baseline: null,
        transparency: null,
        calculationState: "unchanged",
      });
    }
    const on = index === 1 && regenAcceptedR2;
    return mockReview({
      run: runs2[index],
      batch: regenPayload.batch,
      baseline: bases2[index],
      transparency: transes2[index],
      calculationState: "unchanged",
      stale: false,
      isAccepted: on,
      acceptance: on
        ? mockAcceptance(
            "s59-regen-acc-2222-4222-8222-222222222222",
            draftId,
            runIds2[1],
            batchId2,
            "s59_r2",
            baseIds2[1],
          )
        : null,
      acceptances: on
        ? [
            mockAcceptance(
              "s59-regen-acc-2222-4222-8222-222222222222",
              draftId,
              runIds2[1],
              batchId2,
              "s59_r2",
              baseIds2[1],
            ),
          ]
        : [],
    });
  };

  let regenerated = false;
  const regenPosts: { headers: Record<string, string>; body: Record<string, unknown> }[] = [];
  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      let body: Record<string, unknown> = {};
      try {
        body = route.request().postDataJSON() as Record<string, unknown>;
      } catch {
        body = {};
      }
      regenPosts.push({ headers: route.request().headers(), body });
      regenerated = true;
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: regenPayload.batch, question_runs: regenPayload.question_runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(regenerated ? regenPayload : stalePayload),
    }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    if (!regenerated) {
      const index = runIds.findIndex((id) => url.includes(id));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(staleReviewFor(index < 0 ? 4 : index)),
      });
    }
    const index = runIds2.findIndex((id) => url.includes(id));
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(regenReviewFor(index < 0 ? 4 : index)),
    });
  });
  const accR2new = "s59-regen-acc-2222-4222-8222-222222222222";
  await page.route("**/api/v1/question-runs/*/acceptance", async (route) => {
    regenAcceptedR2 = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        acceptance: mockAcceptance(accR2new, draftId, runIds2[1], batchId2, "s59_r2", baseIds2[1]),
        is_accepted: true,
        current_cpt_revision_id: null,
        review_revision: 1,
        cpt_hash: "d".repeat(64),
      }),
    });
  });
  let signPosts = 0;
  await page.route("**/api/v1/encounters/*/sign", (route) => {
    signPosts += 1;
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Questions (5)", { timeout: 15000 });

    // Affected-only stale labels: edited question out of date, others carried.
    await expect(panel).toContainText("This run is stale");
    await expect(panel).toContainText("unaffected questions stay valid and carry forward");
    const freshR2 = page.getByTestId("proposal-freshness-s59_r2");
    await expect(freshR2).toContainText("Out of date", { timeout: 15000 });
    await expect(freshR2).toContainText("◍");
    await expect(freshR2).toContainText("Only this question needs regeneration");
    const freshR1 = page.getByTestId("proposal-freshness-s59_r1");
    await expect(freshR1).toContainText("Current inputs");
    await expect(freshR1).toContainText("●");
    await expect(freshR1).toContainText("acceptance stands");
    await expect(page.getByTestId("proposal-freshness-s59_r5")).toHaveCount(0);

    // Stale values block sign at the entry, with no SignPanel POST path.
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toContainText("Signing is blocked");
    await expect(page.getByTestId("sign-panel")).toHaveCount(0);
    expect(signPosts).toBe(0);

    // Regen via keyboard: the affected-only action operates by keyboard and
    // starts exactly one new run (atomic locator keypress, no focus race with
    // the 2s poll re-render).
    const regenButton = panel.getByRole("button", { name: "Start new run with current inputs" });
    await regenButton.focus();
    await expect(regenButton).toBeFocused();
    await regenButton.press("Enter");
    await expect
      .poll(() => regenPosts.length, { timeout: 15000 })
      .toBe(1);
    await expect(page.getByTestId("proposal-status-s59_r2")).toContainText("Completed", {
      timeout: 15000,
    });
    expect(regenPosts).toHaveLength(1);
    expect((regenPosts[0].headers["idempotency-key"] ?? "").length).toBeGreaterThan(0);
    expect((regenPosts[0].body["packages"] as unknown[]).length).toBe(5);

    // New original baseline for r2: no transferred adjustment, re-review needed.
    await expect(page.getByTestId("proposal-freshness-s59_r2")).toHaveCount(0);
    await page.getByTestId("cpt-toggle-s59_r2").click();
    const cpt2 = page.getByTestId("cpt-panel-s59_r2");
    await expect(cpt2).toContainText("Current inputs", { timeout: 15000 });
    await expect(cpt2).toContainText("original (no edits)");
    await expect(cpt2).toContainText("Not accepted");
    await expect(cpt2.getByTestId("cpt-accept-s59_r2")).toBeEnabled();
    await cpt2.getByTestId("cpt-accept-s59_r2").click();
    await expect(cpt2).toContainText("Accepted ✓", { timeout: 15000 });
    await expect(cpt2).toContainText(accR2new);

    // Other questions still need their own review: with a saved plan, sign
    // stays blocked naming an unaccepted question, with no POST.
    const replan = `Regen plan ${Date.now().toString(36)} saved before blocked sign.`;
    await page.getByTestId("secondary-plan-textarea").fill(replan);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });
    await expect(page.getByTestId("sign-panel")).toBeVisible({ timeout: 15000 });
    await page.getByTestId("sign-button").click();
    await expect(page.getByTestId("sign-panel")).toContainText("Signing is blocked", {
      timeout: 10000,
    });
    await expect(page.getByTestId("sign-panel")).toContainText("s59_r1");
    expect(signPosts).toBe(0);
    await expect(page.getByTestId("sign-panel")).not.toContainText("Signed ✓");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
    await page.unroute("**/api/v1/question-runs/*/acceptance");
    await page.unroute("**/api/v1/encounters/*/sign");
  }
});

// S59 §3: note-only edits cause no spinners and preserve acceptance.
test("S59 §3 note-only edit shows no spinners and preserves acceptance", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es59note");
  const { draftId } = await createPatientWithDraft(request, physician);
  const packages = regPackages();
  const rev2 = await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: regHistoryValues() }, gate: "true" },
    1,
  );
  const sentinel = `s59note${Date.now().toString(36)}`;
  await postNoteWithRetry(request, physician, draftId, "proposal", sentinel, rev2);

  const batchId = "s59-note-batch-1111-4111-8111-111111111111";
  const runIds = [
    "s59-note-run-1111-4111-8111-111111111111",
    "s59-note-run-2222-4222-8222-222222222222",
    "s59-note-run-3333-4333-8333-333333333333",
    "s59-note-run-4444-4444-8444-444444444444",
    "s59-note-run-5555-4555-8555-555555555555",
  ];
  const runs = REG_KEYS.map((key, i) =>
    key === "s59_r5"
      ? mockRun(runIds[i], batchId, key, {
          status: "not_applicable",
          gateReason: "gate false; expression is not satisfied",
        })
      : mockRun(runIds[i], batchId, key),
  );
  const bases = [0, 1, 2, 3].map((i) =>
    mockBaseline(`s59-note-base-${i}-4111-8111-111111111111`, runIds[i], batchId, REG_KEYS[i]),
  );
  const transes = ["s59_r1", "s59_r2", "s59_r3", "s59_r4"].map((key) => mockTransparency(key, 2));
  const specs: BatchSpec[] = runs.map((run, i) =>
    REG_KEYS[i] === "s59_r5"
      ? { run, baseline: null, transparency: null, jobStatus: null }
      : { run, baseline: bases[i], transparency: transes[i], jobStatus: "succeeded" },
  );
  const batchPayload = mockBatch(batchId, draftId, specs);
  const accId = "s59-note-acc-1111-4111-8111-111111111111";
  const acc = mockAcceptance(accId, draftId, runIds[0], batchId, "s59_r1", bases[0].id);
  const reviewFor = (index: number) => {
    if (index === 4) {
      return mockReview({
        run: runs[4],
        batch: batchPayload.batch,
        baseline: null,
        transparency: null,
        calculationState: "unchanged",
      });
    }
    return mockReview({
      run: runs[index],
      batch: batchPayload.batch,
      baseline: bases[index],
      transparency: transes[index],
      calculationState: "unchanged",
      stale: false,
      isAccepted: index === 0,
      acceptance: index === 0 ? acc : null,
      acceptances: index === 0 ? [acc] : [],
    });
  };

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
    const index = runIds.findIndex((id) => url.includes(id));
    const payload = reviewFor(index < 0 ? 4 : index);
    const body = JSON.stringify(payload);
    expect(body).not.toContain(sentinel);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Questions (5)", { timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s59_r1")).toContainText("Completed", {
      timeout: 10000,
    });

    // Terminal fresh batch: no spinners, no freshness labels.
    await expect(panel).not.toContainText("Recalculating");
    await expect(panel).not.toContainText("Checking which questions are affected");
    await expect(page.getByTestId("proposal-freshness-s59_r1")).toHaveCount(0);

    // Acceptance preserved through the note edit.
    await page.getByTestId("cpt-toggle-s59_r1").click();
    const cpt = page.getByTestId("cpt-panel-s59_r1");
    await expect(cpt).toContainText("Current inputs", { timeout: 15000 });
    await expect(cpt).toContainText("●");
    await expect(cpt).toContainText("Accepted");
    await expect(cpt).toContainText("✓");
    await expect(cpt).toContainText(accId);

    // Notes never leak into proposal/CPT DOM; typing starts no run.
    await expect(page.locator("#notes-proposal-list")).toContainText(sentinel, { timeout: 10000 });
    await expect(panel).not.toContainText(sentinel);
    await expect(cpt).not.toContainText(sentinel);
    await page.locator("#wizard-step-register").click();
    await page.getByLabel("Draft working note").fill("s59 note probe, no new run");
    await expect(page.locator("#draft-save-status")).toContainText("Saved (revision", {
      timeout: 10000,
    });
    expect(postCount).toBe(0);
    await page.locator("#wizard-step-proposal-review").click();
    await page.getByTestId("cpt-toggle-s59_r1").click();
    await expect(page.getByTestId("cpt-panel-s59_r1")).toContainText("Accepted");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S59 §3: follow-up creates a new encounter; the prior snapshot hash is
// unchanged in the chart.
test("S59 §3 follow-up creates a new encounter with prior snapshot hash unchanged", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es59fu");
  const { patientId, draftId } = await createPatientWithDraft(request, physician);
  const csrf = await physicianSession(request, physician);
  const discard = await request.post(`/api/v1/encounters/${draftId}/discard`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: { confirm: true },
  });
  expect(discard.status(), await discard.text()).toBe(200);

  const encounterId = "s59-fu-enc-1111-4111-8111-111111111111";
  const batchId = "s59-fu-batch-1111-4111-8111-111111111111";
  const snapshot = mockSignedSnapshot({
    snapshotId: "s59-fu-snap-9999-4999-8999-999999999999",
    encounterId,
    patientId,
    batchId,
    signerUsername: physician.username,
    planText: "Frozen registration plan before follow-up.",
    questions: [
      { key: "s59_r1", runId: "s59-fu-run-1111-4111-8111-111111111111", baselineId: "s59-fu-base-1111-4111-8111-111111111111", mode: "unchanged" },
      { key: "s59_r2", runId: "s59-fu-run-2222-4222-8222-222222222222", baselineId: "s59-fu-base-2222-4222-8222-222222222222", mode: "adjusted", revId: "s59-fu-rev-2222-4222-8222-222222222222", calcId: "s59-fu-calc-2222-4222-8222-222222222222", wordingUnchanged: false },
      { key: "s59_r3", runId: "s59-fu-run-3333-4333-8333-333333333333", baselineId: "s59-fu-base-3333-4333-8333-333333333333", mode: "reset", revId: "s59-fu-revr-3333-4333-8333-333333333333", calcId: "s59-fu-calcr-3333-4333-8333-333333333333", wordingUnchanged: true },
      { key: "s59_r4", runId: "s59-fu-run-4444-4444-8444-444444444444", baselineId: "s59-fu-base-4444-4444-8444-444444444444", mode: "unchanged" },
      { key: "s59_r5", runId: "s59-fu-run-5555-4555-8555-555555555555", baselineId: "unused", mode: "skipped" },
    ],
  });
  const chart = mockChart({ patientId, identifier: "0012345678", snapshots: [snapshot], addenda: [] });
  await page.route("**/api/v1/patients/*/chart", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(chart) }),
  );
  try {
    await gotoChart(page, patientId);
    await expect(page.getByTestId(`signed-view-${encounterId}`)).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText("f".repeat(8));

    // Real follow-up creation from the chart: a new draft encounter.
    await page.getByTestId("chart-create-followup").click();
    await expect(page).toHaveURL(/#\/encounters\/.+/, { timeout: 15000 });
    const followId = page.url().split("#/encounters/")[1]?.split(/[?#]/)[0] ?? "";
    expect(followId).not.toBe("");
    expect(followId).not.toBe(encounterId);
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByTestId("followup-baseline")).toBeVisible({ timeout: 15000 });

    // The prior snapshot hash is byte-identical back on the chart.
    await gotoChart(page, patientId);
    await expect(page.getByTestId(`signed-view-${encounterId}`)).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText(
      "s59-fu-snap-9999-4999-8999-999999999999",
    );
    await expect(page.getByTestId(`signed-view-hash-${encounterId}`)).toContainText("f".repeat(64));
  } finally {
    await page.unroute("**/api/v1/patients/*/chart");
  }
});

// S59 §4: reload resumes batch + CPT + plan state with zero new POSTs.
test("S59 §4 reload resumes batch CPT and plan with zero new generation POSTs", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es59resu");
  const { draftId } = await createPatientWithDraft(request, physician);
  const packages = regPackages();
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: regHistoryValues() }, gate: "true" },
    1,
  );

  const batchId = "s59-resu-batch-1111-4111-8111-111111111111";
  const runIds = [
    "s59-resu-run-1111-4111-8111-111111111111",
    "s59-resu-run-2222-4222-8222-222222222222",
    "s59-resu-run-3333-4333-8333-333333333333",
    "s59-resu-run-4444-4444-8444-444444444444",
    "s59-resu-run-5555-4555-8555-555555555555",
  ];
  const runs = REG_KEYS.map((key, i) =>
    key === "s59_r5"
      ? mockRun(runIds[i], batchId, key, {
          status: "not_applicable",
          gateReason: "gate false; expression is not satisfied",
        })
      : mockRun(runIds[i], batchId, key),
  );
  const bases = [0, 1, 2, 3].map((i) =>
    mockBaseline(`s59-resu-base-${i}-4111-8111-111111111111`, runIds[i], batchId, REG_KEYS[i]),
  );
  const transes = ["s59_r1", "s59_r2", "s59_r3", "s59_r4"].map((key) => mockTransparency(key, 2));
  const specs: BatchSpec[] = runs.map((run, i) =>
    REG_KEYS[i] === "s59_r5"
      ? { run, baseline: null, transparency: null, jobStatus: null }
      : { run, baseline: bases[i], transparency: transes[i], jobStatus: "succeeded" },
  );
  const batchPayload = mockBatch(batchId, draftId, specs);
  const accId = "s59-resu-acc-1111-4111-8111-111111111111";
  const acc = mockAcceptance(accId, draftId, runIds[0], batchId, "s59_r1", bases[0].id);
  const reviewFor = (index: number) => {
    if (index === 4) {
      return mockReview({
        run: runs[4],
        batch: batchPayload.batch,
        baseline: null,
        transparency: null,
        calculationState: "unchanged",
      });
    }
    return mockReview({
      run: runs[index],
      batch: batchPayload.batch,
      baseline: bases[index],
      transparency: transes[index],
      calculationState: "unchanged",
      isAccepted: index === 0,
      acceptance: index === 0 ? acc : null,
      acceptances: index === 0 ? [acc] : [],
    });
  };

  let postCount = 0;
  let getCount = 0;
  await page.route("**/api/v1/encounters/*/generation-batches", (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) => {
    getCount += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(batchPayload) });
  });
  await page.route("**/api/v1/question-runs/*/review", (route) => {
    const url = route.request().url();
    const index = runIds.findIndex((id) => url.includes(id));
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(reviewFor(index < 0 ? 4 : index)),
    });
  });
  try {
    await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s59_r1")).toContainText("Completed", {
      timeout: 15000,
    });

    const planText = `Resume plan ${Date.now().toString(36)} survives reload.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });
    await page.getByTestId("cpt-toggle-s59_r1").click();
    await expect(page.getByTestId("cpt-panel-s59_r1")).toContainText("Accepted ✓", {
      timeout: 15000,
    });
    expect(postCount).toBe(0);
    const getsBefore = getCount;
    expect(getsBefore).toBeGreaterThanOrEqual(1);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s59_r1")).toContainText("Completed", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-status-s59_r5")).toContainText("Skipped");
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
    await expect(page.getByTestId("secondary-plan-revision")).toContainText("Plan revision 2");
    await page.getByTestId("cpt-toggle-s59_r1").click();
    await expect(page.getByTestId("cpt-panel-s59_r1")).toContainText("Accepted ✓", {
      timeout: 15000,
    });
    await expect(page.getByTestId("cpt-panel-s59_r1")).toContainText(accId);
    expect(postCount).toBe(0);
    expect(getCount).toBeGreaterThan(getsBefore);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S59 §4: pending and failed values block sign with plan text preserved;
// the server denies a forged sign (T1, no mocks).
test("S59 §4 pending and failed block sign with plan preserved, server denies forged sign", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2es59blck");
  const { draftId } = await createPatientWithDraft(request, physician);
  const packages = regPackages();
  await patchDraftWithRetry(
    request,
    physician,
    draftId,
    { history: { values: regHistoryValues() }, gate: "true" },
    1,
  );

  const batchId = "s59-blck-batch-1111-4111-8111-111111111111";
  const runIds = [
    "s59-blck-run-1111-4111-8111-111111111111",
    "s59-blck-run-2222-4222-8222-222222222222",
    "s59-blck-run-3333-4333-8333-333333333333",
    "s59-blck-run-4444-4444-8444-444444444444",
    "s59-blck-run-5555-4555-8555-555555555555",
  ];
  const runs = REG_KEYS.map((key, i) =>
    key === "s59_r5"
      ? mockRun(runIds[i], batchId, key, {
          status: "not_applicable",
          gateReason: "gate false; expression is not satisfied",
        })
      : mockRun(runIds[i], batchId, key),
  );
  const bases = [0, 1, 2].map((i) =>
    mockBaseline(`s59-blck-base-${i}-4111-8111-111111111111`, runIds[i], batchId, REG_KEYS[i]),
  );
  const transes = ["s59_r1", "s59_r2", "s59_r3"].map((key) => mockTransparency(key, 2));
  let failed = false;
  const payload = () => {
    const specs: BatchSpec[] = runs.map((run, i) => {
      if (REG_KEYS[i] === "s59_r5") {
        return { run, baseline: null, transparency: null, jobStatus: null };
      }
      if (i === 3) {
        // s59_r4 pending first, then the failure moves to s59_r2's job.
        return failed
          ? { run: runs[1], baseline: null, transparency: null, jobStatus: "failed" }
          : { run, baseline: null, transparency: null, jobStatus: "queued" };
      }
      if (failed && i === 1) {
        return { run, baseline: null, transparency: null, jobStatus: "failed" };
      }
      return { run, baseline: bases[i], transparency: transes[i], jobStatus: "succeeded" };
    });
    // Deduplicate runs for the failed phase (r2 appears once as failed).
    const seen = new Set<string>();
    const deduped = specs.filter((s) => {
      if (seen.has(s.run.id)) {
        return false;
      }
      seen.add(s.run.id);
      return true;
    });
    return mockBatch(batchId, draftId, deduped, { complete: false });
  };

  let signPosts = 0;
  await seedStoredProposal(page, draftId, { batchId, packages, sourceRevision: 2 });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload()) }),
  );
  await page.route("**/api/v1/encounters/*/sign", (route) => {
    signPosts += 1;
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    await expect(page.getByTestId("proposal-status-s59_r1")).toBeVisible({ timeout: 15000 });

    // Pending values block sign; the plan survives the block.
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toContainText("Signing is blocked");
    await expect(blocked).toContainText("proposal is incomplete");
    await expect(blocked.getByRole("button", { name: "Sign encounter (blocked)" })).toBeDisabled();
    await expect(page.getByTestId("sign-panel")).toHaveCount(0);
    const planText = `Block plan ${Date.now().toString(36)} is never lost.`;
    await page.getByTestId("secondary-plan-textarea").fill(planText);
    await page.getByTestId("secondary-plan-save").click();
    await expect(page.getByTestId("secondary-plan-status")).toContainText("Saved (plan revision 2)", {
      timeout: 10000,
    });
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
    expect(signPosts).toBe(0);

    // Failed values block sign and keep a per-question retry.
    failed = true;
    await page.reload();
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-status-s59_r2")).toContainText("Failed", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-status-s59_r2")).toContainText("✕");
    await expect(page.getByTestId("proposal-sign-blocked")).toContainText("has failed (s59_r2)");
    await expect(page.getByTestId("proposal-retry-s59_r2")).toBeVisible();
    await expect(page.getByTestId("secondary-plan-textarea")).toHaveValue(planText);
    expect(signPosts).toBe(0);

    // Server truth (T1, no mocks): a forged sign on the real draft is denied.
    const csrf = await physicianSession(request, physician);
    const denied = await request.post(`/api/v1/encounters/${draftId}/sign`, {
      headers: {
        "X-CSRF-Token": csrf,
        "If-Match": '"2"',
        "Idempotency-Key": `e2es59blck${Date.now().toString(36)}`,
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

// S59 §4: a stranger sees the signed-only chart, appends an attributed
// addendum, and cannot read draft content (403 with no CPT leak).
test("S59 §4 stranger sees signed-only chart appends addendum and draft stays denied", async ({
  page,
  request,
  browser,
}) => {
  const author = await loginPhysician(page, request, "e2es59auth");
  const created = await createPatientWithDraft(request, author);
  const { patientId } = created;

  const encounterId = "s59-strg-enc-1111-4111-8111-111111111111";
  const batchId = "s59-strg-batch-1111-4111-8111-111111111111";
  const planText = "Frozen five-question plan for cross-physician read.";
  const snapshot = mockSignedSnapshot({
    snapshotId: "s59-strg-snap-9999-4999-8999-999999999999",
    encounterId,
    patientId,
    batchId,
    signerUsername: author.username,
    planText,
    questions: [
      { key: "s59_r1", runId: "s59-strg-run-1111-4111-8111-111111111111", baselineId: "s59-strg-base-1111-4111-8111-111111111111", mode: "unchanged" },
      { key: "s59_r2", runId: "s59-strg-run-2222-4222-8222-222222222222", baselineId: "s59-strg-base-2222-4222-8222-222222222222", mode: "adjusted", revId: "s59-strg-rev-2222-4222-8222-222222222222", calcId: "s59-strg-calc-2222-4222-8222-222222222222", wordingUnchanged: false },
      { key: "s59_r3", runId: "s59-strg-run-3333-4333-8333-333333333333", baselineId: "s59-strg-base-3333-4333-8333-333333333333", mode: "reset", revId: "s59-strg-revr-3333-4333-8333-333333333333", calcId: "s59-strg-calcr-3333-4333-8333-333333333333", wordingUnchanged: true },
      { key: "s59_r4", runId: "s59-strg-run-4444-4444-8444-444444444444", baselineId: "s59-strg-base-4444-4444-8444-444444444444", mode: "unchanged" },
      { key: "s59_r5", runId: "s59-strg-run-5555-4555-8555-555555555555", baselineId: "unused", mode: "skipped" },
    ],
  });
  let addenda: Record<string, unknown>[] = [];
  const chartBody = () =>
    JSON.stringify(
      mockChart({ patientId, identifier: "0012345678", snapshots: [snapshot], addenda }),
    );

  await page.route("**/api/v1/patients/*/chart", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: chartBody() }),
  );
  try {
    await gotoChart(page, patientId);
    await expect(page.getByTestId(`signed-view-${encounterId}`)).toBeVisible({ timeout: 15000 });
  } finally {
    await page.unroute("**/api/v1/patients/*/chart");
  }

  const otherCtx = await browser.newContext();
  const otherPage = await otherCtx.newPage();
  try {
    const otherName = uniqueName("e2es59other");
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
        id: "s59-strg-addendum-1111-4111-8111-111111111111",
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
    const view = otherPage.getByTestId(`signed-view-${encounterId}`);
    await expect(view).toBeVisible({ timeout: 15000 });
    await expect(view).toContainText(planText);
    for (const key of REG_KEYS) {
      await expect(view).toContainText(key);
    }
    await expect(otherPage.getByTestId(`signed-view-signer-${encounterId}`)).toContainText(
      author.username,
    );
    // Signed-only: no plan editor, no sign action, no sliders.
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

    // Attributed append with the exact addendum fence.
    await expect(otherPage.getByTestId(`addendum-list-${encounterId}`)).toContainText("No addenda yet");
    const correction = `Attributed correction ${Date.now().toString(36)}: plan wording clarified.`;
    await otherPage.getByTestId(`addendum-textarea-${encounterId}`).fill(correction);
    await otherPage.getByTestId(`addendum-submit-${encounterId}`).click();
    await expect(otherPage.getByTestId(`addendum-list-${encounterId}`)).toContainText(correction, {
      timeout: 10000,
    });
    await expect(otherPage.getByTestId(`addendum-list-${encounterId}`)).toContainText(otherName);
    await expect(otherPage.getByTestId(`addendum-status-${encounterId}`)).toBeVisible();
    expect(addendumPosts).toBe(1);
    expect(addendumHeaders["if-match"]).toBe('"5"');
    expect((addendumHeaders["idempotency-key"] ?? "").length).toBeGreaterThan(0);
    expect(addendumBody["text"]).toBe(correction);
    expect(addendumBody["expected_encounter_revision"]).toBe(5);

    // Reload keeps separate chronology; the frozen plan is untouched.
    await otherPage.reload();
    await expect(otherPage.getByTestId("chart-heading")).toBeVisible({ timeout: 15000 });
    await expect(otherPage.getByTestId(`signed-view-signer-${encounterId}`)).toContainText(
      author.username,
      { timeout: 15000 },
    );
    const list = otherPage.getByTestId(`addendum-list-${encounterId}`);
    await expect(list).toContainText(correction);
    await expect(list).toContainText(otherName);
    await expect(list).toContainText("2026-10-11");
    await expect(otherPage.getByTestId(`signed-view-plan-${encounterId}`)).toContainText(planText);
    await expect(otherPage.getByTestId(`signed-view-signer-${encounterId}`)).not.toContainText(otherName);
  } finally {
    await otherPage.close();
    await otherCtx.close();
  }
});
