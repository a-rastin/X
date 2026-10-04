import { useCallback, useEffect, useState } from "react";

export type Route =
  | "login"
  | "dashboard"
  | "physicians"
  | "account"
  | "patients"
  | "patients-new"
  | "encounter";

const HASHES: Record<string, Route> = {
  "#/login": "login",
  "#/dashboard": "dashboard",
  "#/physicians": "physicians",
  "#/account": "account",
  "#/patients": "patients",
  "#/patients/new": "patients-new",
};

const ROUTE_HASHES: Record<Exclude<Route, "encounter">, string> = {
  login: "#/login",
  dashboard: "#/dashboard",
  physicians: "#/physicians",
  account: "#/account",
  patients: "#/patients",
  "patients-new": "#/patients/new",
};

export function parseHash(hash: string): Route | null {
  if (hash in HASHES) {
    return HASHES[hash];
  }
  // Parametric draft route (S07): `#/encounters/:id` mirrors the backend
  // `/encounters/{id}` resource (see features/encounters/api.ts for why the
  // encounter id — not a patient sub-path — is in the URL).
  if (hash.startsWith("#/encounters/") && hash.length > "#/encounters/".length) {
    return "encounter";
  }
  return null;
}

export function routeToHash(route: Route): string {
  if (route === "encounter") {
    return "#/encounters";
  }
  return ROUTE_HASHES[route];
}

/** Current encounter id for the `encounter` route (null elsewhere). */
export function encounterIdFromHash(hash: string): string | null {
  const prefix = "#/encounters/";
  if (!hash.startsWith(prefix)) {
    return null;
  }
  const id = hash.slice(prefix.length).split(/[?#]/)[0] ?? "";
  return id === "" || id.includes("/") ? null : id;
}

/** Minimal hash router (no extra dependency). Hash routes keep every view
 * servable from the static preview server, including on reload. The draft
 * route carries its encounter id (`#/encounters/:id`); navigate there by
 * assigning `window.location.hash` (or the `encounterHash` helper) since a
 * bare route has no target. */
export function useRoute(): [
  Route | null,
  (route: Exclude<Route, "encounter">) => void,
] {
  const [route, setRoute] = useState<Route | null>(() =>
    parseHash(window.location.hash),
  );

  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((next: Exclude<Route, "encounter">) => {
    if (parseHash(window.location.hash) === next) {
      setRoute(next);
      return;
    }
    window.location.hash = routeToHash(next);
  }, []);

  return [route, navigate];
}
