import { Button } from "@astryxdesign/core/Button";
import { useLanguage } from "../../shared/i18n";
import { useRef, useState } from "react";
import { ApiError } from "./api";
import { useAuth } from "./auth";
import type { Theme } from "./api";

/** Required theme toggle (FR-03/NFR-03). Persists via PATCH
 * /me/preferences and reflects the server value; a failed save reverts the
 * control label and announces the error instead of sticking. */
export function ThemeToggle() {
  const { t, errorText } = useLanguage();
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
      // Keep keyboard focus on the control after the preference is saved.
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
    <span className="xi-theme-control">
      <Button
        ref={buttonRef}
        className="xi-rail-action"
        variant="secondary"
        label={`${t(theme === "dark" ? "dark" : "light")}: ${t(theme === "dark" ? "switchLight" : "switchDark")}`}
        onClick={handleToggle}
        isDisabled={busy}
      >
        {t(theme === "dark" ? "dark" : "light")}
      </Button>
      {error !== null && (
        <span className="xi-field-error" role="alert">
          {errorText(error)}
        </span>
      )}
    </span>
  );
}
