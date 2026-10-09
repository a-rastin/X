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

/* S48 automatic proposal review + transparency UI (seams T1/T9).
 *
 * Backend: existing GET /generation-batches/{id} + GET /question-runs/{id}/review
 * supply ordered states, baselines join, five transparency fields, proposal,
 * workflow, freshness, safe_* no-note-leakage (verified 75 passed worker+mcp+provider).
 * Frontend (read-only): web/src/features/reasoning/api.ts + ProposalReviewPanel.tsx
 * (wizard step 8, auto entry flushes autosave + gates, POST If-Match+Idempotency-Key,
 * polls ~2s active / 15s hidden backoff, stops terminal; testids proposal-review,
 * proposal-status-<key>, proposal-retry-<key>, proposal-transparency-<key>,
 * proposal-ddi, proposal-sign-blocked; storage xi.proposal.<encounterId>).
 *
 * Known constraints (dev-frontend handoff):
 * - Browser has no package source (S39 bundles pending): fresh entry with no
 *   stored packages shows honest unavailable; §§2-4 mocked fixtures seed
 *   localStorage xi.proposal.<encounterId> with {batchId, packages (exact
 *   array POSTed), sourceRevision} then Check-again POST mock.
 * - No worker runs in e2e: real queued jobs stay pending; failed/completed/
 *   ordered/partial/transparency/proposal fixtures below use controlled
 *   page.route mocks as the documented alternative (deterministic, no sleeps
 *   for concurrency — web-first assertions / polling waits). §§1-2 uses a
 *   real API-created batch pre-seeded BEFORE navigation to prove
 *   mount-restore tracking without POST (mount race fixed).
 *
 * Selector contract (ProposalReviewPanel header):
 * - panel data-testid="proposal-review" (#proposal-review)
 * - per-question data-testid="proposal-status-<key>"
 * - retry data-testid="proposal-retry-<key>" (failed with recorded packages)
 * - transparency data-testid="proposal-transparency-<key>" (after expansion)
 * - DDI data-testid="proposal-ddi" (#proposal-ddi)
 * - sign blocked data-testid="proposal-sign-blocked" (#proposal-sign-blocked)
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

// Backend commit-visibility race (see helpers.ensurePatient): an immediate
// write after login can 401 while the session row becomes visible; one fresh
// login retry then fails loudly. Used for PATCH/notes setup writes.
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

// Synthetic two-question packages (S45/S46 shape, S39 bundles pending).
// Controlled e2e alternative: real POST via T1, no worker runs.
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
  // ponytail: set on the current document (caller ensures origin is loaded).
  // Mount race fixed in ProposalReviewPanel (guards batchIdRef/attemptedRef):
  // pre-seed BEFORE gotoEncounter restores tracking with no POST. Mocked
  // §§2-4 fixtures still seed AFTER unavailable + Check-again POST mock.
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

// S48 §1: fresh entry with no stored packages shows honest unavailable,
// makes no generation POST, keeps the draft editable, has no Generate click.
test("S48 §1 fresh entry shows honest unavailable with no POST and no Generate click", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprfresh");
  const { draftId } = await createPatientWithDraft(request, physician);

  let postCount = 0;
  await page.route("**/api/v1/encounters/*/generation-batches", (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
    }
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Proposal unavailable", { timeout: 15000 });
    await expect(panel).toContainText("No generation run yet", { timeout: 15000 });
    // No Generate affordance anywhere in the proposal step.
    await expect(panel.getByRole("button", { name: /Generate/i })).toHaveCount(0);
    await expect(page.locator("body").getByRole("button", { name: /^Generate/ })).toHaveCount(0);
    // Draft stays editable: working note + save status visible.
    await expect(page.getByLabel("Draft working note")).toBeVisible();
    await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 1)");
    expect(postCount).toBe(0);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
  }
});

// S48 §1: entry flushes autosave (Saving→Saved) then auto-attempts; typing
// more never creates a run per keystroke (POST stays 0 with no packages).
test("S48 §1 entry flushes autosave and never creates a run per keystroke", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprflush");
  const { draftId } = await createPatientWithDraft(request, physician);

  let postCount = 0;
  let patchCount = 0;
  await page.route("**/api/v1/encounters/*/generation-batches", (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
    }
    return route.continue();
  });
  await page.route("**/api/v1/encounters/*", (route) => {
    if (route.request().method() === "PATCH") {
      patchCount += 1;
    }
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Proposal unavailable", { timeout: 15000 });

    // One keystroke burst: autosave flushes (PATCH) and acknowledges.
    const note = page.getByLabel("Draft working note");
    await note.fill("flush probe one");
    await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 2)", {
      timeout: 10000,
    });
    expect(patchCount).toBeGreaterThanOrEqual(1);

    // Second burst: still no generation POST (no run per keystroke).
    await note.fill("flush probe two with more text");
    await expect(page.locator("#draft-save-status")).toContainText("Saved (revision 3)", {
      timeout: 10000,
    });
    await expect(panel).toContainText("Proposal unavailable", { timeout: 10000 });
    expect(postCount).toBe(0);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/encounters/*");
  }
});

const DIAG_REQUIRED = [
  "a_delusions",
  "a_hallucinations",
  "a_disorganized_speech",
  "a_disorganized_behavior",
  "a_negative_symptoms",
  "b_functional_decline",
  "c_six_months",
  "c_active_month",
  "c_shortened_by_intervention",
  "d_mood_exclusion",
  "e_substance_medical_exclusion",
  "f_autism_present",
];

function symptomOnlyAnswers(): Record<string, string> {
  const answers: Record<string, string> = {};
  for (const id of DIAG_REQUIRED) {
    answers[id] = "no";
  }
  answers["a_delusions"] = "yes";
  answers["a_hallucinations"] = "yes";
  answers["a_disorganized_speech"] = "yes";
  return answers;
}

// S48 §1: no trigger while the below-threshold acknowledgment is missing.
test("S48 §1 diagnosis below-threshold without ack blocks generation with no POST", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprdiag");
  const { draftId } = await createPatientWithDraft(request, physician);
  const csrf = await physicianSession(request, physician);
  const seeded = await request.patch(`/api/v1/encounters/${draftId}`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: { draft_data: { diagnosis: { answers: symptomOnlyAnswers() } } },
  });
  expect(seeded.status(), await seeded.text()).toBe(200);

  let postCount = 0;
  await page.route("**/api/v1/encounters/*/generation-batches", (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
    }
    return route.continue();
  });
  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Generation paused", { timeout: 15000 });
    await expect(panel).toContainText("Diagnosis is completed below threshold", {
      timeout: 10000,
    });
    await expect(panel.getByRole("button", { name: "Check again" })).toBeVisible();
    expect(postCount).toBe(0);
    // Prerequisite hint stays honest: diagnosis verdict is below threshold.
    await expect(page.locator("#diagnosis-preview-status")).toContainText(
      "Completed below threshold",
      { timeout: 10000 },
    );
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
  }
});

// S48 §1: no trigger while medication reconciliation is pending (follow-up).
test("S48 §1 pending medication reconciliation blocks generation with no POST", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprmeds");
  const { patientId, draftId: regDraftId } = await createPatientWithDraft(request, physician);
  let csrf = await physicianSession(request, physician);
  const discard = await request.post(`/api/v1/encounters/${regDraftId}/discard`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"1"' },
    data: { confirm: true },
  });
  expect(discard.status(), await discard.text()).toBe(200);

  csrf = await physicianSession(request, physician);
  const createFu = await request.post(`/api/v1/patients/${patientId}/encounters`, {
    headers: { "X-CSRF-Token": csrf },
    data: {
      kind: "follow_up",
      baseline: {
        history_values: { h_exposure_dopamine_blocker: "yes" },
        prior_scores: {},
        medications: [{ catalog_drug_id: "catalog_e2e_alpha" }],
        provenance_note: "e2e signed baseline snapshot",
      },
      baseline_encounter_id: "e2e-signed-baseline-001",
    },
  });
  expect(createFu.status(), await createFu.text()).toBe(201);
  const followId = (await createFu.json()).encounter.id as string;

  let postCount = 0;
  await page.route("**/api/v1/encounters/*/generation-batches", (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
    }
    return route.continue();
  });
  try {
    await gotoEncounter(page, followId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Generation paused", { timeout: 15000 });
    await expect(panel).toContainText("Medication reconciliation is pending", {
      timeout: 10000,
    });
    expect(postCount).toBe(0);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
  }
});

// S48 §§1-2: real two-question batch pre-seeded BEFORE navigation enters
// tracking via GET without any POST/Check-again; reload restores same DOM.
test("S48 §§1-2 pre-seeded real batch tracks via GET with no POST, restores DOM after reload", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprreuse");
  const { draftId } = await createPatientWithDraft(request, physician);
  // Real API-created batch (history gates ready, no worker runs in e2e).
  const { batchId, packages, revision } = await seedHistoryAndStartTwoQuestionBatch(
    request,
    physician,
    draftId,
  );

  let postCount = 0;
  let getCount = 0;
  const getUrls: string[] = [];
  await page.route("**/api/v1/encounters/*/generation-batches", (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) => {
    getCount += 1;
    getUrls.push(route.request().url());
    return route.continue();
  });
  try {
    // Pre-seed BEFORE encounter navigation: mount restores tracking.
    await seedStoredProposal(page, draftId, {
      batchId,
      packages,
      sourceRevision: revision,
    });
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    // WITHOUT any Check-again click/POST the panel enters tracking via GET.
    await expect(panel).toContainText("Questions (2)", { timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s48_q1")).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId("proposal-status-s48_q2")).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("s48_q1");
    // No worker runs in e2e: first question stays queued/pending, never fake-completed.
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText(/Pending|Running/);
    await expect(panel.getByRole("button", { name: "Check again" })).toHaveCount(0);
    expect(postCount).toBe(0);
    expect(getCount).toBeGreaterThanOrEqual(1);
    expect(getUrls.some((u) => u.includes(batchId))).toBe(true);

    // Typing never creates a run per keystroke.
    await page.getByLabel("Draft working note").fill("reuse probe, no new run");
    await expect(page.locator("#draft-save-status")).toContainText("Saved (revision", {
      timeout: 10000,
    });
    await expect(page.getByTestId("proposal-status-s48_q1")).toBeVisible();
    expect(postCount).toBe(0);

    // Reload restores the same DOM from GET without a new POST.
    const getBeforeReload = getCount;
    await page.reload();
    await expect(page.getByRole("heading", { name: "Encounter draft" })).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-review")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("proposal-review")).toContainText("Questions (2)", {
      timeout: 15000,
    });
    await expect(page.getByTestId("proposal-status-s48_q1")).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId("proposal-status-s48_q2")).toBeVisible({ timeout: 10000 });
    await expect.poll(() => getCount, { timeout: 10000 }).toBeGreaterThan(getBeforeReload);
    expect(postCount).toBe(0);
    const storedAfterReload = await page.evaluate(
      (id) => localStorage.getItem(`xi.proposal.${id}`),
      draftId,
    );
    expect(storedAfterReload).not.toBeNull();
    const parsed = JSON.parse(storedAfterReload as string) as {
      batchId: string;
      packages: unknown;
      sourceRevision: number;
    };
    expect(parsed.batchId).toBe(batchId);
    expect(parsed.packages).toEqual(packages);
    expect(parsed.sourceRevision).toBe(revision);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
  }
});

// --- S48 §§2-4 mocked fixtures (controlled alternative: no worker in e2e) ---

function mockCptTables() {
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

function mockPosteriors() {
  return [
    { node_id: "A", states: ["no", "yes"], probabilities: [0.8, 0.2] },
    { node_id: "B", states: ["no", "yes"], probabilities: [0.78, 0.22] },
  ];
}

function mockSavedInputs(sourceRevision: number) {
  // Exact frozen inputs: two observed + one missing (S48 §3 missingness).
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
    {
      node_id: "C",
      patient_type: "tristate",
      status: "missing",
      value: null,
      source_path: "synthetic/history/h_s48_c",
      source_revision: sourceRevision,
    },
  ];
}

function mockBaseline(
  baselineId: string,
  runId: string,
  batchId: string,
  questionKey: string,
  networkVersion = "s48-test-v1",
) {
  return {
    id: baselineId,
    question_run_id: runId,
    batch_id: batchId,
    source_hash: "a".repeat(64),
    effective_hash: "b".repeat(64),
    effective_xml: "<BIF>mock effective</BIF>",
    raw_response: { network_hash: "a".repeat(64), tables: mockCptTables() },
    validated_tables: mockCptTables(),
    query_nodes: ["A", "B"],
    posteriors: mockPosteriors(),
    section_text: `${questionKey} absent: B no 78.00% outcome.`,
    template_version: "v1",
    prompt_version: "v1",
    network_version: networkVersion,
    provider_model: "mock-model",
    projection_hash: "c".repeat(64),
    provenance: { question_key: questionKey, provider_model: "mock-model" },
    created_at: "2026-10-09T00:00:00Z",
  };
}

function mockTransparency(questionKey: string, sourceRevision: number, networkVersion = "s48-test-v1") {
  return {
    question_key: questionKey,
    network_version: networkVersion,
    saved_patient_inputs: mockSavedInputs(sourceRevision),
    returned_cpt_percentages: mockCptTables(),
    deterministic_result: {
      posteriors: mockPosteriors(),
      section_text: `${questionKey} absent: B no 78.00% outcome.`,
      query_nodes: ["A", "B"],
      effective_hash: "b".repeat(64),
    },
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
    generated_at: "2026-10-09T00:00:00Z",
  };
}

// S48 §2: ordered pending/running/skipped/clarification/failed/completed/stale
// render position-ordered with text + glyph (never color alone).
test("S48 §2 ordered seven states render position-ordered with glyphs", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprorder");
  const { draftId } = await createPatientWithDraft(request, physician);
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
        history: { values: { h_s48_a: "yes", h_s48_b: "no", h_s48_c: "yes", h_s48_d: "no" } },
        gate: "true",
      },
    },
  });
  expect(patched.status(), await patched.text()).toBe(200);

  const batchId = "66666666-6666-4666-8666-666666666666";
  const keys = ["s48_pend", "s48_run", "s48_skip", "s48_clar", "s48_fail", "s48_done", "s48_stale"] as const;
  const runIds = keys.map((_, i) => `77777777-7777-4777-8777-77777777777${i}`);
  const doneBase = mockBaseline("88888888-8888-4888-8888-888888888888", runIds[5], batchId, "s48_done");
  const doneTrans = mockTransparency("s48_done", 2);
  const runs = [
    { id: runIds[0], batch_id: batchId, question_key: "s48_pend", status: "ready", gate_reason: "gate true", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    { id: runIds[1], batch_id: batchId, question_key: "s48_run", status: "ready", gate_reason: "gate true", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    { id: runIds[2], batch_id: batchId, question_key: "s48_skip", status: "not_applicable", gate_reason: "gate false; expression is not satisfied", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    { id: runIds[3], batch_id: batchId, question_key: "s48_clar", status: "needs_clarification", gate_reason: "required field synthetic/history/h_x is missing", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    { id: runIds[4], batch_id: batchId, question_key: "s48_fail", status: "ready", gate_reason: "gate true", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    { id: runIds[5], batch_id: batchId, question_key: "s48_done", status: "ready", gate_reason: "gate true", projection: { variables: mockSavedInputs(2) }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    { id: runIds[6], batch_id: batchId, question_key: "s48_stale", status: "stale", gate_reason: "superseded", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
  ];
  const jobs = [
    { id: "j-pend", batch_id: batchId, question_run_id: runIds[0], job_class: "generation", status: "queued", attempt_index: 0, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
    { id: "j-run", batch_id: batchId, question_run_id: runIds[1], job_class: "generation", status: "leased", attempt_index: 1, max_attempts: 3, lease_deadline: new Date(Date.now() + 60000).toISOString(), last_heartbeat: new Date().toISOString(), next_eligible_at: null, result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
    { id: "j-fail", batch_id: batchId, question_run_id: runIds[4], job_class: "generation", status: "failed", attempt_index: 3, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: { error_code: "PROVIDER_TRANSIENT" }, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
    { id: "j-done", batch_id: batchId, question_run_id: runIds[5], job_class: "generation", status: "succeeded", attempt_index: 1, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
  ];
  const payload = {
    batch: { id: batchId, encounter_id: draftId, author_id: "a", source_revision: 2, fingerprint: "fp", status: "ready", pinned_bundle: {}, created_at: "2026-10-09T00:00:00Z" },
    question_runs: runs,
    freshness: { stale: false, reason: "current", current_fingerprint: "fp" },
    job: jobs[0],
    jobs,
    attempts: 4,
    queue: { busy: false, leased_count: 1, queued_count: 1, max_provider_slots: 2, max_queued_runs: 100, queue_position: null },
    baseline: doneBase,
    transparency: doneTrans,
    baselines: [
      { question_run_id: runIds[0], question_key: "s48_pend", position: 0, status: "ready", baseline: null, transparency: null },
      { question_run_id: runIds[1], question_key: "s48_run", position: 1, status: "ready", baseline: null, transparency: null },
      { question_run_id: runIds[2], question_key: "s48_skip", position: 2, status: "not_applicable", baseline: null, transparency: null },
      { question_run_id: runIds[3], question_key: "s48_clar", position: 3, status: "needs_clarification", baseline: null, transparency: null },
      { question_run_id: runIds[4], question_key: "s48_fail", position: 4, status: "ready", baseline: null, transparency: null },
      { question_run_id: runIds[5], question_key: "s48_done", position: 5, status: "ready", baseline: doneBase, transparency: doneTrans },
      { question_run_id: runIds[6], question_key: "s48_stale", position: 6, status: "stale", baseline: null, transparency: null },
    ],
    proposal: null,
    workflow: { complete: false, status: "incomplete", pending_question_keys: ["s48_pend", "s48_run", "s48_fail", "s48_done"], needs_clarification: ["s48_clar"], skipped: [{ question_key: "s48_skip", position: 2, reason: "gate false; expression is not satisfied" }], coverage_warnings: [], ddi_status: "valid" },
  };

  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: payload.batch, question_runs: runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        question_run: runs[5],
        batch: payload.batch,
        baseline: doneBase,
        adjustable: true,
        transparency: doneTrans,
        freshness: payload.freshness,
        job: jobs[3],
        attempts: 1,
        queue: payload.queue,
      }),
    }),
  );
  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Proposal unavailable", { timeout: 15000 });
    await seedStoredProposal(page, draftId, { batchId: "pending-ui-post", packages, sourceRevision: 2 });
    await panel.getByRole("button", { name: "Check again" }).click();
    const expected: Array<[string, string, string]> = [
      ["s48_pend", "Pending", "○"],
      ["s48_run", "Running", "◐"],
      ["s48_skip", "Skipped", "■"],
      ["s48_clar", "Needs clarification", "▲"],
      ["s48_fail", "Failed", "✕"],
      ["s48_done", "Completed", "●"],
      ["s48_stale", "Stale", "◍"],
    ];
    for (const [key, label, glyph] of expected) {
      const el = page.getByTestId(`proposal-status-${key}`);
      await expect(el).toBeVisible({ timeout: 15000 });
      await expect(el).toContainText(label);
      await expect(el).toContainText(glyph);
    }
    // Position order preserved in DOM (bounding tops strictly increasing).
    const tops: number[] = [];
    for (const [key] of expected) {
      const top = await page.getByTestId(`proposal-status-${key}`).evaluate((el) => el.getBoundingClientRect().top);
      tops.push(top);
    }
    for (let i = 1; i < tops.length; i += 1) {
      expect(tops[i]).toBeGreaterThan(tops[i - 1]);
    }
    // Partial: clarification tail in proposal + failed precedence in sign block.
    await expect(panel).toContainText("needs clarification for s48_clar");
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toContainText("Signing is blocked");
    await expect(blocked).toContainText("s48_fail");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48 §2: active runs poll (~2s), hidden tabs back off, terminal stops.
// Controlled timing: 2s vs 15s margins, no concurrency sleeps.
test("S48 §2 active polls, hidden backoff delays, terminal stops polling", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprpoll");
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

  const batchId = "poll-batch-1111-4111-8111-111111111111";
  const run1 = "poll-run-2222-4222-8222-222222222222";
  const run2 = "poll-run-3333-4333-8333-333333333333";
  let terminal = false;
  const activePayload = {
    batch: { id: batchId, encounter_id: draftId, author_id: "a", source_revision: 2, fingerprint: "fp", status: "ready", pinned_bundle: {}, created_at: "2026-10-09T00:00:00Z" },
    question_runs: [
      { id: run1, batch_id: batchId, question_key: "s48_q1", status: "ready", gate_reason: "gate true", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
      { id: run2, batch_id: batchId, question_key: "s48_q2", status: "ready", gate_reason: "gate true", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    ],
    freshness: { stale: false, reason: "current", current_fingerprint: "fp" },
    job: null,
    jobs: [
      { id: "j-poll", batch_id: batchId, question_run_id: run1, job_class: "generation", status: "queued", attempt_index: 0, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
    ],
    attempts: 1,
    queue: { busy: false, leased_count: 0, queued_count: 1, max_provider_slots: 2, max_queued_runs: 100, queue_position: null },
    baseline: null,
    transparency: null,
    baselines: [
      { question_run_id: run1, question_key: "s48_q1", position: 0, status: "ready", baseline: null, transparency: null },
      { question_run_id: run2, question_key: "s48_q2", position: 1, status: "ready", baseline: null, transparency: null },
    ],
    proposal: null,
    workflow: { complete: false, status: "incomplete", pending_question_keys: ["s48_q1", "s48_q2"], needs_clarification: [], skipped: [], coverage_warnings: [], ddi_status: "valid" },
  };
  const doneBase = mockBaseline("poll-base-4444-4444-8444-444444444444", run1, batchId, "s48_q1");
  const doneTrans = mockTransparency("s48_q1", 2);
  const terminalPayload = {
    ...activePayload,
    jobs: [
      { id: "j-poll", batch_id: batchId, question_run_id: run1, job_class: "generation", status: "succeeded", attempt_index: 1, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
    ],
    baselines: [
      { question_run_id: run1, question_key: "s48_q1", position: 0, status: "ready", baseline: doneBase, transparency: doneTrans },
      { question_run_id: run2, question_key: "s48_q2", position: 1, status: "not_applicable", baseline: null, transparency: null },
    ],
    question_runs: [
      { ...activePayload.question_runs[0] },
      { ...activePayload.question_runs[1], status: "not_applicable", gate_reason: "gate false" },
    ],
    proposal: {
      id: "prop-1",
      batch_id: batchId,
      fingerprint: "fp",
      sections: [
        { question_run_id: run1, question_key: "s48_q1", position: 0, baseline_id: doneBase.id, section_text: doneBase.section_text, posteriors: doneBase.posteriors, query_nodes: ["A", "B"], effective_hash: doneBase.effective_hash, template_version: "v1", network_version: "s48-test-v1" },
      ],
      skipped: [{ question_key: "s48_q2", position: 1, reason: "gate false" }],
      coverage_warnings: [],
      ddi_report: mockDdiReport(),
      created_at: "2026-10-09T00:00:00Z",
    },
    workflow: { complete: true, status: "complete", pending_question_keys: [], needs_clarification: [], skipped: [{ question_key: "s48_q2", position: 1, reason: "gate false" }], coverage_warnings: [], ddi_status: "valid" },
  };

  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: activePayload.batch, question_runs: activePayload.question_runs }),
      });
    }
    return route.continue();
  });
  let getCount = 0;
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(terminal ? terminalPayload : activePayload),
    }).then(() => {
      getCount += 1;
    }),
  );

  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Proposal unavailable", { timeout: 15000 });
    await seedStoredProposal(page, draftId, { batchId: "pending-ui-post", packages, sourceRevision: 2 });
    await panel.getByRole("button", { name: "Check again" }).click();
    await expect(page.getByTestId("proposal-status-s48_q1")).toBeVisible({ timeout: 15000 });

    // Active polls roughly every 2s: at least 2 GETs within 7s (web-first poll).
    await expect.poll(() => getCount, { timeout: 8000 }).toBeGreaterThanOrEqual(2);

    // Hidden-tab backoff: hide the document, then no new GET within 3.5s
    // (visible would have polled; hidden waits 15s). Controlled timing with
    // clear 2s vs 15s margins.
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { value: true, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const hiddenBaseline = getCount;
    await page.waitForTimeout(3500);
    // One in-flight 2s tick scheduled before hiding may still land; hidden
    // backoff (15s) means at most one more GET in 3.5s, never visible-rate polling.
    expect(getCount).toBeLessThanOrEqual(hiddenBaseline + 1);
    // Becoming visible triggers an immediate refresh tick.
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { value: false, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect.poll(() => getCount, { timeout: 8000 }).toBeGreaterThan(hiddenBaseline);

    // Terminal stops: switch to complete, polling must settle (no new GETs).
    terminal = true;
    const terminalBaseline = getCount;
    // Allow one in-flight tick to land, then assert stability over 3.5s.
    await page.waitForTimeout(3500);
    const afterTerminal = getCount;
    await page.waitForTimeout(3500);
    expect(getCount).toBe(afterTerminal);
    expect(afterTerminal).toBeLessThanOrEqual(terminalBaseline + 2);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
  }
});

// S48 §4: complete proposal renders sections/DDI/secondary plan, sign ready;
// stale/partial visibly block signing while draft editing stays available.
test("S48 §4 complete proposal with DDI and secondary plan, stale blocks signing", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprprop");
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
  const sentinel = `propsentinel${Date.now().toString(36)}`;
  await postNoteWithRetry(request, physician, draftId, "proposal", sentinel, 2);

  const batchId = "prop-batch-1111-4111-8111-111111111111";
  const run1 = "prop-run-2222-4222-8222-222222222222";
  const run2 = "prop-run-3333-4333-8333-333333333333";
  const base1 = mockBaseline("prop-base-4444-4444-8444-444444444444", run1, batchId, "s48_q1");
  const trans1 = mockTransparency("s48_q1", 2);
  const ddi = mockDdiReport();
  const completePayload = {
    batch: { id: batchId, encounter_id: draftId, author_id: "a", source_revision: 2, fingerprint: "fp", status: "ready", pinned_bundle: {}, created_at: "2026-10-09T00:00:00Z" },
    question_runs: [
      { id: run1, batch_id: batchId, question_key: "s48_q1", status: "ready", gate_reason: "gate true", projection: { variables: mockSavedInputs(2) }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
      { id: run2, batch_id: batchId, question_key: "s48_q2", status: "not_applicable", gate_reason: "gate false; not applicable", projection: { variables: [] }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    ],
    freshness: { stale: false, reason: "current", current_fingerprint: "fp" },
    job: null,
    jobs: [
      { id: "j1", batch_id: batchId, question_run_id: run1, job_class: "generation", status: "succeeded", attempt_index: 1, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
    ],
    attempts: 1,
    queue: { busy: false, leased_count: 0, queued_count: 0, max_provider_slots: 2, max_queued_runs: 100, queue_position: null },
    baseline: base1,
    transparency: trans1,
    baselines: [
      { question_run_id: run1, question_key: "s48_q1", position: 0, status: "ready", baseline: base1, transparency: trans1 },
      { question_run_id: run2, question_key: "s48_q2", position: 1, status: "not_applicable", baseline: null, transparency: null },
    ],
    proposal: {
      id: "prop-1",
      batch_id: batchId,
      fingerprint: "fp",
      sections: [
        { question_run_id: run1, question_key: "s48_q1", position: 0, baseline_id: base1.id, section_text: base1.section_text, posteriors: base1.posteriors, query_nodes: ["A", "B"], effective_hash: base1.effective_hash, template_version: "v1", network_version: "s48-test-v1" },
      ],
      skipped: [{ question_key: "s48_q2", position: 1, reason: "gate false; not applicable" }],
      coverage_warnings: ["coverage unavailable: catalog_e2e_gamma"],
      ddi_report: ddi,
      created_at: "2026-10-09T00:00:00Z",
    },
    workflow: { complete: true, status: "complete", pending_question_keys: [], needs_clarification: [], skipped: [{ question_key: "s48_q2", position: 1, reason: "gate false; not applicable" }], coverage_warnings: ["coverage unavailable: catalog_e2e_gamma"], ddi_status: "valid" },
  };
  let stale = false;
  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: completePayload.batch, question_runs: completePayload.question_runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) => {
    const payload = stale
      ? { ...completePayload, freshness: { stale: true, reason: "analysis facts changed since freeze", current_fingerprint: "fp-new" } }
      : completePayload;
    const body = JSON.stringify(payload);
    expect(body).not.toContain(sentinel);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
  await page.route("**/api/v1/question-runs/*/review", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        question_run: completePayload.question_runs[0],
        batch: completePayload.batch,
        baseline: base1,
        adjustable: true,
        transparency: trans1,
        freshness: completePayload.freshness,
        job: completePayload.jobs[0],
        attempts: 1,
        queue: completePayload.queue,
      }),
    }),
  );

  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Proposal unavailable", { timeout: 15000 });
    await seedStoredProposal(page, draftId, { batchId: "pending-ui-post", packages, sourceRevision: 2 });
    await panel.getByRole("button", { name: "Check again" }).click();

    // Final proposal: position-ordered sections + skipped + DDI.
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", { timeout: 15000 });
    await expect(panel).toContainText("Proposal");
    await expect(panel).toContainText("s48_q1 absent: B no 78.00% outcome.");
    await expect(panel).toContainText("Skipped questions");
    await expect(panel).toContainText("s48_q2");
    await expect(panel).toContainText("skipped: gate false");
    const ddiBlock = page.getByTestId("proposal-ddi");
    await expect(ddiBlock).toBeVisible();
    await expect(ddiBlock).toContainText("DDI coverage (pinned report)");
    await expect(ddiBlock).toContainText("ddi-mock/1");
    await expect(ddiBlock).toContainText("Coverage unavailable");
    await expect(ddiBlock).toContainText("catalog_e2e_gamma");
    await expect(ddiBlock).toContainText("Interaction found");
    await expect(ddiBlock).toContainText("serious");
    await expect(ddiBlock).toContainText("coverage unavailable: catalog_e2e_gamma");
    await expect(ddiBlock).not.toContainText("safe", { ignoreCase: true });
    // Separate secondary-plan entry (placeholder, no editing here).
    await expect(panel).toContainText("Secondary plan (S50)");
    await expect(panel).toContainText("physician-edited secondary plan lands in S50");
    // Complete + current: no sign block, ready notice instead.
    await expect(page.getByTestId("proposal-sign-blocked")).toHaveCount(0);
    await expect(panel).toContainText("Proposal complete and current");
    await expect(page.getByLabel("Draft working note")).toBeEnabled();
    // No note leakage in proposal DOM.
    await expect(page.locator("#notes-proposal-list")).toContainText(sentinel, { timeout: 10000 });
    await expect(page.getByTestId("proposal-review")).not.toContainText(sentinel);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// S48 §4: stale run visibly blocks signing while draft editing/retry stay available.
test("S48 §4 stale run blocks signing with reason, draft editing preserved", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprstale");
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
  const batchId = "stale-batch-1111-4111-8111-111111111111";
  const run1 = "stale-run-2222-4222-8222-222222222222";
  const base1 = mockBaseline("stale-base-4444-4444-8444-444444444444", run1, batchId, "s48_q1");
  const trans1 = mockTransparency("s48_q1", 2);
  const stalePayload = {
    batch: { id: batchId, encounter_id: draftId, author_id: "a", source_revision: 2, fingerprint: "fp-old", status: "ready", pinned_bundle: {}, created_at: "2026-10-09T00:00:00Z" },
    question_runs: [
      { id: run1, batch_id: batchId, question_key: "s48_q1", status: "ready", gate_reason: "gate true", projection: { variables: mockSavedInputs(2) }, projection_hash: "c".repeat(64), fingerprint: "fp-old", created_at: "2026-10-09T00:00:00Z" },
    ],
    freshness: { stale: true, reason: "analysis facts changed since freeze", current_fingerprint: "fp-new" },
    job: null,
    jobs: [
      { id: "j1", batch_id: batchId, question_run_id: run1, job_class: "generation", status: "succeeded", attempt_index: 1, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
    ],
    attempts: 1,
    queue: { busy: false, leased_count: 0, queued_count: 0, max_provider_slots: 2, max_queued_runs: 100, queue_position: null },
    baseline: base1,
    transparency: trans1,
    baselines: [
      { question_run_id: run1, question_key: "s48_q1", position: 0, status: "ready", baseline: base1, transparency: trans1 },
    ],
    proposal: null,
    workflow: { complete: false, status: "incomplete", pending_question_keys: ["s48_q1"], needs_clarification: [], skipped: [], coverage_warnings: [], ddi_status: "valid" },
  };
  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: stalePayload.batch, question_runs: stalePayload.question_runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stalePayload) }),
  );
  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Proposal unavailable", { timeout: 15000 });
    await seedStoredProposal(page, draftId, { batchId: "pending-ui-post", packages, sourceRevision: 2 });
    await panel.getByRole("button", { name: "Check again" }).click();
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", { timeout: 15000 });
    // Stale banner + sign blocked with reason, draft editing + retry preserved.
    await expect(panel).toContainText("This run is stale");
    await expect(panel).toContainText("analysis facts changed since freeze");
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toBeVisible();
    await expect(blocked).toContainText("Run is stale");
    await expect(blocked).toContainText("Signing is blocked");
    await expect(blocked.getByRole("button", { name: "Sign encounter (blocked)" })).toBeDisabled();
    await expect(page.getByLabel("Draft working note")).toBeEnabled();
    await expect(panel).toContainText("Start new run with current inputs");
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
  }
});

// S48 §3: lazy transparency shows all five fields + CPT vs posterior labels +
// exact saved inputs, from persisted data, no note leakage.
test("S48 §3 transparency shows five fields, distinct CPT/posterior, exact saved inputs", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprtrans");
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
  const sentinel = `transsentinel${Date.now().toString(36)}`;
  await postNoteWithRetry(request, physician, draftId, "proposal", sentinel, rev2);

  const batchId = "99999999-9999-4999-8999-999999999999";
  const run1 = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa";
  const run2 = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb";
  const base1 = mockBaseline("cccccccc-cccc-4ccc-cccc-cccccccccccc", run1, batchId, "s48_q1");
  const trans1 = mockTransparency("s48_q1", 2);
  const payload = {
    batch: { id: batchId, encounter_id: draftId, author_id: "a", source_revision: 2, fingerprint: "fp", status: "ready", pinned_bundle: {}, created_at: "2026-10-09T00:00:00Z" },
    question_runs: [
      { id: run1, batch_id: batchId, question_key: "s48_q1", status: "ready", gate_reason: "gate true", projection: { variables: mockSavedInputs(2) }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
      { id: run2, batch_id: batchId, question_key: "s48_q2", status: "ready", gate_reason: "gate true", projection: { variables: mockSavedInputs(2) }, projection_hash: "c".repeat(64), fingerprint: "fp", created_at: "2026-10-09T00:00:00Z" },
    ],
    freshness: { stale: false, reason: "current", current_fingerprint: "fp" },
    job: null,
    jobs: [
      { id: "j1", batch_id: batchId, question_run_id: run1, job_class: "generation", status: "succeeded", attempt_index: 1, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: null, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
      { id: "j2", batch_id: batchId, question_run_id: run2, job_class: "generation", status: "failed", attempt_index: 3, max_attempts: 3, lease_deadline: null, last_heartbeat: null, next_eligible_at: null, result: { error_code: "PROVIDER_TRANSIENT" }, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z" },
    ],
    attempts: 4,
    queue: { busy: false, leased_count: 0, queued_count: 0, max_provider_slots: 2, max_queued_runs: 100, queue_position: null },
    baseline: base1,
    transparency: trans1,
    baselines: [
      { question_run_id: run1, question_key: "s48_q1", position: 0, status: "ready", baseline: base1, transparency: trans1 },
      { question_run_id: run2, question_key: "s48_q2", position: 1, status: "ready", baseline: null, transparency: null },
    ],
    proposal: null,
    workflow: { complete: false, status: "incomplete", pending_question_keys: ["s48_q2"], needs_clarification: [], skipped: [], coverage_warnings: [], ddi_status: "valid" },
  };
  let reviewCount = 0;
  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ batch: payload.batch, question_runs: payload.question_runs }),
      });
    }
    return route.continue();
  });
  await page.route("**/api/v1/generation-batches/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
  );
  await page.route("**/api/v1/question-runs/*/review", async (route) => {
    reviewCount += 1;
    const url = route.request().url();
    const body = url.includes(run1)
      ? JSON.stringify({
          question_run: payload.question_runs[0],
          batch: payload.batch,
          baseline: base1,
          adjustable: true,
          transparency: trans1,
          freshness: payload.freshness,
          job: payload.jobs[0],
          attempts: 1,
          queue: payload.queue,
        })
      : JSON.stringify({
          question_run: payload.question_runs[1],
          batch: payload.batch,
          baseline: null,
          adjustable: false,
          transparency: null,
          freshness: payload.freshness,
          job: payload.jobs[1],
          attempts: 3,
          queue: payload.queue,
        });
    expect(body).not.toContain(sentinel);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });

  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Proposal unavailable", { timeout: 15000 });
    await seedStoredProposal(page, draftId, { batchId: "pending-ui-post", packages, sourceRevision: 2 });
    await panel.getByRole("button", { name: "Check again" }).click();
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", { timeout: 15000 });

    // Lazy: no review fetch before expansion.
    expect(reviewCount).toBe(0);
    await page.getByRole("button", { name: "Show transparency for s48_q1" }).click();
    const region = page.getByTestId("proposal-transparency-s48_q1");
    await expect(region).toBeVisible({ timeout: 10000 });
    expect(reviewCount).toBe(1);

    // Five fields beside the recommendation: question, network, saved inputs,
    // returned CPTs, deterministic result (+ versions/hashes/provenance).
    await expect(region).toContainText("s48_q1");
    await expect(region).toContainText("Network version");
    await expect(region).toContainText("s48-test-v1");
    await expect(region).toContainText("Saved patient inputs (frozen at run start)");
    await expect(region).toContainText("Returned CPT percentages (LLM-estimated values)");
    await expect(region).toContainText("Deterministic network result (computed posteriors)");
    await expect(region).toContainText("Recommendation (templated section)");
    await expect(region).toContainText("s48_q1 absent: B no 78.00% outcome.");
    // Sources / missingness / versions: exact saved-input display.
    await expect(region).toContainText("synthetic/history/h_s48_a");
    await expect(region).toContainText("yes");
    await expect(region).toContainText("observed");
    await expect(region).toContainText("synthetic/history/h_s48_c");
    await expect(region).toContainText("missing");
    await expect(region).toContainText("80");
    await expect(region).toContainText("20");
    await expect(region).toContainText("78.00");
    await expect(region).toContainText("Query nodes");
    // CPT vs posterior distinct labels (never conflated).
    await expect(region).toContainText("LLM-estimated");
    await expect(region).toContainText("computed");
    // Persisted, not recomputed: second toggle uses cache (no refetch).
    await page.getByRole("button", { name: "Hide transparency for s48_q1" }).click();
    await expect(region).toBeHidden();
    await page.getByRole("button", { name: "Show transparency for s48_q1" }).click();
    await expect(page.getByTestId("proposal-transparency-s48_q1")).toBeVisible();
    expect(reviewCount).toBe(1);

    // Failed original exposes no baseline (adjustable false).
    await page.getByRole("button", { name: "Show transparency for s48_q2" }).click();
    const region2 = page.getByTestId("proposal-transparency-s48_q2");
    await expect(region2).toContainText("No completed baseline", { timeout: 10000 });
    expect(reviewCount).toBe(2);

    // No note leakage: sentinel only in notes, never in proposal/transparency.
    await expect(page.locator("#notes-proposal-list")).toContainText(sentinel, { timeout: 10000 });
    await expect(page.getByTestId("proposal-review")).not.toContainText(sentinel);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});

// Two-question failure→retry browser case (S48 §§2+4 verify): q1 completed,
// q2 failed (config-error style), partial proposal null, sign blocked.
test("S48 §§2+4 two-question failure shows partial, retry re-POSTs exact packages, sign blocked", async ({
  page,
  request,
}) => {
  const physician = await loginPhysician(page, request, "e2eprfail");
  const { draftId } = await createPatientWithDraft(request, physician);
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
        history: { values: { h_s48_a: "yes", h_s48_b: "no", h_s48_c: "yes", h_s48_d: "no" } },
        gate: "true",
      },
    },
  });
  expect(patched.status(), await patched.text()).toBe(200);

  // Sentinel note: must never leak into proposal/transparency payloads or DOM.
  const sentinel = `notesentinel${Date.now().toString(36)}`;
  csrf = await physicianSession(request, physician);
  const noted = await request.post(`/api/v1/encounters/${draftId}/notes`, {
    headers: { "X-CSRF-Token": csrf, "If-Match": '"2"' },
    data: { page: "proposal", text: sentinel },
  });
  expect(noted.status(), await noted.text()).toBe(201);

  const batchId = "11111111-1111-4111-8111-111111111111";
  const run1 = "22222222-2222-4222-8222-222222222222";
  const run2 = "33333333-3333-4333-8333-333333333333";
  const base1 = mockBaseline("44444444-4444-4444-8444-444444444444", run1, batchId, "s48_q1");
  const trans1 = mockTransparency("s48_q1", 2);

  const batchPayload = {
    batch: {
      id: batchId,
      encounter_id: draftId,
      author_id: "author-mock",
      source_revision: 2,
      fingerprint: "fp-mock-1",
      status: "ready",
      pinned_bundle: {},
      created_at: "2026-10-09T00:00:00Z",
    },
    question_runs: [
      {
        id: run1,
        batch_id: batchId,
        question_key: "s48_q1",
        status: "ready",
        gate_reason: "gate true; all 2 required fields observed",
        projection: { variables: mockSavedInputs(2) },
        projection_hash: "c".repeat(64),
        fingerprint: "fp-mock-1",
        created_at: "2026-10-09T00:00:00Z",
      },
      {
        id: run2,
        batch_id: batchId,
        question_key: "s48_q2",
        status: "ready",
        gate_reason: "gate true; all 2 required fields observed",
        projection: { variables: mockSavedInputs(2) },
        projection_hash: "c".repeat(64),
        fingerprint: "fp-mock-1",
        created_at: "2026-10-09T00:00:00Z",
      },
    ],
    freshness: { stale: false, reason: "current", current_fingerprint: "fp-mock-1" },
    job: null,
    jobs: [
      {
        id: "job-1",
        batch_id: batchId,
        question_run_id: run1,
        job_class: "generation",
        status: "succeeded",
        attempt_index: 1,
        max_attempts: 3,
        lease_deadline: null,
        last_heartbeat: null,
        next_eligible_at: null,
        result: null,
        created_at: "2026-10-09T00:00:00Z",
        updated_at: "2026-10-09T00:00:00Z",
      },
      {
        id: "job-2",
        batch_id: batchId,
        question_run_id: run2,
        job_class: "generation",
        status: "failed",
        attempt_index: 3,
        max_attempts: 3,
        lease_deadline: null,
        last_heartbeat: null,
        next_eligible_at: null,
        result: { error_code: "PROVIDER_TRANSIENT", retryable: true },
        created_at: "2026-10-09T00:00:00Z",
        updated_at: "2026-10-09T00:00:00Z",
      },
    ],
    attempts: 4,
    queue: { busy: false, leased_count: 0, queued_count: 0, max_provider_slots: 2, max_queued_runs: 100, queue_position: null },
    baseline: base1,
    transparency: trans1,
    baselines: [
      { question_run_id: run1, question_key: "s48_q1", position: 0, status: "ready", baseline: base1, transparency: trans1 },
      { question_run_id: run2, question_key: "s48_q2", position: 1, status: "ready", baseline: null, transparency: null },
    ],
    proposal: null,
    workflow: { complete: false, status: "incomplete", pending_question_keys: ["s48_q2"], needs_clarification: [], skipped: [], coverage_warnings: [], ddi_status: "valid" },
  };

  const review1 = {
    question_run: batchPayload.question_runs[0],
    batch: batchPayload.batch,
    baseline: base1,
    adjustable: true,
    transparency: trans1,
    freshness: batchPayload.freshness,
    job: batchPayload.jobs[0],
    attempts: 1,
    queue: batchPayload.queue,
  };
  const review2 = {
    question_run: batchPayload.question_runs[1],
    batch: batchPayload.batch,
    baseline: null,
    adjustable: false,
    transparency: null,
    freshness: batchPayload.freshness,
    job: batchPayload.jobs[1],
    attempts: 3,
    queue: batchPayload.queue,
  };

  let postCount = 0;
  const postedBodies: unknown[] = [];
  const nextBatchId = "55555555-5555-4555-8555-555555555555";
  await page.route("**/api/v1/encounters/*/generation-batches", async (route) => {
    if (route.request().method() === "POST") {
      postCount += 1;
      try {
        postedBodies.push(route.request().postDataJSON());
      } catch {
        postedBodies.push(null);
      }
      // Precise retry: re-POST same stored packages; backend resumes at the
      // failed stage (S47). Return a fresh batch identity with the same runs.
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          batch: { ...batchPayload.batch, id: postCount === 1 ? batchId : nextBatchId },
          question_runs: batchPayload.question_runs.map((r) => ({
            ...r,
            batch_id: postCount === 1 ? batchId : nextBatchId,
          })),
        }),
      });
    }
    return route.continue();
  });
  let getBatchCount = 0;
  await page.route(`**/api/v1/generation-batches/*`, async (route) => {
    getBatchCount += 1;
    const url = route.request().url();
    const id = url.split("/").pop()?.split("?")[0] ?? "";
    const payload = id === nextBatchId
      ? { ...batchPayload, batch: { ...batchPayload.batch, id: nextBatchId } }
      : batchPayload;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.route("**/api/v1/question-runs/*/review", async (route) => {
    const url = route.request().url();
    const payload = url.includes(run1) ? review1 : review2;
    const body = JSON.stringify(payload);
    // No note leakage by construction: sentinel must not appear in payloads.
    expect(body).not.toContain(sentinel);
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });

  try {
    await gotoEncounter(page, draftId);
    const panel = page.getByTestId("proposal-review");
    await expect(panel).toContainText("Proposal unavailable", { timeout: 15000 });
    await seedStoredProposal(page, draftId, { batchId: "pending-ui-post", packages, sourceRevision: 2 });
    await panel.getByRole("button", { name: "Check again" }).click();

    // Ordered failed/completed with glyph + text (never color alone).
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("Completed", { timeout: 15000 });
    await expect(page.getByTestId("proposal-status-s48_q1")).toContainText("●");
    await expect(page.getByTestId("proposal-status-s48_q2")).toContainText("Failed", { timeout: 10000 });
    await expect(page.getByTestId("proposal-status-s48_q2")).toContainText("✕");
    await expect(page.getByTestId("proposal-status-s48_q2")).toContainText("Failed after 3 of 3 attempts");
    await expect(page.getByTestId("proposal-status-s48_q2")).toContainText("PROVIDER_TRANSIENT");
    // Position order: q1 before q2.
    const q1Pos = await page.getByTestId("proposal-status-s48_q1").evaluate((el) => el.getBoundingClientRect().top);
    const q2Pos = await page.getByTestId("proposal-status-s48_q2").evaluate((el) => el.getBoundingClientRect().top);
    expect(q1Pos).toBeLessThan(q2Pos);
    expect(postCount).toBe(1);
    expect((postedBodies[0] as Record<string, unknown>)["packages"]).toEqual(packages);

    // Partial stays readable but incomplete; sign visibly blocked with reason.
    await expect(panel).toContainText("Proposal unavailable");
    await expect(panel).toContainText("waiting on s48_q2");
    const blocked = page.getByTestId("proposal-sign-blocked");
    await expect(blocked).toBeVisible();
    await expect(blocked).toContainText("Signing is blocked");
    await expect(blocked).toContainText("s48_q2");
    await expect(blocked.getByRole("button", { name: "Sign encounter (blocked)" })).toBeDisabled();
    await expect(blocked).toContainText("Draft editing above stays available");
    // Draft editing preserved.
    await expect(page.getByLabel("Draft working note")).toBeEnabled();
    // Failed question keeps its precise retry.
    const retry = page.getByTestId("proposal-retry-s48_q2");
    await expect(retry).toBeVisible();
    await expect(retry).toContainText("Retry s48_q2");

    // Precise retry re-POSTs the exact stored packages at the current revision.
    await retry.click();
    await expect.poll(() => postCount, { timeout: 10000 }).toBe(2);
    expect((postedBodies[1] as Record<string, unknown>)["packages"]).toEqual(packages);
    const storedAfter = await page.evaluate((id) => localStorage.getItem(`xi.proposal.${id}`), draftId);
    expect(storedAfter).not.toBeNull();
    expect(storedAfter as string).toContain(nextBatchId);

    // No note leakage in DOM: sentinel lives only in the notes panel.
    await expect(page.locator("#notes-proposal-list")).toContainText(sentinel, { timeout: 10000 });
    await expect(page.getByTestId("proposal-review")).not.toContainText(sentinel);
    expect(getBatchCount).toBeGreaterThan(0);
  } finally {
    await page.unroute("**/api/v1/encounters/*/generation-batches");
    await page.unroute("**/api/v1/generation-batches/*");
    await page.unroute("**/api/v1/question-runs/*/review");
  }
});
