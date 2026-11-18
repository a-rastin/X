import { useId, useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { Stack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useLanguage } from "../../shared/i18n";
import { ChartValuesTable, chartText, oralTreatmentName } from "./chartPresentation";
import type { Attribution, FactorId, JsonValue, OralTreatmentId, SimulationResponse } from "./types";

type Props = {
  attribution: Attribution | null;
  context: Pick<SimulationResponse, "patient" | "scenario" | "active_graph">;
  selectedFactorId: FactorId | null;
  onSelectFactor: (id: FactorId) => void;
  treatmentLabel?: (id: OralTreatmentId) => string;
};

const numericallyZero = 1e-12;
const sides = ["patient", "scenario"] as const;

function ContributionMarks({ patient, scenario, extent }: {
  patient: number | null;
  scenario: number | null;
  extent: number;
}) {
  const center = 52;
  return (
    <svg aria-hidden="true" viewBox="0 0 104 28" preserveAspectRatio="none"
      style={{ display: "block", direction: "ltr", width: "100%", height: "1.75em" }}>
      <line x1={2} x2={102} y1={14} y2={14} stroke="var(--border)"
        vectorEffect="non-scaling-stroke" />
      {sides.map((side) => {
        const value = side === "patient" ? patient : scenario;
        if (value === null) return null;
        const offset = 50 * value / extent;
        return (
          <rect key={side} className={`xi-${side}-contribution`}
            x={center + Math.min(0, offset)} y={side === "patient" ? 3 : 15}
            width={Math.abs(offset)} height={10}
            fill={side === "patient" ? "none" : "var(--primary-action)"}
            stroke={side === "patient" ? "var(--ink-muted)" : undefined}
            vectorEffect="non-scaling-stroke" />
        );
      })}
      <line className="xi-model-reference" x1={center} x2={center} y1={0} y2={28}
        stroke="var(--ink)" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function FactorDetails({ id, attribution, context, label, treatmentName }: {
  id: FactorId;
  attribution: Attribution;
  context: Props["context"];
  label: ReturnType<typeof chartText>;
  treatmentName: string;
}) {
  const factorNode = `factor:${id}`;
  const treatmentNode = `treatment:${attribution.treatment_id}`;
  const { edges, nodes } = context.active_graph;
  const showsPath = edges.some((edge) => edge.kind === "bn_dependency"
    && edge.source === factorNode && edge.target === treatmentNode);
  const incoming = edges.filter((edge) => edge.kind === "input_derivation" && edge.target === factorNode);
  const displayValue = (value: JsonValue) => {
    if (value === null) return label.unrecorded;
    if (typeof value === "boolean") return value ? label.yes : label.no;
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  };

  return (
    <Stack gap={1} padding={2} style={{ background: "var(--surface-1)", borderRadius: "var(--radius-sm)" }}>
      <Text type="supporting">
        {label.pathway} · {showsPath ? <bdi>{label.factor(id)} / {treatmentName}</bdi> : label.noPathway}
      </Text>
      {sides.map((side) => {
        if (!attribution[side]) return null;
        const factor = context[side].factors[id];
        const sources = incoming
          .filter((edge) => edge.path_state === side || edge.path_state === "both")
          .flatMap((edge) => {
            const source = nodes.find((node) => node.id === edge.source);
            const observation = source?.[side];
            return source && observation ? [{ path: source.subject, value: observation.value }] : [];
          });
        return (
          <Stack key={side} gap={0.5}>
            <Text type="supporting">
              {label[side]} · {label.effectiveValue} · {displayValue(factor.value)}
            </Text>
            {sources.map((source) => (
              <Text key={source.path} type="supporting" style={{ overflowWrap: "anywhere" }}>
                {label.source} · <bdi dir="ltr">{source.path}</bdi> · {displayValue(source.value)}
              </Text>
            ))}
            {sources.length === 0 && (
              <Text type="supporting" style={{ overflowWrap: "anywhere" }}>
                {label.source} · <bdi dir="ltr">{factor.source}</bdi>
              </Text>
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}

export function FactorAttributionTornado({
  attribution, context, selectedFactorId, onSelectFactor, treatmentLabel = oralTreatmentName,
}: Props) {
  const { language } = useLanguage();
  const label = chartText(language);
  const headingId = useId();
  const listId = useId();
  const [expanded, setExpanded] = useState(false);

  // Both payloads use the pinned parent order. Stable sorting preserves it on ties.
  const patient = new Map(attribution?.patient?.contributions.map((factor) => [factor.factor_id, factor.contribution]));
  const scenario = new Map(attribution?.scenario?.contributions.map((factor) => [factor.factor_id, factor.contribution]));
  const ordered = (attribution?.patient ?? attribution?.scenario)?.contributions.map(({ factor_id }) => {
    const patientValue = patient.get(factor_id) ?? null;
    const scenarioValue = scenario.get(factor_id) ?? null;
    return {
      id: factor_id, patient: patientValue, scenario: scenarioValue,
      magnitude: Math.max(Math.abs(patientValue ?? 0), Math.abs(scenarioValue ?? 0)),
    };
  }).sort((left, right) => right.magnitude - left.magnitude) ?? [];
  const visible = ordered.filter((row) => row.magnitude > numericallyZero || row.id === selectedFactorId);
  const hidden = ordered.length - visible.length;
  const extent = ordered[0]?.magnitude > numericallyZero ? ordered[0].magnitude : 0.01;
  const name = attribution ? treatmentLabel(attribution.treatment_id) : "";
  const points = (value: number | null) => value === null
    ? label.notEvaluated
    : <><bdi dir="ltr">{label.signedPoints(value)}</bdi> {label.pp}</>;
  const tablePoints = (value: number | null) => value === null
    ? label.notEvaluated
    : `${label.signedPoints(value)} ${label.pp}`;

  return (
    <section aria-labelledby={headingId} className="xi-card" dir={language === "fa" ? "rtl" : "ltr"}>
      <Stack gap={3}>
        <h3 id={headingId}>{label.tornado}{attribution && <> · <bdi>{name}</bdi></>}</h3>
        {attribution === null ? (
          <Text as="p" type="supporting">{label.notEvaluated}</Text>
        ) : (
          <>
            <Stack gap={1} padding={2} style={{ background: "var(--surface-1)", borderRadius: "var(--radius-sm)" }}>
              <Text type="label">
                {label.modelReference} · <bdi dir="ltr">{label.probability(attribution.model_reference_probability)}</bdi>
              </Text>
              {sides.map((side) => {
                const result = attribution[side];
                return (
                  <Text key={side} type="supporting">
                    {label[side]} · {result === null ? label.notEvaluated : <>
                      {label.posterior} <bdi dir="ltr">{label.probability(result.posterior_probability)}</bdi>
                      {" · "}{label.reconstruction} <bdi dir="ltr">
                        {label.probability(result.posterior_probability - result.reconstruction_residual)}
                      </bdi>
                    </>}
                  </Text>
                );
              })}
            </Stack>
            <Stack direction="horizontal" gap={3} wrap="wrap">
              <Text type="supporting">{label.patientOutline}</Text>
              <Text type="supporting">{label.scenarioBar}</Text>
            </Stack>
            <Text as="p" type="supporting">{label.contributionAxis}</Text>
            <Stack direction="horizontal" justify="between" dir="ltr" aria-hidden="true">
              {[-extent, 0, extent].map((value) => (
                <Text key={value} type="supporting">{label.axisPoints(value)}</Text>
              ))}
            </Stack>
            <ul id={listId} role="list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {(expanded ? ordered : visible).map((row) => {
                const showDetails = row.id === selectedFactorId;
                const detailId = `${listId}-${row.id}`;
                return (
                  <li key={row.id} style={{ borderBlockStart: "1px solid var(--border)" }}>
                    <Stack gap={1} paddingBlock={2}>
                      <Stack direction="horizontal">
                        <Button type="button" size="sm" variant={row.id === selectedFactorId ? "secondary" : "ghost"}
                          style={{ whiteSpace: "normal", textAlign: "start", height: "auto", minHeight: "2em" }}
                          label={label.factor(row.id)} aria-pressed={row.id === selectedFactorId}
                          aria-describedby={showDetails ? detailId : undefined}
                          onFocus={() => onSelectFactor(row.id)}
                          onClick={() => onSelectFactor(row.id)} />
                      </Stack>
                      <ContributionMarks patient={row.patient} scenario={row.scenario} extent={extent} />
                      <Stack direction="horizontal" gap={2} wrap="wrap">
                        <Text type="supporting">{label.patient} · {points(row.patient)}</Text>
                        <Text type="supporting">{label.scenario} · {points(row.scenario)}</Text>
                        {row.patient !== null && row.scenario !== null && (
                          <Text type="supporting">{label.change} · {points(row.scenario - row.patient)}</Text>
                        )}
                      </Stack>
                      {showDetails && (
                        <Stack id={detailId}>
                          <FactorDetails id={row.id} attribution={attribution}
                            context={context} label={label} treatmentName={name} />
                        </Stack>
                      )}
                    </Stack>
                  </li>
                );
              })}
            </ul>
            {hidden > 0 && (
              <Stack direction="horizontal">
                <Button type="button" size="sm" variant="secondary" aria-controls={listId} aria-expanded={expanded}
                  label={`${expanded ? label.hideZero : label.showZero} (${label.count(hidden)})`}
                  onClick={() => setExpanded(!expanded)} />
              </Stack>
            )}
            <ChartValuesTable label={label} caption={label.tornado} heading={label.factorName}
              rows={ordered.map((row) => ({
                id: row.id,
                name: label.factor(row.id),
                patient: tablePoints(row.patient),
                scenario: tablePoints(row.scenario),
                change: tablePoints(row.patient === null || row.scenario === null ? null : row.scenario - row.patient),
              }))} />
          </>
        )}
      </Stack>
    </section>
  );
}
