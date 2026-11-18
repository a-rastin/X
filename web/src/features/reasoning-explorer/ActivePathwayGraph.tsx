import { useId, useLayoutEffect, useRef, useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { Stack } from "@astryxdesign/core/Stack";
import { useLanguage } from "../../shared/i18n";
import { useFollowUpText } from "../follow-up/text";
import { chartText, oralTreatmentName } from "./chartPresentation";
import { formatSimulationValue } from "./SimulationDiff";
import { simulationControlLabel } from "./WhatIfControls";
import type { ActiveGraph, FactorId, GraphEdge, GraphNode, OralTreatmentId, SimulationResponse } from "./types";

const columns = [
  ["Source inputs", "ورودی‌های منبع"],
  ["Factors and rules", "عوامل و قواعد"],
  ["Outcomes and decisions", "نتایج و تصمیم‌ها"],
  ["Eligibility and selection", "شرایط و انتخاب"],
];

const edgeKinds = {
  input_derivation: ["Input derivation", "استخراج از ورودی", "2 4"],
  bn_dependency: ["Bayesian dependency", "وابستگی بیزی", undefined],
  rule_dependency: ["Rule dependency", "وابستگی قاعده", "9 4"],
  candidate_filter: ["Candidate filter", "پالایش گزینه‌ها", "9 4 2 4"],
} as const;

const memberships = {
  patient: ["P", "ب", "Patient only", "فقط بیمار"],
  scenario: ["S", "س", "Scenario only", "فقط سناریو"],
  both: ["P+S", "ب+س", "Both paths", "هر دو مسیر"],
} as const;
const membershipMarks = {
  patient: "M1 1 L7 4 L1 7 Z",
  scenario: "M1 0 L8 4 L1 8 Z",
  both: "M0 1 L3 4 L0 7 M4 1 L7 4 L4 7",
};
const provenanceLabels = {
  source: ["Recorded input", "ورودی ثبت‌شده"],
  recorded_override: ["Recorded override", "مقدار دستی ثبت‌شده"],
  simulated_override: ["Scenario override", "مقدار تغییریافتهٔ سناریو"],
  workflow_default: ["Workflow default", "پیش‌فرض روند کار"],
  derived: ["Derived", "استخراج‌شده"],
};

function MembershipMark({ state }: { state: GraphNode["path_state"] }) {
  return <path d={membershipMarks[state]} fill={state === "scenario" ? "var(--ink-muted)" : "none"}
    stroke="var(--ink-muted)" strokeWidth="1" />;
}

function column(node: GraphNode): number {
  if (node.kind === "source") return 0;
  if (node.kind === "selection" || (node.kind === "rule" && node.subject.startsWith("filter:"))) return 3;
  if (node.kind === "treatment" || node.kind === "section" ||
    (node.kind === "rule" && node.subject.startsWith("terminal:"))) return 2;
  return 1;
}

export function factorPath(graph: ActiveGraph, factor: FactorId | null) {
  const nodes = new Set<string>();
  const edges = new Set<string>();
  const sources = new Set<string>();
  if (factor === null) return { nodes, edges, sources };
  const origin = graph.nodes.find((node) => node.kind === "factor" && node.subject === factor);
  if (!origin) return { nodes, edges, sources };
  nodes.add(origin.id);
  for (const [from, to] of [["source", "target"], ["target", "source"]] as const) {
    const seen = new Set([origin.id]);
    const queue = [origin.id];
    for (const id of queue) {
      for (const edge of graph.edges) {
        if (edge[from] !== id) continue;
        edges.add(edge.id);
        nodes.add(edge[to]);
        if (!seen.has(edge[to])) {
          seen.add(edge[to]);
          queue.push(edge[to]);
        }
      }
    }
  }
  for (const node of graph.nodes) {
    if (node.kind === "source" && nodes.has(node.id)) sources.add(node.subject);
  }
  return { nodes, edges, sources };
}

function visibleGraph(result: SimulationResponse, focused: ReturnType<typeof factorPath>, expanded: boolean) {
  const graph = result.active_graph;
  const nonzero = new Set(
    [result.selected_attribution?.patient, result.selected_attribution?.scenario]
      .flatMap((side) => side?.contributions ?? [])
      .filter(({ contribution }) => contribution !== 0)
      .map(({ factor_id }) => factor_id as string),
  );
  const unchanged = (node: GraphNode) => node.path_state === "both" &&
    JSON.stringify(node.patient) === JSON.stringify(node.scenario);
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const collapsible = new Set(graph.nodes.filter((node) => node.kind === "factor" &&
    !nonzero.has(node.subject) && !focused.nodes.has(node.id) && unchanged(node) &&
    graph.edges.filter((edge) => edge.target === node.id || edge.source === node.id).every((edge) =>
      edge.path_state === "both" && (edge.source === node.id ||
        unchanged(byId.get(edge.source)!))))
    .map(({ id }) => id));
  const edges = graph.edges.filter(({ source, target }) => !collapsible.has(source) && !collapsible.has(target));
  const connected = new Set(edges.flatMap(({ source, target }) => [source, target]));
  const compact = graph.nodes.filter((node) => !collapsible.has(node.id) &&
    (node.kind !== "source" || !unchanged(node) || connected.has(node.id)));
  return {
    nodes: expanded ? graph.nodes : compact,
    edges: expanded ? graph.edges : edges,
    hidden: graph.nodes.length - compact.length,
  };
}

const columnWidth = 220;
const rowHeight = 68;
const nodeWidth = 150;
const nodeHeight = 50;
const headerHeight = 46;

function positions(nodes: GraphNode[], previous: Map<string, number>) {
  const slots = new Map(previous);
  const used = new Set(slots.values());
  for (const node of nodes) {
    if (slots.has(node.id)) continue;
    let index = column(node);
    while (used.has(index)) index += columns.length;
    slots.set(node.id, index);
    used.add(index);
  }
  return slots;
}

function edgePath(edge: GraphEdge, point: (id: string) => { x: number; y: number }) {
  const from = point(edge.source);
  const to = point(edge.target);
  const end = to.y + nodeHeight / 2;
  const start = from.y + nodeHeight / 2;
  if (from.x === to.x) {
    const right = from.x + nodeWidth;
    return `M ${right} ${start} C ${right + 48} ${start}, ${right + 48} ${end}, ${right + 4} ${end}`;
  }
  const forward = from.x < to.x;
  const x1 = from.x + (forward ? nodeWidth : 0);
  const x2 = to.x + (forward ? -4 : nodeWidth + 4);
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${start} C ${mid} ${start}, ${mid} ${end}, ${x2} ${end}`;
}

export function ActivePathwayGraph({ result, focused }: {
  result: SimulationResponse;
  focused: ReturnType<typeof factorPath>;
}) {
  const heading = useId();
  const plot = useId();
  const details = useId();
  const { language } = useLanguage();
  const label = useFollowUpText();
  const chart = chartText(language);
  const en = language === "en";
  const lang = en ? 0 : 1;
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState<{ id: string; factorId: string | undefined } | null>(null);
  const graph = result.active_graph;
  const history = useRef(new Map<string, number>());
  const slots = positions(graph.nodes, history.current);
  useLayoutEffect(() => { history.current = slots; }, [slots]);
  const { nodes, edges, hidden } = visibleGraph(result, focused, expanded);
  const factor = nodes.find(({ kind, id }) => kind === "factor" && focused.nodes.has(id));
  const chosen = (selected?.factorId === factor?.id && nodes.find(({ id }) => id === selected?.id)) || factor;
  const point = (id: string) => {
    const index = slots.get(id)!;
    return { x: index % columns.length * columnWidth + 12, y: headerHeight + Math.floor(index / columns.length) * rowHeight };
  };
  const membership = (state: GraphNode["path_state"]) => memberships[state][lang + 2];
  const nodeLabel = (node: GraphNode) => {
    if (node.kind === "factor") return chart.factor(node.subject as FactorId);
    if (node.kind === "treatment" && node.subject.startsWith("oral:")) {
      return oralTreatmentName(node.subject as OralTreatmentId);
    }
    const control = node.kind === "source" && result.controls.find(({ source_path }) => source_path === node.subject);
    if (control) return simulationControlLabel(control, label);
    if (node.kind === "assessment") return node.subject === "panss" ? "PANSS" : "C-SSRS";
    if (node.subject === "final") return en ? "Final selection" : "انتخاب نهایی";
    if (node.subject.startsWith("eligible:oral:")) {
      return `${chart.eligibleRank} · ${oralTreatmentName(node.subject.slice(9) as OralTreatmentId)}`;
    }
    return label(node.subject.split(/[:.]/).at(-1)!);
  };

  return (
    <section className="xi-card" aria-labelledby={heading}>
      <Stack gap={3}>
        <h3 id={heading}>{en ? "Active reasoning pathways" : "مسیرهای فعال استدلال"}</h3>
        {nodes.length > 0 && <>
          <Stack direction="horizontal" gap={2} wrap="wrap" aria-label={en ? "Edge types" : "انواع پیوند"}>
            {Object.entries(edgeKinds).map(([kind, entry]) => (
              <small key={kind}>
                <svg width="28" height="10" aria-hidden="true">
                  <line x1="0" x2="26" y1="5" y2="5" stroke="currentColor" strokeWidth="2" strokeDasharray={entry[2]} />
                </svg>{" "}{entry[lang]}
              </small>
            ))}
          </Stack>
          <Stack direction="horizontal" gap={2} wrap="wrap">
            {(Object.keys(memberships) as GraphNode["path_state"][]).map((state) => (
              <small key={state}>
                <svg viewBox="0 0 9 9" width="14" height="14" aria-hidden="true">
                  <MembershipMark state={state} />
                </svg>{" "}<bdi>{memberships[state][lang]}</bdi> · {membership(state)}
              </small>
            ))}
          </Stack>
          <Stack id={plot} dir="ltr" tabIndex={0} role="region"
            aria-label={en ? "Pathway diagram" : "نمودار مسیرها"}
            style={{ overflow: "auto", maxHeight: "34rem", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>
            <Stack style={{ position: "relative", width: columnWidth * columns.length,
              height: Math.max(headerHeight, ...nodes.map(({ id }) => point(id).y + rowHeight)), flexShrink: 0 }}>
              <Stack direction="horizontal" style={{ position: "sticky", top: 0, zIndex: 1, background: "var(--surface-1)" }}>
                {columns.map((name) => <strong key={name[0]} dir={en ? "ltr" : "rtl"}
                  style={{ width: columnWidth, padding: "var(--space-2)", textAlign: "start" }}>{name[lang]}</strong>)}
              </Stack>
              <svg aria-hidden="true" width="100%" height="100%" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
                <defs>
                  {(Object.keys(memberships) as GraphNode["path_state"][]).map((state) => (
                    <marker key={state} id={`${plot}-${state}`} viewBox="-1 -1 10 10" refX="8" refY="4"
                      markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse" orient="auto">
                      <MembershipMark state={state} />
                    </marker>
                  ))}
                </defs>
                {edges.slice().sort((a, b) => Number(focused.edges.has(a.id)) - Number(focused.edges.has(b.id)))
                  .map((edge) => {
                    const active = focused.edges.has(edge.id);
                    return <path key={edge.id} d={edgePath(edge, point)} fill="none"
                      data-path-state={edge.path_state} data-highlighted={active || undefined}
                      stroke={active ? "var(--primary-action)" : "var(--ink-muted)"} strokeWidth={active ? 3 : 1.5}
                      strokeOpacity={active || edge.path_state !== "patient" ? 0.9 : 0.45}
                      strokeDasharray={edgeKinds[edge.kind][2]} markerEnd={`url(#${plot}-${edge.path_state})`} />;
                  })}
              </svg>
              {nodes.map((node) => {
                const { x, y } = point(node.id);
                const name = nodeLabel(node);
                const active = focused.nodes.has(node.id);
                return <Button key={node.id} type="button" size="sm" variant="secondary"
                  label={`${memberships[node.path_state][lang]} · ${name}`} dir={en ? "ltr" : "rtl"}
                  aria-label={`${name} · ${membership(node.path_state)}`}
                  aria-pressed={chosen?.id === node.id} aria-controls={details} data-highlighted={active || undefined}
                  onClick={() => setSelected({ id: node.id, factorId: factor?.id })}
                  style={{ position: "absolute", left: x, top: y, width: nodeWidth, height: nodeHeight,
                    whiteSpace: "normal", overflowWrap: "anywhere", lineHeight: 1.15,
                    outline: active ? "2px solid var(--primary-action)" : undefined }} />;
              })}
            </Stack>
          </Stack>
          {hidden > 0 && (
            <Stack direction="horizontal">
              <Button type="button" size="sm" variant="secondary" aria-controls={plot} aria-expanded={expanded}
                label={`${expanded ? en ? "Hide unchanged details" : "پنهان کردن جزئیات ثابت"
                  : en ? "Show unchanged details" : "نمایش جزئیات ثابت"} (${chart.count(hidden)})`}
                onClick={() => setExpanded(!expanded)} />
            </Stack>
          )}
          <Stack id={details} gap={1} aria-live="polite" style={{ overflowWrap: "anywhere" }}>
            {chosen ? <>
              <strong>{nodeLabel(chosen)} · {membership(chosen.path_state)}</strong>
              {(["patient", "scenario"] as const).map((side) => {
                const observation = chosen[side];
                const provenance = observation?.provenance;
                const paths = provenance?.paths ?? (provenance?.path ? [provenance.path] : []);
                return <p key={side}>{chart[side]} · <bdi>{!observation ? chart.notEvaluated
                  : chosen.kind === "treatment" && typeof observation.value === "number"
                    ? chart.probability(observation.value)
                    : formatSimulationValue(observation.value, chart, label)}</bdi>
                  {provenance && <small> · {chart.source} · <bdi>{[
                    provenanceLabels[provenance.kind][lang], provenance.source, ...paths,
                  ].filter(Boolean).join(" / ")}</bdi></small>}
                </p>;
              })}
              <ul>{graph.edges.filter(({ source, target }) => source === chosen.id || target === chosen.id)
                .map((edge) => {
                  const from = graph.nodes.find(({ id }) => id === edge.source)!;
                  const to = graph.nodes.find(({ id }) => id === edge.target)!;
                  return <li key={edge.id}><bdi>{nodeLabel(from)} → {nodeLabel(to)}</bdi> ·{" "}
                    {edgeKinds[edge.kind][lang]} · {membership(edge.path_state)}</li>;
                })}</ul>
            </> : <p>{en ? "Select a node to see its values and sources." : "برای دیدن مقادیر و منابع، یک گره را انتخاب کنید."}</p>}
          </Stack>
        </>}
      </Stack>
    </section>
  );
}
