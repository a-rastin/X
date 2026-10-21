import type { DdiEvidence } from "./api";

/** Both live and frozen reports retain the same escaped source details. */
export function DdiEvidenceList({ evidence }: { evidence: DdiEvidence[] }) {
  if (evidence.length === 0) {
    return null;
  }
  const csv = evidence.some((item) => item.source_format === "csv");
  return (
    <div>
      {csv && evidence.length > 1 && (
        <p className="xi-hint">
          Repeated export occurrences retain their source locations; they are
          not independent clinical corroboration.
        </p>
      )}
      <details className="xi-proposal-details">
        <summary>{csv ? "CSV provenance" : "Evidence assertions"} ({evidence.length})</summary>
        <ul>
          {evidence.map((item, index) => (
            <li key={`${item.source_path}:${String(item.span_start)}:${index}`}>
              <p style={{ marginBottom: 2 }}>
                Severity: {item.source_severity ?? "unknown"} · direction: {item.direction ?? "unknown"}
              </p>
              {item.raw_text && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.raw_text}</pre>}
              {item.management && <p>Management: {item.management}</p>}
              {item.source_record && (
                <dl>
                  {Object.entries(item.source_record).map(([key, value]) => (
                    <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>
                  ))}
                </dl>
              )}
              <p className="xi-hint">
                Source: {item.source_name ? `${item.source_name} · ` : ""}{item.source_path ?? "unknown"}
                {item.source_format ? ` · format ${item.source_format}` : ""}
                {item.source_record_number != null ? ` · record ${item.source_record_number}` : ""}
                {` · ${item.source_format === "csv" ? "lines" : "span"} ${item.span_start ?? "unknown"}–${item.span_end ?? "unknown"}`}
              </p>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
