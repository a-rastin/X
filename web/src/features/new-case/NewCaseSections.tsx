import { useEffect, useState, useId } from "react";
import { useLanguage } from "../../shared/i18n";
import { clinicalText, type MessageKey } from "../../shared/messages";
import { getCssrsDefinition } from "../assessments/cssrs/api";
import { searchDrugs, type CatalogDrug } from "../medications/api";
import type { ClinicalContent, Manual, NewCaseRaw, State } from "./api";
import "./new-case.css";

export function useNewCaseText() {
  const { t } = useLanguage();
  return (key: string) => t(("nc." + key) as MessageKey);
}
export function Required() {
  const n = useNewCaseText();
  return (
    <span className="nc-required" aria-label={n("required")}>
      *
    </span>
  );
}
export function YesNo({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  const n = useNewCaseText();
  return (
    <fieldset className="nc-answer">
      <legend>{label}</legend>
      <div className="xi-radio-group">
        {[true, false].map((v) => (
          <label key={String(v)}>
            <input
              type="radio"
              name={label}
              checked={value === v}
              onChange={() => onChange(v)}
            />
            {n(v ? "yes" : "no")}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
function Magnifier({
  help,
  onClick,
  expanded,
}: {
  help: string;
  onClick?: () => void;
  expanded?: boolean;
}) {
  const tooltip = useId();
  return (
    <span className="nc-magnifier">
      <button
        aria-describedby={tooltip}
        className="xi-btn xi-btn-secondary"
        type="button"
        aria-label={help}
        aria-expanded={expanded}
        onClick={onClick}
      >
        ⌕
      </button>
      <span role="tooltip" id={tooltip}>
        {help}
      </span>
    </span>
  );
}
export function HarmSection({
  raw,
  answers,
  onRaw,
  onAnswers,
}: {
  raw: NewCaseRaw;
  answers: Record<string, string>;
  onRaw: (v: NewCaseRaw) => void;
  onAnswers: (v: Record<string, string>) => void;
}) {
  const n = useNewCaseText();
  const { language } = useLanguage();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<{ id: string; prompt: string }[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoadError(false);
    void getCssrsDefinition()
      .then((d) => {
        if (!cancelled) setItems(d.items);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [retry]);
  return (
    <section>
      <YesNo
        label={n("self")}
        value={raw.harm.self}
        onChange={(self) => onRaw({ ...raw, harm: { ...raw.harm, self } })}
      />
      <Magnifier
        help={n("harmHelp")}
        expanded={open}
        onClick={() => setOpen(!open)}
      />
      {open && (
        <div className="xi-card">
          <p>{n("harmHelp")}</p>
          {loadError && (
            <p role="alert">
              {n("failed")}{" "}
              <button type="button" onClick={() => setRetry((r) => r + 1)}>
                {n("retry")}
              </button>
            </p>
          )}
          {items.map((item) => (
            <YesNo
              key={item.id}
              label={clinicalText(
                language,
                "cssrs",
                "v2",
                item.id,
                item.prompt,
              )}
              value={answers[item.id] === "yes"}
              onChange={(v) =>
                onAnswers({ ...answers, [item.id]: v ? "yes" : "no" })
              }
            />
          ))}
        </div>
      )}
      <YesNo
        label={n("others")}
        value={raw.harm.others}
        onChange={(others) => onRaw({ ...raw, harm: { ...raw.harm, others } })}
      />
    </section>
  );
}
interface Props {
  raw: NewCaseRaw;
  onRaw: (v: NewCaseRaw) => void;
  state: State | null;
  content: ClinicalContent;
  sex: string;
}
function OverrideAnswer({
  name,
  answer,
  state,
  onChange,
}: {
  name: string;
  answer: Manual;
  state: State | null;
  onChange: (v: Manual) => void;
}) {
  const n = useNewCaseText();
  const flag = state?.flags[name];
  return (
    <div>
      <YesNo
        label={n(name)}
        value={answer.manual ?? flag?.effective ?? false}
        onChange={(v) => onChange({ ...answer, manual: v })}
      />
      {answer.manual !== null && (
        <p>
          {n("manual")}{" "}
          <button
            className="xi-btn xi-btn-secondary"
            type="button"
            onClick={() => onChange({ ...answer, manual: null })}
          >
            {n("automatic")}
          </button>
        </p>
      )}
      {flag && (
        <p>
          {n("suggested")}: {n(flag.suggested ? "yes" : "no")}
        </p>
      )}
      {flag?.triggering_findings.map((finding, i) => (
        <p key={i}>
          <bdi>
            {finding.name ??
              (finding.field === "heart_failure"
                ? n("heart_failure")
                : finding.field)}{" "}
            {String(finding.value ?? "")} {finding.unit}
          </bdi>{" "}
          {typeof finding.source === "string" && (
            <a href={finding.source} target="_blank" rel="noreferrer">
              {n("source")}
            </a>
          )}
        </p>
      ))}
    </div>
  );
}
export function HistorySection({ raw, onRaw, state, content, sex }: Props) {
  const n = useNewCaseText();
  const { language } = useLanguage();
  const set = <K extends keyof NewCaseRaw>(key: K, value: NewCaseRaw[K]) =>
    onRaw({ ...raw, [key]: value });
  const conditions = [
    "hepatic_failure",
    "renal_failure",
    "glucose_abnormality",
    "hyperlipidemia",
    "hyperprolactinemia",
  ];
  const bmi = raw.bmi;
  const labs = content["labs.json"];
  const renderLab = (id: string) => {
    const field = labs.fields[id]!;
    if (field.sex && field.sex !== sex) return null;
    return (
      <div className="xi-field" key={id}>
        <label htmlFor={"nc-lab-" + id}>
          {labs.labels[id]?.[language] ?? id} <bdi>{field.unit}</bdi>
        </label>
        <input
          className="xi-input"
          dir="ltr"
          id={"nc-lab-" + id}
          type="number"
          min="0"
          step="any"
          value={raw.labs.values[id] ?? ""}
          onChange={(e) =>
            set("labs", {
              ...raw.labs,
              values: { ...raw.labs.values, [id]: e.target.value || null },
            })
          }
        />
        <small>
          {n("reference")}:{" "}
          <bdi>
            {(() => {
              const r = (field.reference[sex] ?? field.reference) as Record<
                string,
                unknown
              >;
              return [
                r.min !== undefined
                  ? `${r.min_inclusive === false ? ">" : "≥"} ${r.min}`
                  : "",
                r.max !== undefined
                  ? `${r.max_inclusive === false ? "<" : "≤"} ${r.max}`
                  : "",
              ]
                .filter(Boolean)
                .join(" — ");
            })()}
          </bdi>{" "}
          <a href={labs.sources[field.source]} target="_blank" rel="noreferrer">
            {n("source")}
          </a>
        </small>
        {state?.lab_warnings
          .filter((w) => w.field === id)
          .map((w) => (
            <p className="xi-field-error" role="status" key={w.code}>
              {n(w.code)}:{" "}
              <bdi>
                {w.value} {field.unit}
              </bdi>
            </p>
          ))}
      </div>
    );
  };
  const cbc = [
    "wbc",
    "rbc",
    "hemoglobin",
    "hematocrit",
    "platelets",
    "mcv",
    "mch",
    "mchc",
    "rdw",
    "mpv",
    "neutrophils_pct",
    "bands_pct",
    "lymphocytes_pct",
    "monocytes_pct",
    "basophils_pct",
    "eosinophils_pct",
  ];
  return (
    <section>
      <YesNo
        label={n("psychiatric")}
        value={raw.psychiatric.present}
        onChange={(present) =>
          set("psychiatric", { ...raw.psychiatric, present })
        }
      />
      {raw.psychiatric.present && (
        <fieldset>
          <legend>
            {n("psychiatric")} <Required />
          </legend>
          {["depression", "ptsd", "adhd", "other"].map((id) => (
            <label className="nc-choice" key={id}>
              <input
                type="checkbox"
                checked={raw.psychiatric.conditions.includes(id)}
                onChange={(e) =>
                  set("psychiatric", {
                    ...raw.psychiatric,
                    conditions: e.target.checked
                      ? [...raw.psychiatric.conditions, id]
                      : raw.psychiatric.conditions.filter((v) => v !== id),
                  })
                }
              />
              {n(id)}
            </label>
          ))}
          {raw.psychiatric.conditions.includes("other") && (
            <label>
              {n("otherText")}
              <input
                className="xi-input"
                maxLength={2000}
                value={raw.psychiatric.other_text}
                onChange={(e) =>
                  set("psychiatric", {
                    ...raw.psychiatric,
                    other_text: e.target.value,
                  })
                }
              />
            </label>
          )}
        </fieldset>
      )}
      <YesNo
        label={n("labs")}
        value={raw.labs.present}
        onChange={(present) => set("labs", { ...raw.labs, present })}
      />
      {raw.labs.present && (
        <fieldset>
          <legend>
            {n("labs")} <Required />
          </legend>
          <details>
            <summary>
              <bdi>CBC / Diff</bdi>
            </summary>
            <div className="nc-labs">
              {cbc.filter((id) => id in labs.fields).map(renderLab)}
            </div>
          </details>
          <div className="nc-labs">
            {Object.keys(labs.fields)
              .filter((id) => !cbc.includes(id))
              .map(renderLab)}
          </div>
        </fieldset>
      )}
      {conditions.map((id) => (
        <OverrideAnswer
          key={id}
          name={id}
          answer={raw.conditions[id]!}
          state={state}
          onChange={(v) => set("conditions", { ...raw.conditions, [id]: v })}
        />
      ))}
      <YesNo
        label={n("heart_failure")}
        value={raw.heart_failure}
        onChange={(v) => set("heart_failure", v)}
      />
      <YesNo
        label={n("tobacco")}
        value={raw.tobacco}
        onChange={(v) => set("tobacco", v)}
      />
      <YesNo
        label={n("substances")}
        value={raw.substances.present}
        onChange={(present) =>
          set("substances", { ...raw.substances, present })
        }
      />
      {raw.substances.present && (
        <fieldset>
          <legend>
            {n("substances")} <Required />
          </legend>
          {(["marijuana", "other"] as const).map((id) => (
            <label className="nc-choice" key={id}>
              <input
                type="checkbox"
                checked={raw.substances[id]}
                onChange={(e) =>
                  set("substances", {
                    ...raw.substances,
                    [id]: e.target.checked,
                  })
                }
              />
              {n(id === "other" ? "otherSubstance" : id)}
            </label>
          ))}
          {raw.substances.other && (
            <label>
              {n("otherText")}
              <input
                className="xi-input"
                maxLength={2000}
                value={raw.substances.other_text}
                onChange={(e) =>
                  set("substances", {
                    ...raw.substances,
                    other_text: e.target.value,
                  })
                }
              />
            </label>
          )}
        </fieldset>
      )}
      <fieldset>
        <legend>
          {n("bmi")} <Required />
        </legend>
        <div className="nc-labs">
          {(["height_cm", "weight_kg"] as const).map((id) => (
            <label key={id}>
              {n(id)}
              <input
                className="xi-input"
                dir="ltr"
                type="number"
                min="0"
                step="any"
                value={bmi[id] ?? ""}
                onChange={(e) =>
                  set("bmi", { ...bmi, [id]: e.target.value || null })
                }
              />
            </label>
          ))}
        </div>
        {state?.calculated_bmi && (
          <p>
            {n("calculated")}:{" "}
            <bdi>{Number(state.calculated_bmi).toFixed(2)}</bdi>
          </p>
        )}
        <div className="nc-category" role="radiogroup" aria-label={n("bmi")}>
          {[
            "underweight",
            "normal",
            "overweight",
            "obesity_i",
            "obesity_ii",
            "obesity_iii",
          ].map((category) => (
            <label key={category}>
              <input
                type="radio"
                name="nc-bmi"
                checked={
                  (bmi.source === "manual"
                    ? bmi.manual_category
                    : state?.bmi_category) === category
                }
                onChange={() =>
                  set("bmi", {
                    ...bmi,
                    source: "manual",
                    manual_category: category,
                  })
                }
              />
              {n(category)}
            </label>
          ))}
        </div>
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          onClick={() => set("bmi", { ...bmi, source: "calculated" })}
        >
          {n("useCalculated")}
        </button>
      </fieldset>
      {sex === "F" && (
        <>
          <OverrideAnswer
            name="pregnant"
            answer={raw.conditions.pregnant!}
            state={state}
            onChange={(v) =>
              set("conditions", { ...raw.conditions, pregnant: v })
            }
          />
          <YesNo
            label={n("breastfeeding")}
            value={raw.breastfeeding}
            onChange={(v) => set("breastfeeding", v)}
          />
        </>
      )}
      {(
        [
          "focal_neurological_deficit",
          "suspected_brain_lesion",
          "suspected_autoimmune_encephalitis",
        ] as const
      ).map((id) => (
        <YesNo
          key={id}
          label={n(id)}
          value={raw[id]}
          onChange={(v) => set(id, v)}
        />
      ))}
      <details>
        <summary title={n("encephalitisHelp")}>
          {n("suspected_autoimmune_encephalitis")} ⌕
        </summary>
        <p>{n("encephalitisHelp")}</p>
      </details>
    </section>
  );
}
export function ConsiderationsSection({
  raw,
  onRaw,
  state,
}: Pick<Props, "raw" | "onRaw" | "state">) {
  const n = useNewCaseText();
  return (
    <section>
      {(
        [
          "basic_needs_met",
          "needs_observation",
          "lai",
          "seizure_risk",
          "avoid_sedation",
          "qtc_risk",
        ] as const
      ).map((id) => (
        <YesNo
          key={id}
          label={n(id)}
          value={raw.considerations[id]}
          onChange={(v) =>
            onRaw({
              ...raw,
              considerations: { ...raw.considerations, [id]: v },
            })
          }
        />
      ))}
      <OverrideAnswer
        name="orthostatic_risk"
        answer={raw.considerations.orthostatic_risk}
        state={state}
        onChange={(v) =>
          onRaw({
            ...raw,
            considerations: { ...raw.considerations, orthostatic_risk: v },
          })
        }
      />
    </section>
  );
}
export function MedicationsSection({
  storageKey,
  ids,
  names,
  version,
  onChange,
  onUnmatched,
}: {
  storageKey: string;
  ids: string[];
  names: Record<string, string>;
  version: string;
  onChange: (ids: string[]) => void;
  onUnmatched: (v: boolean) => void;
}) {
  const n = useNewCaseText();
  const [query, setQuery] = useState(
    () => sessionStorage.getItem(storageKey) ?? "",
  );
  const [options, setOptions] = useState<CatalogDrug[]>([]);
  const [index, setIndex] = useState(0);
  useEffect(() => {
    onUnmatched(query.trim() !== "");
    if (query) sessionStorage.setItem(storageKey, query);
    else sessionStorage.removeItem(storageKey);
  }, [query, onUnmatched, storageKey]);
  useEffect(() => {
    let cancelled = false;
    setOptions([]);
    setIndex(0);
    if (query.trim().length >= 3) {
      void searchDrugs(query.trim(), 25, 0, version)
        .then((r) => {
          if (!cancelled) setOptions(r.items);
        })
        .catch(() => {
          if (!cancelled) setOptions([]);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [query, version]);
  const select = (id: string) => {
    onChange([...new Set([...ids, id])]);
    setQuery("");
    setOptions([]);
  };
  return (
    <section className="nc-medications">
      <ul className="nc-medication-list">
        {ids.map((id) => (
          <li key={id}>
            <bdi>{names[id] ?? id}</bdi>{" "}
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => onChange(ids.filter((v) => v !== id))}
            >
              {n("remove")}
            </button>
          </li>
        ))}
      </ul>
      <label htmlFor="nc-drug-search">{n("search")}</label>
      <input
        className="xi-input"
        id="nc-drug-search"
        dir="ltr"
        value={query}
        disabled={ids.length >= 100}
        role="combobox"
        aria-autocomplete="list"
        aria-controls="nc-drug-options"
        aria-expanded={options.length > 0}
        aria-activedescendant={options.length ? "nc-drug-" + index : undefined}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setIndex((i) => Math.min(i + 1, options.length - 1));
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setIndex((i) => Math.max(i - 1, 0));
          }
          if (e.key === "Enter" && options[index]) {
            e.preventDefault();
            select(options[index]!.catalog_drug_id);
          }
          if (e.key === "Escape") setOptions([]);
        }}
      />
      <ul id="nc-drug-options" role="listbox">
        {options.map((drug, i) => (
          <li
            role="option"
            aria-selected={i === index}
            key={drug.catalog_drug_id}
            id={"nc-drug-" + i}
          >
            <button type="button" onClick={() => select(drug.catalog_drug_id)}>
              <bdi>{drug.canonical_name}</bdi>
            </button>
          </li>
        ))}
      </ul>
      {query.trim() !== "" && <p role="alert">{n("unmatched")}</p>}
    </section>
  );
}
