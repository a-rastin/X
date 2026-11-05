import { LanguageButton, useLanguage } from "../../shared/i18n";
import { researchWarningPresentation } from "../../shared/messages";
import { useEffect, useRef } from "react";
import { useAuth } from "./auth";

/** Blocking research-use acknowledgement, backed by persisted server state. */
export function ResearchWarningGate() {
  const { t, language, errorText } = useLanguage();
  const {
    researchWarning,
    researchWarningVersion,
    warningPending,
    warningError,
    acknowledgeWarning,
  } = useAuth();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (researchWarning !== null && !dialogRef.current?.open) {
      dialogRef.current?.showModal();
      // Start long-form reading at the title; native modal behavior contains focus.
      titleRef.current?.focus({ preventScroll: true });
    }
  }, [researchWarning, researchWarningVersion]);

  if (researchWarning === null) {
    return null;
  }

  // Translate presentation only when both server source and version match.
  const warning = researchWarningPresentation(language, researchWarningVersion, researchWarning);
  const paragraphs = warning.text.split(/(?<=[.!?؟])\s+/);

  return (
    <dialog
      ref={dialogRef}
      className="xi-research-dialog"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="research-warning-title"
      aria-describedby="research-warning-text"
      onCancel={(event) => event.preventDefault()}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const targets = event.currentTarget.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [tabindex="0"]',
        );
        const first = targets[0];
        const last = targets[targets.length - 1];
        if (
          event.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === titleRef.current)
        ) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
    >
      <div className="xi-dialog-language"><LanguageButton /></div>
      <header className="xi-research-header">
        <svg
          className="xi-research-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8l-5-5Z" />
          <path d="M14 3v5h5M9 12h6M9 16h6" />
        </svg>
        <h2 ref={titleRef} tabIndex={-1} id="research-warning-title">
          {t("warningTitle")}</h2>
      </header>
      <div
        className="xi-research-body"
        id="research-warning-text"
        tabIndex={0}
        aria-label={t("warningText")}
      >
        {!warning.available && <p className="xi-warning-panel" role="status">{t("translationUnavailable")}</p>}
        {paragraphs.map((paragraph, index) => (
          <p
            key={index}
            lang={warning.available ? language : "en"}
            dir={warning.available ? undefined : "ltr"}
            className={index === 1 ? "xi-research-limitation" : undefined}
          >
            {paragraph}
            {index < paragraphs.length - 1 ? " " : ""}
          </p>
        ))}
      </div>
      <footer className="xi-research-footer">
        <p>{t("warningReview")}</p>
        {warningError !== null && (
          <p className="xi-form-error" role="alert">
            {errorText(warningError)}
          </p>
        )}
        <button
          className="xi-btn xi-btn-primary"
          type="button"
          disabled={warningPending}
          onClick={() => {
            void acknowledgeWarning();
          }}
        >
          {warningPending ? t("acknowledging") : t("acknowledgeContinue")}
        </button>
      </footer>
    </dialog>
  );
}
