import { useLanguage } from "../../shared/i18n";
import { useState } from "react";
import { ApiError } from "./api";
import { useAuth } from "./auth";
import type { Role } from "./api";

export function LoginPage() {
  const { t, errorText } = useLanguage();
  const { login } = useAuth();
  const [role, setRole] = useState<Role>("physician");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password, role);
    } catch (err) {
      // Generic failure text only: never enumerate which field was wrong,
      // and never leak throttling internals beyond the server message.
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Sign-in failed. Check the connection and retry.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="xi-auth-layout">
      <div className="xi-auth-intro">
        <span className="xi-eyebrow">{t("connectedWorkflow")}</span>
        <h2 className="xi-auth-title">{t("psychiatristSupport")}</h2>
        <p>
          {t("workflowIntroduction")}</p>
        <p>
          {t("prototypeLimitation")}</p>
        <ol className="xi-auth-workflow" aria-label={t("workspaceWorkflow")}>
          <li>
            <span>01</span>
            <div><strong>{t("buildPatientRecord")}</strong><p>{t("structuredHistory")}</p></div>
          </li>
          <li>
            <span>02</span>
            <div><strong>{t("reviewEvidence")}</strong><p>{t("assessmentReview")}</p></div>
          </li>
          <li>
            <span>03</span>
            <div><strong>{t("documentPlan")}</strong><p>{t("signedRecords")}</p></div>
          </li>
        </ol>
      </div>
      <div className="xi-auth">
        <h2 className="xi-section-title">{t("signIn")}</h2>
        <p className="xi-auth-subtitle">{t("workspaceAccess")}</p>
        <form onSubmit={handleSubmit} noValidate>
        <div className="xi-field">
          <label className="xi-label" htmlFor="login-role">
            {t("role")}</label>
          <select
            className="xi-select"
            id="login-role"
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
          >
            <option value="physician">{t("physician")}</option>
            <option value="admin">{t("administrator")}</option>
          </select>
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="login-username">
            {t("username")}</label>
          <input dir="ltr"
            className="xi-input"
            id="login-username"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            aria-invalid={error !== null}
            aria-describedby={error ? "login-error" : undefined}
          />
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="login-password">
            {t("password")}</label>
          <input
            className="xi-input"
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={error !== null}
            aria-describedby={error ? "login-error" : undefined}
          />
        </div>
        {error !== null && (
          <p className="xi-form-error" role="alert" id="login-error">
            {errorText(error)}
          </p>
        )}
        <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
          {busy ? t("signingIn") : t("signIn")}
        </button>
        </form>
        <p className="xi-hint">
          {t("noSelfRegistration")}</p>
      </div>
    </div>
  );
}
