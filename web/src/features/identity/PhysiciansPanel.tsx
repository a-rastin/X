import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  conflictHint,
  createPhysician,
  deactivatePhysician,
  getOpenDrafts,
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
  | { kind: "refreshing"; items: SafePhysician[]; total: number }
  | { kind: "error"; message: string }
  | { kind: "ready"; items: SafePhysician[]; total: number };

function CreateForm({ onCreated }: { onCreated: () => void }) {
  const { sessionExpired } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setErrors({});
    setFormError(null);
    setCreated(false);
    setBusy(true);
    try {
      await createPhysician(username, password);
      setUsername("");
      setPassword("");
      setCreated(true);
      onCreated();
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
      setBusy(false);
    }
  }

  const usernameError = errors["username"]?.join(" ") ?? null;
  const passwordError = errors["password"]?.join(" ") ?? null;

  return (
    <section className="xi-card" aria-labelledby="create-physician-heading">
      <h3 className="xi-section-title" id="create-physician-heading">
        Create physician
      </h3>
      <form aria-label="Create physician" onSubmit={handleSubmit} noValidate>
        <div className="xi-field">
          <label className="xi-label" htmlFor="create-username">
            Username
          </label>
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
              {usernameError}
            </p>
          )}
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="create-password">
            Password
          </label>
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
              {passwordError}
            </p>
          )}
        </div>
        {formError !== null && (
          <p className="xi-form-error" role="alert">
            {formError}
          </p>
        )}
        {created && (
          <p className="xi-status" role="status">
            Physician created.
          </p>
        )}
        <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
          {busy ? "Creating…" : "Create physician"}
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
    <form aria-label="Edit physician" onSubmit={handleSubmit} noValidate>
      <div className="xi-form-row">
        <div className="xi-field">
          <label className="xi-label" htmlFor={`edit-username-${physician.id}`}>
            Username
          </label>
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
              {usernameError}
            </p>
          )}
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor={`edit-password-${physician.id}`}>
            New password (blank keeps current)
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
              {passwordError}
            </p>
          )}
        </div>
      </div>
      {formError !== null && (
        <p className="xi-form-error" role="alert">
          {formError}
        </p>
      )}
      <div className="xi-row-actions">
        <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save changes"}
        </button>
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          onClick={onCancel}
        >
          Cancel
        </button>
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
    <form aria-label="Deactivate physician" onSubmit={handleSubmit} noValidate>
      <div data-testid={`deactivate-preview-${physician.id}`} role="status">
        {preview.kind === "loading" && (
          <p className="xi-hint">Loading open-draft preview…</p>
        )}
        {preview.kind === "error" && (
          <div>
            <p className="xi-form-error" role="alert">
              {preview.message}
            </p>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => void loadPreview()}
            >
              Retry preview
            </button>
          </div>
        )}
        {preview.kind === "ready" && (
          <p className="xi-hint" style={{ marginBottom: 4 }}>
            Open drafts: {preview.preview.reviewed_drafts.length} (set revision{" "}
            {preview.preview.draft_set_revision}; identifiers only, no draft contents).{" "}
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              disabled={busy}
              onClick={() => void loadPreview()}
            >
              Refresh preview
            </button>
          </p>
        )}
      </div>
      <div className="xi-radio-group" role="radiogroup" aria-label="Draft handling">
        <label htmlFor={`draft-retain-${physician.id}`}>
          <input
            id={`draft-retain-${physician.id}`}
            type="radio"
            name={`draft-action-${physician.id}`}
            value="retain"
            checked={draftAction === "retain"}
            onChange={() => setDraftAction("retain")}
          />
          Retain drafts (read-only, still occupy the single-draft slot)
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
          Discard drafts (releases the slots; reactivation never resurrects them)
        </label>
      </div>
      {needsReview && (
        <div className="xi-notice" role="group" aria-label="Confirmed discard">
          <label htmlFor={`draft-reviewed-${physician.id}`}>
            <input
              id={`draft-reviewed-${physician.id}`}
              type="checkbox"
              checked={reviewed}
              onChange={(event) => setReviewed(event.target.checked)}
            />
            I reviewed the current set of {draftCount} open draft(s) at revision{" "}
            {draftRevision} (identifiers only).
          </label>
          <label htmlFor={`draft-confirm-discard-${physician.id}`}>
            <input
              id={`draft-confirm-discard-${physician.id}`}
              type="checkbox"
              checked={confirmedDiscard}
              onChange={(event) => setConfirmedDiscard(event.target.checked)}
            />
            Confirm discard: the drafts release their slots permanently.
          </label>
        </div>
      )}
      {formError !== null && (
        <p className="xi-form-error" role="alert">
          {formError}
        </p>
      )}
      {done && (
        <p className="xi-status" role="status">
          Physician deactivated.
        </p>
      )}
      <p className="xi-hint">
        Deactivation revokes access and cancels queued work immediately.
      </p>
      <div className="xi-row-actions">
        <button
          className="xi-btn xi-btn-primary"
          type="submit"
          disabled={!canSubmit}
        >
          {busy ? "Deactivating…" : "Confirm deactivation"}
        </button>
        <button
          className="xi-btn xi-btn-secondary"
          type="button"
          onClick={onCancel}
        >
          {done ? "Close" : "Cancel"}
        </button>
      </div>
    </form>
  );
}

export function PhysiciansPanel() {
  const { sessionExpired } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [notice, setNotice] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deactivatingId, setDeactivatingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    // Background refreshes keep the current rows (and any open row form
    // with its success/412 message) mounted; only the first load spins.
    setState((previous) =>
      previous.kind === "ready" || previous.kind === "refreshing"
        ? { kind: "refreshing", items: previous.items, total: previous.total }
        : { kind: "loading" },
    );
    try {
      const result = await listPhysicians();
      setState({ kind: "ready", items: result.items, total: result.total });
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
      <h2 className="xi-page-title">Physicians</h2>
      {notice !== null && (
        <p className="xi-status" role="status">
          {notice}
        </p>
      )}
      <CreateForm
        onCreated={() => {
          setNotice("Physician created.");
          void reload();
        }}
      />
      <section className="xi-card" aria-labelledby="physician-list-heading">
        <h3 className="xi-section-title" id="physician-list-heading">
          Physician accounts
          {(state.kind === "ready" || state.kind === "refreshing") &&
            ` (${state.total})`}
        </h3>
        {state.kind === "loading" && <p>Loading physicians…</p>}
        {state.kind === "error" && (
          <div>
            <p className="xi-form-error" role="alert">
              {state.message}
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
        {(state.kind === "ready" || state.kind === "refreshing") &&
          (state.items.length === 0 ? (
            <p>No physicians yet.</p>
          ) : (
            <table className="xi-table" aria-label="Physicians">
              <thead>
                <tr>
                  <th scope="col">Username</th>
                  <th scope="col">Status</th>
                  <th scope="col">Actions</th>
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
                        {physician.active ? "Active" : "Inactive"}
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
                            Edit
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
                              Deactivate
                            </button>
                          ) : (
                            <button
                              className="xi-btn xi-btn-secondary"
                              type="button"
                              disabled={busyId === physician.id}
                              onClick={() => void handleReactivate(physician)}
                              title="Reactivation does not resurrect discarded drafts nor old sessions."
                            >
                              Reactivate
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
