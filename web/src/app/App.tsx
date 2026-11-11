import { UnavailableDrugsPage } from "../features/identity/UnavailableDrugsPage";
import { LanguageButton, useLanguage } from "../shared/i18n";
import React from "react";
import { SideNavItem } from "@astryxdesign/core/SideNav";
import { Theme } from "@astryxdesign/core/theme";
import { academicTheme } from "../shared/astryx/built/academic.js";
import { useAuth } from "../features/identity/auth";
import { LoginPage } from "../features/identity/LoginPage";
import { ResearchWarningGate } from "../features/identity/ResearchWarning";
import { ThemeToggle } from "../features/identity/ThemeToggle";
import { EncounterPage } from "../features/encounters/EncounterPage";
import { ChartPage } from "../features/patients/chart/ChartPage";
import { PatientsPage } from "../features/patients/DirectoryPage";
import { RegistrationPage } from "../features/patients/RegistrationForm";
import { saveBeforeNavigation } from "./navigationGuard";
import {
  encounterIdFromHash,
  patientIdFromHash,
  reportIdFromHash,
  useRoute,
  routeToHash,
  type Route,
} from "./router";
import {
  AccountPage,
  AdminDashboard,
  AuditRoutePage,
  BackupsRoutePage,
  ExportsRoutePage,
  NetworksRoutePage,
  PhysicianDashboard,
  PhysiciansPage,
  ProviderSettingsRoutePage,
  ReportRoutePage,
  RestoresRoutePage,
  StatusRoutePage,
} from "./pages";

function NavLink({
  route,
  current,
  navigate,
  children,
}: {
  route: Exclude<Route, "encounter" | "chart" | "report">;
  current: Route;
  navigate: (route: Exclude<Route, "encounter" | "chart" | "report">) => void;
  children: string;
}) {
  return (
    <SideNavItem
      label={children}
      href={routeToHash(route)}
      isSelected={
        current === route ||
        (route === "patients" &&
          ["patients-new", "encounter", "chart", "report"].includes(current))
      }
      onClick={(event) => {
        event.preventDefault();
        navigate(route);
      }}
    />
  );
}

function EncounterRoute() {
  const { t } = useLanguage();
  const [hash, setHash] = React.useState(() =>
    typeof window !== "undefined" ? window.location.hash : "",
  );
  React.useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const encounterId = encounterIdFromHash(hash);
  if (encounterId === null) {
    return (
      <div>
        <h2 className="xi-page-title">{t("encounterDraft")}</h2>
        <p className="xi-form-error" role="alert">
          {t("missingDraft")}</p>
        <p>
          <a className="xi-btn xi-btn-secondary" href="#/patients">
            {t("backToDirectory")}</a>
        </p>
      </div>
    );
  }
  return <EncounterPage key={encounterId} encounterId={encounterId} />;
}

function ChartRoute() {
  const { t } = useLanguage();
  const [hash, setHash] = React.useState(() =>
    typeof window !== "undefined" ? window.location.hash : "",
  );
  React.useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const patientId = patientIdFromHash(hash);
  if (patientId === null) {
    return (
      <div>
        <h2 className="xi-page-title" data-testid="chart-heading" id="chart-heading">
          {t("patientChart")}</h2>
        <p className="xi-form-error" role="alert">
          {t("missingPatient")}</p>
        <p>
          <a className="xi-btn xi-btn-secondary" href="#/patients">
            {t("backToDirectory")}</a>
        </p>
      </div>
    );
  }
  return <ChartPage key={patientId} patientId={patientId} />;
}

function ReportRoute() {
  const { t } = useLanguage();
  const [hash, setHash] = React.useState(() =>
    typeof window !== "undefined" ? window.location.hash : "",
  );
  React.useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const patientId = reportIdFromHash(hash);
  if (patientId === null) {
    return (
      <div>
        <h2 className="xi-page-title" data-testid="report-heading" id="report-heading">
          {t("longitudinalReport")}</h2>
        <p className="xi-form-error" role="alert">
          {t("missingReportPatient")}</p>
        <p>
          <a className="xi-btn xi-btn-secondary" href="#/patients">
            {t("backToDirectory")}</a>
        </p>
      </div>
    );
  }
  return <ReportRoutePage key={patientId} patientId={patientId} />;
}

export function App() {
  const { t, language } = useLanguage();
  const { status, user, warningAcked, logout } = useAuth();
  const [hashRoute, navigate] = useRoute();
  const previousStatus = React.useRef(status);

  React.useEffect(() => {
    const loggedIn = previousStatus.current === "anonymous" && status === "ready";
    previousStatus.current = status;
    if (status === "ready" && user !== null && (loggedIn || hashRoute === "login")) {
      navigate("dashboard", { replace: true });
    }
  }, [status, user, hashRoute, navigate]);

  // Route guards complement the server checks: anonymous users always land
  // on login, authenticated users never sit on it. Authority stays
  // server-side; these only choose what to render.
  let route: Route;
  if (status !== "ready" || user === null) {
    route = "login";
  } else if (hashRoute === null || hashRoute === "login") {
    route = "dashboard";
  } else {
    route = hashRoute;
  }

  const showWarningGate =
    status === "ready" && user !== null && !warningAcked;

  return (
    <Theme theme={academicTheme} mode={user?.theme ?? "light"}>
    <div className={user === null ? "xi-shell xi-shell-gate" : "xi-shell"}>
      <a className="xi-skip-link" href="#main-content">
        {t("skipToContent")}</a>
      <div className="xi-header-language"><LanguageButton /></div>
      <header className="xi-header">
        <h1 className="xi-wordmark">
          <span className="xi-mark" aria-hidden="true">X</span>
          <span className="xi-brand-copy">
            X-INSIGHT
            <span className="xi-brand-caption">{t("researchWorkspace")}</span>
          </span>
        </h1>
        {user !== null && (
          <nav className="xi-nav" aria-label={t("primaryNavigation")}>
            <NavLink route="dashboard" current={route} navigate={navigate}>
              {t("dashboard")}</NavLink>
            <NavLink route="patients" current={route} navigate={navigate}>
              {t("patients")}</NavLink>
            {user.role === "admin" && (
              <NavLink route="physicians" current={route} navigate={navigate}>
                {t("physicians")}</NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="networks" current={route} navigate={navigate}>
                {t("networks")}</NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="audit" current={route} navigate={navigate}>
                {t("audit")}</NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="exports" current={route} navigate={navigate}>
                {t("exports")}</NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="backups" current={route} navigate={navigate}>
                {t("backups")}</NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="restores" current={route} navigate={navigate}>
                {t("restores")}</NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="provider-settings" current={route} navigate={navigate}>
                {t("provider")}</NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="status" current={route} navigate={navigate}>
                {t("status")}</NavLink>
            )}
            {user.role === "physician" && <NavLink route="unavailable-drugs" current={route} navigate={navigate}>{t("nc.unavailableDrugs")}</NavLink>}
            <NavLink route="account" current={route} navigate={navigate}>
              {t("account")}</NavLink>
          </nav>
        )}
        {user !== null && (
          <div className="xi-header-meta">
            <span className="xi-identity">
              <bdi className="xi-identity-name">{(language === "fa" ? user.name_fa || user.name_en : user.name_en || user.name_fa) || user.username}</bdi>
              <span className="xi-identity-role">{t(user.role === "admin" ? "administrator" : "physician")}</span>
            </span>
            <ThemeToggle />
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => {
                void saveBeforeNavigation().then((ok) => {
                  if (ok) void logout().then(() => navigate("login"));
                });
              }}
            >
              {t("signOut")}</button>
          </div>
        )}
      </header>
      <main className="xi-main" id="main-content" tabIndex={-1}>
        {status === "loading" && <p className="xi-hint">{t("loading")}</p>}
        {status !== "loading" && route === "login" && <LoginPage />}
        {status === "ready" && user !== null && route !== "login" && (
          <>
            {showWarningGate && <ResearchWarningGate />}
            {!showWarningGate && route === "dashboard" && user.role === "admin" && (
              <AdminDashboard />
            )}
            {!showWarningGate && route === "dashboard" && user.role === "physician" && (
              <PhysicianDashboard />
            )}
            {!showWarningGate && route === "patients" && <PatientsPage />}
            {!showWarningGate && route === "patients-new" && <RegistrationPage />}
            {!showWarningGate && route === "encounter" && (
              <EncounterRoute />
            )}
            {!showWarningGate && route === "chart" && <ChartRoute />}
            {!showWarningGate && route === "physicians" && <PhysiciansPage />}
            {!showWarningGate && route === "networks" && <NetworksRoutePage />}
            {!showWarningGate && route === "audit" && <AuditRoutePage />}
            {!showWarningGate && route === "exports" && <ExportsRoutePage />}
            {!showWarningGate && route === "backups" && <BackupsRoutePage />}
            {!showWarningGate && route === "restores" && <RestoresRoutePage />}
            {!showWarningGate && route === "provider-settings" && (
              <ProviderSettingsRoutePage />
            )}
            {!showWarningGate && route === "status" && <StatusRoutePage />}
            {!showWarningGate && route === "report" && <ReportRoute />}
            {!showWarningGate && route === "unavailable-drugs" && <UnavailableDrugsPage />}
            {!showWarningGate && route === "account" && <AccountPage />}
          </>
        )}
      </main>
    </div>
    </Theme>
  );
}
