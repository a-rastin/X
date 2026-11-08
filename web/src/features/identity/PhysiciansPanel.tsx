import { useLanguage } from "../../shared/i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  conflictHint,
  createPhysician,
  deactivatePhysician,
  getOpenDrafts,
  getPhysician,
  listPhysicians,
  patchPhysician,
  reactivatePhysician,
  type DraftAction,
  type OpenDraftPreview,
  type SafePhysician,
} from "./api";
import { useAuth } from "./auth";

type LoadState =
  | { kind: "loading" }
  | { kind: "refreshing"; items: SafePhysician[]; total: number; additional: boolean }
  | { kind: "error"; message: string }
  | { kind: "ready"; items: SafePhysician[]; total: number; additional: boolean };

function CreateForm({ onCreated }: { onCreated: (physician: SafePhysician) => void }) {
  const { t, errorText } = useLanguage();
  const { sessionExpired } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setErrors({});
    setFormError(null);
    setCreated(false);
    setBusy(true);
    try {
      const physician = await createPhysician(username, password);
      setUsername("");
      setPassword("");
      setCreated(true);
      onCreated(physician);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError) {
        setErrors(err.fieldErrors);
        setFormError(err.message);
        return;
      }
      setFormError("Physician creation failed. Check the connection and retry.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const usernameError = errors["username"]?.join(" ") ?? null;
  const passwordError = errors["password"]?.join(" ") ?? null;

  return (
    <section className="xi-card" aria-labelledby="create-physician-heading">
      <h3 className="xi-section-title" id="create-physician-heading">
        {t("createPhysician")}
      </h3>
      <form aria-label={t("createPhysician")} onSubmit={handleSubmit} noValidate>
        <div className="xi-field">
          <label className="xi-label" htmlFor="create-username">
            {t("username")}</label>
          <input
            className="xi-input"
            id="create-username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            aria-invalid={usernameError !== null}
            aria-describedby={usernameError ? "create-username-error" : undefined}
          />
          {usernameError !== null && (
            <p className="xi-field-error" id="create-username-error">
              {errorText(usernameError)}
            </p>
          )}
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="create-password">
            {t("password")}</label>
          <input
            className="xi-input"
            id="create-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={passwordError !== null}
            aria-describedby={passwordError ? "create-password-error" : undefined}
          />
          {passwordError !== null && (
            <p className="xi-field-error" id="create-password-error">
              {errorText(passwordError)}
            </p>
          )}
        </div>
        {formError !== null && (
          <p className="xi-form-error" role="alert">
            {errorText(formError)}
          </p>
        )}
        {created && (
          <p className="xi-status" role="status">
            {t("physicianCreated")}
          </p>
        )}
        <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
          {busy ? t("creating") : t("createPhysician")}
        </button>
      </form>
    </section>
  );
}

function EditForm({
  physician,
  onSaved,
  onCancel,
  onStale,
}: {
  physician: SafePhysician;
  onSaved: () => void;
  onCancel: () => void;
  onStale: () => void;
}) {
  const { t, errorText } = useLanguage();
  const { sessionExpired } = useAuth();
  const [username, setUsername] = useState(physician.username);
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setErrors({});
    setFormError(null);
    setBusy(true);
    try {
      const patch: { username?: string; password?: string } = {};
      if (username !== physician.username) {
        patch.username = username;
      }
      if (password !== "") {
        patch.password = password;
      }
      if (Object.keys(patch).length === 0) {
        // Match the server's empty-patch contract without a round trip.
        setFormError("Nothing to update.");
        return;
      }
      // Optimistic revision from the listed row; the server answers 412
      // when another admin changed the account first. The form stays open
      // with the message while the row refreshes underneath.
      await patchPhysician(physician.id, patch, physician.revision);
      onSaved();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (err instanceof ApiError && err.status === 412) {
        onStale();
        setFormError(
          "The account changed. Reload and reconcile your edits.",
        );
        return;
      }
      if (err instanceof ApiError) {
        setErrors(err.fieldErrors);
        setFormError(err.message);
        return;
      }
      setFormError("Physician update failed. Check the connection and retry.");
    } finally {
      setBusy(false);
    }
  }

  const usernameError = errors["username"]?.join(" ") ?? null;
  const passwordError = errors["password"]?.join(" ") ?? null;

  return (
    <form aria-label={t("editPhysician")} onSubmit={handleSubmit} noValidate>
      <div className="xi-form-row">
        <div className="xi-field">
          <label className="xi-label" htmlFor={`edit-username-${physician.id}`}>
            {t("username")}</label>
          <input
            className="xi-input"
            id={`edit-username-${physician.id}`}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            aria-invalid={usernameError !== null}
            aria-describedby={usernameError ? `edit-username-error-${physician.id}` : undefined}
          />
          {usernameError !== null && (
            <p className="xi-field-error" id={`edit-username-error-${physician.id}`}>
              {errorText(usernameError)}
            </p>
          )}
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor={`edit-password-${physician.id}`}>
            {t("optionalPassword")}
          </label>
          <input
            className="xi-input"
            id={`edit-password-${physician.id}`}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={passwordError !== null}
            aria-describedby={passwordError ? `edit-password-error-${physician.id}` : undefined}
          />
          {passwordError !== null && (
            <p className="xi-field-error" id={`edit-password-error-${physician.id}`}>
              {errorText(passwordError)}
            </p>
          )}
        </div>
      </div>
      {formError !== null && (
        <p className="xi-form-error" role="alert">
          {errorText(formError)}
        </p>
      )}
      <div className="xi-row-actions">
        <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
          {busy ? t("saving") : t("saveChanges")}
        </button>
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          onClick={onCancel}
        >
          {t("cancel")}</button>
      </div>
    </form>
  );
}

/* Deactivation with confirmed draft disposition (S51 §2, plan §2.1).
 *
 * The form opens by fetching the admin-only open-draft preview (GET
 * .../open-drafts): a count of open-draft IDs plus the stable
 * draft_set_revision — never clinical content. Retain revokes access and
 * fences queued work while keeping drafts occupying the single-draft slot
 * (author-reserved); discard needs the exact reviewed revision, so a changed
 * set answers 409 DRAFT_SET_CHANGED and forces a re-review + reconfirm.
 * Discard is a second explicit confirmation (checkbox) and reactivation
 * never resurrects discarded drafts nor old sessions. */

type PreviewState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; preview: OpenDraftPreview };

function DeactivateForm({
  physician,
  onDeactivated,
  onCancel,
}: {
  physician: SafePhysician;
  onDeactivated: (message: string) => void;
  onCancel: () => void;
}) {
  const { text: uiText, t, errorText } = useLanguage();
  const { sessionExpired } = useAuth();
  const [draftAction, setDraftAction] = useState<DraftAction>("retain");
  const [preview, setPreview] = useState<PreviewState>({ kind: "loading" });
  const [reviewed, setReviewed] = useState(false);
  const [confirmedDiscard, setConfirmedDiscard] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadPreview = useCallback(async () => {
    setPreview({ kind: "loading" });
    try {
      const result = await getOpenDrafts(physician.id);
      setPreview({ kind: "ready", preview: result });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setPreview({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Open-draft preview failed to load. Check the connection and retry.",
      });
    }
  }, [physician.id, sessionExpired]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  const draftCount =
    preview.kind === "ready" ? preview.preview.reviewed_drafts.length : null;
  const draftRevision =
    preview.kind === "ready" ? preview.preview.draft_set_revision : null;
  const needsReview = draftAction === "discard" && (draftCount ?? 0) > 0;
  const canSubmit =
    !busy &&
    !done &&
    preview.kind === "ready" &&
    (!needsReview || (reviewed && confirmedDiscard));

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit || draftRevision === null) {
      return;
    }
    setFormError(null);
    setBusy(true);
    try {
      const result = await deactivatePhysician(physician.id, draftAction, draftRevision);
      setDone(true);
      onDeactivated(
        result.draft_action === "discard"
          ? `Physician deactivated; ${result.reviewed_drafts.length} open draft(s) discarded and the slots released. Access revoked immediately; queued work cancelled. Reactivation does not resurrect discarded drafts.`
          : `Physician deactivated; ${result.reviewed_drafts.length} open draft(s) retained (author-reserved, still occupying the single-draft slot). Access revoked immediately; queued work cancelled.`,
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        err.code === "DRAFT_SET_CHANGED"
      ) {
        // The set changed under review: force a re-review + reconfirm
        // against the fresh set, never submit the stale revision.
        setReviewed(false);
        setConfirmedDiscard(false);
        setFormError(
          `${conflictHint(err.code)} The preview below is refreshed — review the current set and confirm again.`,
        );
        void loadPreview();
        return;
      }
      if (err instanceof ApiError) {
        setFormError(`${err.message} (${err.code}). Nothing was changed.`);
        return;
      }
      setFormError("Deactivation failed. Check the connection and retry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form aria-label={t("deactivatePhysician")} onSubmit={handleSubmit} noValidate>
      <div data-testid={`deactivate-preview-${physician.id}`} role="status">
        {preview.kind === "loading" && (
          <p className="xi-hint">{t("loadingOpenDrafts")}</p>
        )}
        {preview.kind === "error" && (
          <div>
            <p className="xi-form-error" role="alert">
              {uiText(preview.message)}
            </p>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void loadPreview()}
            >
              {t("retryPreview")}
            </button>
          </div>
        )}
        {preview.kind === "ready" && (
          <p className="xi-hint" style={{ marginBottom: 4 }}>
            
            {t("openDrafts")} {preview.preview.reviewed_drafts.length}  {t("setRevision")}{" "}
            {preview.preview.draft_set_revision}{t("identifiersOnlyNoDraftContents")}{" "}
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={busy}
              onClick={() => void loadPreview()}
            >
              {t("refreshPreview")}
            </button>
          </p>
        )}
      </div>
      <div className="xi-radio-group" role="radiogroup" aria-label={t("draftHandling")}>
        <label htmlFor={`draft-retain-${physician.id}`}>
          <input
            id={`draft-retain-${physician.id}`}
            type="radio"
            name={`draft-action-${physician.id}`}
            value="retain"
            checked={draftAction === "retain"}
            onChange={() => setDraftAction("retain")}
          />
          {t("retainDrafts")}
        </label>
        <label htmlFor={`draft-discard-${physician.id}`}>
          <input
            id={`draft-discard-${physician.id}`}
            type="radio"
            name={`draft-action-${physician.id}`}
            value="discard"
            checked={draftAction === "discard"}
            onChange={() => setDraftAction("discard")}
          />
          {t("discardDrafts")}
        </label>
      </div>
      {needsReview && (
        <div className="xi-notice" role="group" aria-label={t("confirmedDiscard")}>
          <label htmlFor={`draft-reviewed-${physician.id}`}>
            <input
              id={`draft-reviewed-${physician.id}`}
              type="checkbox"
              checked={reviewed}
              onChange={(event) => setReviewed(event.target.checked)}
            />
            
            {t("iReviewedTheCurrentSetOf")} {draftCount}  {t("openDraftSAtRevision")}{" "}
            {draftRevision}  {t("identifiersOnly")}
          </label>
          <label htmlFor={`draft-confirm-discard-${physician.id}`}>
            <input
              id={`draft-confirm-discard-${physician.id}`}
              type="checkbox"
              checked={confirmedDiscard}
              onChange={(event) => setConfirmedDiscard(event.target.checked)}
            />
            {t("confirmPermanentDiscard")}
          </label>
        </div>
      )}
      {formError !== null && (
        <p className="xi-form-error" role="alert">
          {errorText(formError)}
        </p>
      )}
      {done && (
        <p className="xi-status" role="status">
          {t("physicianDeactivated")}
        </p>
      )}
      <p className="xi-hint">
        {t("deactivationEffect")}
      </p>
      <div className="xi-row-actions">
        <button
          className="xi-btn xi-btn-primary"
          type="submit"
          disabled={!canSubmit}
        >
          {busy ? t("deactivating") : t("confirmDeactivation")}
        </button>
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          onClick={onCancel}
        >
          {done ? t("close") : t("cancel")}
        </button>
      </div>
    </form>
  );
}

export function PhysiciansPanel() {
  const { text: uiText, t, text } = useLanguage();
  const { sessionExpired } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [notice, setNotice] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deactivatingId, setDeactivatingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const latestReload = useRef(0);
  const createdPhysician = useRef<SafePhysician | null>(null);

  const reload = useCallback(async () => {
    const request = ++latestReload.current;
    // Background refreshes keep the current rows (and any open row form
    // with its success/412 message) mounted; only the first load spins.
    setState((previous) =>
      previous.kind === "ready" || previous.kind === "refreshing"
        ? { ...previous, kind: "refreshing" }
        : { kind: "loading" },
    );
    try {
      const result = await listPhysicians();
      if (request !== latestReload.current) return;
      const created = createdPhysician.current;
      const additional = created !== null && !result.items.some((item) => item.id === created.id);
      const current = additional ? await getPhysician(created.id) : null;
      if (request !== latestReload.current) return;
      const items = current ? [...result.items, current].sort((a, b) => a.username.localeCompare(b.username)) : result.items;
      setState({ kind: "ready", items, total: result.total, additional });
    } catch (err) {
      if (request !== latestReload.current) return;
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setState({
        kind: "error",
        message:
          err instanceof ApiError
            ? err.message
            : "Physician list failed to load. Check the connection and retry.",
      });
    }
  }, [sessionExpired]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function handleReactivate(physician: SafePhysician) {
    setNotice(null);
    setBusyId(physician.id);
    try {
      await reactivatePhysician(physician.id);
      setNotice(
        "Physician reactivated (new login required; old sessions stay revoked). Discarded drafts are not resurrected.",
      );
      await reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setNotice(
        err instanceof ApiError
          ? err.message
          : "Reactivation failed. Check the connection and retry.",
      );
    } finally {
      setBusyId(null);
    }
  }

  function handleSaved(message: string) {
    setEditingId(null);
    setDeactivatingId(null);
    setNotice(message);
    void reload();
  }

  return (
    <div>
      <h2 className="xi-page-title">{t("physicians")}</h2>
      {notice !== null && (
        <p className="xi-status" role="status">
          {text(notice)}
        </p>
      )}
      <CreateForm
        onCreated={(physician) => {
          setNotice(null);
          createdPhysician.current = physician;
          setState((previous) => {
            if (previous.kind !== "ready" && previous.kind !== "refreshing") return previous;
            if (previous.items.some((item) => item.id === physician.id)) return previous;
            return { ...previous, items: [...previous.items, physician].sort((a, b) => a.username.localeCompare(b.username)), additional: true };
          });
          void reload();
        }}
      />
      <section className="xi-card" aria-labelledby="physician-list-heading">
        <h3 className="xi-section-title" id="physician-list-heading">
          {t("physicianAccounts")}
          {(state.kind === "ready" || state.kind === "refreshing") &&
            ` (${state.total})`}
        </h3>
        {(state.kind === "ready" || state.kind === "refreshing") && state.additional && (
          <p className="xi-hint">{t("firstPageNewPhysician")}</p>
        )}
        {(state.kind === "ready" || state.kind === "refreshing") &&
          state.items.length < state.total && (
            <p className="xi-hint">{t("showingValue1OfValue2", { value1: state.items.length, value2: state.total })}</p>
          )}
        {state.kind === "loading" && <p>{t("loadingPhysicians")}</p>}
        {state.kind === "error" && (
          <div>
            <p className="xi-form-error" role="alert">
              {uiText(state.message)}
            </p>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void reload()}
            >
              {t("retry")}</button>
          </div>
        )}
        {(state.kind === "ready" || state.kind === "refreshing") &&
          (state.items.length === 0 ? (
            <p>{t("noPhysicians")}</p>
          ) : (
            <table className="xi-table" aria-label={t("physicians")}>
              <thead>
                <tr>
                  <th scope="col">{t("username")}</th>
                  <th scope="col">{t("status")}</th>
                  <th scope="col">{t("actions")}</th>
                </tr>
              </thead>
              <tbody>
                {state.items.map((physician) => (
                  <tr key={physician.id}>
                    <td>{physician.username}</td>
                    <td>
                      <span
                        className={
                          physician.active
                            ? "xi-badge xi-badge-active"
                            : "xi-badge xi-badge-inactive"
                        }
                      >
                        {physician.active ? t("active") : t("inactive")}
                      </span>
                    </td>
                    <td>
                      {editingId === physician.id ? (
                        <EditForm
                          physician={physician}
                          onSaved={() => handleSaved("Physician updated.")}
                          onCancel={() => setEditingId(null)}
                          onStale={() => void reload()}
                        />
                      ) : deactivatingId === physician.id ? (
                        <DeactivateForm
                          physician={physician}
                          onDeactivated={(message) => {
                            setNotice(message);
                            setDeactivatingId(null);
                            void reload();
                          }}
                          onCancel={() => setDeactivatingId(null)}
                        />
                      ) : (
                        <div className="xi-row-actions">
                          <button
                            className="xi-btn xi-btn-secondary"
                            type="button"
                            onClick={() => {
                              setNotice(null);
                              setDeactivatingId(null);
                              setEditingId(physician.id);
                            }}
                          >
                            {t("edit")}
                          </button>
                          {physician.active ? (
                            <button
                              className="xi-btn xi-btn-secondary"
                              type="button"
                              onClick={() => {
                                setNotice(null);
                                setEditingId(null);
                                setDeactivatingId(physician.id);
                              }}
                            >
                              {t("deactivate")}
                            </button>
                          ) : (
                            <button
                              className="xi-btn xi-btn-secondary"
                              type="button"
                              disabled={busyId === physician.id}
                              onClick={() => void handleReactivate(physician)}
                              title={t("reactivationLimit")}
                            >
                              {t("reactivate")}
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>
    </div>
  );
}
