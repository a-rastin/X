import { useCallback, useEffect, useState } from "react";

export type Route =
  | "login"
  | "dashboard"
  | "physicians"
  | "account"
  | "patients"
  | "patients-new";

const HASHES: Record<string, Route> = {
  "#/login": "login",
  "#/dashboard": "dashboard",
  "#/physicians": "physicians",
  "#/account": "account",
  "#/patients": "patients",
  "#/patients/new": "patients-new",
};

const ROUTE_HASHES: Record<Route, string> = {
  login: "#/login",
  dashboard: "#/dashboard",
  physicians: "#/physicians",
  account: "#/account",
  patients: "#/patients",
  "patients-new": "#/patients/new",
};

export function parseHash(hash: string): Route | null {
  return HASHES[hash] ?? null;
}

export function routeToHash(route: Route): string {
  return ROUTE_HASHES[route];
}

/** Minimal hash router (no extra dependency). Hash routes keep every view
 * servable from the static preview server, including on reload. */
export function useRoute(): [Route | null, (route: Route) => void] {
  const [route, setRoute] = useState<Route | null>(() =>
    parseHash(window.location.hash),
  );

  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((next: Route) => {
    if (parseHash(window.location.hash) === next) {
      setRoute(next);
      return;
    }
    window.location.hash = routeToHash(next);
  }, []);

  return [route, navigate];
}
