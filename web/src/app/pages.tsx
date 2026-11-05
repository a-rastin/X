import { useLanguage } from "../shared/i18n";
import { useAuth } from "../features/identity/auth";
import { PasswordForm } from "../features/identity/PasswordForm";
import { PhysiciansPanel } from "../features/identity/PhysiciansPanel";
import { AuditPage } from "../features/admin/audit/AuditPage";
import { BackupsPage } from "../features/admin/backups/BackupsPage";
import { RestoresPage } from "../features/admin/restores/RestoresPage";
import { ExportsPage } from "../features/admin/exports/ExportsPage";
import { ReportPage } from "../features/admin/exports/ReportPage";
import { NetworksPage } from "../features/admin/networks/NetworksPage";
import { ProviderSettingsPage } from "../features/admin/provider-settings/ProviderSettingsPage";
import { StatusPage } from "../features/admin/status/StatusPage";
import { PatientDirectory } from "../features/patients/DirectoryPage";

export function AdminDashboard() {
  const { t } = useLanguage();
  return (
    <div>
      <h2 className="xi-page-title">{t("adminDashboard")}</h2>
      <p className="xi-hint">{t("adminIntroduction")}</p>
      <section className="xi-card" aria-labelledby="governance-heading">
        <h3 className="xi-section-title" id="governance-heading">
          {t("governance")}</h3>
        <p className="xi-lede">
          {t("governanceIntroduction")}</p>
      </section>
      <PatientDirectory />
    </div>
  );
}

export function PhysicianDashboard() {
  const { t } = useLanguage();
  return (
    <div>
      <h2 className="xi-page-title">{t("physicianDashboard")}</h2>
      <p className="xi-hint">{t("physicianIntroduction")}</p>
      <PatientDirectory />
    </div>
  );
}

export function AccountPage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  return (
    <div>
      <h2 className="xi-page-title">{t("account")}</h2>
      {user !== null && (
        <section className="xi-card" aria-labelledby="identity-heading">
          <h3 className="xi-section-title" id="identity-heading">
            {t("identity")}</h3>
          <p>
            {t("signedInAs", { username: `\u2068${user.username}\u2069` })} ({t(user.role === "admin" ? "administrator" : "physician")}).
          </p>
          <p className="xi-hint">
            {t("themePreference", { theme: t(user.theme === "dark" ? "dark" : "light") })}
          </p>
        </section>
      )}
      <PasswordForm />
    </div>
  );
}

export function PhysiciansPage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 403: never grant authority,
    // just avoid rendering governance controls to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">{t("physicians")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <PhysiciansPanel />;
}

export function NetworksRoutePage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 403: never grant authority,
    // just avoid rendering model administration to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">{t("modelNetworks")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <NetworksPage />;
}

export function AuditRoutePage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 403: never grant authority,
    // just avoid rendering the audit trail to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">{t("auditTrail")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <AuditPage />;
}

export function ExportsRoutePage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 403: never grant authority,
    // just avoid rendering the export actions to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">{t("dataExports")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <ExportsPage />;
}

export function BackupsRoutePage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 401/403 on /backups: never
    // grant authority, just avoid rendering backup actions to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">{t("fullBackups")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <BackupsPage />;
}

export function RestoresRoutePage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 401/403 on /restores: never
    // grant authority, just avoid rendering staging actions to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">{t("stagedRestores")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <RestoresPage />;
}

export function ProviderSettingsRoutePage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 401/403 on /api-settings:
    // never grant authority, just avoid rendering the form to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">{t("providerSettings")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <ProviderSettingsPage />;
}

export function StatusRoutePage() {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server's 401/403 on /ops/status: never
    // grant authority, just avoid rendering gauges to the wrong role.
    return (
      <div>
        <h2 className="xi-page-title">{t("operationalStatus")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <StatusPage />;
}

export function ReportRoutePage({ patientId }: { patientId: string }) {
  const { t } = useLanguage();
  const { user } = useAuth();
  if (user?.role !== "admin") {
    // Client-side complement to the server 403 on
    // `GET /patients/{id}/report`: physicians never get a report button or
    // report content here (provisional print policy, plan §§1.3, 2.1).
    return (
      <div>
        <h2 className="xi-page-title">{t("longitudinalReport")}</h2>
        <p className="xi-form-error" role="alert">
          {t("administratorRequired")}</p>
      </div>
    );
  }
  return <ReportPage patientId={patientId} />;
}
