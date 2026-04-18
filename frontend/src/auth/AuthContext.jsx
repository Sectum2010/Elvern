import { createContext, useContext, useEffect, useRef, useState } from "react";
import { apiRequest } from "../lib/api";


const AuthContext = createContext(null);
const SESSION_HEARTBEAT_MS = 15000;


export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authNotice, setAuthNotice] = useState("");
  const refreshInFlightRef = useRef(false);
  const userRef = useRef(null);

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
      setUser(payload.user);
      userRef.current = payload.user;
      if (notifyOnFailure) {
        setAuthNotice("");
      }
      return payload.user;
    } catch (error) {
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
        setUser(null);
        userRef.current = null;
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
        setUser(null);
        userRef.current = null;
        return false;
      }
      return Boolean(userRef.current);
    }
  }

  async function login(credentials) {
    setAuthNotice("");
    await apiRequest("/api/auth/login", {
      method: "POST",
      data: credentials,
    });
    return refreshAuth();
  }

  async function logout() {
    try {
      await apiRequest("/api/auth/logout", { method: "POST" });
    } finally {
      setAuthNotice("");
      setUser(null);
      userRef.current = null;
    }
  }

  function clearAuthNotice() {
    setAuthNotice("");
  }

  useEffect(() => {
    refreshAuth();
  }, []);

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
