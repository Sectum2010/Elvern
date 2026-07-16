import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  apiRequest,
  isMaintenanceModeError,
  MAINTENANCE_MODE_BLOCKED_EVENT,
  MAINTENANCE_MODE_MESSAGE,
} from "../lib/api";
import { clearProtectedQueryCache } from "../lib/queryClient";


const AuthContext = createContext(null);
const SESSION_HEARTBEAT_MS = 15000;


function getProtectedCacheIdentity(user) {
  if (!user) {
    return "";
  }
  return JSON.stringify({
    id: user.id ?? null,
    role: user.role ?? "",
    assistantBetaEnabled: Boolean(user.assistant_beta_enabled),
  });
}


export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authNotice, setAuthNotice] = useState("");
  const refreshInFlightRef = useRef(false);
  const userRef = useRef(null);

  const applyAuthenticatedUser = useCallback((nextUser) => {
    if (getProtectedCacheIdentity(userRef.current) !== getProtectedCacheIdentity(nextUser)) {
      clearProtectedQueryCache();
    }
    userRef.current = nextUser;
    setUser(nextUser);
  }, []);

  const endSessionWithNotice = useCallback((message) => {
    setAuthNotice(message);
    applyAuthenticatedUser(null);
    setLoading(false);
  }, [applyAuthenticatedUser]);

  useEffect(() => {
    userRef.current = user;
  }, [user]);

  async function refreshAuth({ notifyOnFailure = false } = {}) {
    if (refreshInFlightRef.current) {
      return user;
    }
    refreshInFlightRef.current = true;
    try {
      const payload = await apiRequest("/api/auth/me");
      applyAuthenticatedUser(payload.user);
      if (notifyOnFailure) {
        setAuthNotice("");
      }
      return payload.user;
    } catch (error) {
      const maintenanceModeBlock = isMaintenanceModeError(error);
      if (maintenanceModeBlock) {
        endSessionWithNotice(MAINTENANCE_MODE_MESSAGE);
        return null;
      }
      const authFailure = error.status === 401 || error.status === 403;
      if (notifyOnFailure) {
        if (error.status === 403) {
          setAuthNotice(error.message || "This account has been disabled");
        } else if (error.status === 401) {
          setAuthNotice("Your session has ended. Sign in again.");
        }
      } else if (!authFailure) {
        console.error("Failed to load session", error);
      }
      if (authFailure) {
        applyAuthenticatedUser(null);
        return null;
      }
      return userRef.current;
    } finally {
      refreshInFlightRef.current = false;
      setLoading(false);
    }
  }

  async function heartbeatAuth({ notifyOnFailure = false } = {}) {
    try {
      await apiRequest("/api/auth/heartbeat", {
        method: "POST",
      });
      if (notifyOnFailure) {
        setAuthNotice("");
      }
      return true;
    } catch (error) {
      const maintenanceModeBlock = isMaintenanceModeError(error);
      if (maintenanceModeBlock) {
        endSessionWithNotice(MAINTENANCE_MODE_MESSAGE);
        return false;
      }
      const authFailure = error.status === 401 || error.status === 403;
      if (notifyOnFailure) {
        if (error.status === 403) {
          setAuthNotice(error.message || "This account has been disabled");
        } else if (error.status === 401) {
          setAuthNotice("Your session has ended. Sign in again.");
        }
      } else if (!authFailure) {
        console.error("Failed to send session heartbeat", error);
      }
      if (authFailure) {
        applyAuthenticatedUser(null);
        return false;
      }
      return Boolean(userRef.current);
    }
  }

  async function login(credentials) {
    setAuthNotice("");
    let payload;
    try {
      payload = await apiRequest("/api/auth/login", {
        method: "POST",
        data: credentials,
      });
    } catch (error) {
      if (isMaintenanceModeError(error)) {
        endSessionWithNotice(MAINTENANCE_MODE_MESSAGE);
      }
      throw error;
    }
    if (payload?.session === "pending_totp") {
      return payload;
    }
    if (payload?.user) {
      applyAuthenticatedUser(payload.user);
      setLoading(false);
      return payload;
    }
    return refreshAuth();
  }

  async function logout() {
    clearProtectedQueryCache();
    try {
      await apiRequest("/api/auth/logout", { method: "POST" });
    } finally {
      setAuthNotice("");
      applyAuthenticatedUser(null);
    }
  }

  function clearAuthNotice() {
    setAuthNotice("");
  }

  useEffect(() => {
    refreshAuth();
  }, []);

  useEffect(() => {
    function handleMaintenanceModeBlocked(event) {
      const message = typeof event.detail?.message === "string"
        ? event.detail.message
        : MAINTENANCE_MODE_MESSAGE;
      endSessionWithNotice(message === MAINTENANCE_MODE_MESSAGE ? message : MAINTENANCE_MODE_MESSAGE);
    }

    window.addEventListener(MAINTENANCE_MODE_BLOCKED_EVENT, handleMaintenanceModeBlocked);
    return () => {
      window.removeEventListener(MAINTENANCE_MODE_BLOCKED_EVENT, handleMaintenanceModeBlocked);
    };
  }, [endSessionWithNotice]);

  useEffect(() => {
    if (!user) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      heartbeatAuth({ notifyOnFailure: true });
    }, SESSION_HEARTBEAT_MS);

    function handleVisibilityChange() {
      if (document.visibilityState === "visible") {
        refreshAuth({ notifyOnFailure: true });
      }
    }

    function handleWindowFocus() {
      refreshAuth({ notifyOnFailure: true });
    }

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleWindowFocus);

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleWindowFocus);
    };
  }, [user]);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        logout,
        refreshAuth,
        authNotice,
        clearAuthNotice,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}


export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
