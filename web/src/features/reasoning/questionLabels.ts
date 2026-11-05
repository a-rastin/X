/// <reference types="vite/client" />
import type { Language } from "../../shared/messages";
/** Presentation only: workflow keys remain unchanged in persisted references. */
export const questionLabels: Record<string, string> = {
  pharmacotherapy: "Pharmacotherapy",
  high_suicide_clozapine: "Clozapine for high suicide risk",
  lai_indication_choice: "Long-acting injectable indication and choice",
  aggression_clozapine: "Clozapine for aggression",
  established_case_clozapine: "Clozapine for an established case",
  tardive_dyskinesia: "Tardive dyskinesia",
  akathisia: "Akathisia",
  parkinsonism: "Parkinsonism",
  acute_dystonia: "Acute dystonia",
  no_improvement_clozapine: "Clozapine when there is no improvement",
  continue_or_adjust: "Continue or adjust treatment",
};

const persianQuestionLabels: Record<string, string> = {
  pharmacotherapy: "دارودرمانی",
  high_suicide_clozapine: "کلوزاپین برای خطر بالای خودکشی",
  lai_indication_choice: "اندیکاسیون و انتخاب داروی تزریقی طولانی‌اثر",
  aggression_clozapine: "کلوزاپین برای پرخاشگری",
  established_case_clozapine: "کلوزاپین برای بیمار با تشخیص قبلی",
  tardive_dyskinesia: "دیسکینزی دیررس",
  akathisia: "آکاتیزی",
  parkinsonism: "پارکینسونیسم",
  acute_dystonia: "دیستونی حاد",
  no_improvement_clozapine: "کلوزاپین در صورت عدم بهبود",
  continue_or_adjust: "ادامه یا تعدیل درمان",
};

export function missingQuestionLabels(keys: string[]): string[] {
  return keys.filter((key) => !Object.hasOwn(questionLabels, key) || !questionLabels[key].trim());
}

export function questionLabel(key: string, development = import.meta.env?.DEV ?? false, warn = console.warn, language: Language = "en"): string {
  if (Object.hasOwn(questionLabels, key) && questionLabels[key].trim()) {
    if (language === "en") return questionLabels[key];
    if (Object.hasOwn(persianQuestionLabels, key)) return persianQuestionLabels[key];
  }
  if (development) warn("A workflow question has no presentation label.");
  const readable = key.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  const label = readable ? readable[0].toUpperCase() + readable.slice(1) : "Unknown question";
  return language === "fa" ? `ترجمه در دسترس نیست؛ متن اصلی در ادامه آمده است. ${label}` : label;
}
