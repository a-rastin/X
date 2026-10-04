import { useAuth } from "../features/identity/auth";
import { PasswordForm } from "../features/identity/PasswordForm";
import { PhysiciansPanel } from "../features/identity/PhysiciansPanel";

export function AdminDashboard() {
  return (
    <div>
      <h2 className="xi-page-title">Administrator dashboard</h2>
      <section className="xi-card" aria-labelledby="governance-heading">
        <h3 className="xi-section-title" id="governance-heading">
          Governance
        </h3>
        <p>
          Manage physician accounts, review identity settings, and adjust the
          workspace theme. Patient workspaces land in later sessions.
        </p>
      </section>
    </div>
  );
}

export function PhysicianDashboard() {
  const { researchWarning } = useAuth();
  return (
    <div>
      <h2 className="xi-page-title">Physician dashboard</h2>
      {researchWarning !== null && (
        <div className="xi-notice">
          <p style={{ margin: 0 }}>{researchWarning}</p>
        </div>
      )}
      <section className="xi-card" aria-labelledby="workspace-heading">
        <h3 className="xi-section-title" id="workspace-heading">
          Clinical workspace
        </h3>
        <p>
          Patient registration and encounter workspaces are not part of this
          session; they land in later sessions. No clinical content is shown
          here.
        </p>
      </section>
    </div>
  );
}

export function AccountPage() {
  const { user } = useAuth();
  return (
    <div>
      <h2 className="xi-page-title">Account</h2>
      {user !== null && (
        <section className="xi-card" aria-labelledby="identity-heading">
          <h3 className="xi-section-title" id="identity-heading">
            Identity
          </h3>
          <p>
            Signed in as <strong>{user.username}</strong> ({user.role}).
          </p>
          <p className="xi-hint">
            Theme preference: {user.theme}. Use the theme button in the header
            to switch; it is saved to your profile.
          </p>
        </section>
      )}
      <PasswordForm />
    </div>
  );
}

export function PhysiciansPage() {
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 403: never grant authority,
    // just avoid rendering governance controls to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">Physicians</h2>
        <p className="xi-form-error" role="alert">
          Administrator access required.
        </p>
      </div>
    );
  }
  return <PhysiciansPanel />;
}
