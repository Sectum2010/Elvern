import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  AUTH_REVALIDATION_REQUESTED_EVENT,
  apiRequest,
  isMaintenanceModeError,
  isTransientNetworkError,
  MAINTENANCE_MODE_BLOCKED_EVENT,
  MAINTENANCE_MODE_MESSAGE,
} from "../lib/api";
import { clearProtectedQueryCache } from "../lib/queryClient";
import { prepareAuthViewportExit } from "../lib/authViewportNavigation.js";
import { PAGE_RESUME_EVENT } from "../lib/pageResume.js";
import { clearControlCenterSessionState } from "../lib/controlCenterSession.js";
import {
  clearPendingLogoutMarker,
  createPendingLogoutMarker,
  PENDING_LOGOUT_STORAGE_KEY,
  readPendingLogoutMarker,
  writePendingLogoutMarker,
} from "../lib/pendingLogout.js";
import { LogoutPendingView } from "../components/LogoutPendingView.jsx";
import { wakePlaybackDiagnosticsRecovery } from "../lib/playbackDiagnostics/workerClient.js";


const AuthContext = createContext(null);
const SESSION_HEARTBEAT_MS = 15000;
const LOGOUT_RETRY_DELAYS_MS = Object.freeze([0, 400, 1200, 2800]);


function getProtectedCacheIdentity(user) {
  if (!user) {
    return "";
  }
  const rawRole = String(user.role || "").trim().toLowerCase();
  const role = rawRole === "user" || rawRole === "standard" || rawRole === "standard-user"
    ? "standard_user"
    : rawRole === "administrator"
      ? "admin"
      : rawRole;
  return JSON.stringify({
    id: String(user.id ?? ""),
    role,
    assistantBetaEnabled: Boolean(user.assistant_beta_enabled),
    ageCredential: Number(user.age_credential ?? 18),
  });
}


export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authNotice, setAuthNotice] = useState("");
  const [logoutPending, setLogoutPending] = useState(() => Boolean(readPendingLogoutMarker()));
  const [logoutPendingError, setLogoutPendingError] = useState("");
  const [logoutRetrying, setLogoutRetrying] = useState(false);
  const refreshInFlightRef = useRef(false);
  const logoutInFlightRef = useRef(null);
  const userRef = useRef(null);

  const applyAuthenticatedUser = useCallback((nextUser) => {
    const previousIdentity = getProtectedCacheIdentity(userRef.current);
    const nextIdentity = getProtectedCacheIdentity(nextUser);
    if (previousIdentity !== nextIdentity) {
      clearProtectedQueryCache();
      if (previousIdentity) {
        clearControlCenterSessionState();
      }
    }
    userRef.current = nextUser;
    setUser(nextUser);
    if (nextUser?.playback_diagnostics_enabled === true) {
      wakePlaybackDiagnosticsRecovery(nextUser.id, { authenticationRestored: true });
    }
  }, []);

  const endSessionWithNotice = useCallback((message) => {
    setAuthNotice(message);
    applyAuthenticatedUser(null);
    setLoading(false);
  }, [applyAuthenticatedUser]);

  useEffect(() => {
    userRef.current = user;
  }, [user]);

  const refreshAuth = useCallback(async ({ notifyOnFailure = false } = {}) => {
    if (refreshInFlightRef.current) {
      return userRef.current;
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
  }, [applyAuthenticatedUser, endSessionWithNotice]);

  const heartbeatAuth = useCallback(async ({ notifyOnFailure = false } = {}) => {
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
  }, [applyAuthenticatedUser, endSessionWithNotice]);

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
      clearControlCenterSessionState();
      return payload;
    }
    if (payload?.user) {
      clearControlCenterSessionState();
      await prepareAuthViewportExit();
      applyAuthenticatedUser(payload.user);
      setLoading(false);
      return payload;
    }
    return refreshAuth();
  }

  const finishConfirmedLogout = useCallback(() => {
    clearPendingLogoutMarker();
    clearProtectedQueryCache();
    clearControlCenterSessionState();
    setAuthNotice("");
    applyAuthenticatedUser(null);
    setLogoutPending(false);
    setLogoutPendingError("");
    setLogoutRetrying(false);
  }, [applyAuthenticatedUser]);

  const confirmPendingLogout = useCallback(async () => {
    if (logoutInFlightRef.current) {
      return logoutInFlightRef.current;
    }
    const attempt = (async () => {
      setLogoutPending(true);
      setLogoutPendingError("");
      setLogoutRetrying(true);
      let lastError = null;
      for (const delayMs of LOGOUT_RETRY_DELAYS_MS) {
        if (delayMs > 0) {
          await new Promise((resolve) => window.setTimeout(resolve, delayMs));
        }
        try {
          await apiRequest("/api/auth/logout", { method: "POST" });
          finishConfirmedLogout();
          return true;
        } catch (error) {
          if (error?.status === 401 || error?.status === 403) {
            finishConfirmedLogout();
            return true;
          }
          lastError = error;
          if (!isTransientNetworkError(error)) {
            break;
          }
        }
      }
      setLogoutRetrying(false);
      setLogoutPendingError(
        lastError?.message || "Elvern could not confirm sign out. Check your connection and retry.",
      );
      return false;
    })();
    logoutInFlightRef.current = attempt;
    try {
      return await attempt;
    } finally {
      logoutInFlightRef.current = null;
    }
  }, [finishConfirmedLogout]);

  const logout = useCallback(async () => {
    if (!readPendingLogoutMarker()) {
      const marker = createPendingLogoutMarker(userRef.current);
      if (!marker.userId || !marker.sessionId) {
        throw new Error("Elvern could not identify the active session for logout.");
      }
      writePendingLogoutMarker(marker);
    }
    clearProtectedQueryCache();
    clearControlCenterSessionState();
    setAuthNotice("");
    applyAuthenticatedUser(null);
    setLogoutPending(true);
    return confirmPendingLogout();
  }, [applyAuthenticatedUser, confirmPendingLogout]);

  function clearAuthNotice() {
    setAuthNotice("");
  }

  useEffect(() => {
    if (readPendingLogoutMarker()) {
      setLoading(false);
      void confirmPendingLogout();
      return;
    }
    void refreshAuth();
  }, [confirmPendingLogout, refreshAuth]);

  useEffect(() => {
    function handleStorage(event) {
      if (event.key !== PENDING_LOGOUT_STORAGE_KEY || event.newValue !== null) {
        return;
      }
      finishConfirmedLogout();
    }

    function handleOnline() {
      if (readPendingLogoutMarker()) {
        void confirmPendingLogout();
      }
    }

    window.addEventListener("storage", handleStorage);
    window.addEventListener("online", handleOnline);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener("online", handleOnline);
    };
  }, [confirmPendingLogout, finishConfirmedLogout]);

  useEffect(() => {
    function handleAuthRevalidationRequested() {
      void refreshAuth({ notifyOnFailure: true });
    }

    window.addEventListener(AUTH_REVALIDATION_REQUESTED_EVENT, handleAuthRevalidationRequested);
    return () => {
      window.removeEventListener(AUTH_REVALIDATION_REQUESTED_EVENT, handleAuthRevalidationRequested);
    };
  }, [refreshAuth]);

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
      void heartbeatAuth({ notifyOnFailure: true });
    }, SESSION_HEARTBEAT_MS);

    function handlePageResume() {
      void refreshAuth({ notifyOnFailure: true });
    }

    window.addEventListener(PAGE_RESUME_EVENT, handlePageResume);

    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener(PAGE_RESUME_EVENT, handlePageResume);
    };
  }, [heartbeatAuth, refreshAuth, user]);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        logout,
        logoutPending,
        retryLogout: confirmPendingLogout,
        refreshAuth,
        authNotice,
        clearAuthNotice,
      }}
    >
      {logoutPending ? (
        <LogoutPendingView
          error={logoutPendingError}
          onRetry={confirmPendingLogout}
          retrying={logoutRetrying}
        />
      ) : children}
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
