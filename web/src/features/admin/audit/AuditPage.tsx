/* Readable admin audit table/detail view (S52 §4, seam T9; plan.md §10.1).
 *
 * Admin-only navigation/route guard lives in app/pages.tsx (complement to
 * the server 403, never a replacement). This page reads the real
 * `GET /audit-events` endpoint only — no fake rows, no update/delete
 * surface (none exists server-side; the trail is append-only).
 *
 * Table shows actor/operation/time/target; the detail panel under the table
 * shows the selected event's correlation references (patient/encounter/
 * question/run/revision/request_id), the timestamp in UTC (`Z`, as stored)
 * plus the viewer's local timezone rendering, and bounded metadata (capped
 * key count/value length, rendered as React text so markup stays inert;
 * obvious credential-shaped keys are withheld even though the server
 * already excludes keys and clinical bodies from safe details).
 *
 * Stable wiring for the dev-test e2e/audit.spec.ts seam (route `#/audit`):
 * - heading `data-testid="audit-heading"` (`#audit-heading`)
 * - filters `data-testid="audit-filters"` (`#audit-filters`)
 * - table `data-testid="audit-table"` (`#audit-table`)
 * - detail `data-testid="audit-detail"` (`#audit-detail`)
 * - pager `data-testid="audit-prev"` / `data-testid="audit-next"`
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError, listAuditEvents, type AuditEvent } from "./api";
import { useAuth } from "../../identity/auth";

/** Correlation keys rendered first in the detail panel, when present. */
const REF_KEYS = [
  "patient_id",
  "encounter_id",
  "question_run_id",
  "batch_id",
  "question_key",
  "revision",
  "revision_id",
  "sequence",
  "proposal_id",
  "snapshot_id",
] as const;

/** Bounded-metadata ceilings (task §4: bounded, no keys/clinical bodies). */
// ponytail: fixed ceilings, per-event "show all" only if an admin asks for it.
const MAX_META_KEYS = 24;
const MAX_VALUE_CHARS = 240;

/** Never render values under credential-shaped keys, defense in depth —
 * the server already excludes keys/clinical bodies from safe details. */
const WITHHELD_KEY_PATTERN = /password|passwd|secret|api[_-]?key|credential|session[_-]?token/i;

interface AppliedFilters {
  actor: string;
  operation: string;
  since: string;
  until: string;
  patientId: string;
  encounterId: string;
  questionRunId: string;
  batchId: string;
  questionKey: string;
}

const EMPTY_FILTERS: AppliedFilters = {
  actor: "",
  operation: "",
  since: "",
  until: "",
  patientId: "",
  encounterId: "",
  questionRunId: "",
  batchId: "",
  questionKey: "",
};

interface AuditData {
  items: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "refreshing"; data: AuditData }
  | { kind: "error"; message: string; forbidden: boolean }
  | { kind: "ready"; data: AuditData };

function shortId(value: string | null): string {
  if (value === null) {
    return "—";
  }
  return value.length > 8 ? `${value.slice(0, 8)}…` : value;
}

/** `occurred_at` is stored UTC (`Z`); show it verbatim plus the viewer's
 * local rendering (with zone abbreviation) so the timezone is explicit. */
function formatLocalTime(utcIso: string): string {
  const moment = new Date(utcIso);
  if (Number.isNaN(moment.getTime())) {
    return utcIso;
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "medium",
      timeZoneName: "short",
    }).format(moment);
  } catch {
    return moment.toLocaleString();
  }
}

/** Compact target cell: question key first, else the first present id. */
function targetSummary(event: AuditEvent): string {
  const details = event.details ?? {};
  const questionKey = details["question_key"];
  if (typeof questionKey === "string" && questionKey !== "") {
    return questionKey;
  }
  for (const key of ["question_run_id", "batch_id", "encounter_id", "patient_id"] as const) {
    const value = details[key];
    if (typeof value === "string" && value !== "") {
      return shortId(value);
    }
  }
  return "—";
}

function stringifyBounded(value: unknown): string {
  const text =
    typeof value === "string" ? value : JSON.stringify(value) ?? String(value);
  return text.length > MAX_VALUE_CHARS
    ? `${text.slice(0, MAX_VALUE_CHARS)}…`
    : text;
}

function AuditDetail({
  event,
  headingRef,
}: {
  event: AuditEvent;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
}) {
  const details = event.details ?? {};
  const refEntries = REF_KEYS.flatMap((key) => {
    const value = details[key];
    if (value === undefined || value === null || value === "") {
      return [];
    }
    return [[key, stringifyBounded(value)] as const];
  });
  const extraEntries = Object.entries(details)
    .filter(([key]) => !(REF_KEYS as readonly string[]).includes(key))
    .slice(0, MAX_META_KEYS);
  const truncatedExtras = Object.keys(details).length - refEntries.length > MAX_META_KEYS;

  return (
    <section
      className="xi-card"
      aria-labelledby="audit-detail-heading"
      data-testid="audit-detail"
      id="audit-detail"
    >
      <h3
        className="xi-section-title"
        id="audit-detail-heading"
        tabIndex={-1}
        ref={headingRef}
      >
        Event detail
      </h3>
      <dl className="xi-proposal-facts">
        <div>
          <dt>Event id</dt>
          <dd className="xi-mono">{event.id}</dd>
        </div>
        <div>
          <dt>Time (UTC, as stored)</dt>
          <dd className="xi-mono">{event.occurred_at}</dd>
        </div>
        <div>
          <dt>Time (your timezone)</dt>
          <dd>{formatLocalTime(event.occurred_at)}</dd>
        </div>
        <div>
          <dt>Actor</dt>
          <dd>{event.actor_display ?? event.actor ?? "—"}</dd>
        </div>
        <div>
          <dt>Action</dt>
          <dd className="xi-mono">{event.operation}</dd>
        </div>
        <div>
          <dt>Request id</dt>
          <dd className="xi-mono">{event.request_id ?? "—"}</dd>
        </div>
      </dl>
      <h4 className="xi-proposal-subhead">Correlation references</h4>
      {refEntries.length === 0 ? (
        <p className="xi-hint">No patient/encounter/question/run references on this event.</p>
      ) : (
        <dl className="xi-proposal-facts">
          {refEntries.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd className="xi-mono">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <h4 className="xi-proposal-subhead">Metadata (bounded)</h4>
      {extraEntries.length === 0 ? (
        <p className="xi-hint">No additional metadata.</p>
      ) : (
        <dl className="xi-proposal-facts">
          {extraEntries.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd className="xi-mono">
                {WITHHELD_KEY_PATTERN.test(key) ? "[withheld]" : stringifyBounded(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {truncatedExtras && (
        <p className="xi-hint">
          Showing the first {MAX_META_KEYS} metadata keys; the stored event is unchanged.
        </p>
      )}
      <p className="xi-hint" style={{ marginBottom: 0 }}>
        Payload hash (tamper-evidence, not secrecy):{" "}
        <span className="xi-mono">{event.payload_hash}</span>
      </p>
    </section>
  );
}

export function AuditPage() {
  const { sessionExpired } = useAuth();
  const uid = useId();
  const [draft, setDraft] = useState<AppliedFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<AppliedFilters>(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filterError, setFilterError] = useState<string | null>(null);
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);

  const reload = useCallback(async () => {
    setState((previous) =>
      previous.kind === "ready" || previous.kind === "refreshing"
        ? { kind: "refreshing", data: previous.data }
        : { kind: "loading" },
    );
    try {
      const result = await listAuditEvents({
        actor: applied.actor,
        operation: applied.operation,
        since: applied.since,
        until: applied.until,
        patientId: applied.patientId,
        encounterId: applied.encounterId,
        questionRunId: applied.questionRunId,
        batchId: applied.batchId,
        questionKey: applied.questionKey,
        offset,
      });
      setState({
        kind: "ready",
        data: {
          items: result.items,
          total: result.total,
          limit: result.limit,
          offset: result.offset,
        },
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Audit trail failed to load. Check the connection and retry.",
        forbidden: err instanceof ApiError && err.status === 403,
      });
    }
  }, [applied, offset, sessionExpired]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Keep selection to the visible page; a filter/page change drops a stale one.
  const data =
    state.kind === "ready" || state.kind === "refreshing" ? state.data : null;
  const selected =
    data !== null
      ? (data.items.find((item) => item.id === selectedId) ?? null)
      : null;

  useEffect(() => {
    if (selected !== null) {
      detailHeadingRef.current?.focus();
    }
  }, [selected]);

  function idFor(name: string): string {
    // Stable ids (not useId) so e2e and QA can target filter inputs;
    // section headings keep the unique prefix instead.
    return `audit-${name}`;
  }

  function setDraftField(field: keyof AppliedFilters, value: string): void {
    setDraft((previous) => ({ ...previous, [field]: value }));
  }

  /** `datetime-local` reads as local wall time; the trail stores UTC (`Z`). */
  function toUtcIso(localValue: string, fieldLabel: string): string | null {
    if (localValue.trim() === "") {
      return "";
    }
    const moment = new Date(localValue);
    if (Number.isNaN(moment.getTime())) {
      setFilterError(`${fieldLabel} is not a valid date and time.`);
      return null;
    }
    return moment.toISOString();
  }

  function handleApply(event: React.FormEvent): void {
    event.preventDefault();
    setFilterError(null);
    const since = toUtcIso(draft.since, "Since");
    if (since === null) {
      return;
    }
    const until = toUtcIso(draft.until, "Until");
    if (until === null) {
      return;
    }
    setApplied({ ...draft, since, until });
    setSelectedId(null);
    setOffset(0);
  }

  function handleClear(): void {
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setFilterError(null);
    setSelectedId(null);
    setOffset(0);
  }

  const from = data === null || data.total === 0 ? 0 : data.offset + 1;
  const to = data === null ? 0 : Math.min(data.offset + data.items.length, data.total);

  const textField = (
    field: keyof AppliedFilters,
    label: string,
    hint?: string,
    inputMode?: "text" | "search",
  ) => (
    <div className="xi-field">
      <label className="xi-label" htmlFor={idFor(field)}>
        {label}
      </label>
      <input
        className="xi-input"
        id={idFor(field)}
        type={inputMode === "search" ? "search" : "text"}
        autoComplete="off"
        spellCheck={false}
        value={draft[field]}
        onChange={(event) => setDraftField(field, event.target.value)}
      />
      {hint !== undefined && <p className="xi-hint" style={{ margin: 0 }}>{hint}</p>}
    </div>
  );

  return (
    <div>
      <h2 className="xi-page-title" data-testid="audit-heading" id="audit-heading">
        Audit trail
      </h2>
      <p className="xi-hint">
        Append-only administrator inspection. Successful mutations and their events commit
        together; this view is read-only and never claims tamper-proof storage — a
        database owner or restore can replace history.
      </p>
      <section className="xi-card" aria-labelledby={`${uid}-filters-heading`}>
        <h3 className="xi-section-title" id={`${uid}-filters-heading`}>
          Filters
        </h3>
        <form
          aria-label="Filter audit events"
          data-testid="audit-filters"
          id="audit-filters"
          onSubmit={handleApply}
          noValidate
        >
          <div className="xi-form-row">
            {textField("actor", "Actor", "Username or actor id substring.", "search")}
            {textField("operation", "Action (operation)", "Exact operation name.")}
            {textField("questionKey", "Question key")}
          </div>
          <div className="xi-form-row">
            <div className="xi-field">
              <label className="xi-label" htmlFor={idFor("since")}>
                Since (your timezone)
              </label>
              <input
                className="xi-input"
                id={idFor("since")}
                type="datetime-local"
                value={draft.since}
                onChange={(event) => setDraftField("since", event.target.value)}
              />
            </div>
            <div className="xi-field">
              <label className="xi-label" htmlFor={idFor("until")}>
                Until (your timezone)
              </label>
              <input
                className="xi-input"
                id={idFor("until")}
                type="datetime-local"
                value={draft.until}
                onChange={(event) => setDraftField("until", event.target.value)}
              />
            </div>
          </div>
          <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="xi-label" style={{ marginBottom: 8 }}>
              Target / correlation ids
            </legend>
            <div className="xi-form-row">
              {textField("patientId", "Patient id")}
              {textField("encounterId", "Encounter id")}
              {textField("questionRunId", "Question run id")}
              {textField("batchId", "Batch id")}
            </div>
          </fieldset>
          {filterError !== null && (
            <p className="xi-form-error" role="alert">
              {filterError}
            </p>
          )}
          <div className="xi-row-actions">
            <button className="xi-btn xi-btn-primary" type="submit">
              Apply filters
            </button>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={handleClear}
            >
              Clear
            </button>
          </div>
        </form>
      </section>

      <section className="xi-card" aria-labelledby={`${uid}-results-heading`}>
        <h3 className="xi-section-title" id={`${uid}-results-heading`}>
          Events
          {data !== null && ` (${data.total})`}
        </h3>
        {state.kind === "loading" && <p>Loading audit events…</p>}
        {state.kind === "error" && (
          <div>
            <p className="xi-form-error" role="alert">
              {state.forbidden
                ? "Administrator access required."
                : state.message}
            </p>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void reload()}
            >
              Retry
            </button>
          </div>
        )}
        {data !== null &&
          (data.total === 0 ? (
            <p>No audit events found for these filters.</p>
          ) : (
            <div>
              <table className="xi-table" aria-label="Audit events" data-testid="audit-table" id="audit-table">
                <thead>
                  <tr>
                    <th scope="col">Time (UTC)</th>
                    <th scope="col">Actor</th>
                    <th scope="col">Action</th>
                    <th scope="col">Target</th>
                    <th scope="col">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((event) => (
                    <tr key={event.id}>
                      <td className="xi-mono">{event.occurred_at}</td>
                      <td>{event.actor_display ?? event.actor ?? "—"}</td>
                      <td className="xi-mono">{event.operation}</td>
                      <td className="xi-mono">{targetSummary(event)}</td>
                      <td>
                        <button
                          className="xi-btn xi-btn-secondary"
                          type="button"
                          aria-expanded={selected?.id === event.id}
                          aria-label={`View detail for ${event.operation} at ${event.occurred_at}`}
                          onClick={() =>
                            setSelectedId((previous) =>
                              previous === event.id ? null : event.id,
                            )
                          }
                        >
                          {selected?.id === event.id ? "Hide" : "View"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="xi-hint" role="status">
                Showing {from}–{to} of {data.total} (stable time order).
              </p>
              <div className="xi-row-actions">
                <button
                  className="xi-btn xi-btn-secondary"
                  type="button"
                  data-testid="audit-prev"
                  disabled={data.offset === 0}
                  onClick={() => {
                    setSelectedId(null);
                    setOffset(Math.max(0, data.offset - data.limit));
                  }}
                >
                  Previous
                </button>
                <button
                  className="xi-btn xi-btn-secondary"
                  type="button"
                  data-testid="audit-next"
                  disabled={data.offset + data.items.length >= data.total}
                  onClick={() => {
                    setSelectedId(null);
                    setOffset(data.offset + data.limit);
                  }}
                >
                  Next
                </button>
              </div>
            </div>
          ))}
      </section>

      {selected !== null && (
        <AuditDetail event={selected} headingRef={detailHeadingRef} />
      )}
    </div>
  );
}
