import { formatLabReference } from "../new-case/NewCaseSections";
import { useId } from "react";
import { useLanguage } from "../../shared/i18n";
import type { FollowUpConfig, FollowUpRaw, FollowUpState } from "./api";
import { useFollowUpText } from "./text";

export function Answer({
  label,
  value,
  onChange,
  required = true,
}: {
  label: string;
  value: boolean | null;
  onChange: (v: boolean) => void;
  required?: boolean;
}) {
  const id = useId();
  const n = useFollowUpText();
  return (
    <fieldset className="nc-answer">
      <legend>
        {label}
        {required && ` (${n("required")})`}
      </legend>
      <section className="xi-radio-group">
        {[true, false].map((v) => (
          <label key={String(v)}>
            <input
              type="radio"
              name={id}
              required={required}
              checked={value === v}
              onClick={() => {
                if (value === v) onChange(v);
              }}
              onChange={() => onChange(v)}
            />
            {n(v ? "yes" : "no")}
          </label>
        ))}
      </section>
    </fieldset>
  );
}
export function FollowUpHistory({
  raw,
  onRaw,
  state,
  config,
  sex,
}: {
  raw: FollowUpRaw;
  onRaw: (v: FollowUpRaw) => void;
  state: FollowUpState | null;
  config: FollowUpConfig;
  sex: string;
}) {
  const n = useFollowUpText();
  const { language } = useLanguage();
  const h = raw.history;
  const change = (value: Partial<typeof h>) =>
    onRaw({ ...raw, history: { ...h, ...value } });
  const conditions = Object.keys(h.conditions) as (keyof typeof h.conditions)[];
  return (
    <section>
      <h3>{n("current")}</h3>
      <Answer
        label={n("psychiatric")}
        value={h.psychiatric.present}
        onChange={(present) =>
          change({ psychiatric: { ...h.psychiatric, present } })
        }
      />
      {h.psychiatric.present && (
        <fieldset>
          <legend>{n("psychiatric")}</legend>
          {(["depression", "ptsd", "adhd", "other"] as const).map((key) => (
            <label key={key}>
              <input
                type="checkbox"
                checked={h.psychiatric.conditions.includes(key)}
                onChange={(e) =>
                  change({
                    psychiatric: {
                      ...h.psychiatric,
                      conditions: e.target.checked
                        ? [...h.psychiatric.conditions, key]
                        : h.psychiatric.conditions.filter((k) => k !== key),
                    },
                  })
                }
              />
              {n(key)}
            </label>
          ))}
          {h.psychiatric.conditions.includes("other") && (
            <label>
              {n("other")}
              <input
                className="xi-input"
                maxLength={2000}
                value={h.psychiatric.other_text}
                onChange={(e) =>
                  change({
                    psychiatric: {
                      ...h.psychiatric,
                      other_text: e.target.value,
                    },
                  })
                }
              />
            </label>
          )}
        </fieldset>
      )}
      {h.psychiatric.present &&
        h.psychiatric.conditions.some(
          (k) => k === "depression" || k === "ptsd",
        ) && (
          <Answer
            label={n("current_antidepressant")}
            value={h.current_antidepressant}
            onChange={(current_antidepressant) =>
              change({ current_antidepressant })
            }
          />
        )}
      <Answer
        label={n("labs")}
        value={h.labs.present}
        onChange={(present) => change({ labs: { ...h.labs, present } })}
      />
      {h.labs.present && (
        <section className="nc-labs-grid">
          {Object.entries(config.lab_fields)
            .filter(([key]) => key !== "beta_hcg" || sex === "F")
            .map(([key, spec]) => (
              <section className="xi-field" key={key}>
                <label htmlFor={"fu-lab-" + key}>
                  {config.lab_labels[key]?.[language] ?? key}{" "}
                  <bdi dir="ltr">({spec.unit})</bdi>
                </label>
                <input
                  id={"fu-lab-" + key}
                  className="xi-input"
                  inputMode="decimal"
                  dir="ltr"
                  value={h.labs.values[key] ?? ""}
                  onChange={(e) =>
                    change({
                      labs: {
                        ...h.labs,
                        values: {
                          ...h.labs.values,
                          [key]: e.target.value || null,
                        },
                      },
                    })
                  }
                />
                <small dir="ltr">
                  {n("reference")}: {formatLabReference(spec.reference, sex)} —{" "}
                  <a
                    href={config.lab_sources[spec.source]}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {n("source")}
                  </a>
                </small>
              </section>
            ))}
        </section>
      )}
      {conditions
        .filter((key) => key !== "pregnant")
        .map((key) => {
          const flag = state?.flags[key] as
            | {
                effective: boolean;
                established: boolean;
                triggering_findings: {
                  field: string;
                  value: string;
                  unit: string;
                }[];
              }
            | undefined;
          const answer = h.conditions[key];
          return (
            <section key={key}>
              <Answer
                label={n(key)}
                value={
                  answer.manual ??
                  (flag?.established ? flag.effective : answer.inherited)
                }
                required={key !== "hyperprolactinemia"}
                onChange={(manual) =>
                  change({
                    conditions: {
                      ...h.conditions,
                      [key]: { ...answer, manual },
                    },
                  })
                }
              />
              <p>
                {n("prior")}:{" "}
                {answer.inherited === null
                  ? n("unknown")
                  : n(answer.inherited ? "yes" : "no")}
              </p>
              {flag?.triggering_findings.map((f) => (
                <p key={f.field}>
                  <bdi dir="ltr">
                    {f.field}: {f.value} {f.unit}
                  </bdi>{" "}
                  — {n("suggested")}
                </p>
              ))}
              {answer.manual !== null && (
                <button
                  className="xi-btn xi-btn-secondary"
                  onClick={() =>
                    change({
                      conditions: {
                        ...h.conditions,
                        [key]: { ...answer, manual: null },
                      },
                    })
                  }
                >
                  {n("automatic")}
                </button>
              )}
            </section>
          );
        })}
      {(["heart_failure", "tobacco"] as const).map((key) => (
        <Answer
          key={key}
          label={n(key)}
          value={h[key]}
          onChange={(v) => change({ [key]: v })}
        />
      ))}
      <Answer
        label={n("substances")}
        value={h.substances.present}
        onChange={(present) =>
          change({ substances: { ...h.substances, present } })
        }
      />
      {h.substances.present && (
        <fieldset>
          <legend>{n("substances")}</legend>
          {(["marijuana", "opioids", "methamphetamine", "other"] as const).map(
            (key) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={h.substances[key]}
                  onChange={(e) =>
                    change({
                      substances: { ...h.substances, [key]: e.target.checked },
                    })
                  }
                />
                {n(key)}
              </label>
            ),
          )}
          {h.substances.other && (
            <label>
              {n("other")}
              <input
                className="xi-input"
                value={h.substances.other_text}
                maxLength={2000}
                onChange={(e) =>
                  change({
                    substances: { ...h.substances, other_text: e.target.value },
                  })
                }
              />
            </label>
          )}
        </fieldset>
      )}
      {sex === "F" &&
        (["pregnant", "breastfeeding"] as const).map((key) => (
          <Answer
            key={key}
            label={n(key)}
            value={h[key]}
            onChange={(v) => change({ [key]: v })}
          />
        ))}
      <fieldset>
        <legend>
          {n("bmi")} ({n("required")})
        </legend>
        {(["height_cm", "weight_kg"] as const).map((key) => (
          <label key={key}>
            {n(key)}
            <input
              className="xi-input"
              dir="ltr"
              inputMode="decimal"
              value={h.bmi[key] ?? ""}
              onChange={(e) =>
                change({ bmi: { ...h.bmi, [key]: e.target.value || null } })
              }
            />
          </label>
        ))}
        <label>
          {n("bmiMode")}
          <select
            className="xi-input"
            value={h.bmi.source ?? ""}
            onChange={(e) =>
              change({
                bmi: {
                  ...h.bmi,
                  source: e.target.value as "calculated" | "manual",
                },
              })
            }
          >
            <option value="">{n("select")}</option>
            <option value="calculated">{n("calculated")}</option>
            <option value="manual">{n("manual")}</option>
          </select>
        </label>
        {h.bmi.source === "manual" && (
          <label>
            {n("bmiCategory")}
            <select
              className="xi-input"
              value={h.bmi.manual_category ?? ""}
              onChange={(e) =>
                change({
                  bmi: { ...h.bmi, manual_category: e.target.value || null },
                })
              }
            >
              <option value="">{n("select")}</option>
              {(config.definitions.bmi_categories as string[]).map((key) => (
                <option key={key} value={key}>
                  {n(key)}
                </option>
              ))}
            </select>
          </label>
        )}
        <p>
          <bdi dir="ltr">{state?.calculated_bmi}</bdi>{" "}
          {state?.bmi_category && n(state.bmi_category)}
        </p>
      </fieldset>
      {(Object.keys(h.neurological) as (keyof typeof h.neurological)[]).map(
        (key) => (
          <Answer
            key={key}
            label={n(key)}
            value={h.neurological[key]}
            onChange={(v) =>
              change({ neurological: { ...h.neurological, [key]: v } })
            }
          />
        ),
      )}
      <details>
        <summary>{n("suspected_autoimmune_encephalitis")}</summary>
        <p>{n("encephalitisHelp")}</p>
      </details>
      <label>
        {n("hospitalization_count")}
        <input
          className="xi-input"
          type="number"
          min={0}
          max={10000}
          step={1}
          value={h.hospitalization_count ?? ""}
          onChange={(e) =>
            change({
              hospitalization_count:
                e.target.value === "" ? null : Number(e.target.value),
            })
          }
        />
      </label>
      {state?.lab_warnings.map((w, i) => (
        <p role="status" key={i}>
          <bdi dir="ltr">
            {String(w.field)}: {String(w.value)}
          </bdi>{" "}
          {n(String(w.code))}
        </p>
      ))}
    </section>
  );
}
