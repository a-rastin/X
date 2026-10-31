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
  const [effectiveEndpoint, setEffectiveEndpoint] = useState("");
  const [localEndpointApproved, setLocalEndpointApproved] = useState(false);
  const [trustedProxyApproved, setTrustedProxyApproved] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [model, setModel] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
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
        setEffectiveEndpoint(current.effective_endpoint);
        setLocalEndpointApproved(current.local_endpoint_approved);
        setTrustedProxyApproved(current.trusted_proxy_approved);
        setModel(current.model);
        setTimeoutSeconds(current.timeout_seconds === null ? "" : String(current.timeout_seconds));
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
    setFieldErrors({});
    try {
      const saved = await saveProviderSettings({
        baseUrl,
        model,
        timeoutSeconds,
        revision,
        secretAction,
        apiKey,
        localEndpointApproved,
        trustedProxyApproved,
      });
      setRevision(saved.revision);
      setKeyStatus(saved.api_key);
      setCapability(saved.capability);
      setBaseUrl(saved.base_url);
      setEffectiveEndpoint(saved.effective_endpoint);
      setLocalEndpointApproved(saved.local_endpoint_approved);
      setTrustedProxyApproved(saved.trusted_proxy_approved);
      setModel(saved.model);
      setTimeoutSeconds(saved.timeout_seconds === null ? "" : String(saved.timeout_seconds));
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
      if (err instanceof ApiError) {
        setFieldErrors(err.fieldErrors);
      }
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
    setFieldErrors({});
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
      if (err instanceof ApiError) {
        setFieldErrors(err.fieldErrors);
      }
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
              onChange={(event) => {
                setBaseUrl(event.target.value);
                setLocalEndpointApproved(false);
                setTrustedProxyApproved(false);
                setEffectiveEndpoint("");
              }}
              aria-invalid={Boolean(fieldErrors.base_url?.length)}
              aria-describedby={`provider-base-url-help${fieldErrors.base_url?.length ? " provider-base-url-error" : ""}`}
              autoComplete="off"
            />
            <p className="xi-hint" id="provider-base-url-help">
              Enter an HTTPS origin, a base ending in /v1, or a full chat endpoint.
              An origin uses /v1/chat/completions; /v1 adds /chat/completions.
              Custom endpoint paths are preserved.
            </p>
            {fieldErrors.base_url?.length > 0 && (
              <p className="xi-form-error" id="provider-base-url-error">
                {fieldErrors.base_url.join(" ")}
              </p>
            )}
            {effectiveEndpoint && (
              <p className="xi-hint" id="provider-effective-endpoint">
                Saved endpoint: {effectiveEndpoint}
              </p>
            )}
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
              aria-invalid={Boolean(fieldErrors.model?.length)}
              aria-describedby={fieldErrors.model?.length ? "provider-model-error" : undefined}
              autoComplete="off"
            />
            {fieldErrors.model?.length > 0 && (
              <p className="xi-form-error" id="provider-model-error">
                {fieldErrors.model.join(" ")}
              </p>
            )}
          </div>
          <div className="xi-field">
            <label className="xi-label" htmlFor="provider-timeout">Provider timeout (seconds)</label>
            <input
              className="xi-input"
              id="provider-timeout"
              type="number"
              min={10}
              max={900}
              step="any"
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
              aria-describedby="provider-timeout-help provider-timeout-error"
              aria-invalid={Boolean(fieldErrors.timeout_seconds?.length)}
            />
            <p className="xi-hint" id="provider-timeout-help">
              10–900 seconds per provider request. Leave blank to use the deployment setting
              (300 seconds by default). The effective value appears on Status.
            </p>
            <p className="xi-form-error" id="provider-timeout-error">
              {fieldErrors.timeout_seconds?.join(" ")}
            </p>
          </div>
          <div className="xi-field">
            <label>
              <input
                id="provider-local-approval"
                type="checkbox"
                checked={localEndpointApproved}
                onChange={(event) => setLocalEndpointApproved(event.target.checked)}
                aria-invalid={Boolean(fieldErrors.local_endpoint_approved?.length)}
                aria-describedby={`provider-local-approval-help${fieldErrors.local_endpoint_approved?.length ? " provider-local-approval-error" : ""}`}
              />{" "}
              Approve this local endpoint
            </label>
            <p className="xi-hint" id="provider-local-approval-help">
              Allow a self-hosted provider on this saved host and port, including HTTP.
              Metadata and link-local destinations remain blocked.
              Changing the URL clears this approval.
            </p>
            {fieldErrors.local_endpoint_approved?.length > 0 && (
              <p className="xi-form-error" id="provider-local-approval-error">
                {fieldErrors.local_endpoint_approved.join(" ")}
              </p>
            )}
          </div>
          <div className="xi-field">
            <label>
              <input
                id="provider-proxy-approval"
                type="checkbox"
                checked={trustedProxyApproved}
                onChange={(event) => setTrustedProxyApproved(event.target.checked)}
                aria-invalid={Boolean(fieldErrors.trusted_proxy_approved?.length)}
                aria-describedby={`provider-proxy-approval-help${fieldErrors.trusted_proxy_approved?.length ? " provider-proxy-approval-error" : ""}`}
              />{" "}
              Trust this endpoint&apos;s fake-IP proxy
            </label>
            <p className="xi-hint" id="provider-proxy-approval-help">
              Allow this saved host to resolve through a trusted proxy using 198.18.0.0/15.
              Enable only when your network uses that proxy.
              Changing the URL clears this approval.
            </p>
            {fieldErrors.trusted_proxy_approved?.length > 0 && (
              <p className="xi-form-error" id="provider-proxy-approval-error">
                {fieldErrors.trusted_proxy_approved.join(" ")}
              </p>
            )}
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
                aria-invalid={Boolean(fieldErrors.secret_action?.length)}
                aria-describedby={fieldErrors.secret_action?.length ? "provider-secret-action-error" : undefined}
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
              {fieldErrors.secret_action?.length > 0 && (
                <p className="xi-form-error" id="provider-secret-action-error">
                  {fieldErrors.secret_action.join(" ")}
                </p>
              )}
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
              aria-invalid={Boolean(fieldErrors.api_key?.length)}
              aria-describedby={fieldErrors.api_key?.length ? "provider-api-key-error" : undefined}
              autoComplete="off"
            />
            {fieldErrors.api_key?.length > 0 && (
              <p className="xi-form-error" id="provider-api-key-error">
                {fieldErrors.api_key.join(" ")}
              </p>
            )}
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
