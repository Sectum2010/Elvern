import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { PasswordInput } from "../components/PasswordInput";
import { isMaintenanceModeError, MAINTENANCE_MODE_MESSAGE } from "../lib/api";

const VIEWPORT_SYNC_API_KEY = "__elvernRequestViewportNormalization";

function clearStaleInteractionState() {
  if (typeof document === "undefined") {
    return;
  }
  document.body?.style.removeProperty("overflow");
  document.body?.style.removeProperty("pointer-events");
  document.body?.removeAttribute("inert");
  document.documentElement?.removeAttribute("inert");
}

function isEditableElement(element) {
  if (!(element instanceof HTMLElement)) {
    return false;
  }
  return element.matches("input, textarea, select, [contenteditable='true']");
}

export function LoginPage() {
  const { user, login, loading, authNotice, clearAuthNotice } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const maintenanceModeNotice = authNotice === MAINTENANCE_MODE_MESSAGE ? authNotice : "";
  const inlineAuthNotice = maintenanceModeNotice ? "" : authNotice;

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }

    clearStaleInteractionState();

    window.scrollTo(0, 0);
    const requestViewportNormalization = window[VIEWPORT_SYNC_API_KEY];
    if (typeof requestViewportNormalization === "function") {
      requestViewportNormalization({ resetViewport: !isEditableElement(document.activeElement) });
    }

    const settleTimer = window.setTimeout(() => {
      if (isEditableElement(document.activeElement)) {
        return;
      }
      window.scrollTo(0, 0);
      if (typeof requestViewportNormalization === "function") {
        requestViewportNormalization({ resetViewport: true });
      }
    }, 180);

    return () => {
      window.clearTimeout(settleTimer);
    };
  }, []);

  if (!loading && user) {
    return <Navigate to="/library" replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setPending(true);
    setError("");
    clearAuthNotice();
    try {
      const payload = await login({
        username: username.trim(),
        password,
      });
      if (payload?.session === "pending_totp") {
        sessionStorage.setItem("elvern_totp_challenge", payload.challenge_token || "");
        sessionStorage.setItem(
          "elvern_totp_expires",
          String(Date.now() + Number(payload.expires_in_seconds || 300) * 1000),
        );
        navigate("/login/totp", { replace: true });
        return;
      }
      if (payload?.session === "ok" && payload.totp_setup_required) {
        navigate("/setup/totp", { replace: true });
      }
    } catch (requestError) {
      if (isMaintenanceModeError(requestError)) {
        setError("");
        return;
      }
      setError(requestError.message || "Login failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <p className="eyebrow">Private media app</p>
        <h1>Elvern</h1>
        <p className="login-copy">
          Sign in with your own family account before browsing the library.
        </p>

        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            Username
            <input
              autoComplete="username"
              name="username"
              onChange={(event) => {
                if (inlineAuthNotice) {
                  clearAuthNotice();
                }
                setUsername(event.target.value);
              }}
              placeholder="username"
              required
              type="text"
              value={username}
            />
          </label>

          <label>
            Password
            <PasswordInput
              autoComplete="current-password"
              name="password"
              onChange={(event) => {
                if (inlineAuthNotice) {
                  clearAuthNotice();
                }
                setPassword(event.target.value);
              }}
              required
              value={password}
            />
          </label>

          {inlineAuthNotice ? <p className="form-error">{inlineAuthNotice}</p> : null}
          {error ? <p className="form-error">{error}</p> : null}

          <button className="primary-button" disabled={pending} type="submit">
            {pending ? "Signing in..." : "Sign in"}
          </button>
          <div className="login-links">
            <Link to="/new-user">New user?</Link>
            <Link to="/forgot-password">Forgot password?</Link>
          </div>
        </form>
      </div>
      {maintenanceModeNotice ? (
        <p className="login-maintenance-notice">{maintenanceModeNotice}</p>
      ) : null}
    </div>
  );
}
