/// <reference types="vite/client" />
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

export function missingQuestionLabels(keys: string[]): string[] {
  return keys.filter((key) => !Object.hasOwn(questionLabels, key) || !questionLabels[key].trim());
}

export function questionLabel(key: string, development = import.meta.env?.DEV ?? false, warn = console.warn): string {
  if (Object.hasOwn(questionLabels, key) && questionLabels[key].trim()) return questionLabels[key];
  if (development) warn("A workflow question has no presentation label.");
  const readable = key.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  return readable ? readable[0].toUpperCase() + readable.slice(1) : "Unknown question";
}
