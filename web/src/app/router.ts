import { useCallback, useEffect, useState } from "react";

export type Route = "login" | "dashboard" | "physicians" | "account";

const HASHES: Record<string, Route> = {
  "#/login": "login",
  "#/dashboard": "dashboard",
  "#/physicians": "physicians",
  "#/account": "account",
};

export function parseHash(hash: string): Route | null {
  return HASHES[hash] ?? null;
}

export function routeToHash(route: Route): string {
  return `#/${route}`;
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
