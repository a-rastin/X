import { useFollowUpText } from "../follow-up/text";
import {
  FollowUpProposalView,
  followUpProposalText,
} from "../follow-up/FollowUpPlanPage";
import * as followUpApi from "../follow-up/api";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { useLanguage } from "../../shared/i18n";
import { ApiError, newIdempotencyKey } from "../identity/api";
import {
  getSecondaryPlan,
  saveSecondaryPlan,
  type SecondaryPlanPayload,
} from "../plans/api";
import {
  command as newCaseCommand,
  latestProposal as latestNewCaseProposal,
  type Proposal,
} from "./api";
import { useNewCaseText } from "./NewCaseSections";

export function newCaseProposalText(
  proposal: Proposal,
  n: (s: string) => string,
): string {
  return (
    Object.entries(proposal.payload.sections)
      .filter(
        ([id]) =>
          !["antipsychotic_candidates", "drug_interactions"].includes(id),
      )
      .map(([id, s]) => {
        const value =
          typeof s.value === "boolean"
            ? n(s.value ? "yes" : "no")
            : Array.isArray(s.value)
              ? s.value.join(", ")
              : n(String(s.value));
        return (
          n(id) +
          ": " +
          value +
          (s.reasons?.length ? " (" + s.reasons.map(n).join(", ") + ")" : "") +
          (s.actions?.length ? "\n" + s.actions.map(n).join("\n") : "") +
          (s.must_not_delay_treatment ? "\n" + n("mustNotDelay") : "")
        );
      })
      .join("\n\n") +
    "\n\n" +
    n("antipsychotic_candidates") +
    ":\n" +
    (proposal.payload.ranking.status === "model_unavailable"
      ? n("model_unavailable")
      : proposal.payload.ranking.candidates
          .map((c) => c.name + " " + (c.probability * 100).toFixed(2) + "%")
          .join("\n"))
  );
}
export function ProposalView({ proposal }: { proposal: Proposal }) {
  const n = useNewCaseText();
  const report = proposal.payload.candidate_interactions;
  const names = new Map(
    report?.resolved_medications.map((m) => [
      m.catalog_drug_id,
      m.canonical_name,
    ]) ?? [],
  );
  return (
    <section className="xi-card nc-plan">
      <h3>{n("original")}</h3>
      <pre>{newCaseProposalText(proposal, n)}</pre>
      <p>{n("rankingShare")}</p>
      {proposal.payload.ranking.status !== "model_unavailable" &&
        proposal.payload.ranking.candidates.length === 0 && (
          <p>{n("emptyCandidates")}</p>
        )}
      <h4>{n("drug_interactions")}</h4>
      <p>{n("interactionScope")}</p>
      <details>
        <summary>{n("drug_interactions")}</summary>
        <p>{n("ratingHelp")}</p>
      </details>
      {report?.pairs.map((pair) => (
        <article key={pair.drug_a + pair.drug_b}>
          <h5>
            <bdi>
              {names.get(pair.drug_a)} — {names.get(pair.drug_b)}
            </bdi>
            : {pair.risk_rating ?? n("coverage_unavailable")}
          </h5>
          {pair.evidence.map((e, i) => (
            <p dir="ltr" style={{ whiteSpace: "pre-wrap" }} key={i}>
              {e.raw_text.replace(/\\n/g, "\n")}
            </p>
          ))}
        </article>
      ))}
    </section>
  );
}
export function NewCasePlanPage({
  encounterId,
  proposal,
  onProposal,
  flushInputs,
  flushRef,
  dirtyRef,
  onSigned,
  onError,
  workflow = "new-case",
}: {
  encounterId: string;
  workflow?: "new-case" | "follow-up";
  proposal: Proposal;
  onProposal: (p: Proposal) => void;
  flushInputs: () => Promise<{ ok: true; revision: number } | { ok: false }>;
  flushRef: RefObject<(() => Promise<void>) | null>;
  dirtyRef: RefObject<boolean>;
  onSigned: () => void;
  onError: (e: unknown) => void;
}) {
  const legacyText = useNewCaseText();
  const followUpText = useFollowUpText();
  const n = workflow === "follow-up" ? followUpText : legacyText;
  const command =
    workflow === "follow-up" ? followUpApi.command : newCaseCommand;
  const latestProposal =
    workflow === "follow-up"
      ? followUpApi.latestProposal
      : latestNewCaseProposal;
  const proposalText =
    workflow === "follow-up" ? followUpProposalText : newCaseProposalText;
  const { language } = useLanguage();
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [server, setServer] = useState<SecondaryPlanPayload | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const textRef = useRef("");
  const revision = useRef(1);
  const saved = useRef("");
  const conflict = useRef(false);
  const pending = useRef<Promise<void> | null>(null);
  const attempt = useRef<{
    key: string;
    text: string;
    revision: number;
  } | null>(null);
  const storageKey = "xi-" + workflow + "-plan:" + encounterId;
  const setDraft = useCallback(
    (value: string) => {
      textRef.current = value;
      setText(value);
      dirtyRef.current = value !== saved.current;
      sessionStorage.setItem(
        storageKey,
        JSON.stringify({ text: value, revision: revision.current }),
      );
    },
    [dirtyRef, storageKey],
  );
  const save = useCallback(
    async function flushPlan(): Promise<void> {
      if (pending.current) {
        await pending.current;
        return flushPlan();
      }
      if (conflict.current) throw new Error("PLAN_CONFLICT");
      while (textRef.current !== saved.current) {
        const previous = attempt.current;
        const item =
          previous &&
          previous.text === textRef.current &&
          previous.revision === revision.current
            ? previous
            : {
                key: newIdempotencyKey(),
                text: textRef.current,
                revision: revision.current,
              };
        attempt.current = item;
        const run = (async () => {
          try {
            const p = await saveSecondaryPlan(
              encounterId,
              item.text,
              item.revision,
              { idempotencyKey: item.key },
            );
            revision.current = p.revision;
            saved.current = item.text;
            attempt.current = null;
            dirtyRef.current = textRef.current !== saved.current;
            if (!dirtyRef.current) sessionStorage.removeItem(storageKey);
            setStatus("saved");
          } catch (e) {
            if (e instanceof ApiError && e.status === 412) {
              conflict.current = true;
              setServer(await getSecondaryPlan(encounterId));
            }
            throw e;
          }
        })();
        pending.current = run;
        try {
          await run;
        } finally {
          pending.current = null;
        }
      }
    },
    [encounterId, dirtyRef, storageKey],
  );
  useEffect(() => {
    let cancelled = false;
    void getSecondaryPlan(encounterId)
      .then((p) => {
        if (cancelled) return;
        revision.current = p.revision;
        saved.current = p.text;
        let draft = p.text;
        const kept = sessionStorage.getItem(storageKey);
        if (kept) {
          try {
            const v = JSON.parse(kept) as { text: string; revision: number };
            draft = v.text;
            if (v.revision !== p.revision && draft !== p.text) {
              conflict.current = true;
              setServer(p);
            }
          } catch {
            sessionStorage.removeItem(storageKey);
          }
        } else if (p.revision === 1 && p.text === "")
          draft = proposalText(proposal, n);
        setDraft(draft);
        setLoaded(true);
      })
      .catch(onError);
    return () => {
      cancelled = true;
    };
  }, [encounterId]); // Seed once; language/regeneration never replace edits.
  useEffect(() => {
    if (!loaded) return;
    const timer = window.setTimeout(() => {
      void save().catch((e) => {
        setStatus("failed");
        onError(e);
      });
    }, 500);
    return () => window.clearTimeout(timer);
  }, [text, loaded, save]);
  useEffect(() => {
    flushRef.current = async () => {
      if (!loaded) throw new Error("PLAN_LOADING");
      await save();
    };
    return () => {
      flushRef.current = null;
    };
  }, [loaded, save, flushRef]);
  useEffect(() => {
    const refresh = () => {
      void latestProposal(encounterId)
        .then((r) => {
          if (r.proposal?.id === proposal.id) onProposal(r.proposal);
        })
        .catch(onError);
    };
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [encounterId, proposal.id]);
  const commandAttempt = useRef<{ signature: string; key: string } | null>(
    null,
  );
  const stableKey = (signature: string) => {
    if (commandAttempt.current?.signature !== signature)
      commandAttempt.current = { signature, key: newIdempotencyKey() };
    return commandAttempt.current.key;
  };
  const regenerate = async () => {
    setBusy(true);
    try {
      await save();
      const input = await flushInputs();
      if (!input.ok) return;
      const r = await command<{ proposal: Proposal }>(
        encounterId,
        "proposals",
        input.revision,
        {},
        stableKey(`generate:${input.revision}`),
      );
      commandAttempt.current = null;
      onProposal(r.proposal);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  const sign = async () => {
    setBusy(true);
    try {
      await save();
      const input = await flushInputs();
      if (!input.ok) return;
      const latest = await latestProposal(encounterId);
      if (!latest.proposal || latest.proposal.stale) {
        onProposal({ ...proposal, stale: true });
        return;
      }
      await command(
        encounterId,
        "sign",
        input.revision,
        {
          proposal_id: proposal.id,
          expected_plan_revision: revision.current,
        },
        stableKey(`sign:${input.revision}:${proposal.id}:${revision.current}`),
      );
      commandAttempt.current = null;
      sessionStorage.removeItem(storageKey);
      dirtyRef.current = false;
      onSigned();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="nc-plan">
      {workflow === "follow-up" ? (
        <FollowUpProposalView proposal={proposal} />
      ) : (
        <ProposalView proposal={proposal} />
      )}
      {proposal.stale && <p role="alert">{n("stale")}</p>}
      <div className="nc-actions">
        <button
          className="xi-btn xi-btn-secondary"
          disabled={busy}
          onClick={() => void regenerate()}
        >
          {n("generate")}
        </button>
        <button
          className="xi-btn xi-btn-secondary"
          onClick={() =>
            void navigator.clipboard.writeText(proposalText(proposal, n))
          }
        >
          {n("copy")}
        </button>
      </div>
      <section className="xi-card">
        <h3>{n("finalPlan")}</h3>
        <label htmlFor="nc-final-plan">{n("finalPlan")}</label>
        <textarea
          id="nc-final-plan"
          className="xi-input"
          lang={language}
          maxLength={10000}
          value={text}
          disabled={!loaded || busy}
          onChange={(e) => setDraft(e.target.value)}
        />
        <p role="status">{status && n(status)}</p>
        {server && (
          <div role="alert">
            <p>{n("planConflict")}</p>
            <pre>{server.text}</pre>
            <button
              onClick={() => {
                revision.current = server.revision;
                saved.current = server.text;
                conflict.current = false;
                setServer(null);
                setDraft(server.text);
              }}
            >
              {n("reload")}
            </button>
            <button
              onClick={() => {
                revision.current = server.revision;
                conflict.current = false;
                attempt.current = null;
                setServer(null);
                void save().catch(onError);
              }}
            >
              {n("reconcile")}
            </button>
          </div>
        )}
        <div className="nc-actions">
          <button
            className="xi-btn xi-btn-secondary"
            disabled={busy}
            onClick={() => setDraft(proposalText(proposal, n))}
          >
            {n("replace")}
          </button>
          <button
            className="xi-btn xi-btn-secondary"
            disabled={busy}
            onClick={() => void save().catch(onError)}
          >
            {n("save")}
          </button>
        </div>
        <label>
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          {n("confirmHelp")}
        </label>
        <button
          className="xi-btn xi-btn-primary"
          disabled={
            busy || !loaded || !confirmed || proposal.stale || !text.trim()
          }
          onClick={() => void sign()}
        >
          {n("confirm")}
        </button>
      </section>
    </div>
  );
}

export { newCaseProposalText as proposalText };
