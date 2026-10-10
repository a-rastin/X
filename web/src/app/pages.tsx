import { useAuth } from "../features/identity/auth";
import { PasswordForm } from "../features/identity/PasswordForm";
import { PhysiciansPanel } from "../features/identity/PhysiciansPanel";
import { AuditPage } from "../features/admin/audit/AuditPage";
import { NetworksPage } from "../features/admin/networks/NetworksPage";
import { PatientDirectory } from "../features/patients/DirectoryPage";

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
          workspace theme. The patient directory below is read-only.
        </p>
      </section>
      <PatientDirectory />
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
      <PatientDirectory />
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

export function NetworksRoutePage() {
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 403: never grant authority,
    // just avoid rendering model administration to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">Model networks</h2>
        <p className="xi-form-error" role="alert">
          Administrator access required.
        </p>
      </div>
    );
  }
  return <NetworksPage />;
}

export function AuditRoutePage() {
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 403: never grant authority,
    // just avoid rendering the audit trail to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">Audit trail</h2>
        <p className="xi-form-error" role="alert">
          Administrator access required.
        </p>
      </div>
    );
  }
  return <AuditPage />;
}
