import { useState } from "react";
import { ApiError, changePassword } from "./api";
import { useAuth } from "./auth";

function fieldError(errors: Record<string, string[]>, field: string): string | null {
  const messages = errors[field];
  return messages && messages.length > 0 ? messages.join(" ") : null;
}

/** Own password change (POST /me/password). Wrong current password yields
 * the generic 401 message; empty new password yields 422 field errors. */
export function PasswordForm() {
  const { refresh, sessionExpired } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});
    setSuccess(false);
    setBusy(true);
    try {
      await changePassword(currentPassword, newPassword);
      // Password rotation issues a fresh session: re-read /me so identity
      // and theme state track the server.
      await refresh();
      setSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        // Distinguish a revoked session (must re-authenticate) from a wrong
        // current password (generic message, stay on the form). A wrong
        // current password returns the generic login message; an expired
        // session reports that authentication is required.
        if (err.message === "Authentication required.") {
          sessionExpired();
          return;
        }
        setFormError(err.message);
        return;
      }
      if (err instanceof ApiError) {
        setFieldErrors(err.fieldErrors);
        setFormError(err.message);
        return;
      }
      setFormError("Password change failed. Check the connection and retry.");
    } finally {
      setBusy(false);
    }
  }

  const newError = fieldError(fieldErrors, "new_password");

  return (
    <section className="xi-card" aria-labelledby="password-heading">
      <h3 className="xi-section-title" id="password-heading">
        Change password
      </h3>
      <form aria-label="Change password" onSubmit={handleSubmit} noValidate>
        <div className="xi-field">
          <label className="xi-label" htmlFor="password-current">
            Current password
          </label>
          <input
            className="xi-input"
            id="password-current"
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            aria-invalid={formError !== null}
            aria-describedby={formError ? "password-form-error" : undefined}
          />
        </div>
        <div className="xi-field">
          <label className="xi-label" htmlFor="password-new">
            New password
          </label>
          <input
            className="xi-input"
            id="password-new"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            aria-invalid={newError !== null}
            aria-describedby={newError ? "password-new-error" : undefined}
          />
          {newError !== null && (
            <p className="xi-field-error" id="password-new-error">
              {newError}
            </p>
          )}
        </div>
        {formError !== null && (
          <p className="xi-form-error" role="alert" id="password-form-error">
            {formError}
          </p>
        )}
        {success && (
          <p className="xi-status" role="status">
            Password changed.
          </p>
        )}
        <button className="xi-btn xi-btn-primary" type="submit" disabled={busy}>
          {busy ? "Changing…" : "Change password"}
        </button>
      </form>
    </section>
  );
}
