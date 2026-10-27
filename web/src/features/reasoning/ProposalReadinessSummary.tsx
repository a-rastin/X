import type { ProposalBlocker, ProposalReadiness } from "./api";

export function historyControlId(sourcePath: string): string {
  return `hist-${sourcePath}`;
}

export function focusHistoryControl(sourcePath: string): void {
  const control = document.getElementById(historyControlId(sourcePath));
  control?.closest("section")?.scrollIntoView?.({ block: "start" });
  control?.focus();
}

function blockerLink(blocker: ProposalBlocker, encounterId: string, local: boolean) {
  if (blocker.source_path?.startsWith("candidate/history/")) {
    return (
      <a href={local ? `#${historyControlId(blocker.source_path)}` : `#/encounters/${encounterId}#history?field=${encodeURIComponent(blocker.source_path)}`}
        onClick={local ? (event) => { event.preventDefault(); focusHistoryControl(blocker.source_path!); } : undefined}>
        {blocker.label}
      </a>
    );
  }
  if (blocker.source_path === "synthetic/assessments/cssrs") {
    return <a href={`#/encounters/${encounterId}#cssrs`}>{blocker.label}</a>;
  }
  return blocker.label;
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
  const blocked = !readiness.ready || readiness.blockers.length > 0;
  const warnings = readiness.warnings ?? [];
  const warnedQuestions = readiness.questions.filter((question) => (question.warnings ?? []).length > 0);
  return (
    <div data-testid="proposal-readiness" aria-live="polite">
      <h4 className="xi-section-title">Proposal readiness</h4>
      <p>{blocked ? "Proposal inputs need clarification." : "Ready for proposal generation."}</p>
      <p className="xi-hint">
        {readiness.answered_count} / {readiness.required_count} required responses answered;{" "}
        {readiness.execution_ready_count} / {readiness.required_count} execution-ready.
        {" "}Based on saved revision {readiness.source_revision}.{" "}
        {warnings.length > 0 && !blocked
          ? "Uncertain inputs below are estimated with missing context; they do not block generation."
          : "Only the blocking inputs below prevent generation."}
      </p>
      {readiness.questions.filter((question) => question.blockers.length > 0).map((question) => (
        <div key={question.question_key}>
          <h5 className="xi-proposal-subhead">{question.question_label}</h5>
          <ul className="xi-diagnosis-missing">
            {question.blockers.map((blocker, index) => (
              <li key={`${blocker.source_path ?? "gate"}-${index}`}>
                {blockerLink(blocker, encounterId, local)}
                {" — "}{blocker.reason}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {warnedQuestions.length > 0 && (
        <div>
          <h5 className="xi-proposal-subhead">Uncertain context (does not block generation)</h5>
          <p className="xi-hint" style={{ marginTop: 0 }}>
            Generation estimates these inputs with missing context and disclosed
            uncertainty. No need to resolve every field before generating.
          </p>
          {warnedQuestions.map((question) => (
            <div key={question.question_key}>
              <p className="xi-hint" style={{ marginBottom: 0 }}>{question.question_label}</p>
              <ul className="xi-diagnosis-missing">
                {(question.warnings ?? []).map((warning, index) => (
                  <li key={`${warning.source_path ?? "gate"}-${index}`}>
                    {blockerLink(warning, encounterId, local)}
                    {" — "}{warning.reason}
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
