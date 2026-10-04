import { useState } from "react";
import { ApiError } from "./api";
import { useAuth } from "./auth";
import type { Role } from "./api";

export function LoginPage() {
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
    <div className="xi-auth">
      <h2 className="xi-section-title">Sign in</h2>
      <form onSubmit={handleSubmit} noValidate>
        <div className="xi-field">
          <label className="xi-label" htmlFor="login-role">
            Role
          </label>
          <select
            className="xi-select"
            id="login-role"
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
          >
            <option value="physician">Physician</option>
            <option value="admin">Administrator</option>
          </select>
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="login-username">
            Username
          </label>
          <input
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
            Password
          </label>
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
            {error}
          </p>
        )}
        <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="xi-hint">
        To register a new account, contact administrator. There is no
        self-registration.
      </p>
    </div>
  );
}
