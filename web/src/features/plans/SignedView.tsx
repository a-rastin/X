/* Read-only signed encounter view + any-physician addenda (S50 §§3-4,
 * plan.md §9.2; FR-20, FR-22, NFR-03).
 *
 * Rendered on the shared chart (any physician + admin may read) from
 * GET /patients/{id}/chart `signed_snapshots[]` + `addenda[]` — frozen
 * persisted data only, never draft content. §3: everything is read-only,
 * including original/final probabilities with exact attribution and
 * history; nothing here edits. Print permission follows the declared
 * provisional policy (see the print hint below — administrator print
 * allowed, physician print provisional, never claimed owner-confirmed).
 * §4: any active physician appends an attributed correction via
 * POST .../addenda (server-derived author/time, signed-only 409,
 * Idempotency-Key + disabled-while-pending so double-click/retry does not
 * duplicate); chronology shows the original signer and each addendum
 * author/date separately. Another physician can read but not alter signed
 * content — the form POSTs only text for a signed encounter and the server
 * 403s/409s everything else.
 *
 * Accessibility + theming: native headings/tables/textarea/buttons, status
 * role=status, errors role=alert with focus, semantic tokens only (both
 * themes), every state text + glyph (never color alone), print CSS keeps
 * complete tables readable (see theme.css `@media print`).
 *
 * E2E selector contract (dev-test owns e2e/signing.spec.ts;
 * `<id>` is the full encounter UUID):
 * - section `data-testid="signed-view-<id>"`
 * - signer `data-testid="signed-view-signer-<id>"`
 * - hash `data-testid="signed-view-hash-<id>"`
 * - plan `data-testid="signed-view-plan-<id>"`
 * - questions `data-testid="signed-view-questions-<id>"`
 * - addendum list `data-testid="addendum-list-<id>"`
 * - addendum editor `data-testid="addendum-textarea-<id>"`
 * - addendum submit `data-testid="addendum-submit-<id>"`
 * - addendum status `data-testid="addendum-status-<id>"` (role=status)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../identity/api";
import {
  MAX_ADDENDUM_CHARS,
  createAddendum,
  type AddendumRow,
  type SignedQuestionFrozen,
  type SignedSnapshotRow,
} from "./api";
import type { CptTable, Posterior } from "../reasoning/api";

function posteriorsSummary(posteriors: Posterior[]): string {
  return posteriors
    .map((post) => {
      const pairs = (post.states ?? []).map((state, index) => {
        const prob = post.probabilities?.[index];
        return `${state} ${typeof prob === "number" ? (prob * 100).toFixed(2) : "?"}%`;
      });
      return `${post.node_id}: ${pairs.join(", ")}`;
    })
    .join("; ");
}

function ReadonlyCptTables({ tables, label }: { tables: CptTable[]; label: string }) {
  if (tables.length === 0) {
    return <p className="xi-hint">No CPT tables recorded.</p>;
  }
  return (
    <div>
      {tables.map((table) => (
        <div key={table.node_id} style={{ marginBottom: 8 }}>
          <p style={{ marginBottom: 4 }}>
            <strong>
              <code className="xi-mono">{table.node_id}</code>
            </strong>{" "}
            <span className="xi-hint">
              states {(table.states ?? []).join(" / ")}
              {(table.parent_ids ?? []).length > 0
                ? ` · parents ${(table.parent_ids ?? []).join(", ")}`
                : " · root distribution"}
            </span>
          </p>
          <table className="xi-table" aria-label={`${label} for ${table.node_id}`}>
            <thead>
              <tr>
                <th scope="col">Parent states</th>
                {(table.states ?? []).map((state) => (
                  <th scope="col" key={state}>
                    {state} %
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(table.rows ?? []).map((row, index) => (
                // ponytail: index key is fine — rows arrive in pinned order.
                <tr key={index}>
                  <td>
                    <code className="xi-mono">
                      {(row.parent_states ?? []).length > 0 ? (row.parent_states ?? []).join(", ") : "—"}
                    </code>
                  </td>
                  {(row.percentages ?? []).map((pct, cell) => (
                    <td key={cell}>
                      <code className="xi-mono">{pct}</code>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function SignedQuestion({ frozen }: { frozen: SignedQuestionFrozen }) {
  if (frozen.status !== "ready") {
    return (
      <li>
        <p style={{ marginBottom: 2 }}>
          <strong>
            <code className="xi-mono">{frozen.question_key}</code>
          </strong>{" "}
          — <span className="xi-hint">■ Skipped ({frozen.status})</span>
        </p>
        <p className="xi-hint" style={{ marginTop: 0 }}>
          {frozen.gate_reason !== "" ? frozen.gate_reason : "Not applicable — no acceptance applies."}
        </p>
      </li>
    );
  }
  const original = frozen.original_baseline;
  const adjusted = frozen.current_cpt_revision_id !== null;
  return (
    <li>
      <p style={{ marginBottom: 2 }}>
        <strong>
          <code className="xi-mono">{frozen.question_key}</code>
        </strong>{" "}
        —{" "}
        <span className="xi-hint">
          ● {adjusted ? "Adjusted then accepted" : "Accepted unchanged"} · revision{" "}
          <code className="xi-mono">{frozen.current_cpt_revision_id ?? "original"}</code>
        </span>
      </p>
      <div className="xi-cpt-compare">
        <div>
          <p style={{ marginBottom: 4 }}>
            <strong>Original probabilities (LLM-estimated)</strong>{" "}
            <span className="xi-hint">
              network <code className="xi-mono">{original?.network_version ?? "?"}</code> ·
              template <code className="xi-mono">{original?.template_version ?? "?"}</code>
            </span>
          </p>
          {original === null ? (
            <p className="xi-hint">No original baseline recorded.</p>
          ) : (
            <div>
              <ReadonlyCptTables tables={original.validated_tables ?? []} label={`Original CPTs for ${frozen.question_key}`} />
              <p className="xi-hint" style={{ marginTop: 0 }}>
                Computed result: {posteriorsSummary(original.posteriors ?? [])}
              </p>
              <p style={{ marginTop: 0 }}>{original.section_text}</p>
            </div>
          )}
        </div>
        <div>
          <p style={{ marginBottom: 4 }}>
            <strong>Final accepted probabilities</strong>{" "}
            <span className="xi-hint">
              result <code className="xi-mono">{frozen.result_id ?? "—"}</code> (
              {frozen.result_kind ?? "?"})
            </span>
          </p>
          {frozen.current_tables === null ? (
            <p className="xi-hint">No final tables recorded.</p>
          ) : (
            <div>
              <ReadonlyCptTables tables={frozen.current_tables} label={`Final CPTs for ${frozen.question_key}`} />
              {frozen.current_result !== null && (
                <div>
                  <p className="xi-hint" style={{ marginTop: 0 }}>
                    Computed result: {posteriorsSummary(frozen.current_result.posteriors ?? [])}
                    {frozen.current_result.reused_from_baseline_id !== null && (
                      <span>
                        {" "}
                        · verified reuse of baseline{" "}
                        <code className="xi-mono">{frozen.current_result.reused_from_baseline_id}</code>{" "}
                        (adjusted then reset — final equality shown, history retained above)
                      </span>
                    )}
                  </p>
                  <p style={{ marginTop: 0 }}>{frozen.current_result.section_text}</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {frozen.acceptance !== null ? (
        <p className="xi-hint">
          <span aria-hidden="true">✓</span> Accepted by {frozen.acceptance.actor_username} at{" "}
          {frozen.acceptance.created_at} (acceptance{" "}
          <code className="xi-mono">{frozen.acceptance.id}</code> · review revision{" "}
          {frozen.review_revision}).
        </p>
      ) : (
        <p className="xi-hint">No acceptance recorded.</p>
      )}
    </li>
  );
}

export function SignedEncounterView({
  snapshot,
  addenda,
  encounterRevision,
  canAppend,
  onSessionExpired,
  onChanged,
}: {
  snapshot: SignedSnapshotRow;
  addenda: AddendumRow[];
  /** Current signed encounter revision (If-Match fence for addenda). */
  encounterRevision: number;
  /** True for active physicians; admins read but never append. */
  canAppend: boolean;
  onSessionExpired: () => void;
  /** Reload the chart after a successful append. */
  onChanged: () => void;
}) {
  const id = snapshot.encounter_id;
  const questions = snapshot.snapshot.questions ?? [];
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (error !== null) {
      errorRef.current?.focus();
    }
  }, [error]);

  const submit = useCallback(async () => {
    if (busy) {
      return;
    }
    const trimmed = text.trim();
    if (trimmed === "") {
      setError("Addendum text must be non-empty — nothing was sent.");
      return;
    }
    if (trimmed.length > MAX_ADDENDUM_CHARS) {
      setError(
        `Addendum must be at most ${MAX_ADDENDUM_CHARS} characters — shorten it before appending. Nothing was sent.`,
      );
      return;
    }
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const result = await createAddendum(id, encounterRevision, trimmed);
      setText("");
      setStatus(
        `✓ Addendum appended by ${result.addendum.author_display} at ${result.addendum.created_at}. The original signed content is unchanged.`,
      );
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setError(
        err instanceof ApiError
          ? `${err.message} (${err.code}). The typed text above is preserved — retry, do not retype.`
          : "Addendum failed. Check the connection and retry — the typed text above is preserved.",
      );
    } finally {
      setBusy(false);
    }
  }, [busy, text, id, encounterRevision, onSessionExpired, onChanged]);

  return (
    <section
      className="xi-card xi-signed-view"
      aria-labelledby={`signed-view-heading-${id}`}
      data-testid={`signed-view-${id}`}
      id={`signed-view-${id}`}
    >
      <h4 className="xi-section-title" id={`signed-view-heading-${id}`} style={{ fontSize: 16 }}>
        Signed encounter (read-only)
      </h4>
      <p style={{ marginTop: 0 }} data-testid={`signed-view-signer-${id}`}>
        <span aria-hidden="true">✓</span> <strong>Signed</strong> by {snapshot.signer_username} at{" "}
        {snapshot.signed_at} (encounter revision {snapshot.encounter_revision})
      </p>
      <p className="xi-hint" data-testid={`signed-view-hash-${id}`} style={{ marginTop: 0 }}>
        Snapshot <code className="xi-mono">{snapshot.id}</code> · hash{" "}
        <code className="xi-mono">{snapshot.snapshot_hash}</code> · batch{" "}
        <code className="xi-mono">{snapshot.batch_id}</code> · proposal{" "}
        <code className="xi-mono">{snapshot.proposal_id}</code>
      </p>

      <h5 className="xi-proposal-subhead">Frozen secondary plan (revision {snapshot.secondary_plan_revision})</h5>
      <div data-testid={`signed-view-plan-${id}`}>
        {snapshot.secondary_plan_text === "" ? (
          <p className="xi-hint">No plan text recorded.</p>
        ) : (
          <p className="xi-plan-text">{snapshot.secondary_plan_text}</p>
        )}
      </div>

      <h5 className="xi-proposal-subhead">
        Questions — original vs final accepted ({questions.length})
      </h5>
      <div data-testid={`signed-view-questions-${id}`}>
        {questions.length === 0 ? (
          <p className="xi-hint" role="status">
            No questions recorded in this snapshot.
          </p>
        ) : (
          <ol className="xi-proposal-sections">
            {questions.map((frozen) => (
              <SignedQuestion key={frozen.question_run_id} frozen={frozen} />
            ))}
          </ol>
        )}
      </div>

      <h5 className="xi-proposal-subhead">Addenda ({addenda.length})</h5>
      <div data-testid={`addendum-list-${id}`}>
        {addenda.length === 0 ? (
          <p className="xi-hint" role="status">
            No addenda yet — any active physician may append an attributed correction below. The
            original signer and content stay intact.
          </p>
        ) : (
          <ul>
            {addenda.map((entry) => (
              <li key={entry.id}>
                <p style={{ marginBottom: 2 }}>
                  <strong>{entry.author_display}</strong>{" "}
                  <span className="xi-hint">· {entry.created_at}</span>
                </p>
                <p style={{ marginTop: 0 }}>{entry.text}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      {canAppend ? (
        <div>
          <div className="xi-field">
            <label className="xi-label" htmlFor={`addendum-textarea-${id}`}>
              Append attributed correction
            </label>
            <textarea
              className="xi-input"
              id={`addendum-textarea-${id}`}
              data-testid={`addendum-textarea-${id}`}
              rows={3}
              autoComplete="off"
              value={text}
              onChange={(event) => {
                setText(event.target.value);
                setError(null);
              }}
              aria-describedby={`addendum-status-${id}`}
              maxLength={MAX_ADDENDUM_CHARS + 1}
            />
          </div>
          <p className="xi-hint" style={{ marginTop: 0 }}>
            {text.trim().length} of {MAX_ADDENDUM_CHARS} characters · attributed to you with the
            server date — the signed content above cannot be altered, only corrected by a new
            dated entry.
          </p>
          {status !== null && (
            <p className="xi-status" role="status" data-testid={`addendum-status-${id}`} id={`addendum-status-${id}`}>
              {status}
            </p>
          )}
          {error !== null && (
            <p className="xi-form-error" role="alert" tabIndex={-1} ref={errorRef}>
              {error}
            </p>
          )}
          {status === null && error === null && (
            <p className="xi-hint" role="status" data-testid={`addendum-status-${id}`} id={`addendum-status-${id}`}>
              No addendum submitted yet in this view.
            </p>
          )}
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              data-testid={`addendum-submit-${id}`}
              disabled={busy}
              onClick={() => void submit()}
            >
              {busy ? "Appending…" : "Append addendum"}
            </button>
          </div>
        </div>
      ) : (
        <p className="xi-hint" style={{ marginBottom: 0 }}>
          Read-only: only active physicians append addenda to signed encounters.
        </p>
      )}
    </section>
  );
}
