import { useEffect, useState } from "react";
import { ApiError, readGraph, type GraphNetwork, type GraphPayload } from "./api";
import { useAuth } from "../../identity/auth";

/* Read-only network graph (S24 slice 2, seam T9).
 *
 * Renders the actual ordered nodes/edges/readable states plus validation
 * status from GET /network-versions/{id}/graph. There is deliberately no
 * graphical edit operation: no click/drag/keyboard handler mutates nodes or
 * edges, and none is wired for the future e2e spec to find. Layout is a
 * plain layered SVG (columns by longest-path depth, DEFINITION order within
 * a column) plus ordered text lists, so no new layout dependency was needed.
 * Semantic tokens only; keyboard-focusable figure with text fallback.
 */

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; payload: GraphPayload };

const NODE_WIDTH = 168;
const ROW_HEIGHT = 22;
const HEADER_HEIGHT = 26;
const COLUMN_GAP = 96;
const ROW_GAP = 28;
const PAD = 16;

function nodeHeight(node: string, states: Record<string, string[]>): number {
  return HEADER_HEIGHT + (states[node] ?? []).length * ROW_HEIGHT + 12;
}

function layoutDepths(graph: GraphNetwork): Map<string, number> {
  const depth = new Map<string, number>();
  for (const node of graph.nodes) {
    depth.set(node, 0);
  }
  // Edges arrive in DEFINITION/GIVEN order with parents declared before
  // children (XSD order), so one longest-path pass over ordered edges holds.
  // Unknown endpoints are ignored rather than rendered as phantom nodes.
  for (const [parent, child] of graph.edges) {
    if (!depth.has(parent) || !depth.has(child)) {
      continue;
    }
    const next = (depth.get(parent) ?? 0) + 1;
    if (next > (depth.get(child) ?? 0)) {
      depth.set(child, next);
    }
  }
  return depth;
}

function GraphSvg({ graph, idSuffix }: { graph: GraphNetwork; idSuffix: string }) {
  const depths = layoutDepths(graph);
  const columns = new Map<number, string[]>();
  for (const node of graph.nodes) {
    const column = depths.get(node) ?? 0;
    const list = columns.get(column) ?? [];
    list.push(node);
    columns.set(column, list);
  }
  const positions = new Map<string, { x: number; y: number; height: number }>();
  let maxX = 0;
  let maxY = 0;
  for (const [column, nodes] of [...columns.entries()].sort(([a], [b]) => a - b)) {
    // Compact per-column stacking: each node starts below the previous
    // node's own height, so small networks stay readable without scrolling.
    let cursor = PAD;
    nodes.forEach((node) => {
      const height = nodeHeight(node, graph.states);
      const x = PAD + column * (NODE_WIDTH + COLUMN_GAP);
      const y = cursor;
      positions.set(node, { x, y, height });
      maxX = Math.max(maxX, x + NODE_WIDTH);
      maxY = Math.max(maxY, y + height);
      cursor += height + ROW_GAP;
    });
  }
  const width = maxX + PAD;
  const height = Math.max(maxY + PAD, 120);
  // Marker ids must be URL-safe: the stored network name is arbitrary text,
  // so the id uses the graph position, never the raw name.
  const markerId = `xi-arrow-${idSuffix}`;

  return (
    <svg
      role="img"
      tabIndex={0}
      aria-label={`Network ${graph.network_name}: ${graph.nodes.join(", ")}`}
      viewBox={`0 0 ${width} ${height}`}
      style={{ width: "100%", maxWidth: width, height: "auto", display: "block" }}
    >
      <defs>
        <marker
          id={markerId}
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--ink-muted)" />
        </marker>
      </defs>
      {graph.edges.map(([parent, child], index) => {
        const from = positions.get(parent);
        const to = positions.get(child);
        if (!from || !to) {
          return null;
        }
        const x1 = from.x + NODE_WIDTH;
        const y1 = from.y + HEADER_HEIGHT / 2;
        const x2 = to.x;
        const y2 = to.y + HEADER_HEIGHT / 2;
        return (
          <line
            key={`${parent}-${child}-${index}`}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke="var(--ink-muted)"
            strokeWidth={1.5}
            markerEnd={`url(#${markerId})`}
          />
        );
      })}
      {graph.nodes.map((node) => {
        const pos = positions.get(node);
        if (!pos) {
          return null;
        }
        const states = graph.states[node] ?? [];
        return (
          <g key={node}>
            <rect
              x={pos.x}
              y={pos.y}
              width={NODE_WIDTH}
              height={pos.height}
              rx={8}
              fill="var(--surface-2)"
              stroke="var(--border-strong)"
              strokeWidth={1.5}
            />
            <text
              x={pos.x + 10}
              y={pos.y + 18}
              fill="var(--ink)"
              fontSize={13}
              fontWeight={700}
            >
              {node}
            </text>
            {states.map((state, index) => (
              <text
                key={state}
                x={pos.x + 10}
                y={pos.y + HEADER_HEIGHT + 16 + index * ROW_HEIGHT}
                fill="var(--ink-muted)"
                fontSize={12}
              >
                {state}
              </text>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

export function GraphView({ versionId }: { versionId: string }) {
  const { sessionExpired } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    readGraph(versionId)
      .then((payload) => {
        if (!cancelled) {
          setState({ kind: "ready", payload });
        }
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          sessionExpired();
          return;
        }
        setState({
          kind: "error",
          message:
            err instanceof ApiError
              ? err.message
              : "Graph failed to load. Check the connection and retry.",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, sessionExpired]);

  if (state.kind === "loading") {
    return (
      <section className="xi-card" aria-labelledby="graph-heading" aria-busy="true">
        <h3 className="xi-section-title" id="graph-heading">
          Read-only graph
        </h3>
        <p>Loading graph…</p>
      </section>
    );
  }

  if (state.kind === "error") {
    return (
      <section className="xi-card" aria-labelledby="graph-heading">
        <h3 className="xi-section-title" id="graph-heading">
          Read-only graph
        </h3>
        <p className="xi-form-error" role="alert">
          {state.message}
        </p>
      </section>
    );
  }

  const { payload } = state;
  const validation = payload.validation;

  return (
    <section
      className="xi-card"
      aria-labelledby="graph-heading"
      data-testid="networks-graph"
      id="networks-graph"
    >
      <h3 className="xi-section-title" id="graph-heading">
        Read-only graph
      </h3>
      <p className="xi-hint">
        Version {payload.version_number} · source {payload.source_hash.slice(0, 12)}…
        Structure is read only; the graph cannot be edited here.
      </p>
      <p>
        <span className={validation.xsd_valid ? "xi-badge xi-badge-active" : "xi-badge xi-badge-inactive"}>
          {validation.xsd_valid ? "XSD structurally valid" : "XSD structurally invalid"}
        </span>{" "}
        <span className={validation.semantic_valid ? "xi-badge xi-badge-active" : "xi-badge xi-badge-inactive"}>
          {validation.semantic_valid ? "Semantics valid" : "Semantics invalid"}
        </span>{" "}
        <span className="xi-badge">
          {validation.activatable_v1 ? "Activatable (v1 admission)" : "Not activatable (v1 admission)"}
        </span>
      </p>
      {validation.nonactivatable_reasons.length > 0 && (
        <ul aria-label="Reasons this version cannot activate">
          {validation.nonactivatable_reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {payload.graphs.length === 0 ? (
        <p>No networks in this version.</p>
      ) : (
        payload.graphs.map((graph, graphIndex) => (
          <article key={graph.network_name} aria-label={`Network ${graph.network_name}`}>
            <h4>{graph.network_name}</h4>
            <GraphSvg graph={graph} idSuffix={String(graphIndex)} />
            <h5>Variables in stored order</h5>
            <table className="xi-table" aria-label={`Variables of ${graph.network_name}`}>
              <thead>
                <tr>
                  <th scope="col">Variable</th>
                  <th scope="col">Outcomes in stored order</th>
                </tr>
              </thead>
              <tbody>
                {graph.nodes.map((node) => (
                  <tr key={node}>
                    <td>{node}</td>
                    <td>{(graph.states[node] ?? []).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h5>Edges in definition order</h5>
            {graph.edges.length === 0 ? (
              <p>No edges: all variables are roots.</p>
            ) : (
              <ol>
                {graph.edges.map(([parent, child], index) => (
                  <li key={`${parent}-${child}-${index}`}>
                    {parent} → {child}
                  </li>
                ))}
              </ol>
            )}
          </article>
        ))
      )}
    </section>
  );
}
