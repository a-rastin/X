import type { Proposal } from "../new-case/api";
import { useFollowUpText } from "./text";

type Pair = {
  drug_a: string;
  drug_b: string;
  risk_rating: string | null;
  evidence: { raw_text: string }[];
};
type Report = {
  pairs: Pair[];
  resolved_medications: { catalog_drug_id: string; canonical_name: string }[];
};
type Ranking = {
  final_decision: {
    terminal: string;
    reason: string;
    adjuncts: string[];
    trace: string[];
  };
  distribution: {
    name: string;
    probability: number;
    removal_reasons: string[];
  }[];
  direct: { drug_id: string; coverage_unavailable: unknown } | null;
  current_interactions: Report;
  candidate_interactions: Report | null;
};
function formatSection(value: unknown, n: (s: string) => string): string {
  if (typeof value === "boolean") return n(value ? "yes" : "no");
  if (value == null) return n("unknown");
  if (Array.isArray(value))
    return value.map((v) => formatSection(v, n)).join(", ");
  if (typeof value === "object")
    return Object.entries(value)
      .filter(([key]) => key !== "prior")
      .map(([key, v]) => `${n(key)}: ${formatSection(v, n)}`)
      .join("; ");
  return n(String(value));
}
export function followUpProposalText(
  proposal: Proposal,
  n: (s: string) => string,
) {
  const ranking = proposal.payload.ranking as unknown as Ranking;
  const raw = proposal.payload.raw_inputs as {
    follow_up: { treatment: { main_catalog_drug_id: string | null } };
  };
  const assets = (
    proposal.payload.content_snapshot as {
      assets: Record<
        string,
        { entries: { id: string; catalog_drug_id?: string; name: string }[] }
      >;
    }
  ).assets;
  const main = assets[
    "new-case/v1/antipsychotic-formulations.json"
  ].entries.find(
    (e) => e.catalog_drug_id === raw.follow_up.treatment.main_catalog_drug_id,
  );
  return [
    n(ranking.final_decision.terminal),
    ...(main ? [main.name] : []),
    ...(ranking.direct
      ? [ranking.direct.drug_id.replace(/^(oral|lai):/, "")]
      : []),
    ...ranking.final_decision.adjuncts.map((key) =>
      n(key === "anticholinergic" ? "anticholinergic_advice" : key),
    ),
    ...proposal.payload.ranking.candidates.map(
      (c) => `${c.name} ${(c.probability * 100).toFixed(2)}%`,
    ),
    ...Object.entries(proposal.payload.sections)
      .filter(
        ([key, value]) =>
          key !== "contraception" || (value as { visible: boolean }).visible,
      )
      .map(([key, value]) => `${n(key)}: ${formatSection(value, n)}`),
  ].join("\n\n");
}
export function FollowUpProposalView({ proposal }: { proposal: Proposal }) {
  const n = useFollowUpText();
  const ranking = proposal.payload.ranking as unknown as Ranking;
  return (
    <section className="xi-card nc-plan">
      <h3>{n("original")}</h3>
      <pre>{followUpProposalText(proposal, n)}</pre>
      <p>{n(ranking.final_decision.reason)}</p>
      {ranking.direct && (
        <p>
          <bdi dir="ltr">
            {ranking.direct.drug_id.replace(/^(oral|lai):/, "")}
          </bdi>
          {(Array.isArray(ranking.direct.coverage_unavailable)
            ? ranking.direct.coverage_unavailable.length > 0
            : Boolean(ranking.direct.coverage_unavailable)) &&
            ` — ${n("coverage_unavailable")}`}
        </p>
      )}
      <p>{n("rankingShare")}</p>
      {(["current_interactions", "candidate_interactions"] as const).map(
        (key) => {
          const report = ranking[key];
          const names = new Map(
            report?.resolved_medications.map((m) => [
              m.catalog_drug_id,
              m.canonical_name,
            ]),
          );
          return (
            <details key={key}>
              <summary>{n(key)}</summary>
              <p>{n("interactionScope")}</p>
              {report?.pairs.map((pair) => (
                <article key={pair.drug_a + pair.drug_b}>
                  <h4>
                    <bdi>
                      {names.get(pair.drug_a)} / {names.get(pair.drug_b)}
                    </bdi>
                    : {pair.risk_rating ?? n("coverage_unavailable")}
                  </h4>
                  {pair.evidence.map((e, i) => (
                    <p dir="ltr" key={i}>
                      {e.raw_text.replace(/\\n/g, "\n")}
                    </p>
                  ))}
                </article>
              ))}
            </details>
          );
        },
      )}
      <details>
        <summary>{n("exclusions")}</summary>
        {ranking.distribution
          .filter((c) => c.removal_reasons.length)
          .map((c) => (
            <p key={c.name}>
              <bdi>
                {c.name} {(c.probability * 100).toFixed(2)}%
              </bdi>
              : {c.removal_reasons.map(n).join(", ")}
            </p>
          ))}
      </details>
      <details>
        <summary>{n("trace")}</summary>
        <p dir="ltr">{ranking.final_decision.trace.join(" → ")}</p>
      </details>
    </section>
  );
}
