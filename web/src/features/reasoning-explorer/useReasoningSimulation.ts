import { useEffect, useRef, useState } from "react";
import { simulateReasoning } from "./api";
import type {
  Change, Identity, OralTreatmentId, SimulationRequest, SimulationResponse, Workflow,
} from "./types";

export const changeId = (change: Change) =>
  "item_id" in change ? change.item_id : change.control_id;

function frozenIdentity(identity: Identity) {
  const { client_request_id, scenario_hash, selected_treatment_id, assessment_hashes, ...frozen } = identity;
  return JSON.stringify([
    Object.entries(frozen).sort(),
    Object.entries(assessment_hashes).sort(),
  ]);
}

export function matchesSimulation(
  response: SimulationResponse, request: SimulationRequest, workflow: Workflow,
  baseline: SimulationResponse | null,
) {
  const { identity, diff, patient, scenario, controls } = response;
  const first = patient.candidates[0] ?? patient.distribution[0] ??
    scenario.candidates[0] ?? scenario.distribution[0];
  const selected = request.selected_treatment_id ?? first?.treatment_id ?? null;
  if (identity.schema_version !== request.schema_version ||
      identity.workflow !== workflow ||
      identity.baseline_proposal_id !== request.baseline_proposal_id ||
      identity.client_request_id !== request.client_request_id ||
      identity.selected_treatment_id !== selected ||
      response.active_graph.selected_treatment_id !== selected ||
      (response.selected_attribution && response.selected_attribution.treatment_id !== selected) ||
      !/^[a-f0-9]{64}$/.test(identity.scenario_hash) ||
      (baseline && frozenIdentity(baseline.identity) !== frozenIdentity(identity))) {
    return false;
  }

  const expected = new Map(request.changes.map((change) => [changeId(change), change.value]));
  const descriptors = new Map(controls.map((control) => [control.control_id, control]));
  // Compare normalized raw overrides. An evaluator may elide a no-op (for
  // instance, clearing a BMI source that already resolves to calculated).
  return Object.keys(diff.raw_inputs).every((id) => expected.has(id)) &&
    request.changes.every((change) => {
      const id = changeId(change);
      const control = descriptors.get(id);
      if (!control) return false;
      const { patient_value, scenario_value } = control;
      const normalized = scenario_value === change.value ||
        (id === "bmi.source" && change.value === null && scenario_value === "calculated");
      const delta = diff.raw_inputs[id];
      return normalized && (patient_value === scenario_value
        ? delta === undefined
        : delta?.patient === patient_value && delta.scenario === scenario_value);
    });
}

type Intent = {
  changes: Change[];
  selected: OralTreatmentId | null;
  epoch: number;
  delay: number;
};

export function useReasoningSimulation(encounterId: string, proposalId: string, workflow: Workflow) {
  const intent = useRef<Intent>({ changes: [], selected: null, epoch: 0, delay: 0 });
  const [wanted, setWanted] = useState<Intent | null>(intent.current);
  const baseline = useRef<SimulationResponse | null>(null);
  const selected = useRef<OralTreatmentId | null>(null);
  const controller = useRef<AbortController | null>(null);
  const [result, setResult] = useState<SimulationResponse | null>(null);
  const [status, setStatus] = useState<"pending" | "ready" | "error">("pending");

  function update(changes: Change[], target = selected.current, delay = 0) {
    const next = { changes, selected: target, epoch: intent.current.epoch + 1, delay };
    intent.current = next;
    selected.current = target;
    controller.current?.abort();
    setStatus("pending");
    setWanted(next);
  }

  useEffect(() => {
    if (!wanted) return;
    const active = new AbortController();
    controller.current = active;
    const request: SimulationRequest = {
      schema_version: "reasoning-simulation-v1",
      baseline_proposal_id: proposalId,
      client_request_id: wanted.epoch,
      selected_treatment_id: wanted.selected,
      changes: wanted.changes,
    };
    const current = () => !active.signal.aborted && intent.current.epoch === wanted.epoch;
    const timer = window.setTimeout(async () => {
      if (!current()) return;
      try {
        const response = await simulateReasoning(encounterId, request, active.signal);
        if (!current()) return;
        if (!matchesSimulation(response, request, workflow, baseline.current)) {
          setStatus("error");
          return;
        }
        baseline.current ??= response;
        selected.current = response.identity.selected_treatment_id;
        setResult(response);
        setStatus("ready");
      } catch {
        if (current()) setStatus("error");
      }
    }, wanted.delay);
    return () => {
      window.clearTimeout(timer);
      active.abort();
    };
  }, [encounterId, proposalId, workflow, wanted]);

  return {
    result, status, changes: wanted?.changes ?? [],
    setChange(id: string, change: Change | null, continuous = false) {
      const changes = intent.current.changes.filter((existing) => changeId(existing) !== id);
      if (change) changes.push(change);
      update(changes, selected.current, continuous ? 150 : 0);
    },
    selectTreatment(id: OralTreatmentId) {
      if (selected.current !== id) update(intent.current.changes, id);
    },
    retry() { update(intent.current.changes); },
    reset() {
      const initial = baseline.current;
      if (!initial) return;
      selected.current = initial.identity.selected_treatment_id;
      intent.current = {
        changes: [], selected: selected.current, epoch: intent.current.epoch + 1, delay: 0,
      };
      controller.current?.abort();
      setWanted(null);
      setResult(initial);
      setStatus("ready");
    },
  };
}
