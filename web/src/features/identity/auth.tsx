import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  ApiError,
  me,
  login as apiLogin,
  logout as apiLogout,
  setTheme as apiSetTheme,
  setCsrfToken,
  type PublicUser,
  type Role,
  type Theme,
} from "./api";

const WARNING_ACK_KEY = "xi_warning_ack";

function readAck(): string | null {
  try {
    return sessionStorage.getItem(WARNING_ACK_KEY);
  } catch {
    return null;
  }
}

function writeAck(userId: string | null): void {
  try {
    if (userId === null) {
      sessionStorage.removeItem(WARNING_ACK_KEY);
    } else {
      sessionStorage.setItem(WARNING_ACK_KEY, userId);
    }
  } catch {
    /* acknowledgement still tracked in memory below */
  }
}

interface AuthContextValue {
  status: "loading" | "anonymous" | "ready";
  user: PublicUser | null;
  researchWarning: string | null;
  warningAcked: boolean;
  login: (username: string, password: string, role: Role) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  acknowledgeWarning: () => void;
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
  const [ackedUserId, setAckedUserId] = useState<string | null>(readAck());

  const ingestSession = useCallback((next: PublicUser, warning: string | null) => {
    setUser(next);
    setResearchWarning(warning);
    applyDocumentTheme(next.theme);
    setStatus("ready");
  }, []);

  const refresh = useCallback(async () => {
    try {
      const result = await me();
      ingestSession(result.user, result.research_warning);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setUser(null);
        setResearchWarning(null);
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
      ingestSession(result.user, result.research_warning);
    },
    [ingestSession],
  );

  const logout = useCallback(async () => {
    // Fire the server revocation with the current token, then clear local
    // state immediately: a navigation racing the round trip must never
    // resurrect the previous acknowledgement or identity.
    const revocation = apiLogout().catch(() => undefined);
    writeAck(null);
    setAckedUserId(null);
    setUser(null);
    setResearchWarning(null);
    applyDocumentTheme("light");
    setStatus("anonymous");
    await revocation;
  }, []);

  const sessionExpired = useCallback(() => {
    setCsrfToken(null);
    writeAck(null);
    setAckedUserId(null);
    setUser(null);
    setResearchWarning(null);
    applyDocumentTheme("light");
    setStatus("anonymous");
  }, []);

  const acknowledgeWarning = useCallback(() => {
    if (user) {
      writeAck(user.id);
      setAckedUserId(user.id);
    }
  }, [user]);

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
      warningAcked:
        researchWarning === null ||
        (user !== null && ackedUserId === user.id),
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
      ackedUserId,
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
