import { useId, useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { Stack } from "@astryxdesign/core/Stack";
import { useLanguage } from "../../shared/i18n";
import { en as messages } from "../../shared/messages";
import { useFollowUpText } from "../follow-up/text";
import { ActivePathwayGraph, factorPath } from "./ActivePathwayGraph";
import { FactorAttributionTornado } from "./FactorAttributionTornado";
import { SimulationDiff } from "./SimulationDiff";
import { TreatmentProbabilityChart } from "./TreatmentProbabilityChart";
import { WhatIfControls } from "./WhatIfControls";
import { useReasoningSimulation } from "./useReasoningSimulation";
import type { FactorId, Workflow } from "./types";

export function ReasoningExplorer({ encounterId, proposalId, workflow }: {
  encounterId: string;
  proposalId: string;
  workflow: Workflow;
}) {
  const heading = useId();
  const { language, t } = useLanguage();
  const label = useFollowUpText();
  const en = language === "en";
  const simulation = useReasoningSimulation(encounterId, proposalId, workflow);
  const [factor, setFactor] = useState<FactorId | null>(null);
  const [resetCount, setResetCount] = useState(0);
  const { result, status } = simulation;
  const pending = status === "pending";
  const decision = result?.scenario.final_decision ?? result?.scenario.original_decision;
  const issues = result?.evaluation.scenario.issues ?? [];
  const pathway = result && factorPath(result.active_graph, factor);

  return (
    <section aria-labelledby={heading} style={{ gridColumn: "1 / -1", minWidth: 0 }}>
      <Stack gap={3}>
        <h3 id={heading}>{en ? "What-if simulation" : "شبیه‌سازی فرضی"}</h3>
        <p className="xi-hint" style={{ overflowWrap: "anywhere" }}>
          {en ? "Unsaved simulation for proposal" : "شبیه‌سازی ذخیره‌نشده برای طرح"} <bdi>{proposalId}</bdi>
        </p>
        <section aria-live="polite" aria-atomic="true">
          {pending && <p>{result
            ? en ? "Updating simulation…" : "در حال به‌روزرسانی شبیه‌سازی…"
            : t("nc.loading")}</p>}
          {status === "error" && (
            <Stack gap={2} align="start">
              <p>{result
                ? en ? "Update failed. The previous result is shown." : "به‌روزرسانی انجام نشد. نتیجهٔ قبلی نمایش داده می‌شود."
                : en ? "The simulation could not load." : "بارگذاری شبیه‌سازی انجام نشد."}</p>
              <Button label={t("nc.retry")} variant="secondary" onClick={simulation.retry} />
            </Stack>
          )}
        </section>
        {result && pathway && <>
          <Stack align="start">
            <Button type="button" variant="secondary"
              label={en ? "Reset to Patient Data" : "بازنشانی به داده‌های بیمار"}
              onClick={() => {
                simulation.reset();
                setFactor(null);
                setResetCount((count) => count + 1);
              }} />
          </Stack>
          <Stack direction="horizontal" gap={4} wrap="wrap" align="start">
            <Stack gap={3} style={{ flex: "1 1 16rem", minWidth: 0 }}>
              <WhatIfControls result={result} changes={simulation.changes} onChange={simulation.setChange}
                highlightedSources={pathway.sources} selectedFactor={factor} />
            </Stack>
            <Stack gap={3} style={{ flex: "3 1 28rem", minWidth: 0 }}>
              <SimulationDiff result={result} />
              {(decision?.terminal || issues.length > 0) && (
                <section aria-label={en ? "Scenario decision" : "تصمیم سناریو"}>
                  {decision?.terminal && <p>{label(decision.terminal)}</p>}
                  {issues.length > 0 && <ul>{issues.map(({ field, message_key }) => {
                    const text = Object.hasOwn(messages, message_key)
                      ? t(message_key as keyof typeof messages) : label("requiredInputs");
                    const fieldLabel = label(field.split(".").at(-1)!);
                    return <li key={field + message_key}>{fieldLabel} · {text}</li>;
                  })}</ul>}
                </section>
              )}
              <TreatmentProbabilityChart comparison={result}
                selectedTreatmentId={result.identity.selected_treatment_id}
                onSelectTreatment={simulation.selectTreatment} isInteracting={pending} />
              <FactorAttributionTornado attribution={result.selected_attribution}
                context={result} selectedFactorId={factor} onSelectFactor={setFactor} />
              <ActivePathwayGraph key={resetCount}
                result={result} focused={pathway} />
            </Stack>
          </Stack>
        </>}
      </Stack>
    </section>
  );
}
