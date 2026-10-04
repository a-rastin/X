import { useAuth } from "../features/identity/auth";
import { LoginPage } from "../features/identity/LoginPage";
import { ResearchWarningGate } from "../features/identity/ResearchWarning";
import { ThemeToggle } from "../features/identity/ThemeToggle";
import { useRoute, type Route } from "./router";
import {
  AccountPage,
  AdminDashboard,
  PhysicianDashboard,
  PhysiciansPage,
} from "./pages";

function NavLink({
  route,
  current,
  navigate,
  children,
}: {
  route: Route;
  current: Route;
  navigate: (route: Route) => void;
  children: React.ReactNode;
}) {
  return (
    <a
      href={`#/${route}`}
      aria-current={current === route ? "page" : undefined}
      onClick={(event) => {
        event.preventDefault();
        navigate(route);
      }}
    >
      {children}
    </a>
  );
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
            {user.role === "admin" && (
              <NavLink route="physicians" current={route} navigate={navigate}>
                Physicians
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
            {!showWarningGate && route === "physicians" && <PhysiciansPage />}
            {!showWarningGate && route === "account" && <AccountPage />}
          </>
        )}
      </main>
    </div>
  );
}
