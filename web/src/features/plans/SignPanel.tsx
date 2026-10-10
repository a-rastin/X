/* Explicit encounter sign-off (S50 §2, plan.md §9.2; FR-15, FR-57, NFR-03).
 *
 * Rendered by ProposalReviewPanel only when the run-level gates already
 * pass (no proposal-sign-blocked reason). The button does flush-then-sign:
 * S07 autosave flush first, then fresh reads of the encounter revision, the
 * separately revisioned secondary plan, and every ready run's GET review —
 * and submits the exact acceptance/encounter/plan/review references the
 * server rechecks inside its locked transaction (client flags never grant;
 * the server stays authoritative).
 *
 * §2: pending/failed/stale calculations block signing here without losing
 * plan text — every denial returns before the POST and this panel never
 * writes the plan (SecondaryPlanPanel owns it independently). Empty plan
 * text blocks with the reason while the text stays in place. Double-click
 * is safe: the button stays disabled while pending and each explicit click
 * carries a fresh Idempotency-Key (replay returns the one signature).
 *
 * After signing the draft slot releases and draft routes 404 by server
 * construction; success shows the frozen snapshot identity plus the shared
 * chart link (read-only signed view + addenda live there, S50 §§3-4).
 *
 * Accessibility + theming: native button, status role=status, errors
 * role=alert with focus, semantic tokens only (both themes), state always
 * text + glyph (never color alone).
 *
 * E2E selector contract (dev-test owns e2e/signing.spec.ts):
 * - panel `data-testid="sign-panel"`
 * - sign button `data-testid="sign-button"`
 * - sign status `data-testid="sign-status"` (role=status/role=alert)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../identity/api";
import { chartHash } from "../../app/router";
import { getEncounter } from "../encounters/api";
import type { useAutosave } from "../encounters/useAutosave";
import {
  acceptBlockedReason,
  getQuestionReview,
} from "../reasoning/api";
import {
  getSecondaryPlan,
  signEncounter,
  type SignAcceptanceRef,
} from "./api";

type AutosaveApi = ReturnType<typeof useAutosave>;

export function SignPanel({
  encounterId,
  patientId,
  autosave,
  batchId,
  readyRunIds,
  getBlockReason,
  onSessionExpired,
}: {
  encounterId: string;
  patientId: string | null;
  autosave: AutosaveApi;
  batchId: string;
  /** Exactly the ready question-run ids (skipped runs carry no acceptance). */
  readyRunIds: string[];
  /** Current run-level block reason (fresh closure from the parent batch). */
  getBlockReason: () => string | null;
  onSessionExpired: () => void;
}) {
  const [signing, setSigning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [signed, setSigned] = useState<{
    snapshotId: string;
    hash: string;
    signer: string;
    signedAt: string;
    revision: number;
  } | null>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (error !== null) {
      errorRef.current?.focus();
    }
  }, [error]);

  // ponytail: one linear flush-then-verify-then-POST — no intermediate
  // state machine beyond pending/success/error.
  const onSign = useCallback(async () => {
    if (signing) {
      return;
    }
    setError(null);
    setSigning(true);
    try {
      // Page-transition flush: persist pending draft edits before the sign
      // fence reads the fresh encounter revision.
      await autosave.flush().catch(() => {
        /* fence below reads the fresh revision regardless */
      });
      const meta = await getEncounter(encounterId);
      const plan = await getSecondaryPlan(encounterId);
      if (plan.text.trim() === "") {
        throw new Error(
          "Signing is blocked — no secondary plan is saved yet. Save the physician plan above first; nothing was signed and the plan text is preserved.",
        );
      }
      const blocked = getBlockReason();
      if (blocked !== null) {
        throw new Error(`${blocked} Nothing was signed and the plan text is preserved.`);
      }
      // Exact per-question references from fresh persisted reviews (the
      // `_accept_body(review)` mirror): only currently accepted, fresh,
      // successfully solved results qualify; anything else blocks with the
      // question named and no POST.
      const acceptances: SignAcceptanceRef[] = [];
      for (const runId of readyRunIds) {
        const review = await getQuestionReview(runId);
        const key = review.question_run.question_key;
        const reason = acceptBlockedReason(review);
        if (reason !== null) {
          throw new Error(
            `Signing is blocked — ${key}: ${reason} Nothing was signed and the plan text is preserved.`,
          );
        }
        if (!review.is_accepted || review.acceptance === null) {
          throw new Error(
            `Signing is blocked — ${key} has no current acceptance yet. Accept its exact current result in the CPT review above first; nothing was signed and the plan text is preserved.`,
          );
        }
        acceptances.push({
          question_run_id: review.question_run.id,
          acceptance_id: review.acceptance.id,
          expected_review_revision: review.review_revision,
        });
      }
      const result = await signEncounter(encounterId, meta.revision, {
        expected_encounter_revision: meta.revision,
        expected_plan_revision: plan.revision,
        batch_id: batchId,
        acceptances,
      });
      setSigned({
        snapshotId: result.snapshot.id,
        hash: result.snapshot.snapshot_hash,
        signer: result.snapshot.signer_username,
        signedAt: result.snapshot.signed_at,
        revision: result.revision,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionExpired();
        return;
      }
      setError(
        err instanceof ApiError
          ? `${err.message} (${err.code}). Nothing was signed and the plan text is preserved.`
          : err instanceof Error
            ? err.message
            : "Sign-off failed. Check the connection and try again — nothing was signed.",
      );
    } finally {
      setSigning(false);
    }
  }, [signing, autosave, encounterId, batchId, readyRunIds, getBlockReason, onSessionExpired]);

  if (signed !== null) {
    return (
      <div className="xi-notice" role="status" data-testid="sign-panel" id="sign-panel">
        <p style={{ marginTop: 0 }} data-testid="sign-status" id="sign-status">
          <strong>● Signed ✓</strong> by {signed.signer} at {signed.signedAt} (encounter revision{" "}
          {signed.revision}). Snapshot <code className="xi-mono">{signed.snapshotId}</code> ·
          hash <code className="xi-mono">{signed.hash}</code>. The draft slot is released; the
          frozen record below is read-only.
        </p>
        <p className="xi-hint" style={{ marginBottom: 0 }}>
          This wizard step is now read-only history.{" "}
          {patientId !== null && (
            <a href={chartHash(patientId)}>View the signed chart (read-only + addenda)</a>
          )}
        </p>
      </div>
    );
  }

  return (
    <div className="xi-notice" role="status" data-testid="sign-panel" id="sign-panel">
      <p style={{ marginTop: 0 }} data-testid="sign-status" id="sign-status">
        Proposal complete and current — explicit sign-off flushes pending saves, verifies every
        accepted per-question result and the saved secondary plan, then freezes the record. Sign
        once; double-click is safe (the button disables while pending).
      </p>
      {error !== null && (
        <p className="xi-form-error" role="alert" tabIndex={-1} ref={errorRef}>
          {error}
        </p>
      )}
      <div className="xi-row-actions">
        <button
          className="xi-btn xi-btn-primary"
          type="button"
          data-testid="sign-button"
          id="sign-button"
          disabled={signing}
          onClick={() => void onSign()}
        >
          {signing ? "Signing…" : "Sign encounter"}
        </button>
      </div>
      <p className="xi-hint" style={{ marginBottom: 0 }}>
        Covers exactly the {readyRunIds.length} ready question{readyRunIds.length === 1 ? "" : "s"}{" "}
        with their current acceptances. Pending, failed, or stale results block signing without
        losing plan text.
      </p>
    </div>
  );
}
