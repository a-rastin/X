import type { Language } from "../../shared/messages";
import type { Exclusion, FactorId, OralTreatmentId } from "./types";

const en = {
  probabilities: "Treatment probabilities",
  tornado: "Factor attribution",
  patient: "Patient",
  scenario: "Scenario",
  change: "Change",
  notEvaluated: "Not evaluated for this decision",
  eligibleRank: "Eligible rank",
  patientTick: "Patient · dotted tick",
  scenarioBar: "Scenario · solid bar",
  patientOutline: "Patient · outline",
  showAll: "Show all treatments",
  showFewer: "Show compact list",
  modelReference: "Model reference",
  posterior: "Posterior",
  reconstruction: "Reference + contributions",
  contributionAxis: "Contribution in percentage points",
  showZero: "Show zero contributions",
  hideZero: "Hide zero contributions",
  effectiveValue: "Effective value",
  source: "Source",
  pathway: "Pathway",
  noPathway: "No model dependency in the displayed pathway",
  chartValues: "Table of values",
  treatment: "Treatment",
  factorName: "Factor",
  yes: "Yes",
  no: "No",
  unrecorded: "Unrecorded",
  pp: "pp",
};

const fa: typeof en = {
  probabilities: "احتمال‌های درمان",
  tornado: "سهم عوامل",
  patient: "بیمار",
  scenario: "سناریو",
  change: "تغییر",
  notEvaluated: "برای این تصمیم ارزیابی نشده است",
  eligibleRank: "رتبهٔ واجد شرایط",
  patientTick: "بیمار · نشان نقطه‌چین",
  scenarioBar: "سناریو · میلهٔ توپر",
  patientOutline: "بیمار · میلهٔ توخالی",
  showAll: "نمایش همهٔ درمان‌ها",
  showFewer: "نمایش فهرست کوتاه",
  modelReference: "احتمال مرجع مدل",
  posterior: "احتمال پسین",
  reconstruction: "مرجع + مجموع سهم‌ها",
  contributionAxis: "سهم عامل به واحد درصد",
  showZero: "نمایش سهم‌های صفر",
  hideZero: "پنهان کردن سهم‌های صفر",
  effectiveValue: "مقدار مؤثر",
  source: "منبع",
  pathway: "مسیر",
  noPathway: "در مسیر نمایش‌داده‌شده وابستگی مدل وجود ندارد",
  chartValues: "جدول مقادیر",
  treatment: "درمان",
  factorName: "عامل",
  yes: "بله",
  no: "خیر",
  unrecorded: "ثبت نشده",
  pp: "واحد درصد",
};

const exclusions: Record<Exclusion, [string, string]> = {
  zero_probability: ["Zero probability", "احتمال صفر"],
  unavailable: ["Unavailable", "ناموجود"],
  current_main: ["Current main drug", "داروی اصلی فعلی"],
  interaction_dx: ["D/X interaction", "تداخل D/X"],
  unsupported_direct_mapping: ["No supported formulation", "فرمولاسیون پشتیبانی‌شده وجود ندارد"],
};

const factors: Record<FactorId, [string, string]> = {
  Smoking: ["Smoking", "مصرف دخانیات"],
  Marijuana_Smoking: ["Marijuana smoking", "مصرف ماری‌جوانا"],
  Hepatic_Failure: ["Hepatic failure", "نارسایی کبدی"],
  Renal_Failure: ["Renal failure", "نارسایی کلیه"],
  Sedative_Effects_Concern: ["Concern about sedation", "نگرانی دربارهٔ خواب‌آلودگی"],
  Seizure_Risk: ["Seizure risk", "خطر تشنج"],
  Glucose_Abnormalities: ["Glucose abnormalities", "اختلال قند خون"],
  Hyperlipidemia: ["Hyperlipidemia", "چربی خون بالا"],
  Weight_Gain_Risk: ["Weight gain risk", "خطر افزایش وزن"],
  QTc_Prolongation_Risk: ["QTc prolongation risk", "خطر طولانی شدن QTc"],
  Orthostatic_Hypotension_Risk: ["Orthostatic hypotension risk", "خطر افت فشار خون وضعیتی"],
  Hyperprolactinemia: ["Hyperprolactinemia", "پرولاکتین بالا"],
  Tardive_Dyskinesia: ["Tardive dyskinesia", "دیسکینزی دیررس"],
  Dystonia: ["Dystonia", "دیستونی"],
  Parkinsonism: ["Parkinsonism", "پارکینسونیسم"],
  Akathisia: ["Akathisia", "آکاتیزی"],
};

export function chartText(language: Language) {
  const text = language === "fa" ? fa : en;
  const index = language === "fa" ? 1 : 0;
  // Keep numbers and signs in the same reading order on both chart axes.
  const probability = new Intl.NumberFormat("en", { style: "percent", maximumSignificantDigits: 15 });
  const points = new Intl.NumberFormat("en", { signDisplay: "exceptZero", maximumSignificantDigits: 15 });
  const axis = new Intl.NumberFormat("en", { signDisplay: "exceptZero", maximumSignificantDigits: 3 });
  const count = new Intl.NumberFormat(language);
  return {
    ...text,
    probability: (value: number) => probability.format(value),
    signedPoints: (value: number) => points.format(100 * value),
    axisPoints: (value: number) => axis.format(100 * value),
    count: (value: number) => count.format(value),
    exclusion: (id: Exclusion) => exclusions[id][index],
    factor: (id: FactorId) => factors[id][index],
  };
}

export function oralTreatmentName(id: OralTreatmentId) {
  const name = id.slice("oral:".length);
  return name[0].toUpperCase() + name.slice(1);
}

export function ChartValuesTable({ label, caption, heading, rows }: {
  label: ReturnType<typeof chartText>;
  caption: string;
  heading: string;
  rows: { id: string; name: string; patient: string; scenario: string; change: string }[];
}) {
  const cell = {
    padding: "var(--space-1)",
    borderBlockEnd: "1px solid var(--border)",
    textAlign: "start",
  } as const;
  return (
    <details>
      <summary>{label.chartValues}</summary>
      <table aria-label={caption} style={{ width: "100%", tableLayout: "fixed", overflowWrap: "anywhere" }}>
        <thead>
          <tr>
            {[heading, label.patient, label.scenario, label.change].map((name) => (
              <th key={name} scope="col" style={cell}>{name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <th scope="row" style={cell}><bdi>{row.name}</bdi></th>
              <td style={cell}><bdi>{row.patient}</bdi></td>
              <td style={cell}><bdi>{row.scenario}</bdi></td>
              <td style={cell}><bdi>{row.change}</bdi></td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
