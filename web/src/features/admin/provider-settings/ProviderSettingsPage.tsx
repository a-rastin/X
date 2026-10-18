/* Admin provider settings form (S42).
 *
 * The route guard in app/pages.tsx hides this from non-admins. Authority
 * stays on the server. The password field starts empty and is cleared after
 * a successful save; the typed key is never copied into an alert or into
 * the status text. Saving does not verify the provider.
 */

import { useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  getProviderSettings,
  saveProviderSettings,
  testProviderSettings,
  type Capability,
  type KeyStatus,
  type SecretAction,
} from "./api";
import { useAuth } from "../../identity/auth";

function messageFor(err: unknown): string {
  if (err instanceof ApiError && err.status === 412 && err.code === "STALE_REVISION") {
    return "Provider settings changed. Reload this page before saving again.";
  }
  if (err instanceof ApiError) {
    return err.message;
  }
  return "Provider settings request failed. Check the connection and retry.";
}

export function ProviderSettingsPage() {
  const { sessionExpired } = useAuth();
  const [loaded, setLoaded] = useState(false);
  const [ready, setReady] = useState(false);
  const [revision, setRevision] = useState<number | null>(null);
  const [keyStatus, setKeyStatus] = useState<KeyStatus>("absent");
  const [capability, setCapability] = useState<Capability>("untested");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [secretAction, setSecretAction] = useState<SecretAction>("unchanged");
  const [apiKey, setApiKey] = useState("");
  const [affectedRuns, setAffectedRuns] = useState<number | null>(null);
  const [diagnostic, setDiagnostic] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const current = await getProviderSettings();
        if (cancelled) {
          return;
        }
        setRevision(current.revision);
        setKeyStatus(current.api_key);
        setCapability(current.capability);
        setBaseUrl(current.base_url);
        setModel(current.model);
        setReady(true);
      } catch (err) {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          sessionExpired();
          return;
        }
        setError(messageFor(err));
      } finally {
        if (!cancelled) {
          setLoaded(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionExpired]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy !== null) {
      return;
    }
    setBusy("save");
    setError(null);
    try {
      const saved = await saveProviderSettings({
        baseUrl,
        model,
        revision,
        secretAction,
        apiKey,
      });
      setRevision(saved.revision);
      setKeyStatus(saved.api_key);
      setCapability(saved.capability);
      setBaseUrl(saved.base_url);
      setModel(saved.model);
      setSecretAction("unchanged");
      setApiKey("");
      setDiagnostic(null);
      setAffectedRuns(
        typeof saved.affected_runs === "number" ? saved.affected_runs : null,
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError(messageFor(err));
    } finally {
      setBusy(null);
    }
  }

  async function test(): Promise<void> {
    if (busy !== null) {
      return;
    }
    setBusy("test");
    setError(null);
    try {
      const result = await testProviderSettings();
      setCapability(result.capability);
      setDiagnostic(result.diagnostic);
      setRevision(result.revision);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError(messageFor(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <h2 className="xi-page-title" id="provider-settings-heading">
        Provider settings
      </h2>
      <p className="xi-hint">
        Saving does not verify the provider. Capability returns to untested on
        save. The API key is never shown; status is configured or absent.
      </p>
      {!loaded && <p>Loading provider settings…</p>}
      {error !== null && (
        <p className="xi-form-error" id="provider-error" role="alert">
          {error}
        </p>
      )}
      {ready && (
        <form className="xi-card" onSubmit={(event) => void save(event)} noValidate>
          <div className="xi-meta-pair">
            <p id="provider-key-status">Key status: {keyStatus}</p>
            <p id="provider-capability">Capability: {capability}</p>
          </div>
          <div className="xi-field">
            <label className="xi-label" htmlFor="provider-base-url">
              Base URL
            </label>
            <input
              className="xi-input"
              id="provider-base-url"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="xi-field">
            <label className="xi-label" htmlFor="provider-model">
              Model
            </label>
            <input
              className="xi-input"
              id="provider-model"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              autoComplete="off"
            />
          </div>
          {revision !== null && (
            <div className="xi-field">
              <label className="xi-label" htmlFor="provider-secret-action">
                Saved key
              </label>
              <select
                className="xi-select"
                id="provider-secret-action"
                value={secretAction}
                onChange={(event) =>
                  setSecretAction(event.target.value as SecretAction)
                }
              >
                <option value="unchanged">unchanged</option>
                <option value="replace">replace</option>
                <option value="clear">clear</option>
              </select>
              <p className="xi-hint">
                Unchanged keeps the saved key and does not send a new one.
                Replace stores the key you type. Clear removes it.
              </p>
            </div>
          )}
          <div className="xi-field">
            <label className="xi-label" htmlFor="provider-api-key">
              API key
            </label>
            <input
              className="xi-input"
              id="provider-api-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="xi-row-actions">
            <button
              className="xi-btn xi-btn-primary"
              type="submit"
              id="provider-save"
              disabled={busy !== null}
            >
              {busy === "save" ? "Saving…" : "Save"}
            </button>
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              id="provider-test"
              disabled={busy !== null || revision === null}
              onClick={() => void test()}
            >
              {busy === "test" ? "Testing…" : "Test provider"}
            </button>
          </div>
          {affectedRuns !== null && (
            <p className="xi-status" id="provider-affected-runs" role="status">
              Affected runs: {affectedRuns}
            </p>
          )}
          {diagnostic !== null && (
            <p
              className={capability === "failed" ? "xi-form-error" : "xi-status"}
              id="provider-diagnostic"
              role={capability === "failed" ? "alert" : "status"}
            >
              {diagnostic}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
