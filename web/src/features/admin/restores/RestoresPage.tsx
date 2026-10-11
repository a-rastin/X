/* Staged restore validation + commit with maintenance, fencing, and
 * rollback (S55 §§1-4 staging, S56 commit; seams T10 over T1, T9 page;
 * plan.md §10.3 steps 1-6; FR-41–42, NFR-03/04).
 *
 * Admin-only navigation/route guard lives in app/pages.tsx (complement to
 * the server 401/403, never a replacement). This page calls the real
 * `POST /restores/validate` (raw zip upload to isolated staging),
 * `GET /restores/{id}` (staging + commit progress/impact read),
 * `POST /restores/{id}/commit` (exact-digest replacement behind
 * maintenance), and `POST /restores/{id}/reopen` (verify-then-reopen)
 * endpoints only — authenticated same-origin fetch (session cookie via
 * credentials:include). Mutations carry the per-session CSRF token; reads
 * carry no CSRF/idempotency, like the audit/backups reads.
 *
 * Behavior:
 * - One file input stages a backup zip (bounded synchronous validation;
 *   201 carries the terminal `succeeded`/`failed` job). A "Re-check
 *   status" read re-GETs the current job so TTL expiry, a replacing
 *   upload, or commit progress surfaces without duplicating backend logic.
 * - A succeeded staging shows its impact report (backup date/schema/
 *   content/checksums/live-vs-staged impact, key-reentry consequence)
 *   plus the immutable confirmation digest commit requires exactly.
 * - A failed staging shows its validation error; live patients/login/jobs
 *   are untouched server-side (only staging + audit rows are written).
 * - Commit never exists prematurely: the Commit button stays disabled
 *   until the operator types the exact staged digest into the confirmation
 *   field, the staging is fresh (`expired: false`), no commit is already
 *   recorded/running, maintenance is clear, and no action is in flight.
 * - Commit progress (`commit_status`/`commit_phase`/`commit_history`/
 *   `maintenance_active`, pre-restore backup id, generation before/after,
 *   session-revocation and DEPLOYMENT_FENCED notes) renders from the
 *   polled job; an `in_progress` commit re-GETs on a bounded poll like
 *   the backups page. A 500 rollback returns the rolled-back job
 *   directly: prior database restored, maintenance held until verified
 *   healthy, reopen offered. Stale digests fail 409 with re-validate
 *   guidance and no mutation.
 * - A successful commit revokes every session server-side (re-login
 *   required): the success panel is self-contained from the commit
 *   response, and the next authenticated read naturally returns 401.
 *
 * Stable wiring (mirrors #backups-* conventions):
 * - heading `data-testid="restores-heading"` (`#restores-heading`)
 * - file input `data-testid="restores-upload"` (`#restores-upload`)
 * - status `data-testid="restores-status"` (`#restores-status`, role=status)
 * - digest `data-testid="restores-digest"` (`#restores-digest`)
 * - report `data-testid="restores-report"` (`#restores-report`)
 * - error `data-testid="restores-error"` (`#restores-error`, role=alert)
 * - commit section `data-testid="restores-commit-section"`
 *   (`#restores-commit-section`)
 * - confirmation input `data-testid="restores-commit-digest-input"`
 *   (`#restores-commit-digest-input`)
 * - commit `data-testid="restores-commit-submit"`
 *   (`#restores-commit-submit`)
 * - commit progress `data-testid="restores-commit-progress"`
 *   (`#restores-commit-progress`)
 * - commit summary `data-testid="restores-commit-status"`
 *   (`#restores-commit-status`, role=status)
 * - commit failure `data-testid="restores-commit-error"`
 *   (`#restores-commit-error`, role=alert)
 * - commit history `data-testid="restores-commit-history"`
 *   (`#restores-commit-history`)
 * - reopen section `data-testid="restores-reopen-section"`
 *   (`#restores-reopen-section`)
 * - reopen `data-testid="restores-reopen-submit"`
 *   (`#restores-reopen-submit`)
 * - reopen state `data-testid="restores-reopen-status"`
 *   (`#restores-reopen-status`, role=status)
 * - maintenance banner `data-testid="restores-maintenance-banner"`
 *   (`#restores-maintenance-banner`, role=status)
 * - recovery notes `data-testid="restores-recovery-notes"`
 *   (`#restores-recovery-notes`)
 */

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  commitRestore,
  getRestore,
  reopenRestore,
  validateRestore,
  type RestoreActionError,
  type RestoreJob,
} from "./api";
import { useAuth } from "../../identity/auth";

const POLL_MS = 2000;
const HIDDEN_POLL_MS = 15000;
// ponytail: 60 foreground ticks (~2 min) then stop; the operator re-checks.
const MAX_POLLS = 60;

function shortHash(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 16)}…` : hash;
}

function commitStateOf(job: RestoreJob | null): string {
  if (job === null) {
    return "idle";
  }
  return job.commit_status ?? "idle";
}

export function RestoresPage() {
  const { sessionExpired } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<RestoreJob | null>(null);
  const [confirmInput, setConfirmInput] = useState("");
  const [uploading, setUploading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [reopening, setReopening] = useState(false);
  const [commitPolling, setCommitPolling] = useState(false);
  const [pollsUsed, setPollsUsed] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollTargetRef = useRef<string | null>(null);

  function failAs(err: unknown, fallback: string): string {
    if (err instanceof ApiError && err.status === 403) {
      return "Administrator access required.";
    }
    return err instanceof ApiError ? err.message : fallback;
  }

  function applyJob(staged: RestoreJob): void {
    setJob(staged);
    const commit = staged.commit_status ?? "idle";
    if (staged.status === "failed") {
      setStatus(null);
      setError(
        staged.error_message ?? "Restore validation failed. Live data unchanged.",
      );
      setCommitPolling(false);
      return;
    }
    if (staged.status !== "succeeded") {
      setStatus("Restore staging in progress…");
      setError(null);
      return;
    }
    if (commit === "succeeded") {
      setStatus(
        `Restore committed and verified. Pre-restore backup ${staged.pre_restore_backup_id ?? "unknown"} preserves the prior database. All sessions revoked — sign in again.`,
      );
      setError(null);
      setCommitPolling(false);
      return;
    }
    if (commit === "failed" || commit === "rolled_back") {
      if (staged.maintenance_active) {
        setStatus(null);
        setError(
          `${staged.commit_error_message ?? "Restore commit failed."} Prior database restored; maintenance held until verified healthy — verify, then reopen below.`,
        );
      } else {
        setStatus(
          `Restore commit ${commit} — ${staged.commit_error_message ?? "see rollback below"}. Prior database restored; maintenance cleared after verified health.`,
        );
        setError(null);
      }
      setCommitPolling(false);
      return;
    }
    if (commit === "in_progress") {
      setStatus(
        `Restore commit in progress… phase ${staged.commit_phase ?? "starting"}. Writes held behind maintenance.`,
      );
      setError(null);
      pollTargetRef.current = staged.id;
      setPollsUsed(0);
      setCommitPolling(true);
      return;
    }
    if (staged.expired) {
      setStatus(
        "Staging expired or superseded — this confirmation is stale and must not be committed.",
      );
      setError(null);
    } else {
      setStatus(
        "Archive staged and validated. Live data unchanged — review the report and digest below.",
      );
      setError(null);
    }
    setCommitPolling(false);
  }

  async function upload(): Promise<void> {
    if (uploading || refreshing || committing || reopening || file === null) {
      return;
    }
    setUploading(true);
    setStatus("Validating archive in staging…");
    setError(null);
    try {
      const staged = await validateRestore(file);
      // A replacing upload invalidates the prior confirmation server-side,
      // so the typed confirmation must not carry over to the new digest.
      setConfirmInput("");
      applyJob(staged);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError(failAs(err, "Restore upload failed. Check the connection and retry."));
      setStatus(null);
    } finally {
      setUploading(false);
    }
  }

  async function refresh(): Promise<void> {
    if (uploading || refreshing || committing || reopening || job === null) {
      return;
    }
    setRefreshing(true);
    try {
      const current = await getRestore(job.id);
      applyJob(current);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && (err.status === 503 || err.code === "MAINTENANCE")) {
        setError(`${err.message} Reads stay available for verification; restores progress is below.`);
        return;
      }
      setError(failAs(err, "Restore status re-check failed. Check the connection and retry."));
    } finally {
      setRefreshing(false);
    }
  }

  async function commit(): Promise<void> {
    if (uploading || refreshing || committing || reopening || job === null) {
      return;
    }
    const staged = (job.confirmation_digest ?? "").trim().toLowerCase();
    const typed = confirmInput.trim().toLowerCase();
    if (staged === "" || typed !== staged) {
      setError(
        "Confirmation does not match the staged digest. Type the exact 64-character digest shown above — stale or mistyped confirmations are rejected without touching live data.",
      );
      return;
    }
    setCommitting(true);
    setStatus("Committing restore behind maintenance… pre-restore backup first, then switch.");
    setError(null);
    try {
      const finished = await commitRestore(job.id, confirmInput.trim());
      applyJob(finished);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      const nested = (err as RestoreActionError).job;
      if (nested !== undefined && nested.id !== "") {
        applyJob(nested);
      }
      if (err instanceof ApiError && err.status === 409) {
        setError(
          `${err.message} Re-validate the archive to mint a fresh digest, then confirm the new digest exactly — stale confirmations never touch live data.`,
        );
        setStatus(null);
      } else if (err instanceof ApiError && err.status === 422) {
        setError(
          `${err.message} The digest must be the exact 64-character hex value shown above.`,
        );
        setStatus(null);
      } else if (
        err instanceof ApiError &&
        (err.status === 503 || err.code === "MAINTENANCE")
      ) {
        setError(`${err.message} The system is under maintenance — verify health, then reopen below.`);
      } else if (nested === undefined) {
        setError(failAs(err, "Restore commit failed. Check the connection and retry."));
        setStatus(null);
      } else {
        setError(failAs(err, "Restore commit failed. Prior state is shown below."));
      }
    } finally {
      setCommitting(false);
    }
  }

  async function reopen(): Promise<void> {
    if (uploading || refreshing || committing || reopening || job === null) {
      return;
    }
    setReopening(true);
    setStatus("Verifying health (sign-in, reads, saved-CPT replay) before reopening…");
    setError(null);
    try {
      const current = await reopenRestore(job.id);
      applyJob(current);
      if (!current.maintenance_active) {
        setStatus(
          "Maintenance cleared after verified health. The deployment is open for writes again.",
        );
        setError(null);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      const nested = (err as RestoreActionError).job;
      if (nested !== undefined && nested.id !== "") {
        applyJob(nested);
      }
      if (err instanceof ApiError && (err.status === 503 || err.code === "RESTORE_MAINTENANCE_HELD")) {
        setError(
          `${err.message} Maintenance stays held — fix the deployment, then verify and reopen again. The prior database and pre-restore backup id are shown below.`,
        );
      } else if (nested === undefined) {
        setError(failAs(err, "Reopen failed. Check the connection and retry."));
      } else {
        setError(failAs(err, "Reopen failed. Current state is shown below."));
      }
    } finally {
      setReopening(false);
    }
  }

  // Bounded commit-progress polling with hidden-tab backoff (backups-page
  // shape): re-GET while the commit runs, stop at terminal states or the
  // attempt budget. The synchronous commit rarely leaves `in_progress`
  // observable — this covers concurrent-operator commits.
  useEffect(() => {
    if (!commitPolling || pollTargetRef.current === null) {
      return;
    }
    let cancelled = false;
    let timer: number | null = null;
    let attempts = 0;
    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const tick = async () => {
      clearTimer();
      const target = pollTargetRef.current;
      if (cancelled || target === null) {
        return;
      }
      attempts += 1;
      try {
        const current = await getRestore(target);
        if (cancelled || pollTargetRef.current !== target) {
          return;
        }
        setPollsUsed(attempts);
        const commit = current.commit_status ?? "idle";
        if (commit === "in_progress") {
          setJob(current);
          setStatus(
            `Restore commit in progress… phase ${current.commit_phase ?? "starting"}. Writes held behind maintenance.`,
          );
        } else {
          applyJob(current);
          return;
        }
      } catch (err) {
        if (cancelled || pollTargetRef.current !== target) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          setCommitPolling(false);
          sessionExpired();
          return;
        }
        // ponytail: one transient read failure must not kill the poll loop;
        // the budget below still bounds it.
        setStatus("Restore commit in progress (refresh retrying)…");
      }
      if (attempts >= MAX_POLLS) {
        setCommitPolling(false);
        setStatus(
          "Commit is still running after the progress budget. Use Re-check status later.",
        );
        return;
      }
      timer = window.setTimeout(
        () => {
          void tick();
        },
        typeof document !== "undefined" && document.hidden ? HIDDEN_POLL_MS : POLL_MS,
      );
    };
    const onVisibility = () => {
      if (typeof document === "undefined" || document.hidden) {
        return;
      }
      void tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void tick();
    return () => {
      cancelled = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // sessionExpired is stable from the auth context; polling keys on the
    // commit flag only (fresh job objects per tick must not restart it).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commitPolling]);

  const busy = uploading || refreshing || committing || reopening || commitPolling;
  const report = job?.report ?? null;
  const commitState = commitStateOf(job);
  const stagedDigest = job?.confirmation_digest ?? "";
  const typedDigest = confirmInput.trim().toLowerCase();
  const digestValid =
    stagedDigest !== "" &&
    typedDigest !== "" &&
    typedDigest === stagedDigest.trim().toLowerCase() &&
    /^[0-9a-f]{64}$/.test(typedDigest);
  const showCommit =
    job !== null && job.status === "succeeded" && stagedDigest !== "";
  const commitReady =
    job !== null &&
    job.status === "succeeded" &&
    !job.expired &&
    commitState === "idle" &&
    !job.maintenance_active;
  const canCommit = commitReady && digestValid && !busy;
  let commitBlockedReason: string | null = null;
  if (showCommit && job !== null) {
    if (job.expired) {
      commitBlockedReason =
        "This digest has expired or been superseded — re-validate the archive to mint a fresh digest.";
    } else if (commitState === "succeeded") {
      commitBlockedReason = "Already committed — this staged artifact is live.";
    } else if (commitState === "in_progress") {
      commitBlockedReason = "A commit is already running for this staged artifact.";
    } else if (commitState === "failed" || commitState === "rolled_back") {
      commitBlockedReason = job.maintenance_active
        ? "Rolled back with maintenance held — verify health and reopen below before any new commit."
        : "This commit failed and rolled back — review the rollback below; re-validate before committing again.";
    } else if (job.maintenance_active) {
      commitBlockedReason = "Maintenance is active — verify health and reopen below before committing.";
    } else if (!digestValid) {
      commitBlockedReason =
        "Type the exact 64-character staged digest to enable Commit. Stale or mistyped confirmations are rejected without touching live data.";
    }
  }
  const showProgress =
    job !== null &&
    (commitState !== "idle" ||
      job.maintenance_active ||
      job.pre_restore_backup_id !== null ||
      (job.commit_history?.length ?? 0) > 0);
  const showReopen = job !== null && job.maintenance_active;
  const history = job?.commit_history ?? [];
  const reconciled = report?.reconciled ?? null;
  const rollback = report?.rollback ?? null;
  const generationText =
    job?.generation_before !== null &&
    job?.generation_before !== undefined &&
    job?.generation_after !== null &&
    job?.generation_after !== undefined
      ? `${job.generation_before} → ${job.generation_after}`
      : (job?.generation_before ?? job?.generation_after ?? null) !== null
        ? String(job?.generation_before ?? job?.generation_after)
        : null;
  const inventoryEntries =
    report !== null
      ? Object.entries(report.inventory).sort(([a], [b]) => a.localeCompare(b))
      : [];
  const artifactEntries =
    report !== null
      ? Object.entries(report.artifacts).sort(([a], [b]) => a.localeCompare(b))
      : [];
  const impactEntries =
    report !== null
      ? Object.entries(report.replacement_impact).sort(([a], [b]) => a.localeCompare(b))
      : [];

  return (
    <div>
      <h2 className="xi-page-title" data-testid="restores-heading" id="restores-heading">
        Staged restores
      </h2>
      <p className="xi-hint">
        Administrator restore staging and commit: an uploaded backup zip is
        validated into isolated staging only — live patients, logins, and
        jobs are never touched until an explicit commit. A succeeded staging
        reports backup date, schema, content, checksums, replacement impact,
        and the key-reentry consequence, plus the immutable confirmation
        digest the commit step below requires exactly.
      </p>

      {job?.maintenance_active === true && (
        <div
          className="xi-warning-panel"
          role="status"
          data-testid="restores-maintenance-banner"
          id="restores-maintenance-banner"
        >
          <strong>Maintenance is active.</strong> Writes are held (503
          maintenance) except sign-in and restore routes; reads stay
          available for verification. Verify health, then reopen below —
          maintenance clears only after verified health.
        </div>
      )}

      <section className="xi-card" aria-labelledby="restores-upload-heading">
        <h3 className="xi-section-title" id="restores-upload-heading">
          Upload backup zip to staging
        </h3>
        <div className="xi-row-actions xi-no-print">
          <label className="xi-hint" htmlFor="restores-upload">
            Backup archive (.zip)
          </label>
          <input
            type="file"
            accept=".zip,application/zip,application/octet-stream"
            data-testid="restores-upload"
            id="restores-upload"
            disabled={busy}
            onChange={(event) => {
              const picked = event.target.files?.[0] ?? null;
              setFile(picked);
              setError(null);
              if (picked !== null) {
                setStatus(`Selected ${picked.name} (${picked.size} bytes).`);
              }
            }}
          />
          <button
            className="xi-btn xi-btn-primary"
            type="button"
            disabled={busy || file === null}
            onClick={() => void upload()}
          >
            {uploading ? "Validating…" : "Validate in staging"}
          </button>
          {job !== null && (
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={busy}
              onClick={() => void refresh()}
            >
              {refreshing ? "Re-checking…" : "Re-check status"}
            </button>
          )}
        </div>
        {status !== null && (
          <p
            className="xi-status"
            role="status"
            data-testid="restores-status"
            id="restores-status"
          >
            {status}
          </p>
        )}
        {error !== null && (
          <p
            className="xi-form-error"
            role="alert"
            data-testid="restores-error"
            id="restores-error"
          >
            {error}
          </p>
        )}
        {job !== null && (
          <dl className="xi-hint" style={{ marginBottom: 0 }}>
            <dt style={{ display: "inline", fontWeight: 600 }}>Restore id: </dt>
            <dd style={{ display: "inline", margin: 0 }}>{job.id}</dd>
            {" · "}
            <dt style={{ display: "inline", fontWeight: 600 }}>Status: </dt>
            <dd style={{ display: "inline", margin: 0 }}>{job.status}</dd>
            {job.expired && (
              <>
                {" · "}
                <dd style={{ display: "inline", margin: 0 }}>
                  Expired or superseded — confirmation stale.
                </dd>
              </>
            )}
          </dl>
        )}
      </section>

      {job?.confirmation_digest && (
        <section
          className="xi-card"
          aria-labelledby="restores-digest-heading"
          data-testid="restores-digest"
          id="restores-digest"
        >
          <h3 className="xi-section-title" id="restores-digest-heading">
            Confirmation digest
          </h3>
          <p className="xi-hint">
            Immutable staging digest — the commit step below must present
            this same value before expiry.
            {job.expired
              ? " This digest has expired or been superseded and must not be committed."
              : ""}
          </p>
          <p className="xi-hint" style={{ marginBottom: 8 }}>
            <code>{job.confirmation_digest}</code>
          </p>
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            Staging alone never replaces live data — replacement happens
            only through the explicit commit step below, which quiesces
            writes behind maintenance first.
          </p>
        </section>
      )}

      {showCommit && job !== null && (
        <section
          className="xi-card"
          aria-labelledby="restores-commit-heading"
          data-testid="restores-commit-section"
          id="restores-commit-section"
        >
          <h3 className="xi-section-title" id="restores-commit-heading">
            Commit staged restore
          </h3>
          <p className="xi-hint">
            Replacement, never a merge: committing quiesces writes and
            workers behind maintenance, fences the deployment generation,
            takes a pre-restore backup, swaps the validated database,
            restarts processes, revokes every session, reconciles pending
            jobs, audits recovery, and reopens only after verified health.
          </p>
          <p className="xi-hint">
            Staged digest: <code>{stagedDigest}</code>
          </p>
          <div className="xi-field">
            <label className="xi-label" htmlFor="restores-commit-digest-input">
              Confirmation digest — type exactly to enable Commit
            </label>
            <input
              className="xi-input"
              type="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="64-character hex digest"
              data-testid="restores-commit-digest-input"
              id="restores-commit-digest-input"
              disabled={busy}
              value={confirmInput}
              aria-invalid={confirmInput !== "" && !digestValid}
              onChange={(event) => {
                setConfirmInput(event.target.value);
              }}
            />
            {confirmInput !== "" && !digestValid && (
              <p className="xi-field-error">
                Does not match the staged digest.
              </p>
            )}
          </div>
          <div className="xi-row-actions xi-no-print">
            <button
              className="xi-btn xi-btn-primary"
              type="button"
              data-testid="restores-commit-submit"
              id="restores-commit-submit"
              disabled={!canCommit}
              onClick={() => void commit()}
            >
              {committing ? "Committing…" : commitPolling ? `Committing… (${pollsUsed}/${MAX_POLLS})` : "Commit restore"}
            </button>
          </div>
          {commitBlockedReason !== null && (
            <p className="xi-hint" style={{ marginBottom: 0 }}>
              {commitBlockedReason}
            </p>
          )}
        </section>
      )}

      {showProgress && job !== null && (
        <section
          className="xi-card"
          aria-labelledby="restores-commit-progress-heading"
          data-testid="restores-commit-progress"
          id="restores-commit-progress"
        >
          <h3 className="xi-section-title" id="restores-commit-progress-heading">
            Commit progress
          </h3>
          <p
            className="xi-status"
            role="status"
            data-testid="restores-commit-status"
            id="restores-commit-status"
          >
            Commit {commitState}
            {job.commit_phase ? ` — phase ${job.commit_phase}` : ""}
            {job.maintenance_active ? " — maintenance active" : " — maintenance clear"}.
          </p>
          {(commitState === "failed" || commitState === "rolled_back") && (
            <p
              className="xi-form-error"
              role="alert"
              data-testid="restores-commit-error"
              id="restores-commit-error"
            >
              {job.commit_error_code ? `${job.commit_error_code}: ` : ""}
              {job.commit_error_message ?? "Restore commit failed."} Prior
              database restored; maintenance held until verified healthy.
            </p>
          )}
          <dl className="xi-hint" style={{ marginBottom: 8 }}>
            <dt style={{ display: "inline", fontWeight: 600 }}>Pre-restore backup: </dt>
            <dd style={{ display: "inline", margin: 0 }}>
              {job.pre_restore_backup_id ?? "none yet"}
            </dd>
            {generationText !== null && (
              <>
                {" · "}
                <dt style={{ display: "inline", fontWeight: 600 }}>Generation: </dt>
                <dd style={{ display: "inline", margin: 0 }}>{generationText}</dd>
              </>
            )}
            {report?.committed_at && (
              <>
                {" · "}
                <dt style={{ display: "inline", fontWeight: 600 }}>Committed: </dt>
                <dd style={{ display: "inline", margin: 0 }}>{report.committed_at}</dd>
              </>
            )}
          </dl>
          {commitState === "succeeded" && (
            <div className="xi-hint" style={{ marginBottom: 8 }}>
              <p style={{ margin: "0 0 4px" }}>
                All sessions revoked — sign in again. Old provider results
                fail DEPLOYMENT_FENCED after the generation bump
                {generationText !== null ? ` (${generationText})` : ""}.
              </p>
              <p style={{ margin: 0 }}>
                {report?.sessions_revoked !== null &&
                report?.sessions_revoked !== undefined
                  ? `${report.sessions_revoked} session(s) revoked. `
                  : ""}
                {reconciled !== null
                  ? `Pending jobs reconciled: ${reconciled.requeued} requeued, ${reconciled.cancelled} cancelled, ${reconciled.preserved_terminal} terminal preserved. `
                  : ""}
                Key re-entry: {report?.key_consequence ?? "see the validation report above."}
              </p>
            </div>
          )}
          {(commitState === "failed" || commitState === "rolled_back") && rollback !== null && (
            <p className="xi-hint" style={{ marginBottom: 8 }}>
              Rollback {rollback.restored ? "restored the prior database" : "found live unswitched"}
              {rollback.at ? ` at ${rollback.at}` : ""}: {rollback.reason}
            </p>
          )}
          {history.length > 0 ? (
            <>
              <h4 className="xi-section-title" style={{ marginTop: 12 }}>
                Phase history
              </h4>
              <ul
                className="xi-hint"
                style={{ marginBottom: 0 }}
                data-testid="restores-commit-history"
                id="restores-commit-history"
              >
                {history.map((entry, index) => (
                  <li key={`${entry.phase}-${entry.at ?? index}-${index}`}>
                    <code>{entry.phase}</code> — {entry.ok ? "ok" : "failed"}
                    {entry.at ? ` at ${entry.at}` : ""}
                    {entry.detail ? `: ${entry.detail}` : ""}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p
              className="xi-hint"
              style={{ marginBottom: 0 }}
              data-testid="restores-commit-history"
              id="restores-commit-history"
            >
              No commit phases recorded yet.
            </p>
          )}
        </section>
      )}

      {showReopen && job !== null && (
        <section
          className="xi-card"
          aria-labelledby="restores-reopen-heading"
          data-testid="restores-reopen-section"
          id="restores-reopen-section"
        >
          <h3 className="xi-section-title" id="restores-reopen-heading">
            Verify and reopen
          </h3>
          <p className="xi-hint">
            Maintenance is held after the rollback. Reopening first verifies
            health (sign-in, reads, saved-CPT replay) and clears maintenance
            only on success — otherwise it stays held so the deployment can
            be fixed first.
          </p>
          <div className="xi-row-actions xi-no-print">
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              data-testid="restores-reopen-submit"
              id="restores-reopen-submit"
              disabled={busy}
              onClick={() => void reopen()}
            >
              {reopening ? "Verifying…" : "Verify health and reopen"}
            </button>
          </div>
          <p
            className="xi-hint"
            style={{ marginBottom: 0 }}
            role="status"
            data-testid="restores-reopen-status"
            id="restores-reopen-status"
          >
            {job.maintenance_active
              ? "Maintenance is active — writes held."
              : "Maintenance is clear."}
          </p>
        </section>
      )}

      {report !== null && (
        <section
          className="xi-card"
          aria-labelledby="restores-report-heading"
          data-testid="restores-report"
          id="restores-report"
        >
          <h3 className="xi-section-title" id="restores-report-heading">
            Validation report
          </h3>
          <p className="xi-hint">
            Backup <code>{report.backup_id}</code>, created{" "}
            {report.backup_created_at}; manifest{" "}
            <code>{report.manifest_schema}</code>, app schema{" "}
            <code>{report.app_schema_version}</code>; archive{" "}
            <span title={report.archive_sha256}>
              {shortHash(report.archive_sha256)}
            </span>{" "}
            ({report.archive_bytes} bytes).
          </p>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Staged content
          </h4>
          <table className="xi-table" aria-label="Staged table inventory">
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col">Staged rows</th>
                <th scope="col">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {inventoryEntries.map(([name, info]) => (
                <tr key={name}>
                  <td>
                    {name}
                    {info.missing === true ? " (unreadable — exported empty)" : ""}
                  </td>
                  <td>{info.rows}</td>
                  <td title={info.sha256}>{shortHash(info.sha256)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Archive checksums
          </h4>
          <ul className="xi-hint" style={{ marginBottom: 8 }}>
            {artifactEntries.map(([name, hash]) => (
              <li key={name}>
                <code>{name}</code> — <span title={hash}>{shortHash(hash)}</span>
              </li>
            ))}
          </ul>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Replacement impact (live vs staged — comparison only, no merge)
          </h4>
          <table className="xi-table" aria-label="Replacement impact">
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col">Live rows</th>
                <th scope="col">Staged rows</th>
              </tr>
            </thead>
            <tbody>
              {impactEntries.map(([name, counts]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{counts.live}</td>
                  <td>{counts.staged}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="xi-hint" style={{ marginTop: 8 }}>
            Deployment generation: live{" "}
            {report.deployment_generation.live ?? "unknown"}, staged{" "}
            {report.deployment_generation.staged ?? "unknown"}.
          </p>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            Key-reentry consequence
          </h4>
          <p className="xi-hint" style={{ marginBottom: 8 }}>
            {report.key_consequence}
          </p>
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            {report.commit_note}
          </p>
        </section>
      )}

      <section
        className="xi-card"
        aria-labelledby="restores-recovery-heading"
        data-testid="restores-recovery-notes"
        id="restores-recovery-notes"
      >
        <h3 className="xi-section-title" id="restores-recovery-heading">
          Operator recovery procedure
        </h3>
        <ol className="xi-hint" style={{ marginBottom: 0, paddingLeft: 20 }}>
          <li>
            Back up before switching: create a manual backup on the Backups
            page before committing. The commit also takes an automatic
            pre-restore backup — its id in commit progress identifies the
            rollback source.
          </li>
          <li>
            Commit behind maintenance: writes and workers quiesce (503
            maintenance except sign-in and restore routes; reads stay
            available), and the deployment generation is fenced so old
            workers cannot commit during the switch.
          </li>
          <li>
            Switch and restart: the validated tables replace live data
            (never a merge) and connections recycle — no mixed old/new
            traffic is possible.
          </li>
          <li>
            Sign in again and re-enter keys: every session is revoked, so
            sign in again after a commit. Encrypted provider credentials
            travel without the deployment key — migrating without the
            original key requires key re-entry (see the report above).
          </li>
          <li>
            Verify, then reopen: health checks sign-in, reads, and
            saved-CPT replay. Reopening clears maintenance only on
            success. Old provider results fail DEPLOYMENT_FENCED after
            the generation bump.
          </li>
          <li>
            Rollback path: on failure the prior database is restored and
            maintenance stays held until verified healthy — fix the
            deployment, then verify and reopen. The failed commit stays
            audited with its pre-restore backup id.
          </li>
        </ol>
      </section>
    </div>
  );
}
