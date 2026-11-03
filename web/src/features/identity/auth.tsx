import { hasUnsavedChanges } from "../../app/navigationGuard";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  me,
  acknowledgeResearchWarning as apiAcknowledgeWarning,
  login as apiLogin,
  logout as apiLogout,
  setTheme as apiSetTheme,
  setCsrfToken,
  type PublicUser,
  type Role,
  type Theme,
} from "./api";

interface AuthContextValue {
  status: "loading" | "anonymous" | "ready";
  user: PublicUser | null;
  researchWarning: string | null;
  researchWarningVersion: string | null;
  warningAcked: boolean;
  warningPending: boolean;
  warningError: string | null;
  login: (username: string, password: string, role: Role) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  acknowledgeWarning: () => Promise<void>;
  applyTheme: (theme: Theme) => Promise<void>;
  sessionExpired: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function applyDocumentTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<"loading" | "anonymous" | "ready">(
    "loading",
  );
  const [user, setUser] = useState<PublicUser | null>(null);
  const [researchWarning, setResearchWarning] = useState<string | null>(null);
  const [researchWarningVersion, setResearchWarningVersion] = useState<
    string | null
  >(null);
  const [warningPending, setWarningPending] = useState(false);
  const [warningError, setWarningError] = useState<string | null>(null);
  // Guards late mutation responses: a sign-out or second login racing the
  // acknowledgement round trip must not apply its result to another user.
  const userIdRef = useRef<string | null>(null);
  useEffect(() => {
    userIdRef.current = user?.id ?? null;
  }, [user]);

  const ingestSession = useCallback(
    (next: PublicUser, warning: string | null, version: string | null) => {
      setUser(next);
      setResearchWarning(warning);
      setResearchWarningVersion(version);
      setWarningPending(false);
      setWarningError(null);
      applyDocumentTheme(next.theme);
      setStatus("ready");
    },
    [],
  );

  const refresh = useCallback(async () => {
    try {
      const result = await me();
      ingestSession(
        result.user,
        result.research_warning,
        result.research_warning_version,
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setCsrfToken(null);
        setUser(null);
        setResearchWarning(null);
        setResearchWarningVersion(null);
        setWarningPending(false);
        setWarningError(null);
        setStatus("anonymous");
        return;
      }
      throw error;
    }
  }, [ingestSession]);

  useEffect(() => {
    applyDocumentTheme("light");
    void refresh().catch(() => {
      // Unreachable backend on first paint: stay anonymous; the login form
      // surfaces real endpoint errors on submit.
      setStatus((current) => (current === "loading" ? "anonymous" : current));
    });
  }, [refresh]);

  const login = useCallback(
    async (username: string, password: string, role: Role) => {
      const result = await apiLogin(username, password, role);
      ingestSession(
        result.user,
        result.research_warning,
        result.research_warning_version,
      );
    },
    [ingestSession],
  );

  const logout = useCallback(async () => {
    // Fire the server revocation with the current token, then clear local
    // state immediately: a navigation racing the round trip must never
    // resurrect the previous identity or acknowledgement.
    const revocation = apiLogout().catch(() => undefined);
    setUser(null);
    setResearchWarning(null);
    setResearchWarningVersion(null);
    setWarningPending(false);
    setWarningError(null);
    applyDocumentTheme("light");
    setStatus("anonymous");
    await revocation;
  }, []);

  const sessionExpired = useCallback(() => {
    // Keep an unsaved editor mounted so the same physician can reauthenticate
    // in another tab and retry the pending write.
    if (hasUnsavedChanges()) return;
    setCsrfToken(null);
    setUser(null);
    setResearchWarning(null);
    setResearchWarningVersion(null);
    setWarningPending(false);
    setWarningError(null);
    applyDocumentTheme("light");
    setStatus("anonymous");
  }, []);

  const acknowledgeWarning = useCallback(async () => {
    const ackUserId = user?.id ?? null;
    const displayedVersion = researchWarningVersion;
    // No-op when the gate is closed or a submission is already in flight:
    // the button disables while pending, and this covers keyboard
    // double-submit before React re-renders.
    if (
      ackUserId === null ||
      researchWarning === null ||
      displayedVersion === null ||
      warningPending
    ) {
      return;
    }
    setWarningPending(true);
    setWarningError(null);
    try {
      const result = await apiAcknowledgeWarning(displayedVersion);
      // A sign-out or second login racing the round trip owns the new
      // session; never apply this user's committed response to it.
      if (userIdRef.current !== ackUserId) {
        return;
      }
      setResearchWarning(result.research_warning);
      setResearchWarningVersion(result.research_warning_version);
    } catch (error) {
      if (userIdRef.current !== ackUserId) {
        return;
      }
      if (
        error instanceof ApiError &&
        error.code === "RESEARCH_WARNING_CHANGED"
      ) {
        // Stale display: the wording changed while the dialog was open. Show
        // the current text/version; nothing was acknowledged.
        setResearchWarning(error.researchWarning);
        setResearchWarningVersion(error.researchWarningVersion);
        if (error.researchWarning === null) {
          // Stale version submitted while nothing is pending (crafted-request
          // edge): the gate is already closed, so leave no error against it.
          return;
        }
      }
      if (error instanceof ApiError) {
        setWarningError(error.message);
      } else {
        setWarningError("Could not save acknowledgement. Check the connection and retry.");
      }
    } finally {
      if (userIdRef.current === ackUserId) {
        setWarningPending(false);
      }
    }
  }, [user, researchWarning, researchWarningVersion, warningPending]);

  const applyTheme = useCallback(
    async (theme: Theme) => {
      const updated = await apiSetTheme(theme);
      setUser(updated);
      applyDocumentTheme(updated.theme);
    },
    [],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      researchWarning,
      researchWarningVersion,
      warningAcked: researchWarning === null,
      warningPending,
      warningError,
      login,
      logout,
      refresh,
      acknowledgeWarning,
      applyTheme,
      sessionExpired,
    }),
    [
      status,
      user,
      researchWarning,
      researchWarningVersion,
      warningPending,
      warningError,
      login,
      logout,
      refresh,
      acknowledgeWarning,
      applyTheme,
      sessionExpired,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
