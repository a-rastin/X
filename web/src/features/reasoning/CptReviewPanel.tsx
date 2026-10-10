/* Complete CPT review, comparison, and acceptance UI (S48c §§1-3 + §4
 * frontend half; plan.md §9.1, system-design §§8.1/8.3/8.5, ui-context CPT
 * panel; FR-50-57, NFR-03/NFR-06).
 *
 * One file, one panel per completed question run. Mounted inside
 * ProposalReviewPanel's per-question block behind its own lazy toggle so the
 * S48 transparency fetch counts are untouched (this panel GETs
 * `/question-runs/{id}/review` itself, same typed client, no second data
 * path, never recomputed from the chart). Page notes never enter this panel:
 * only the typed `safe_*` review fields below are rendered.
 *
 * §1: every root/conditional row with original/current exact percentages
 * (decimal strings, 6-decimal storage precision), row totals, signed
 * point-differences, direct/redistributed badges; outputs read-only;
 * single-state rows locked at 100% with the constraint explained;
 * keyboard-native sliders (arrow keys, focus visible via the global rule,
 * accessible labels, exact numeric readout); search + per-node folding keep
 * large tables fully reachable (filtering always reports X-of-Y with a
 * clear action — rows are never silently omitted).
 * §2: original vs latest-successful outputs/recommendations side by side
 * (even when wording is unchanged); unchanged/recalculating/successfully_
 * recalculated/failed plus a separate out-of-date (stale) indicator, each
 * text + glyph (never color alone); earlier successes carry an
 * earlier-revision label with their revision id.
 * §3: completed slider edits autosave debounced via POST cpt-adjustments
 * (If-Match + Idempotency-Key, server redistributes authoritatively);
 * sliders stay enabled while recalculating (acceptance/sign blocked);
 * refresh/resume restores from GET review (committed values are durable
 * server-side — no extra localStorage needed); reset/retry affect this
 * panel only and retain history; obsolete browser responses are ignored by
 * monotonic fetch/post sequence fencing.
 * §4 frontend half: per-question Accept POSTs exact references from GET
 * review (see api.buildAcceptanceBody, the `_accept_body(review)` mirror);
 * blocked with reason on recalculating/failed/stale/mismatch; accepted
 * state + history shown; edits/reset visibly invalidate via re-GET.
 * Sign UI (S49) consumes the exposed acceptance references; enforcement
 * itself is not built here.
 *
 * E2E selector contract (dev-test owns e2e/probability-review.spec.ts):
 * - panel root `data-testid="cpt-panel-<question_key>"`
 * - CPT toggle `data-testid="cpt-toggle-<question_key>"`
 * - accept button `data-testid="cpt-accept-<question_key>"`
 * - reset button `data-testid="cpt-reset-<question_key>"`
 * - retry button `data-testid="cpt-retry-<question_key>"` (failed only)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../identity/api";
import {
  acceptBlockedReason,
  buildAcceptanceBody,
  getQuestionReview,
  postAcceptance,
  postCptAdjustment,
  postCptReset,
  postRetryCalculation,
  type CalculationResult,
  type CptTable,
  type Posterior,
  type QuestionReviewPayload,
} from "./api";

const CALC_GLYPH: Record<string, string> = {
  unchanged: "●",
  recalculating: "◐",
  successfully_recalculated: "●",
  failed: "✕",
};

const CALC_LABEL: Record<string, string> = {
  unchanged: "Unchanged",
  recalculating: "Recalculating",
  successfully_recalculated: "Successfully recalculated",
  failed: "Failed",
};

const DEBOUNCE_MS = 700;
const RECALC_POLL_MS = 2500;

function cellKey(nodeId: string, parents: string[], state: string): string {
  return `${nodeId}|${parents.join(",")}|${state}`;
}

/** Decimal percentage string → integer units (100% = 100_000_000). NaN on junk. */
function parseUnits(token: string): number {
  const trimmed = token.trim();
  if (!/^\d+(\.\d{1,6})?$/.test(trimmed)) {
    return NaN;
  }
  const [whole, frac = ""] = trimmed.split(".");
  return Number(whole) * 1_000_000 + Number((frac + "000000").slice(0, 6));
}

function formatUnits(units: number): string {
  const sign = units < 0 ? "-" : units > 0 ? "+" : "";
  const abs = Math.abs(Math.round(units));
  const whole = Math.floor(abs / 1_000_000);
  const frac = abs % 1_000_000;
  if (frac === 0) {
    return `${sign}${whole}`;
  }
  return `${sign}${whole}.${String(frac).padStart(6, "0").replace(/0+$/, "")}`;
}

/** Slider float → POST target string (≤6 decimals, no repair). */
function formatTarget(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return String(rounded);
}

function rowTotalUnits(percentages: string[]): number {
  let total = 0;
  for (const token of percentages) {
    const units = parseUnits(token);
    if (!Number.isFinite(units)) {
      return NaN;
    }
    total += units;
  }
  return total;
}

function totalText(percentages: string[]): string {
  const total = rowTotalUnits(percentages);
  if (!Number.isFinite(total)) {
    return "?";
  }
  const asPercent = total / 1_000_000;
  return `${Number(asPercent.toFixed(6))}%`;
}

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

interface OriginalIndex {
  byCell: Map<string, string>;
}

function indexOriginals(original: CptTable[] | null): OriginalIndex {
  const byCell = new Map<string, string>();
  for (const table of original ?? []) {
    for (const row of table.rows ?? []) {
      const parents = row.parent_states ?? [];
      (table.states ?? []).forEach((state, index) => {
        byCell.set(cellKey(table.node_id, parents, state), row.percentages?.[index] ?? "?");
      });
    }
  }
  return { byCell };
}

export function CptReviewPanel({
  questionKey,
  runId,
  onSessionExpired,
}: {
  questionKey: string;
  runId: string;
  onSessionExpired: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [review, setReview] = useState<QuestionReviewPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [search, setSearch] = useState("");

  const fetchSeq = useRef(0);
  const postSeq = useRef(0);
  const saveBusy = useRef(false);
  const queuedEdit = useRef<{
    nodeId: string;
    parents: string[];
    state: string;
    target: string;
  } | null>(null);
  const debounceTimer = useRef<number | null>(null);
  const reviewRef = useRef<QuestionReviewPayload | null>(null);
  reviewRef.current = review;

  const failText = useCallback((err: unknown, fallback: string): string => {
    if (err instanceof ApiError) {
      return `${err.message} (${err.code})`;
    }
    return fallback;
  }, []);

  const fetchReview = useCallback(
    async (kind: "initial" | "refresh" | "poll") => {
      const seq = (fetchSeq.current += 1);
      if (kind === "initial") {
        setPhase("loading");
        setLoadError(null);
      }
      try {
        const payload = await getQuestionReview(runId);
        // ponytail: fencing — ignore responses overtaken by a newer fetch.
        if (seq !== fetchSeq.current) {
          return;
        }
        setReview(payload);
        setPhase("ready");
        if (kind === "refresh") {
          setSaveError(null);
        }
      } catch (err) {
        if (seq !== fetchSeq.current) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          onSessionExpired();
          return;
        }
        setLoadError(failText(err, "CPT review failed to load. Check the connection and retry."));
        setPhase("error");
      }
    },
    [runId, failText, onSessionExpired],
  );

  const toggle = useCallback(() => {
    const next = !open;
    setOpen(next);
    if (next && phase === "idle") {
      void fetchReview("initial");
    }
  }, [open, phase, fetchReview]);

  // Poll while the current revision is unsolved; hidden tabs skip ticks and
  // refresh on visible (same 2s-class cadence as the proposal panel).
  useEffect(() => {
    if (!open || review?.calculation_state !== "recalculating") {
      return;
    }
    let cancelled = false;
    const tick = () => {
      if (cancelled || document.hidden) {
        return;
      }
      void fetchReview("poll");
    };
    const timer = window.setInterval(tick, RECALC_POLL_MS);
    const onVisibility = () => {
      if (!document.hidden) {
        void fetchReview("poll");
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [open, review?.calculation_state, fetchReview]);

  useEffect(
    () => () => {
      if (debounceTimer.current !== null) {
        window.clearTimeout(debounceTimer.current);
      }
    },
    [],
  );

  const fireAdjustment = useCallback(
    async (edit: { nodeId: string; parents: string[]; state: string; target: string }) => {
      const live = reviewRef.current;
      if (live === null || live.baseline === null) {
        return;
      }
      // ponytail: serialize — one POST at a time in browser order; a newer
      // completed edit waits instead of racing the in-flight command.
      if (saveBusy.current) {
        queuedEdit.current = edit;
        return;
      }
      saveBusy.current = true;
      const seq = (postSeq.current += 1);
      setSaveStatus("Saving…");
      setSaveError(null);
      try {
        await postCptAdjustment(runId, {
          node_id: edit.nodeId,
          parent_states: edit.parents,
          state: edit.state,
          target_percentage: edit.target,
          expected_review_revision: live.review_revision,
        });
        if (seq !== postSeq.current) {
          return;
        }
        setDrafts({});
        setSaveStatus("Saved — recalculating.");
        await fetchReview("refresh");
      } catch (err) {
        if (seq !== postSeq.current) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          onSessionExpired();
          return;
        }
        if (err instanceof ApiError && err.status === 412) {
          setSaveError(
            "The probability review changed elsewhere (another tab saved first). Reload to reconcile — nothing was overwritten.",
          );
        } else {
          setSaveError(failText(err, "Adjustment failed. Check the connection and retry."));
        }
        setSaveStatus(null);
      } finally {
        saveBusy.current = false;
        const queued = queuedEdit.current;
        queuedEdit.current = null;
        if (queued !== null) {
          void fireAdjustment(queued);
        }
      }
    },
    [runId, fetchReview, failText, onSessionExpired],
  );

  const onSlider = useCallback(
    (nodeId: string, parents: string[], state: string, value: number) => {
      const key = cellKey(nodeId, parents, state);
      setDrafts((previous) => ({ ...previous, [key]: value }));
      setSaveStatus("Unsaved changes…");
      if (debounceTimer.current !== null) {
        window.clearTimeout(debounceTimer.current);
      }
      debounceTimer.current = window.setTimeout(() => {
        void fireAdjustment({ nodeId, parents, state, target: formatTarget(value) });
      }, DEBOUNCE_MS);
    },
    [fireAdjustment],
  );

  const doReset = useCallback(async () => {
    const live = reviewRef.current;
    if (live === null) {
      return;
    }
    setActionError(null);
    setActionBusy(true);
    try {
      await postCptReset(runId, live.review_revision);
      setDrafts({});
      setSaveStatus("Saved — reset to original values.");
      await fetchReview("refresh");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setActionError(failText(err, "Reset failed. Check the connection and retry."));
    } finally {
      setActionBusy(false);
    }
  }, [runId, fetchReview, failText, onSessionExpired]);

  const doRetry = useCallback(async () => {
    const live = reviewRef.current;
    if (live === null) {
      return;
    }
    setActionError(null);
    setActionBusy(true);
    try {
      await postRetryCalculation(
        runId,
        live.review_revision,
        live.current_cpt_revision_id,
      );
      await fetchReview("refresh");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setActionError(failText(err, "Local retry failed. Check the connection and retry."));
    } finally {
      setActionBusy(false);
    }
  }, [runId, fetchReview, failText, onSessionExpired]);

  const doAccept = useCallback(async () => {
    const live = reviewRef.current;
    if (live === null) {
      return;
    }
    setActionError(null);
    setActionBusy(true);
    try {
      const body = await buildAcceptanceBody(live);
      await postAcceptance(runId, body);
      await fetchReview("refresh");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setActionError(failText(err, "Acceptance failed. Check the connection and retry."));
    } finally {
      setActionBusy(false);
    }
  }, [runId, fetchReview, failText, onSessionExpired]);

  const regionId = `cpt-region-${questionKey}`;
  return (
    <section
      className="xi-cpt-panel"
      data-testid={`cpt-panel-${questionKey}`}
      id={`cpt-panel-${questionKey}`}
      aria-label={`CPT review for ${questionKey}`}
    >
      <div className="xi-row-actions">
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          data-testid={`cpt-toggle-${questionKey}`}
          aria-expanded={open}
          aria-controls={regionId}
          onClick={toggle}
        >
          {open ? `Hide CPT review for ${questionKey}` : `Show CPT review for ${questionKey}`}
        </button>
      </div>
      {open && (
        <div id={regionId}>
          {phase === "loading" && <p role="status">Loading persisted CPT review…</p>}
          {phase === "error" && (
            <div>
              <p className="xi-form-error" role="alert">
                {loadError}
              </p>
              <div className="xi-row-actions" style={{ marginTop: 8 }}>
                <button
                  className="xi-btn xi-btn-secondary"
                  type="button"
                  onClick={() => void fetchReview("initial")}
                >
                  Retry CPT review
                </button>
              </div>
            </div>
          )}
          {phase === "ready" && review !== null && (
            <CptReviewBody
              review={review}
              drafts={drafts}
              saveStatus={saveStatus}
              saveError={saveError}
              actionError={actionError}
              actionBusy={actionBusy}
              search={search}
              onSearch={setSearch}
              onSlider={onSlider}
              onReload={() => void fetchReview("refresh")}
              onReset={() => void doReset()}
              onRetry={() => void doRetry()}
              onAccept={() => void doAccept()}
            />
          )}
        </div>
      )}
    </section>
  );
}

function CptReviewBody({
  review,
  drafts,
  saveStatus,
  saveError,
  actionError,
  actionBusy,
  search,
  onSearch,
  onSlider,
  onReload,
  onReset,
  onRetry,
  onAccept,
}: {
  review: QuestionReviewPayload;
  drafts: Record<string, number>;
  saveStatus: string | null;
  saveError: string | null;
  actionError: string | null;
  actionBusy: boolean;
  search: string;
  onSearch: (value: string) => void;
  onSlider: (nodeId: string, parents: string[], state: string, value: number) => void;
  onReload: () => void;
  onReset: () => void;
  onRetry: () => void;
  onAccept: () => void;
}) {
  const questionKey = review.question_run.question_key;
  const originals = indexOriginals(review.original_tables);
  const current = review.current_tables;
  const latest = review.revisions.length > 0 ? review.revisions[review.revisions.length - 1] : null;
  const direct = (latest?.direct_edit ?? {}) as {
    node_id?: unknown;
    parent_states?: unknown;
    state?: unknown;
  };
  const directKey =
    typeof direct.node_id === "string" && typeof direct.state === "string" && Array.isArray(direct.parent_states)
      ? cellKey(direct.node_id, (direct.parent_states as unknown[]).map(String), direct.state)
      : null;
  const blockReason = acceptBlockedReason(review);
  const stale = review.input_freshness.stale;

  const query = search.trim().toLowerCase();
  let totalRows = 0;
  let shownRows = 0;
  for (const table of current ?? []) {
    for (const row of table.rows ?? []) {
      totalRows += 1;
      const hay = `${table.node_id} ${(row.parent_states ?? []).join(" ")}`.toLowerCase();
      if (query === "" || hay.includes(query)) {
        shownRows += 1;
      }
    }
  }

  const latestResult: CalculationResult | null = review.current_result_matches
    ? review.calculation_result
    : review.displayed_result;
  const latestIsEarlier =
    !review.current_result_matches && review.displayed_result !== null;
  const originalSection = review.baseline?.section_text ?? null;
  const latestSection = latestResult?.section_text ?? null;
  const wordingUnchanged =
    originalSection !== null && latestSection !== null && originalSection === latestSection;

  return (
    <div className="xi-cpt-body">
      <dl className="xi-proposal-facts">
        <div>
          <dt>Calculation state</dt>
          <dd>
            <span aria-hidden="true">{CALC_GLYPH[review.calculation_state] ?? "○"}</span>{" "}
            {CALC_LABEL[review.calculation_state] ?? review.calculation_state}
          </dd>
        </div>
        <div>
          <dt>Input freshness</dt>
          <dd>
            {stale ? (
              <span>
                <span aria-hidden="true">◍</span> Out of date — {review.input_freshness.reason}
              </span>
            ) : (
              <span>
                <span aria-hidden="true">●</span> Current inputs
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt>Review revision</dt>
          <dd>
            <code className="xi-mono">{review.review_revision}</code>
          </dd>
        </div>
        <div>
          <dt>Current CPT revision</dt>
          <dd>
            <code className="xi-mono">{review.current_cpt_revision_id ?? "original (no edits)"}</code>
          </dd>
        </div>
        <div>
          <dt>Displayed result revision</dt>
          <dd>
            <code className="xi-mono">{review.displayed_result_revision_id ?? "—"}</code>
            {latestIsEarlier && (
              <span className="xi-cpt-badge"> earlier revision</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Acceptance</dt>
          <dd>
            {review.is_accepted ? (
              <span>
                <span aria-hidden="true">✓</span> Accepted
              </span>
            ) : (
              <span>
                <span aria-hidden="true">○</span> Not accepted
              </span>
            )}
          </dd>
        </div>
      </dl>

      {review.calculation_state === "recalculating" && (
        <p className="xi-hint" role="status">
          Recalculating — sliders stay enabled; acceptance and sign-off stay blocked until the
          successful result lands.
        </p>
      )}
      {stale && (
        <div className="xi-warning-panel" role="status">
          <p style={{ margin: 0 }}>
            <strong>Out of date ◍</strong> — {review.input_freshness.reason}. Regeneration with
            current inputs is required; reset cannot make stale inputs current.
          </p>
        </div>
      )}
      {saveStatus !== null && (
        <p className="xi-hint" role="status">
          {saveStatus}
        </p>
      )}
      {saveError !== null && (
        <div>
          <p className="xi-form-error" role="alert">
            {saveError}
          </p>
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button className="xi-btn xi-btn-secondary" type="button" onClick={onReload}>
              Reload current values
            </button>
          </div>
        </div>
      )}

      {review.baseline === null || current === null ? (
        <p className="xi-hint" role="status">
          No adjustable baseline — this question has not completed successfully, so there are no
          CPTs to review or adjust.
        </p>
      ) : (
        <div>
          <h5 className="xi-proposal-subhead">CPT values (exact percentages)</h5>
          <p className="xi-hint" style={{ marginTop: 0 }}>
            Original values are LLM-estimated; current values include physician adjustments.
            Differences are percentage points (current − original). Network outputs below are
            read-only results, not sliders.
          </p>
          <div className="xi-field" style={{ maxWidth: 420 }}>
            <label className="xi-label" htmlFor={`cpt-search-${questionKey}`}>
              Search CPT rows
            </label>
            <input
              className="xi-input"
              id={`cpt-search-${questionKey}`}
              type="search"
              autoComplete="off"
              placeholder="Filter by node or parent states"
              value={search}
              onChange={(event) => onSearch(event.target.value)}
            />
          </div>
          <p className="xi-hint" role="status">
            Showing {shownRows} of {totalRows} rows
            {query !== "" && (
              <>
                {" "}
                for “{search.trim()}” —{" "}
                <button
                  className="xi-btn xi-btn-secondary"
                  type="button"
                  onClick={() => onSearch("")}
                >
                  Clear search
                </button>
              </>
            )}
          </p>
          {current.map((table) => (
            <details
              className="xi-cpt-node"
              key={table.node_id}
              open
            >
              <summary>
                <code className="xi-mono">{table.node_id}</code>{" "}
                <span className="xi-hint">
                  states {(table.states ?? []).join(" / ")}
                  {(table.parent_ids ?? []).length > 0
                    ? ` · parents ${(table.parent_ids ?? []).join(", ")}`
                    : " · root distribution"}
                </span>
              </summary>
              {(table.states ?? []).length === 1 ? (
                <p className="xi-hint" role="status">
                  Single-state row — this distribution remains at 100% by the probability
                  constraint; there is nothing to adjust.
                </p>
              ) : null}
              <table
                className="xi-table"
                aria-label={`CPT review for ${table.node_id} in ${questionKey}`}
              >
                <thead>
                  <tr>
                    <th scope="col">Parent states</th>
                    {(table.states ?? []).map((state) => (
                      <th scope="col" key={state}>
                        {state}
                      </th>
                    ))}
                    <th scope="col">Row total</th>
                  </tr>
                </thead>
                <tbody>
                  {(table.rows ?? [])
                    .map((row, rowIndex) => ({ row, rowIndex }))
                    .filter(({ row }) => {
                      if (query === "") {
                        return true;
                      }
                      const hay =
                        `${table.node_id} ${(row.parent_states ?? []).join(" ")}`.toLowerCase();
                      return hay.includes(query);
                    })
                    .map(({ row, rowIndex }) => {
                      const parents = row.parent_states ?? [];
                      const label = parents.length > 0 ? parents.join(", ") : "— (root)";
                      return (
                        <tr key={rowIndex}>
                          <td>
                            <code className="xi-mono">{label}</code>
                          </td>
                          {(table.states ?? []).map((state, stateIndex) => {
                            const original = originals.byCell.get(
                              cellKey(table.node_id, parents, state),
                            );
                            const committed = row.percentages?.[stateIndex] ?? "?";
                            const key = cellKey(table.node_id, parents, state);
                            const draft = drafts[key];
                            const shown =
                              draft !== undefined ? formatTarget(draft) : committed;
                            const sliderValue =
                              draft !== undefined
                                ? draft
                                : Number.parseFloat(committed);
                            const diffUnits =
                              original !== undefined
                                ? parseUnits(committed) - parseUnits(original)
                                : NaN;
                            const isDirect = directKey === key;
                            const changed =
                              original !== undefined && committed !== original;
                            const isRedistributed =
                              !isDirect && changed && latest?.kind === "adjustment";
                            const singleState = (table.states ?? []).length === 1;
                            const sliderId = `cpt-slider-${questionKey}-${table.node_id}-${rowIndex}-${state}`;
                            return (
                              <td key={state}>
                                <label
                                  className="xi-cpt-cell-label"
                                  htmlFor={sliderId}
                                >
                                  <code className="xi-mono" aria-label={`Current ${state} percentage`}>
                                    {shown}%
                                  </code>{" "}
                                  <span className="xi-hint">
                                    (original{" "}
                                    <code className="xi-mono">{original ?? "?"}%</code>
                                    {Number.isFinite(diffUnits) && (
                                      <> · {formatUnits(diffUnits)} pp</>
                                    )}
                                    )
                                  </span>{" "}
                                  {isDirect && (
                                    <span className="xi-cpt-badge">direct</span>
                                  )}
                                  {isRedistributed && (
                                    <span className="xi-cpt-badge">redistributed</span>
                                  )}
                                </label>
                                {!singleState && (
                                  <input
                                    id={sliderId}
                                    className="xi-cpt-slider"
                                    type="range"
                                    min={0}
                                    max={100}
                                    step={0.01}
                                    disabled={!Number.isFinite(sliderValue)}
                                    value={
                                      Number.isFinite(sliderValue)
                                        ? Math.min(100, Math.max(0, sliderValue))
                                        : 0
                                    }
                                    aria-label={`${table.node_id}, parents ${label}, ${state} percentage (original ${original ?? "?"} percent)`}
                                    onChange={(event) =>
                                      onSlider(
                                        table.node_id,
                                        parents,
                                        state,
                                        event.target.valueAsNumber,
                                      )
                                    }
                                  />
                                )}
                              </td>
                            );
                          })}
                          <td>
                            <code className="xi-mono">{totalText(row.percentages ?? [])}</code>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </details>
          ))}

          <h5 className="xi-proposal-subhead">Outputs and recommendations (read-only)</h5>
          <div className="xi-cpt-compare">
            <div>
              <p style={{ marginBottom: 4 }}>
                <strong>Original result</strong>{" "}
                <span className="xi-hint">
                  baseline{" "}
                  <code className="xi-mono">{review.baseline.id}</code>
                </span>
              </p>
              <p className="xi-hint" style={{ marginTop: 0 }}>
                {posteriorsSummary(review.baseline.posteriors ?? [])}
              </p>
              <p style={{ marginTop: 0 }}>{review.baseline.section_text}</p>
            </div>
            <div>
              <p style={{ marginBottom: 4 }}>
                <strong>Latest successful result</strong>{" "}
                {latestResult !== null ? (
                  <span className="xi-hint">
                    result <code className="xi-mono">{latestResult.id}</code> · revision{" "}
                    <code className="xi-mono">{latestResult.cpt_revision_id}</code>
                    {latestIsEarlier && (
                      <span className="xi-cpt-badge"> earlier revision</span>
                    )}
                    {latestResult.reused_from_baseline_id !== null && (
                      <span className="xi-hint">
                        {" "}
                        · verified reuse of baseline{" "}
                        <code className="xi-mono">
                          {latestResult.reused_from_baseline_id}
                        </code>
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="xi-hint">no successful adjusted result yet</span>
                )}
              </p>
              {latestResult === null ? (
                <p className="xi-hint" role="status">
                  {review.calculation_state === "failed"
                    ? "The current revision failed to calculate — the prior successful result above stays labeled separately. Retry locally or reset."
                    : "No successful adjusted result exists yet; the original above is the current result."}
                </p>
              ) : (
                <div>
                  <p className="xi-hint" style={{ marginTop: 0 }}>
                    {posteriorsSummary(latestResult.posteriors ?? [])}
                  </p>
                  <p style={{ marginTop: 0 }}>{latestResult.section_text}</p>
                  {wordingUnchanged && (
                    <p className="xi-hint">
                      Recommendation wording is unchanged — probabilities and provenance still
                      differ; both sides stay visible.
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>

          {review.calculation_state === "failed" && (
            <div className="xi-warning-panel" role="status">
              <p style={{ margin: 0 }}>
                <strong>Failed ✕</strong> — the current values are preserved but unsolved. Retry
                the local calculation or reset to original values; acceptance stays blocked.
              </p>
            </div>
          )}

          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              data-testid={`cpt-reset-${questionKey}`}
              disabled={actionBusy}
              onClick={onReset}
            >
              {actionBusy ? "Working…" : "Reset to original values"}
            </button>
            {review.calculation_state === "failed" && (
              <button
                className="xi-btn xi-btn-secondary"
                type="button"
                data-testid={`cpt-retry-${questionKey}`}
                disabled={actionBusy}
                onClick={onRetry}
              >
                {actionBusy ? "Retrying…" : "Retry local calculation"}
              </button>
            )}
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={onReload}
            >
              Reload current values
            </button>
          </div>
          <p className="xi-hint">
            Reset creates an audited baseline-equal revision for this question only — other
            panels stay unchanged and history is retained. Retry targets exactly the current
            revision without re-estimation.
          </p>
        </div>
      )}

      <h5 className="xi-proposal-subhead">Acceptance</h5>
      {review.is_accepted && review.acceptance !== null ? (
        <div className="xi-notice" role="status">
          <p style={{ margin: 0 }}>
            <strong>Accepted ✓</strong> by {review.acceptance.actor_username} at{" "}
            {review.acceptance.created_at}
          </p>
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            Acceptance <code className="xi-mono">{review.acceptance.id}</code> · baseline{" "}
            <code className="xi-mono">{review.acceptance.baseline_id}</code> · revision{" "}
            <code className="xi-mono">{review.acceptance.cpt_revision_id ?? "original"}</code>{" "}
            · result <code className="xi-mono">{review.acceptance.result_id}</code> (
            {review.acceptance.result_kind}) · input{" "}
            <code className="xi-mono">{review.acceptance.input_hash.slice(0, 12)}…</code>.
            Sign-off (S49) consumes these references; editing, reset, or relevant patient
            changes invalidate this acceptance.
          </p>
        </div>
      ) : blockReason !== null ? (
        <div className="xi-warning-panel" role="status">
          <p style={{ margin: 0 }}>Acceptance blocked — {blockReason}</p>
          <div className="xi-row-actions" style={{ marginTop: 8 }}>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              data-testid={`cpt-accept-${questionKey}`}
              disabled
            >
              Accept current result (blocked)
            </button>
          </div>
        </div>
      ) : (
        <div>
          <p className="xi-hint" style={{ marginTop: 0 }}>
            Accept the exact current{" "}
            {review.current_cpt_revision_id === null
              ? "original baseline"
              : "adjusted revision"}{" "}
            and its matching successful result.
          </p>
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              data-testid={`cpt-accept-${questionKey}`}
              disabled={actionBusy}
              onClick={onAccept}
            >
              {actionBusy ? "Accepting…" : "Accept current result"}
            </button>
          </div>
        </div>
      )}
      {actionError !== null && (
        <p className="xi-form-error" role="alert">
          {actionError}
        </p>
      )}

      {(review.revisions.length > 0 || review.acceptances.length > 0) && (
        <details className="xi-proposal-details">
          <summary>
            Revision and acceptance history ({review.revisions.length} revisions,{" "}
            {review.acceptances.length} acceptances)
          </summary>
          {review.revisions.length > 0 && (
            <ul>
              {review.revisions.map((revision) => (
                <li key={revision.id}>
                  <code className="xi-mono">rev {revision.sequence}</code> · {revision.kind} ·{" "}
                  <code className="xi-mono">{revision.id}</code> · {revision.actor_username} ·{" "}
                  {revision.created_at}
                </li>
              ))}
            </ul>
          )}
          {review.acceptances.length > 0 && (
            <ul>
              {review.acceptances.map((acceptance) => (
                <li key={acceptance.id}>
                  <code className="xi-mono">{acceptance.id}</code> ·{" "}
                  {acceptance.result_kind} <code className="xi-mono">{acceptance.result_id}</code>{" "}
                  · revision{" "}
                  <code className="xi-mono">
                    {acceptance.cpt_revision_id ?? "original"}
                  </code>{" "}
                  · {acceptance.actor_username} · {acceptance.created_at}
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
    </div>
  );
}
