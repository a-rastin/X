import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { test } from "node:test";

const fixture = JSON.parse(readFileSync(new URL("./reasoning-simulation-contract.json", import.meta.url)));
const types = readFileSync(new URL("../src/features/reasoning-explorer/types.ts", import.meta.url), "utf8");

function literalValues(declaration, field = "") {
  const source = types.match(declaration)?.[1] ?? "";
  const pattern = field ? new RegExp(`${field}: "([^"]+)"`, "g") : /"([^"]+)"/g;
  return [...source.matchAll(pattern)].map(([, value]) => value);
}

test("TypeScript wire declarations mirror the backend schema fixture", () => {
  assert.deepEqual(literalValues(/type Workflow =([^;]+);/), fixture.workflows);
  assert.deepEqual(literalValues(/type OralTreatmentId =([^;]+);/), fixture.treatment_ids);
  assert.deepEqual(literalValues(/type EvaluationStatus =([^;]+);/), fixture.evaluation_statuses);
  assert.deepEqual(literalValues(/type Change =([\s\S]+?)\nexport interface/, "kind").sort(), fixture.change_kinds);
  assert.deepEqual(literalValues(/interface SimulationRequest \{([\s\S]+?)\n\}/, "schema_version"), [
    fixture.schema_version,
  ]);
  assert.deepEqual(literalValues(/interface Identity \{([\s\S]+?)\n\}/, "algorithm_version"), [
    fixture.algorithm_version,
  ]);
  const response = types.match(/interface SimulationResponse \{([\s\S]+?)\n\}/)[1];
  assert.deepEqual([...response.matchAll(/^  (\w+):/gm)].map(([, field]) => field), fixture.response_fields);
});

function resolveTypeScriptImports(context) {
  // Resolve Vite's extensionless relative imports in Node's native TypeScript runner.
  const hooks = registerHooks({
    resolve(specifier, info, nextResolve) {
      try {
        return nextResolve(specifier, info);
      } catch (error) {
        if (error.code !== "ERR_MODULE_NOT_FOUND" || !specifier.startsWith(".")) throw error;
        return nextResolve(`${specifier}.ts`, info);
      }
    },
  });
  context.after(() => hooks.deregister());
}

test("simulation transport forwards CSRF and the caller's AbortSignal", async (context) => {
  resolveTypeScriptImports(context);
  const { simulateReasoning } = await import("../src/features/reasoning-explorer/api.ts");
  const { ApiError, setCsrfToken } = await import("../src/features/identity/api.ts");

  const controller = new AbortController();
  setCsrfToken("test-csrf");
  context.after(() => setCsrfToken(null));
  const body = {
    schema_version: fixture.schema_version,
    baseline_proposal_id: "54a78184-1f35-4a6d-8baf-521b639ce1ec",
    selected_treatment_id: null,
    client_request_id: 0,
    changes: [],
  };

  const calls = context.mock.method(globalThis, "fetch", (path, options) => {
    assert.equal(path, "/api/v1/encounters/encounter%2Fid/reasoning/simulate");
    assert.equal(options.method, "POST");
    assert.equal(options.credentials, "include");
    assert.equal(options.headers["X-CSRF-Token"], "test-csrf");
    assert.deepEqual(JSON.parse(options.body), body);
    assert.equal(options.signal, controller.signal);
    return new Promise((_, reject) => {
      options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
    });
  });
  const pending = simulateReasoning("encounter/id", body, controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(calls.mock.callCount(), 1);

  calls.mock.restore();
  context.mock.method(globalThis, "fetch", () => Response.json({
    code: "REASONING_BASELINE_UNAVAILABLE",
    message: "Frozen proposal unavailable.",
  }, { status: 409 }));
  await assert.rejects(simulateReasoning("encounter/id", body), (error) =>
    error instanceof ApiError && error.status === 409 && error.code === "REASONING_BASELINE_UNAVAILABLE");
});

test("simulation results must match the request and frozen proposal identity", async (context) => {
  resolveTypeScriptImports(context);
  const { matchesSimulation } = await import("../src/features/reasoning-explorer/useReasoningSimulation.ts");
  const proposal = "54a78184-1f35-4a6d-8baf-521b639ce1ec";
  const workflow = "new-case-v1";
  const treatment = "oral:aripiprazole";
  const request = {
    schema_version: fixture.schema_version,
    baseline_proposal_id: proposal,
    selected_treatment_id: treatment,
    client_request_id: 2,
    changes: [{ kind: "choice", control_id: "bmi.source", value: null }],
  };
  const response = {
    identity: {
      schema_version: fixture.schema_version,
      workflow,
      baseline_proposal_id: proposal,
      baseline_payload_hash: "pinned",
      selected_treatment_id: treatment,
      client_request_id: 2,
      assessment_hashes: { panss: "panss-1", cssrs: "cssrs-2" },
      scenario_hash: "a".repeat(64),
    },
    active_graph: { selected_treatment_id: treatment },
    selected_attribution: { treatment_id: treatment },
    patient: { candidates: [{ treatment_id: treatment }], distribution: [] },
    scenario: { candidates: [], distribution: [] },
    controls: [{ control_id: "bmi.source", patient_value: "manual", scenario_value: "calculated" }],
    diff: { raw_inputs: { "bmi.source": { patient: "manual", scenario: "calculated" } } },
  };
  const baseline = structuredClone(response);
  baseline.identity.assessment_hashes = { cssrs: "cssrs-2", panss: "panss-1" };
  assert.equal(matchesSimulation(response, request, workflow, baseline), true);
  assert.equal(matchesSimulation(response, { ...request, selected_treatment_id: null }, workflow, baseline), true);

  for (const mismatch of [
    { client_request_id: 1 },
    { baseline_proposal_id: "other-proposal" },
    { baseline_payload_hash: "different" },
    { selected_treatment_id: "oral:olanzapine" },
    { scenario_hash: "" },
    { workflow: "follow-up-v1" },
  ]) {
    const changed = { ...response, identity: { ...response.identity, ...mismatch } };
    assert.equal(matchesSimulation(changed, request, workflow, baseline), false, JSON.stringify(mismatch));
  }
  assert.equal(matchesSimulation({ ...response, diff: { raw_inputs: {} } }, request, workflow, baseline), false);
  assert.equal(matchesSimulation(response, { ...request, changes: [] }, workflow, baseline), false);
  assert.equal(matchesSimulation(response, {
    ...request, changes: [{ kind: "choice", control_id: "bmi.source", value: "manual" }],
  }, workflow, baseline), false);

  const noOp = {
    ...response,
    controls: [{ ...response.controls[0], patient_value: "calculated" }],
    diff: { raw_inputs: {} },
  };
  assert.equal(matchesSimulation(noOp, request, workflow, baseline), true);
});
