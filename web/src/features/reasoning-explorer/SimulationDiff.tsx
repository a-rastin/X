import { Stack } from "@astryxdesign/core/Stack";
import { useLanguage } from "../../shared/i18n";
import { useFollowUpText } from "../follow-up/text";
import { chartText, oralTreatmentName } from "./chartPresentation";
import { simulationControlLabel, usesAutomaticValue } from "./WhatIfControls";
import type { Exclusion, JsonValue, SimulationResponse } from "./types";

export function formatSimulationValue(
  value: JsonValue, chart: ReturnType<typeof chartText>, label: (key: string) => string,
): string {
  if (value === null) return chart.unrecorded;
  if (typeof value === "boolean") return value ? chart.yes : chart.no;
  if (typeof value === "number") return chart.count(value);
  if (typeof value === "string") return value.startsWith("oral:")
    ? oralTreatmentName(value as Parameters<typeof oralTreatmentName>[0]) : label(value);
  if (Array.isArray(value)) return value.map((item) => formatSimulationValue(item, chart, label)).join(", ") || "—";
  return Object.entries(value).map(([key, item]) =>
    `${label(key)} · ${formatSimulationValue(item, chart, label)}`).join(" / ") || "—";
}

export function SimulationDiff({ result }: { result: SimulationResponse }) {
  const { language } = useLanguage();
  const label = useFollowUpText();
  const chart = chartText(language);
  const en = language === "en";
  const { diff, evaluation } = result;
  const { patient, scenario } = diff.selection;
  const p = patient.top;
  const s = scenario.top;
  const changes = diff.message_parameters.changed_control_ids;
  const changedSections = Object.entries(diff.sections)
    .filter(([key]) => !["antipsychotic_candidates", "antipsychotic_route"].includes(key));
  const rankingChanged = patient.top !== null && scenario.top !== null &&
    patient.candidate_ids.join() !== scenario.candidate_ids.join();
  const sameRanking = (patient.top !== null || scenario.top !== null) &&
    patient.candidate_ids.join() === scenario.candidate_ids.join();
  const valueText = (value: JsonValue) => formatSimulationValue(value, chart, label);
  const absent = en ? "None" : "هیچ‌کدام";
  const topName = (row: typeof p) => row
    ? `${oralTreatmentName(row.treatment_id)} (${chart.probability(row.probability)})` : absent;
  const terminal = (value: string | null | undefined) => value ? label(value) : chart.notEvaluated;
  const decisionRows = (["original_decision", "final_decision"] as const).filter((key) =>
    patient[key]?.terminal !== scenario[key]?.terminal);
  const sameDecisionRows = decisionRows.length === 2 &&
    patient.original_decision?.terminal === patient.final_decision?.terminal &&
    scenario.original_decision?.terminal === scenario.final_decision?.terminal;
  const exclusions = Object.entries(diff.exclusions);
  const exclusionText = (reasons: Exclusion[] | null) =>
    reasons?.length ? reasons.map(chart.exclusion).join(", ") : absent;
  const headline = {
    no_change: en ? "No output change" : "بدون تغییر در نتایج",
    inputs_changed: Object.keys(diff.probabilities).length || rankingChanged
      ? en ? "Treatment results changed" : "نتایج درمان تغییر کرد"
      : en ? "No output change" : "بدون تغییر در نتایج",
    top_choice_changed: en ? "Top eligible treatment changed" : "درمان واجد شرایطِ نخست تغییر کرد",
    top_probability_changed: en ? "Top treatment probability changed" : "احتمال درمان نخست تغییر کرد",
    eligibility_changed: en ? "Treatment eligibility changed" : "شرایط انتخاب درمان تغییر کرد",
    decision_branch_changed: en ? "Decision branch changed" : "شاخهٔ تصمیم تغییر کرد",
    assessment_changed: en ? "Assessment changed" : "ارزیابی تغییر کرد",
    section_changed: en ? "Care sections changed" : "بخش‌های مراقبت تغییر کردند",
  }[diff.banner_kind];

  function tie(side: typeof patient, name: string) {
    return side.tied_eligible_ids.length > 1 && (
      <li>{name} · {en ? "Equal top probability" : "احتمال نخست برابر"} ({chart.count(side.tied_eligible_ids.length)}) ·{" "}
        <bdi>{side.tied_eligible_ids.map(oralTreatmentName).join(", ")}</bdi></li>
    );
  }

  return (
    <section className="xi-card" aria-live="polite" aria-atomic="true"
      aria-label={en ? "Simulation comparison" : "مقایسهٔ شبیه‌سازی"}>
      <Stack gap={2}>
        <h4>{headline}</h4>
        {changes.length > 0 && (
          <details open>
            <summary>{changes.length > 1
              ? en ? "Combined scenario inputs" : "ورودی‌های سناریوی ترکیبی"
              : en ? "Changed input" : "ورودی تغییریافته"}</summary>
            <ul>{changes.map((id) => {
              const control = result.controls.find((item) => item.control_id === id);
              const delta = diff.raw_inputs[id];
              if (!delta) return null;
              const format = (value: JsonValue) => value === null && control && usesAutomaticValue(control)
                ? en ? "Automatic" : "خودکار" : valueText(value);
              return <li key={id}>{control ? simulationControlLabel(control, label) : id} ·{" "}
                <bdi>{format(delta.patient)}</bdi> → <bdi>{format(delta.scenario)}</bdi></li>;
            })}</ul>
          </details>
        )}
        {(p?.treatment_id !== s?.treatment_id || p?.probability !== s?.probability) && (
          <p>{en ? "Top eligible treatment" : "درمان واجد شرایطِ نخست"} ·{" "}
            <bdi>{topName(p)}</bdi> → <bdi>{topName(s)}</bdi>
            {p && s && p.treatment_id === s.treatment_id && <> (<bdi dir="ltr">
              {chart.signedPoints(s.probability - p.probability)}</bdi> {chart.pp})</>}
          </p>
        )}
        {(p?.treatment_id !== s?.treatment_id || p?.probability !== s?.probability ||
          patient.tied_eligible_ids.join() !== scenario.tied_eligible_ids.join()) &&
          (patient.tied_eligible_ids.length > 1 || scenario.tied_eligible_ids.length > 1) && (
            <ul>{tie(patient, chart.patient)}{tie(scenario, chart.scenario)}</ul>
        )}
        {sameRanking && (changedSections.length > 0 || Object.keys(diff.assessments).length > 0) && (
          <p>{en ? "Treatment ranking unchanged" : "رتبه‌بندی درمان بدون تغییر است"}</p>
        )}
        {rankingChanged && p?.treatment_id === s?.treatment_id && (
          <p>{en ? "The order of eligible treatments changed below the first choice."
            : "ترتیب درمان‌های واجد شرایط پس از انتخاب نخست تغییر کرد."}</p>
        )}
        {decisionRows.slice(0, sameDecisionRows ? 1 : undefined).map((key) => (
          <p key={key}>{sameDecisionRows ? en ? "Decision" : "تصمیم"
            : key === "original_decision" ? en ? "Initial decision" : "تصمیم اولیه"
              : en ? "Final decision" : "تصمیم نهایی"} ·{" "}
            <bdi>{terminal(patient[key]?.terminal)}</bdi> → <bdi>{terminal(scenario[key]?.terminal)}</bdi>
          </p>
        ))}
        {(patient.direct?.treatment_id !== scenario.direct?.treatment_id) && (
          <p>{en ? "Direct treatment" : "درمان مستقیم"} ·{" "}
            <bdi>{patient.direct?.treatment_id ? valueText(patient.direct.treatment_id) : absent}</bdi> →{" "}
            <bdi>{scenario.direct?.treatment_id ? valueText(scenario.direct.treatment_id) : absent}</bdi></p>
        )}
        {exclusionText(patient.direct?.exclusion_reasons ?? null) !== exclusionText(scenario.direct?.exclusion_reasons ?? null) && (
          <p>{en ? "Direct treatment exclusions" : "دلایل حذف درمان مستقیم"} ·{" "}
            {exclusionText(patient.direct?.exclusion_reasons ?? null)} → {exclusionText(scenario.direct?.exclusion_reasons ?? null)}</p>
        )}
        {exclusions.length > 0 && (
          <details open={diff.banner_kind === "eligibility_changed" || undefined}>
            <summary>{en ? "Changed candidate exclusions" : "دلایل تغییریافتهٔ حذف گزینه‌ها"} ({chart.count(exclusions.length)})</summary>
            <ul>{exclusions.map(([id, delta]) => <li key={id}><bdi>{valueText(id)}</bdi> ·{" "}
              {exclusionText(delta.patient)} → {exclusionText(delta.scenario)}</li>)}</ul>
          </details>
        )}
        {changedSections.map(([key, delta]) => {
          const sectionValue = (value: JsonValue) =>
            value && typeof value === "object" && !Array.isArray(value)
              ? value.setting ?? value.value ?? value
              : value;
          const before = valueText(sectionValue(delta.patient));
          const after = valueText(sectionValue(delta.scenario));
          return <p key={key}>{label(key)} · {before === after
            ? <><bdi>{before}</bdi> · {en ? "Details changed" : "جزئیات تغییر کرد"}</>
            : <><bdi>{before}</bdi> → <bdi>{after}</bdi></>}</p>;
        })}
        {(["panss", "cssrs"] as const).map((key) => {
          const assessment = diff.assessments[key];
          if (!assessment) return null;
          const score = key === "panss" ? "total" : "ideation_severity_recent";
          const before = assessment.patient?.scores[score] ?? null;
          const after = assessment.scenario?.scores[score] ?? null;
          const status = (value: typeof assessment.patient) => value?.status
            ? label(value.status) : chart.notEvaluated;
          const changed = before !== after || assessment.patient?.status !== assessment.scenario?.status;
          return <p key={key}>{key === "panss" ? "PANSS" : "C-SSRS"} · {changed
            ? <><bdi>{before === null ? status(assessment.patient) : chart.count(before)}</bdi> →{" "}
              <bdi>{after === null ? status(assessment.scenario) : chart.count(after)}</bdi></>
            : en ? "Responses or findings changed" : "پاسخ‌ها یا یافته‌ها تغییر کردند"}</p>;
        })}
        {evaluation.scenario.status === "partial" && (
          <p>{en ? "Some scenario outputs still need answers." : "برخی نتایج سناریو هنوز به پاسخ نیاز دارند."}</p>
        )}
        {evaluation.scenario.unevaluated_reason && result.scenario.distribution.length === 0 && (
          <p>{en ? "Model probabilities" : "احتمال‌های مدل"} · {chart.notEvaluated}</p>
        )}
      </Stack>
    </section>
  );
}
