/* Follow-up baseline panel for the draft wizard (S14, FR-20-23).
 *
 * Mounted when `encounter.kind === "follow_up"`: reads the author-only
 * follow-up baseline and shows copied history/medications provenance plus
 * prior scores labeled distinctly as historical — never as current answers.
 * PANSS/C-SSRS answers live in their own sections and start empty on a
 * fresh follow-up; this panel never prefills them and never invents scores.
 * A draft created without a baseline reports an honest empty note with
 * `not_required` reconciliation. Loading/error states come from the real
 * endpoint with Retry; 401 expires the session, 403/404 render fixed
 * author/not-found copy.
 *
 * E2E selector contract (for e2e/followup.spec.ts):
 * - panel (#followup-baseline), prior scores (#followup-prior-scores,
 *   labeled "Historical scores (not current answers)"), reconciliation
 *   (#followup-reconciliation-status), copy note (#followup-copy-note)
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../identity/api";
import { getFollowupBaseline, type FollowupBaselinePayload } from "./api";

type PanelState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: FollowupBaselinePayload };

const DENIED_MESSAGE =
  "Only the draft author can view the follow-up baseline.";
const NOT_FOUND_MESSAGE =
  "Follow-up baseline not found. The draft may have been discarded.";

export function FollowupBaselinePanel({
  encounterId,
  onSessionExpired,
}: {
  encounterId: string;
  onSessionExpired: () => void;
}) {
  const [state, setState] = useState<PanelState>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const data = await getFollowupBaseline(encounterId);
      setState({ kind: "ready", data });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setState({ kind: "error", message: DENIED_MESSAGE });
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        setState({ kind: "error", message: NOT_FOUND_MESSAGE });
        return;
      }
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Follow-up baseline failed to load. Check the connection and retry.",
      });
    }
  }, [encounterId, onSessionExpired]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="xi-card" aria-labelledby="followup-baseline-heading">
      <h3 className="xi-section-title" id="followup-baseline-heading">
        Follow-up baseline
      </h3>
      <div id="followup-baseline">
        {state.kind === "loading" && <p>Loading baseline…</p>}
        {state.kind === "error" && (
          <div>
            <p className="xi-form-error" role="alert">
              {state.message}
            </p>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void load()}
            >
              Retry
            </button>
          </div>
        )}
        {state.kind === "ready" &&
          (state.data.baseline === null ? (
            <div>
              <p>
                No baseline was copied for this follow-up. History starts
                empty.
              </p>
              <p id="followup-reconciliation-status">
                {`Reconciliation: ${state.data.reconciliation.status}`}
              </p>
            </div>
          ) : (
            <div>
              <p id="followup-copy-note">
                Copied from baseline — reconciliation required.
              </p>
              <div id="followup-prior-scores">
                <p>Historical scores (not current answers)</p>
                {Object.entries(state.data.prior_scores_display).length ===
                0 ? (
                  <p>No prior scores recorded.</p>
                ) : (
                  <ul>
                    {Object.entries(state.data.prior_scores_display).map(
                      ([key, score]) => (
                        <li key={key}>
                          {key}:{" "}
                          {score.value === null
                            ? "not assessed"
                            : String(score.value)}
                        </li>
                      ),
                    )}
                  </ul>
                )}
              </div>
              <p id="followup-reconciliation-status">
                {`Reconciliation: ${state.data.reconciliation.status}`}
                {state.data.reconciliation.baseline_encounter_id !== null &&
                  ` (baseline ${state.data.reconciliation.baseline_encounter_id})`}
              </p>
            </div>
          ))}
      </div>
    </section>
  );
}
