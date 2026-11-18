import { useEffect, useId, useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { Slider } from "@astryxdesign/core/Slider";
import { Stack } from "@astryxdesign/core/Stack";
import { getCssrsDefinition, type CssrsDefinition } from "../assessments/cssrs/api";
import { getPanssDefinition, type PanssDefinition } from "../assessments/panss/api";
import { useLanguage } from "../../shared/i18n";
import { clinicalText } from "../../shared/messages";
import { useFollowUpText } from "../follow-up/text";
import { chartText } from "./chartPresentation";
import { changeId } from "./useReasoningSimulation";
import type { Change, Control, ControlValue, FactorId, SimulationResponse } from "./types";

const sourceLabels: Record<string, string> = {
  "substances.present": "substances",
  "substances.other": "otherSubstance",
  "psychiatric.present": "psychiatric",
  "bmi.source": "bmiMode",
  "bmi.manual_category": "bmiCategory",
};

export function simulationControlLabel(control: Control, label: (key: string) => string) {
  const id = control.control_id;
  if (control.kind === "panss") return `PANSS ${id}`;
  if (control.kind === "cssrs") {
    const item = id.split("_")[1].slice(1);
    return item === "4" || item === "5" ? label(`cssrs_${item}`) : `C-SSRS ${item}`;
  }
  return label(sourceLabels[id] ?? id.replace(/\.manual$/, "").split(".").at(-1)!);
}

export function usesAutomaticValue(control: Control) {
  return control.control_id === "bmi.source" ||
    (control.kind === "nullable_boolean" && !control.control_id.startsWith("branches."));
}

const conditionFactors: Record<string, FactorId> = {
  "conditions.hepatic_failure.manual": "Hepatic_Failure",
  "conditions.renal_failure.manual": "Renal_Failure",
  "conditions.glucose_abnormality.manual": "Glucose_Abnormalities",
  "conditions.hyperlipidemia.manual": "Hyperlipidemia",
  "conditions.hyperprolactinemia.manual": "Hyperprolactinemia",
  "orthostatic_risk.manual": "Orthostatic_Hypotension_Risk",
};

function asChange(control: Control, value: ControlValue): Change {
  const { kind, control_id } = control;
  // The descriptor supplies the kind, item ID and valid values together.
  return (kind === "panss" || kind === "cssrs"
    ? { kind, item_id: control_id, value }
    : { kind, control_id, value }) as Change;
}

export function WhatIfControls({ result, changes, onChange, highlightedSources, selectedFactor }: {
  result: SimulationResponse;
  changes: Change[];
  onChange: (id: string, change: Change | null, continuous?: boolean) => void;
  highlightedSources: ReadonlySet<string>;
  selectedFactor: FactorId | null;
}) {
  const heading = useId();
  const { language } = useLanguage();
  const label = useFollowUpText();
  const chart = chartText(language);
  const en = language === "en";
  const followUp = result.identity.workflow === "follow-up-v1";
  const overrides = new Map(changes.map((change) => [changeId(change), change.value]));
  const active = result.controls.filter((control) => control.active);
  const panssItems = active.filter(({ kind }) => kind === "panss");
  const [item, setItem] = useState("P1");
  const currentItem = panssItems.find(({ control_id }) => control_id === item);
  const [panss, setPanss] = useState<PanssDefinition | null>(null);
  const [cssrs, setCssrs] = useState<CssrsDefinition | null>(null);
  useEffect(() => {
    let active = true;
    void getPanssDefinition().then((definition) => { if (active) setPanss(definition); }, () => {});
    void getCssrsDefinition().then((definition) => { if (active) setCssrs(definition); }, () => {});
    return () => { active = false; };
  }, []);
  const automatic = en ? "Automatic" : "خودکار";
  const revert = en ? "Revert" : "بازگردانی";
  const effective = en ? "Evaluated" : "ارزیابی‌شده";
  const highlighted = (control: Control) => highlightedSources.has(control.source_path) ||
    conditionFactors[control.control_id] === selectedFactor;

  function prompt(control: Control, instrument: "panss" | "cssrs") {
    const definition = instrument === "panss" ? panss : cssrs;
    const version = result.scenario.assessments[instrument].definition_version;
    const text = definition?.items.find(({ id }) => id === control.control_id)?.prompt;
    return text && version === definition?.version
      ? clinicalText(language, instrument, version, control.control_id, text)
      : control.control_id;
  }

  function controlLabel(control: Control) {
    if (control.kind === "panss" || control.kind === "cssrs") return prompt(control, control.kind);
    return simulationControlLabel(control, label);
  }

  function valueLabel(value: ControlValue, control: Control) {
    if (value === null) return usesAutomaticValue(control) ? automatic : chart.unrecorded;
    if (typeof value === "boolean") return value ? chart.yes : chart.no;
    if (typeof value === "number") return chart.count(value);
    return value === "unknown" ? label("unknown") : label(value);
  }

  function factorLabel(value: boolean | null) {
    return value === null ? chart.unrecorded : value ? chart.yes : chart.no;
  }

  function renderControl(control: Control, slider = false) {
    const id = control.control_id;
    const name = controlLabel(control);
    const fieldId = `${heading}-${id}`;
    const changed = overrides.has(id);
    const value = changed ? overrides.get(id)! : control.patient_value;
    const choose = (next: ControlValue, continuous = false) =>
      onChange(id, next === control.patient_value ? null : asChange(control, next), continuous);
    const choices = control.kind === "panss"
      ? Array.from({ length: control.maximum! - control.minimum! + 1 }, (_, index) => index + control.minimum!)
      : control.choices;
    const choiceIndex = choices.indexOf(value);
    const factorId = conditionFactors[id];
    const factor = factorId ? result.scenario.factors[factorId] : undefined;
    const patientFactor = factorId ? result.patient.factors[factorId] : undefined;
    const paths = result.active_graph.nodes.find(({ kind, subject }) =>
      kind === "factor" && subject === factorId);
    const automaticPaths = [paths?.scenario, paths?.patient].flatMap((observation) =>
      (observation?.provenance?.paths ?? []).filter((path) => path !== control.source_path));
    const inherited = automaticPaths.some((path) => path.endsWith(".inherited"));
    const lab = automaticPaths.some((path) => path.includes(".labs.values."));
    const issue = result.evaluation.scenario.issues.some(({ field }) => field === control.source_path);

    return (
      <Stack gap={2} key={id} align="start" style={{ width: "100%", minWidth: 0 }}>
        <Stack gap={1} width="100%">
          <label htmlFor={fieldId}>{name}</label>
          {highlighted(control) && <strong id={`${fieldId}-linked`}>{en ? "Linked factor" : "عامل مرتبط"}</strong>}
          <select id={fieldId} className="xi-input" value={choiceIndex < 0 ? "" : choiceIndex}
            aria-invalid={issue || undefined}
            aria-describedby={highlighted(control) ? `${fieldId}-linked` : undefined}
            onChange={(event) => choose(choices[Number(event.target.value)])}>
            {choiceIndex < 0 && <option value="" disabled>{valueLabel(value, control)}</option>}
            {choices.map((choice, index) => <option key={index} value={index}>{valueLabel(choice, control)}</option>)}
          </select>
        </Stack>
        {slider && typeof value === "number" && (
          <Slider label={en ? `PANSS ${id} score slider` : `لغزندهٔ امتیاز PANSS ${id}`}
            min={control.minimum!} max={control.maximum!} value={value}
            valueDisplay="text" width="100%" formatValue={chart.count}
            onChange={(next: number) => choose(next, true)} />
        )}
        <small>{chart.patient} · {valueLabel(control.patient_value, control)}</small>
        {usesAutomaticValue(control) && (
          <small>
            {id === "bmi.source"
              ? en ? "Automatic uses recorded height and weight." : "حالت خودکار از قد و وزن ثبت‌شده استفاده می‌کند."
              : <>{automatic} · {inherited || id === "tobacco" || id === "substances.present"
                ? en ? "Prior visit" : "ویزیت پیشین"
                : lab ? en ? "Recorded labs" : "آزمایش‌های ثبت‌شده"
                  : followUp
                    ? en ? "Recorded findings or prior visit" : "یافته‌های ثبت‌شده یا ویزیت پیشین"
                    : en ? "Recorded findings" : "یافته‌های ثبت‌شده"}</>}
            {factor && patientFactor && <> · {effective} · {chart.patient} {factorLabel(patientFactor.value)} /
              {" "}{chart.scenario} {factorLabel(factor.value)}</>}
          </small>
        )}
        {changed && <Button label={revert} aria-label={`${revert} — ${name}`}
          variant="secondary" size="sm" onClick={() => onChange(id, null)} />}
      </Stack>
    );
  }

  const group = (title: string, includes: (control: Control) => boolean, open = false) => {
    const controls = active.filter(includes);
    return controls.length > 0 && (
      <details open={open || controls.some(highlighted) || undefined}>
        <summary>{title}</summary>
        <Stack gap={3} paddingBlockStart={3}>{controls.map((control) => renderControl(control))}</Stack>
      </details>
    );
  };
  const isCondition = ({ control_id }: Control) => control_id.startsWith("conditions.") ||
    ["breastfeeding", "psychiatric.present"].includes(control_id);
  const isBranch = ({ control_id }: Control) => control_id.startsWith("branches.");
  const isEffect = ({ control_id }: Control) => control_id.startsWith("effects.");
  const summary = (key: "panss" | "cssrs", score: string) => {
    const patient = result.patient.assessments[key].scores[score] ?? null;
    const scenario = result.scenario.assessments[key].scores[score] ?? null;
    const format = (value: number | null) => value === null ? chart.unrecorded : chart.count(value);
    return <small>{chart.patient} · {format(patient)} / {chart.scenario} · {format(scenario)}</small>;
  };

  return (
    <section className="xi-card" aria-labelledby={heading}>
      <Stack gap={3}>
        <h4 id={heading}>{en ? "Scenario inputs" : "ورودی‌های سناریو"}</h4>
        {group(en ? "Clinical factors and considerations" : "عوامل و ملاحظات بالینی",
          (control) => !isCondition(control) && !isBranch(control) && !isEffect(control) &&
            control.kind !== "panss" && control.kind !== "cssrs")}
        {group(en ? "Comorbidities" : "بیماری‌های همراه", isCondition)}
        {group(label("effects"), isEffect)}
        {group(en ? "Decision questions" : "پرسش‌های تصمیم‌گیری", isBranch, true)}
        <details>
          <summary>PANSS</summary>
          <Stack gap={3} paddingBlockStart={3}>
            <Stack gap={1}>
              <label htmlFor={`${heading}-panss-item`}>{en ? "PANSS item" : "سؤال PANSS"}</label>
              <select id={`${heading}-panss-item`} className="xi-input" value={item}
                onChange={(event) => setItem(event.target.value)}>
                {panssItems.map(({ control_id }) => <option key={control_id}>{control_id}</option>)}
              </select>
            </Stack>
            {currentItem && renderControl(currentItem, true)}
            <details>
              <summary>{en ? "All PANSS items" : "همهٔ سؤال‌های PANSS"}</summary>
              <ul>{panssItems.map((control) => (
                <li key={control.control_id}>
                  {controlLabel(control)} · {valueLabel(
                    overrides.get(control.control_id) ?? control.patient_value, control,
                  )}
                </li>
              ))}</ul>
            </details>
            <p>{en ? "PANSS total" : "مجموع PANSS"} · {summary("panss", "total")}</p>
            <p>{en ? "Items answered" : "سؤال‌های پاسخ‌داده‌شده"} · {chart.patient}{" "}
              {chart.count(panssItems.filter(({ patient_value }) => typeof patient_value === "number").length)} / {chart.scenario}{" "}
              {chart.count(panssItems.filter(({ scenario_value }) => typeof scenario_value === "number").length)}</p>
          </Stack>
        </details>
        {group("C-SSRS", ({ kind }) => kind === "cssrs")}
        <p>{label("cssrsResult")} · {summary("cssrs", "ideation_severity_recent")}</p>
      </Stack>
    </section>
  );
}
