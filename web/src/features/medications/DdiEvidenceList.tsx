import { useLanguage } from "../../shared/i18n";
import type { DdiEvidence } from "./api";

/** Both live and frozen reports retain the same escaped source details. */
export function DdiEvidenceList({ evidence }: { evidence: DdiEvidence[] }) {
  const { t } = useLanguage();
  if (evidence.length === 0) {
    return null;
  }
  const csv = evidence.some((item) => item.source_format === "csv");
  return (
    <div>
      {csv && evidence.length > 1 && (
        <p className="xi-hint">
          {t("ddiRepeatedEvidence")}
        </p>
      )}
      <details className="xi-proposal-details">
        <summary>{t(csv ? "csvProvenance" : "evidenceAssertions")} ({evidence.length})</summary>
        <ul>
          {evidence.map((item, index) => (
            <li key={`${item.source_path}:${String(item.span_start)}:${index}`}>
              <p style={{ marginBottom: 2 }}>
                {item.risk_rating ? `Lexicomp: ${String(item.risk_rating)}` : t("ddiSeverityDirection", { severity: item.source_severity ?? t("unknown"), direction: item.direction ?? t("unknown") })}
              </p>
              {item.raw_text && <pre dir="ltr" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.raw_text}</pre>}
              {item.management && <p>{t("ddiManagement", { management: item.management })}</p>}
              {item.source_record && (
                <dl>
                  {Object.entries(item.source_record).map(([key, value]) => (
                    <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>
                  ))}
                </dl>
              )}
              <p className="xi-hint">
                {t("ddiSource", { source: `${item.source_name ? `${item.source_name} · ` : ""}${item.source_path ?? t("unknown")}` })}
                {item.source_format ? t("ddiFormat", { format: item.source_format }) : ""}
                {item.source_record_number != null ? t("ddiRecord", { record: item.source_record_number }) : ""}
                {t(item.source_format === "csv" ? "ddiLines" : "ddiSpan", { start: item.span_start ?? t("unknown"), end: item.span_end ?? t("unknown") })}
              </p>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
