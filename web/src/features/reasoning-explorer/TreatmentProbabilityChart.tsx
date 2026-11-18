import { useEffect, useId, useRef, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Stack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useLanguage } from "../../shared/i18n";
import { ChartValuesTable, chartText, oralTreatmentName } from "./chartPresentation";
import type { ComparisonRow, OralTreatmentId, SimulationResponse } from "./types";

type Props = {
  comparison: Pick<SimulationResponse, "comparison_rows" | "diff">;
  selectedTreatmentId: OralTreatmentId | null;
  onSelectTreatment: (id: OralTreatmentId) => void;
  isInteracting?: boolean;
  treatmentLabel?: (id: OralTreatmentId) => string;
};

function compactRows(
  { comparison_rows, diff }: Props["comparison"],
  selected: OralTreatmentId | null,
) {
  const candidateIds = new Set([
    ...diff.selection.patient.candidate_ids,
    ...diff.selection.scenario.candidate_ids,
  ]);
  return comparison_rows.filter((row) => {
    const eligibilityChanged =
      (row.patient_eligible_rank === null) !== (row.scenario_eligible_rank === null);
    const newlyExcluded = row.scenario_exclusion_reasons.some(
      (reason) => !row.patient_exclusion_reasons.includes(reason),
    );
    return candidateIds.has(row.treatment_id) || row.treatment_id === selected
      || eligibilityChanged || newlyExcluded;
  });
}

function ProbabilityMarks({ row }: { row: ComparisonRow }) {
  const patient = row.patient_probability;
  const scenario = row.scenario_probability;
  const position = (probability: number) => 2 + 100 * probability;
  return (
    <svg aria-hidden="true" viewBox="0 0 104 24" preserveAspectRatio="none"
      style={{ display: "block", direction: "ltr", width: "100%", height: "1.5em" }}>
      <rect x={2} y={6} width={100} height={12} fill="var(--surface-2)" />
      {[0, 0.25, 0.5, 0.75, 1].map((p) => (
        <line key={p} x1={position(p)} x2={position(p)} y1={4} y2={20}
          stroke="var(--border-strong)" vectorEffect="non-scaling-stroke" />
      ))}
      {scenario !== null && (
        <rect className="xi-scenario-bar" x={2} y={6} width={100 * scenario} height={12}
          fill="var(--primary-action)" />
      )}
      {scenario === 0 && (
        <line x1={2} x2={2} y1={6} y2={18} stroke="var(--primary-action)"
          strokeWidth={2} vectorEffect="non-scaling-stroke" />
      )}
      {patient !== null && (
        <g>
          <line x1={position(patient)} x2={position(patient)} y1={1} y2={23}
            stroke="var(--canvas)" strokeWidth={4} vectorEffect="non-scaling-stroke" />
          <line className="xi-patient-tick" x1={position(patient)} x2={position(patient)} y1={1} y2={23}
            stroke="var(--ink)" strokeWidth={2} strokeDasharray="1 3" strokeLinecap="round"
            vectorEffect="non-scaling-stroke" />
        </g>
      )}
    </svg>
  );
}

export function TreatmentProbabilityChart({
  comparison, selectedTreatmentId, onSelectTreatment,
  isInteracting = false, treatmentLabel = oralTreatmentName,
}: Props) {
  const { language } = useLanguage();
  const label = chartText(language);
  const headingId = useId();
  const listId = useId();
  const [expanded, setExpanded] = useState(false);
  const settledOrder = useRef<OralTreatmentId[]>([]);
  const all = comparison.comparison_rows;
  const compact = compactRows(comparison, selectedTreatmentId);
  const collapsed = compact.length > 0 ? compact : all;
  const byTreatmentId = (a: ComparisonRow, b: ComparisonRow) =>
    a.treatment_id.localeCompare(b.treatment_id, "en");
  const visible = [...(expanded ? all : collapsed)].sort(byTreatmentId);

  useEffect(() => {
    if (!isInteracting) settledOrder.current = [...collapsed].sort(byTreatmentId).map((row) => row.treatment_id);
  });

  // Retain the last settled rows during a drag and append newly relevant rows at the end.
  const byId = new Map(all.map((row) => [row.treatment_id, row]));
  const stableRows = isInteracting && !expanded
    ? settledOrder.current.flatMap((id) => byId.get(id) ?? [])
    : [];
  const stableIds = new Set(stableRows.map((row) => row.treatment_id));
  const rows = [...stableRows, ...visible.filter((row) => !stableIds.has(row.treatment_id))];

  const sides = (row: ComparisonRow) => [
    {
      name: label.patient, value: row.patient_probability, rank: row.patient_eligible_rank,
      exclusions: row.patient_exclusion_reasons,
    },
    {
      name: label.scenario, value: row.scenario_probability, rank: row.scenario_eligible_rank,
      exclusions: row.scenario_exclusion_reasons,
    },
  ];

  return (
    <section aria-labelledby={headingId} className="xi-card" dir={language === "fa" ? "rtl" : "ltr"}>
      <Stack gap={3}>
        <h3 id={headingId}>{label.probabilities}</h3>
        {all.length === 0 ? (
          <Text as="p" type="supporting">{label.notEvaluated}</Text>
        ) : (
          <>
            <Stack direction="horizontal" gap={3} wrap="wrap">
              <Text type="supporting">{label.patientTick}</Text>
              <Text type="supporting">{label.scenarioBar}</Text>
            </Stack>
            <Stack direction="horizontal" justify="between" dir="ltr" aria-hidden="true">
              {[0, 0.25, 0.5, 0.75, 1].map((p) => (
                <Text key={p} type="supporting">{label.probability(p)}</Text>
              ))}
            </Stack>
            <ul id={listId} role="list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {rows.map((row) => (
                <li key={row.treatment_id} style={{ borderBlockStart: "1px solid var(--border)" }}>
                  <Stack gap={1} paddingBlock={2} align="stretch">
                    <Stack direction="horizontal" gap={2} wrap="wrap" align="center">
                      <Button type="button" size="sm" variant={selectedTreatmentId === row.treatment_id ? "secondary" : "ghost"}
                        label={treatmentLabel(row.treatment_id)}
                        aria-pressed={selectedTreatmentId === row.treatment_id}
                        onClick={() => onSelectTreatment(row.treatment_id)} />
                      {sides(row).map(({ name, rank }) => rank !== null && (
                        <Text key={name} type="supporting">
                          {name} · {label.eligibleRank} {label.count(rank)}
                        </Text>
                      ))}
                    </Stack>
                    <ProbabilityMarks row={row} />
                    <Stack direction="horizontal" gap={2} wrap="wrap">
                      {sides(row).map(({ name, value, exclusions }) => (
                        <Stack key={name} direction="horizontal" gap={1} wrap="wrap" align="center">
                          <Text type="supporting">
                            {name} · {value === null ? label.notEvaluated : <bdi dir="ltr">{label.probability(value)}</bdi>}
                          </Text>
                          {exclusions.map((reason) => (
                            <Badge key={reason} label={label.exclusion(reason)} />
                          ))}
                        </Stack>
                      ))}
                      {row.probability_delta !== null && (
                        <Text type="supporting">
                          {label.change} · <bdi dir="ltr">{label.signedPoints(row.probability_delta)}</bdi> {label.pp}
                        </Text>
                      )}
                    </Stack>
                  </Stack>
                </li>
              ))}
            </ul>
            {collapsed.length < all.length && (
              <Stack direction="horizontal">
                <Button type="button" size="sm" variant="secondary" aria-controls={listId} aria-expanded={expanded}
                  label={expanded ? label.showFewer : `${label.showAll} (${label.count(all.length)})`}
                  onClick={() => setExpanded(!expanded)} />
              </Stack>
            )}
            <ChartValuesTable label={label} caption={label.probabilities} heading={label.treatment}
              rows={[...all].sort(byTreatmentId).map((row) => ({
                id: row.treatment_id,
                name: treatmentLabel(row.treatment_id),
                patient: row.patient_probability === null ? label.notEvaluated : label.probability(row.patient_probability),
                scenario: row.scenario_probability === null ? label.notEvaluated : label.probability(row.scenario_probability),
                change: row.probability_delta === null ? label.notEvaluated : `${label.signedPoints(row.probability_delta)} ${label.pp}`,
              }))} />
          </>
        )}
      </Stack>
    </section>
  );
}
