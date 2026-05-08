import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";


const PASSWORD_HELP_SUCCESS = "Request sent. Expect feedback within the next 48 hours.";


export function ForgotPasswordPage() {
  const { user, loading } = useAuth();
  const [username, setUsername] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");

  if (!loading && user) {
    return <Navigate to="/library" replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setPending(true);
    setMessage("");
    try {
      await apiRequest("/api/auth/password-help", {
        method: "POST",
        data: { username: username.trim() },
      });
    } catch {
      // Keep the user-facing result identical.
    } finally {
      setMessage(PASSWORD_HELP_SUCCESS);
      setPending(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <p className="eyebrow">Account help</p>
        <h1>Forgot password?</h1>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            Username
            <input
              autoComplete="username"
              name="username"
              onChange={(event) => setUsername(event.target.value)}
              required
              type="text"
              value={username}
            />
          </label>
          {message ? (
            <p className="form-success" role="status">
              <svg aria-hidden="true" className="form-success__icon" viewBox="0 0 20 20">
                <path d="M4.5 10.4l3.1 3.1 7.9-8.1" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.2" />
              </svg>
              {message}
            </p>
          ) : null}
          <button className="primary-button" disabled={pending} type="submit">
            {pending ? "Sending..." : "Contact admin"}
          </button>
          <div className="login-links">
            <Link to="/login">Back to sign in</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
