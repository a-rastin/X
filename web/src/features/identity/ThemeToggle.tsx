import { useRef, useState } from "react";
import { ApiError } from "./api";
import { useAuth } from "./auth";
import type { Theme } from "./api";

/** Required theme toggle (FR-03/NFR-03). Persists via PATCH
 * /me/preferences and reflects the server value; a failed save reverts the
 * control label and announces the error instead of sticking. */
export function ThemeToggle() {
  const { user, applyTheme, sessionExpired } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  if (user === null) {
    return null;
  }
  const theme: Theme = user.theme;
  const next: Theme = theme === "dark" ? "light" : "dark";

  async function handleToggle() {
    setError(null);
    setBusy(true);
    try {
      await applyTheme(next);
      // Saving briefly disables the button, which drops keyboard focus by
      // design; hand it back so keyboard users keep their place.
      buttonRef.current?.focus();
    } catch (err) {      if (err instanceof ApiError && err.status === 401) {
        sessionExpired();
        return;
      }
      setError("Theme preference could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span>
      <button
        ref={buttonRef}
        className="xi-btn xi-btn-secondary"
        type="button"
        onClick={handleToggle}
        disabled={busy}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      >
        <span aria-hidden="true">{theme === "dark" ? "☾ Dark" : "☀ Light"}</span>
      </button>{" "}
      {error !== null && (
        <span className="xi-field-error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}
