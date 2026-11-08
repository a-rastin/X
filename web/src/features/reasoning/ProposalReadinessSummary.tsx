import { useLanguage } from "../../shared/i18n";
import { questionLabel } from "./questionLabels";
import type { ProposalBlocker, ProposalReadiness } from "./api";
import { clinicalText, present, type Language } from "../../shared/messages";

export function historyControlId(sourcePath: string): string {
  return `hist-${sourcePath}`;
}

export function focusHistoryControl(sourcePath: string): void {
  const control = document.getElementById(historyControlId(sourcePath));
  control?.closest("section")?.scrollIntoView?.({ block: "start" });
  control?.focus();
}

function blockerLink(blocker: ProposalBlocker, encounterId: string, local: boolean, language: Language) {
  const label = blocker.label === blocker.question_label
    ? questionLabel(blocker.question_key, undefined, undefined, language)
    : blocker.source_path?.startsWith("candidate/history/")
      ? clinicalText(language, "workflow", blocker.source_path, "definition", blocker.label)
      : present(language, blocker.label);
  if (blocker.source_path?.startsWith("candidate/history/")) {
    return (
      <a href={local ? `#${historyControlId(blocker.source_path)}` : `#/encounters/${encounterId}#history?field=${encodeURIComponent(blocker.source_path)}`}
        onClick={local ? (event) => { event.preventDefault(); focusHistoryControl(blocker.source_path!); } : undefined}>
        {label}
      </a>
    );
  }
  if (blocker.source_path === "synthetic/assessments/cssrs") {
    return <a href={`#/encounters/${encounterId}#cssrs`}>{label}</a>;
  }
  return label;
}

/** Shares labels and reasons from the server; never evaluates gates.
 *
 * `blockers` need clarification before generation can start. `warnings`
 * are uncertain context (Unknown, omitted, empty) that generation
 * estimates with disclosed missing context — they never block the start.
 */
export function ProposalReadinessSummary({ readiness, encounterId, local = false }: {
  readiness: ProposalReadiness;
  encounterId: string;
  local?: boolean;
}) {
  const { text: uiText, t, language } = useLanguage();
  const blocked = !readiness.ready || readiness.blockers.length > 0;
  const warnings = readiness.warnings ?? [];
  const warnedQuestions = readiness.questions.filter((question) => (question.warnings ?? []).length > 0);
  return (
    <div data-testid="proposal-readiness" aria-live="polite">
      <h4 className="xi-section-title">{t("proposalReadiness")}</h4>
      <p>{blocked ? t("proposalInputsNeedClarification") : t("readyForProposalGeneration")}</p>
      <p className="xi-hint">
        {readiness.answered_count} / {readiness.required_count}  {t("answeredOfCurrentlyRequired")}{" "}
        {readiness.execution_ready_count} / {readiness.required_count}  {t("executionReady")}
        {" "}{t("basedOnSavedRevision")} {readiness.source_revision}.{" "}
        {warnings.length > 0 && !blocked
          ? t("uncertainInputsBelowAreEstimatedWithMissing")
          : t("onlyTheBlockingInputsBelowPreventGeneration")}
      </p>
      <p className="xi-hint">{t("theRequiredTotalCanIncreaseOrDecrease")}</p>
      {readiness.questions.filter((question) => question.blockers.length > 0).map((question) => (
        <div key={question.question_key}>
          <h5 className="xi-proposal-subhead">{questionLabel(question.question_key, undefined, undefined, language)}</h5>
          <ul className="xi-diagnosis-missing">
            {question.blockers.map((blocker, index) => (
              <li key={`${blocker.source_path ?? "gate"}-${index}`}>
                {blockerLink(blocker, encounterId, local, language)}
                {" — "}{uiText(blocker.reason)}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {warnedQuestions.length > 0 && (
        <div>
          <h5 className="xi-proposal-subhead">{t("uncertainNotBlocking")}</h5>
          <p className="xi-hint" style={{ marginTop: 0 }}>
            
            {t("generationEstimatesTheseInputsWithMissingContext")}
          </p>
          {warnedQuestions.map((question) => (
            <div key={question.question_key}>
              <p className="xi-hint" style={{ marginBottom: 0 }}>{questionLabel(question.question_key, undefined, undefined, language)}</p>
              <ul className="xi-diagnosis-missing">
                {(question.warnings ?? []).map((warning, index) => (
                  <li key={`${warning.source_path ?? "gate"}-${index}`}>
                    {blockerLink(warning, encounterId, local, language)}
                    {" — "}{uiText(warning.reason)}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
