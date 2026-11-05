import { useLanguage } from "../../../shared/i18n";
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
  const { t, errorText } = useLanguage();
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
        {t("stagedRestores")}</h2>
      <p className="xi-hint">
        
        {t("administratorRestoreStagingAndCommitAnUploaded")}
      </p>

      {job?.maintenance_active === true && (
        <div
          className="xi-warning-panel"
          role="status"
          data-testid="restores-maintenance-banner"
          id="restores-maintenance-banner"
        >
          <strong>{t("maintenanceIsActive")}</strong>  {t("writesAreHeld503MaintenanceExceptSign")}
        </div>
      )}

      <section className="xi-card" aria-labelledby="restores-upload-heading">
        <h3 className="xi-section-title" id="restores-upload-heading">
          
          {t("uploadBackupZipToStaging")}
        </h3>
        <div className="xi-row-actions xi-no-print">
          <label className="xi-hint" htmlFor="restores-upload">
            
            {t("backupArchiveZip")}
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
            {uploading ? t("validating") : t("validateInStaging")}
          </button>
          {job !== null && (
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={busy}
              onClick={() => void refresh()}
            >
              {refreshing ? t("reChecking") : t("reCheckStatus")}
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
            {errorText(error)}
          </p>
        )}
        {job !== null && (
          <dl className="xi-hint" style={{ marginBottom: 0 }}>
            <dt style={{ display: "inline", fontWeight: 600 }}>{t("restoreId")} </dt>
            <dd style={{ display: "inline", margin: 0 }}>{job.id}</dd>
            {" · "}
            <dt style={{ display: "inline", fontWeight: 600 }}>{t("status387")} </dt>
            <dd style={{ display: "inline", margin: 0 }}>{job.status}</dd>
            {job.expired && (
              <>
                {" · "}
                <dd style={{ display: "inline", margin: 0 }}>
                  
                  {t("expiredOrSupersededConfirmationStale")}
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
            
            {t("confirmationDigest")}
          </h3>
          <p className="xi-hint">
            
            {t("immutableStagingDigestTheCommitStepBelow")}
            {job.expired
              ? t("thisDigestHasExpiredOrBeenSuperseded")
              : ""}
          </p>
          <p className="xi-hint" style={{ marginBottom: 8 }}>
            <code>{job.confirmation_digest}</code>
          </p>
          <p className="xi-hint" style={{ marginBottom: 0 }}>
            
            {t("stagingAloneNeverReplacesLiveDataReplacement")}
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
            
            {t("commitStagedRestore")}
          </h3>
          <p className="xi-hint">
            
            {t("replacementNeverAMergeCommittingQuiescesWrites")}
          </p>
          <p className="xi-hint">
            
            {t("stagedDigest")} <code>{stagedDigest}</code>
          </p>
          <div className="xi-field">
            <label className="xi-label" htmlFor="restores-commit-digest-input">
              
              {t("confirmationDigestTypeExactlyToEnableCommit")}
            </label>
            <input
              className="xi-input"
              type="text"
              autoComplete="off"
              spellCheck={false}
              placeholder={t("confirmationHexDigest")}
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
                
                {t("doesNotMatchTheStagedDigest")}
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
              {committing ? t("committing") : commitPolling ? `Committing… (${pollsUsed}/${MAX_POLLS})` : t("commitRestore")}
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
            
            {t("commitProgress")}
          </h3>
          <p
            className="xi-status"
            role="status"
            data-testid="restores-commit-status"
            id="restores-commit-status"
          >
            
            {t("commit")} {commitState}
            {job.commit_phase ? ` — phase ${job.commit_phase}` : ""}
            {job.maintenance_active ? t("maintenanceActive") : t("maintenanceClear")}.
          </p>
          {(commitState === "failed" || commitState === "rolled_back") && (
            <p
              className="xi-form-error"
              role="alert"
              data-testid="restores-commit-error"
              id="restores-commit-error"
            >
              {job.commit_error_code ? `${job.commit_error_code}: ` : ""}
              {job.commit_error_message ?? "Restore commit failed."}  {t("priorDatabaseRestoredMaintenanceHeldUntilVerified")}
            </p>
          )}
          <dl className="xi-hint" style={{ marginBottom: 8 }}>
            <dt style={{ display: "inline", fontWeight: 600 }}>{t("preRestoreBackup")} </dt>
            <dd style={{ display: "inline", margin: 0 }}>
              {job.pre_restore_backup_id ?? "none yet"}
            </dd>
            {generationText !== null && (
              <>
                {" · "}
                <dt style={{ display: "inline", fontWeight: 600 }}>{t("generation")} </dt>
                <dd style={{ display: "inline", margin: 0 }}>{generationText}</dd>
              </>
            )}
            {report?.committed_at && (
              <>
                {" · "}
                <dt style={{ display: "inline", fontWeight: 600 }}>{t("committed")} </dt>
                <dd style={{ display: "inline", margin: 0 }}>{report.committed_at}</dd>
              </>
            )}
          </dl>
          {commitState === "succeeded" && (
            <div className="xi-hint" style={{ marginBottom: 8 }}>
              <p style={{ margin: "0 0 4px" }}>
                
                {t("allSessionsRevokedSignInAgainOld")}
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
                
                {t("keyReEntry")} {report?.key_consequence ?? "see the validation report above."}
              </p>
            </div>
          )}
          {(commitState === "failed" || commitState === "rolled_back") && rollback !== null && (
            <p className="xi-hint" style={{ marginBottom: 8 }}>
              
              {t("rollback489")} {rollback.restored ? t("restoredThePriorDatabase") : t("foundLiveUnswitched")}
              {rollback.at ? ` at ${rollback.at}` : ""}: {rollback.reason}
            </p>
          )}
          {history.length > 0 ? (
            <>
              <h4 className="xi-section-title" style={{ marginTop: 12 }}>
                
                {t("phaseHistory")}
              </h4>
              <ul
                className="xi-hint"
                style={{ marginBottom: 0 }}
                data-testid="restores-commit-history"
                id="restores-commit-history"
              >
                {history.map((entry, index) => (
                  <li key={`${entry.phase}-${entry.at ?? index}-${index}`}>
                    <code>{entry.phase}</code> — {entry.ok ? t("ok") : t("failed")}
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
              
              {t("noCommitPhasesRecordedYet")}
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
            
            {t("verifyAndReopen")}
          </h3>
          <p className="xi-hint">
            
            {t("maintenanceIsHeldAfterTheRollbackReopening")}
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
              {reopening ? t("verifying") : t("verifyHealthAndReopen")}
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
              ? t("maintenanceIsActiveWritesHeld")
              : t("maintenanceIsClear")}
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
            
            {t("validationReport")}
          </h3>
          <p className="xi-hint">
            
            {t("backup")} <code>{report.backup_id}</code>{t("created")}{" "}
            {report.backup_created_at}{t("manifest503")}{" "}
            <code>{report.manifest_schema}</code>{t("appSchema")}{" "}
            <code>{report.app_schema_version}</code>{t("archive504")}{" "}
            <span title={report.archive_sha256}>
              {shortHash(report.archive_sha256)}
            </span>{" "}
            ({report.archive_bytes}  {t("bytes")}
          </p>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            
            {t("stagedContent")}
          </h4>
          <table className="xi-table" aria-label={t("stagedTableInventory")}>
            <thead>
              <tr>
                <th scope="col">{t("table")}</th>
                <th scope="col">{t("stagedRows")}</th>
                <th scope="col">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {inventoryEntries.map(([name, info]) => (
                <tr key={name}>
                  <td>
                    {name}
                    {info.missing === true ? t("unreadableExportedEmpty") : ""}
                  </td>
                  <td>{info.rows}</td>
                  <td title={info.sha256}>{shortHash(info.sha256)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            
            {t("archiveChecksums")}
          </h4>
          <ul className="xi-hint" style={{ marginBottom: 8 }}>
            {artifactEntries.map(([name, hash]) => (
              <li key={name}>
                <code>{name}</code> — <span title={hash}>{shortHash(hash)}</span>
              </li>
            ))}
          </ul>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            
            {t("replacementImpactLiveVsStagedComparisonOnly")}
          </h4>
          <table className="xi-table" aria-label={t("replacementImpact")}>
            <thead>
              <tr>
                <th scope="col">{t("table")}</th>
                <th scope="col">{t("liveRows")}</th>
                <th scope="col">{t("stagedRows")}</th>
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
            
            {t("deploymentGenerationLive")}{" "}
            {report.deployment_generation.live ?? "unknown"}{t("staged")}{" "}
            {report.deployment_generation.staged ?? "unknown"}.
          </p>
          <h4 className="xi-section-title" style={{ marginTop: 16 }}>
            
            {t("keyReentryConsequence")}
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
          
          {t("operatorRecoveryProcedure")}
        </h3>
        <ol className="xi-hint" style={{ marginBottom: 0, paddingLeft: 20 }}>
          <li>
            
            {t("backUpBeforeSwitchingCreateAManual")}
          </li>
          <li>
            
            {t("commitBehindMaintenanceWritesAndWorkersQuiesce")}
          </li>
          <li>
            
            {t("switchAndRestartTheValidatedTablesReplace")}
          </li>
          <li>
            
            {t("signInAgainAndReEnterKeys")}
          </li>
          <li>
            
            {t("verifyThenReopenHealthChecksSignIn")}
          </li>
          <li>
            
            {t("rollbackPathOnFailureThePriorDatabase")}
          </li>
        </ol>
      </section>
    </div>
  );
}
