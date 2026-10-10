import React from "react";
import { useAuth } from "../features/identity/auth";
import { LoginPage } from "../features/identity/LoginPage";
import { ResearchWarningGate } from "../features/identity/ResearchWarning";
import { ThemeToggle } from "../features/identity/ThemeToggle";
import { EncounterPage } from "../features/encounters/EncounterPage";
import { ChartPage } from "../features/patients/chart/ChartPage";
import { PatientsPage } from "../features/patients/DirectoryPage";
import { RegistrationPage } from "../features/patients/RegistrationForm";
import { shouldBlockNavigation } from "./navigationGuard";
import {
  encounterIdFromHash,
  patientIdFromHash,
  useRoute,
  routeToHash,
  type Route,
} from "./router";
import {
  AccountPage,
  AdminDashboard,
  AuditRoutePage,
  NetworksRoutePage,
  PhysicianDashboard,
  PhysiciansPage,
} from "./pages";

function NavLink({
  route,
  current,
  navigate,
  children,
}: {
  route: Exclude<Route, "encounter" | "chart">;
  current: Route;
  navigate: (route: Exclude<Route, "encounter" | "chart">) => void;
  children: React.ReactNode;
}) {
  return (
    <a
      href={routeToHash(route)}
      aria-current={current === route ? "page" : undefined}
      onClick={(event) => {
        event.preventDefault();
        // In-app hash-route guard: unsaved draft edits warn before leaving.
        if (shouldBlockNavigation()) {
          return;
        }
        navigate(route);
      }}
    >
      {children}
    </a>
  );
}

function EncounterRoute() {
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
        <h2 className="xi-page-title">Encounter draft</h2>
        <p className="xi-form-error" role="alert">
          Missing draft id. Open a draft from the patient directory.
        </p>
        <p>
          <a className="xi-btn xi-btn-secondary" href="#/patients">
            Back to directory
          </a>
        </p>
      </div>
    );
  }
  return <EncounterPage key={encounterId} encounterId={encounterId} />;
}

function ChartRoute() {
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
          Patient chart
        </h2>
        <p className="xi-form-error" role="alert">
          Missing patient id. Open a chart from the patient directory.
        </p>
        <p>
          <a className="xi-btn xi-btn-secondary" href="#/patients">
            Back to directory
          </a>
        </p>
      </div>
    );
  }
  return <ChartPage key={patientId} patientId={patientId} />;
}

export function App() {
  const { status, user, warningAcked, logout } = useAuth();
  const [hashRoute, navigate] = useRoute();

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
    status === "ready" && user !== null && user.role === "physician" && !warningAcked;

  return (
    <div className="xi-shell">
      <a className="xi-skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="xi-header">
        <h1 className="xi-wordmark">X-INSIGHT</h1>
        {user !== null && (
          <nav className="xi-nav" aria-label="Primary">
            <NavLink route="dashboard" current={route} navigate={navigate}>
              Dashboard
            </NavLink>
            <NavLink route="patients" current={route} navigate={navigate}>
              Patients
            </NavLink>
            {user.role === "admin" && (
              <NavLink route="physicians" current={route} navigate={navigate}>
                Physicians
              </NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="networks" current={route} navigate={navigate}>
                Networks
              </NavLink>
            )}
            {user.role === "admin" && (
              <NavLink route="audit" current={route} navigate={navigate}>
                Audit
              </NavLink>
            )}
            <NavLink route="account" current={route} navigate={navigate}>
              Account
            </NavLink>
          </nav>
        )}
        <div className="xi-header-meta">
          {user !== null && <ThemeToggle />}
          {user !== null && (
            <span className="xi-identity">
              {user.username} ({user.role})
            </span>
          )}
          {user !== null && (
            <button
              className="xi-btn xi-btn-secondary"
              type="button"
              onClick={() => {
                void logout().then(() => navigate("login"));
              }}
            >
              Sign out
            </button>
          )}
        </div>
      </header>
      <main className="xi-main" id="main-content" tabIndex={-1}>
        {status === "loading" && <p>Loading…</p>}
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
            {!showWarningGate && route === "account" && <AccountPage />}
          </>
        )}
      </main>
    </div>
  );
}
