import type { ProposalReadiness } from "./api";

export function historyControlId(sourcePath: string): string {
  return `hist-${sourcePath}`;
}

export function focusHistoryControl(sourcePath: string): void {
  const control = document.getElementById(historyControlId(sourcePath));
  control?.closest("section")?.scrollIntoView?.({ block: "start" });
  control?.focus();
}

/** Shares labels and unresolved reasons from the server; never evaluates gates. */
export function ProposalReadinessSummary({ readiness, encounterId, local = false }: {
  readiness: ProposalReadiness;
  encounterId: string;
  local?: boolean;
}) {
  return (
    <div data-testid="proposal-readiness" aria-live="polite">
      <h4 className="xi-section-title">Proposal readiness</h4>
      <p>{readiness.ready ? "Ready for proposal generation." : "Proposal inputs need clarification."}</p>
      <p className="xi-hint">
        {readiness.answered_count} / {readiness.required_count} required responses answered;{" "}
        {readiness.execution_ready_count} / {readiness.required_count} execution-ready.
        {" "}Based on saved revision {readiness.source_revision}. Unknown and Not assessed are answered responses that still need clarification.
      </p>
      {readiness.questions.filter((question) => question.blockers.length > 0).map((question) => (
        <div key={question.question_key}>
          <h5 className="xi-proposal-subhead">{question.question_label}</h5>
          <ul className="xi-diagnosis-missing">
            {question.blockers.map((blocker, index) => (
              <li key={`${blocker.source_path ?? "gate"}-${index}`}>
                {blocker.source_path?.startsWith("candidate/history/") ? (
                  <a href={local ? `#${historyControlId(blocker.source_path)}` : `#/encounters/${encounterId}#history?field=${encodeURIComponent(blocker.source_path)}`}
                    onClick={local ? (event) => { event.preventDefault(); focusHistoryControl(blocker.source_path!); } : undefined}>
                    {blocker.label}
                  </a>
                ) : blocker.label}
                {" — "}{blocker.reason}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
