import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const VIEWPORT_SYNC_API_KEY = "__elvernRequestViewportNormalization";

export function LoginPage() {
  const { user, login, loading, authNotice, clearAuthNotice } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }

    const activeElement = document.activeElement;
    if (activeElement instanceof HTMLElement && typeof activeElement.blur === "function") {
      activeElement.blur();
    }

    window.scrollTo(0, 0);
    const requestViewportNormalization = window[VIEWPORT_SYNC_API_KEY];
    if (typeof requestViewportNormalization === "function") {
      requestViewportNormalization({ resetViewport: true });
    }

    const settleTimer = window.setTimeout(() => {
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
      await login({
        username: username.trim(),
        password,
      });
    } catch (requestError) {
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
          Sign in with your own family account before browsing the library. Elvern is private by default, meant to stay inside Tailscale, and still requires app auth for every user.
        </p>

        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            Username
            <input
              autoComplete="username"
              name="username"
              onChange={(event) => {
                if (authNotice) {
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
            <input
              autoComplete="current-password"
              name="password"
              onChange={(event) => {
                if (authNotice) {
                  clearAuthNotice();
                }
                setPassword(event.target.value);
              }}
              required
              type="password"
              value={password}
            />
          </label>

          {authNotice ? <p className="form-error">{authNotice}</p> : null}
          {error ? <p className="form-error">{error}</p> : null}

          <button className="primary-button" disabled={pending} type="submit">
            {pending ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
