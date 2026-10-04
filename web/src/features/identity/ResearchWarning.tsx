import { useAuth } from "./auth";

/** Blocking research-use acknowledgement: the physician dashboard stays
 * hidden until this is accepted. Acceptance persists for the auth session
 * (tab sessionStorage), so a GET /me refresh does not re-block. */
export function ResearchWarningGate() {
  const { researchWarning, acknowledgeWarning } = useAuth();

  if (researchWarning === null) {
    return null;
  }

  return (
    <div className="xi-modal-backdrop">
      <div
        className="xi-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="research-warning-title"
        aria-describedby="research-warning-text"
      >
        <h2 className="xi-section-title" id="research-warning-title">
          Research use only
        </h2>
        <p id="research-warning-text">{researchWarning}</p>
        <button
          className="xi-btn xi-btn-primary"
          type="button"
          onClick={acknowledgeWarning}
          autoFocus
        >
          Acknowledge and continue
        </button>
      </div>
    </div>
  );
}
