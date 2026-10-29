import { useEffect, useRef } from "react";
import { useAuth } from "./auth";

/** Blocking research-use acknowledgement, scoped to the auth tab session. */
export function ResearchWarningGate() {
  const { researchWarning, acknowledgeWarning } = useAuth();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (researchWarning !== null) {
      dialogRef.current?.showModal();
      // Start long-form reading at the title; native modal behavior contains focus.
      titleRef.current?.focus({ preventScroll: true });
    }
  }, [researchWarning]);

  if (researchWarning === null) {
    return null;
  }

  // Present the server's complete wording; no parallel client-side disclaimer.
  const paragraphs = researchWarning.split(/(?<=[.!?])\s+/);

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
          'button, [tabindex="0"]',
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
          Research prototype — clinical use disclaimer
        </h2>
      </header>
      <div
        className="xi-research-body"
        id="research-warning-text"
        tabIndex={0}
        aria-label="Full clinical use disclaimer"
      >
        {paragraphs.map((paragraph, index) => (
          <p
            key={index}
            className={index === 1 ? "xi-research-limitation" : undefined}
          >
            {paragraph}
            {index < paragraphs.length - 1 ? " " : ""}
          </p>
        ))}
      </div>
      <footer className="xi-research-footer">
        <p>Review this disclaimer before entering the workspace.</p>
        <button
          className="xi-btn xi-btn-primary"
          type="button"
          onClick={acknowledgeWarning}
        >
          Acknowledge and continue
        </button>
      </footer>
    </dialog>
  );
}
