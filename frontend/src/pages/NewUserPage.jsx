import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { NonLoginSecretInput } from "../components/NonLoginSecretInput";
import { apiRequest } from "../lib/api";
import { prepareAuthViewportExit, useAuthViewportRedirectReady } from "../lib/authViewportNavigation.js";


export function NewUserPage() {
  const navigate = useNavigate();
  const { user, loading, refreshAuth } = useAuth();
  const [form, setForm] = useState({
    username: "",
    password: "",
    confirm_password: "",
    invite_code: "",
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const authRedirectReady = useAuthViewportRedirectReady(!loading && Boolean(user));

  if (authRedirectReady) {
    return <Navigate to="/library" replace />;
  }

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      await apiRequest("/api/auth/signup", {
        method: "POST",
        data: {
          username: form.username.trim(),
          password: form.password,
          confirm_password: form.confirm_password,
          invite_code: form.invite_code.trim(),
        },
      });
      await prepareAuthViewportExit();
      await refreshAuth();
      navigate("/library", { replace: true });
    } catch (requestError) {
      setForm((current) => ({
        ...current,
        password: "",
        confirm_password: "",
      }));
      setError(requestError.message || "Unable to create account");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <p className="eyebrow">Invite only</p>
        <h1>New user</h1>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            Username
            <input
              autoComplete="off"
              name="signup-username"
              onChange={(event) => updateField("username", event.target.value)}
              required
              type="text"
              value={form.username}
            />
          </label>
          <label>
            Password
            <NonLoginSecretInput
              autoComplete="new-password"
              onChange={(event) => updateField("password", event.target.value)}
              purpose="signup-primary-secret"
              required
              value={form.password}
            />
          </label>
          <label>
            Confirm password
            <NonLoginSecretInput
              autoComplete="new-password"
              onChange={(event) => updateField("confirm_password", event.target.value)}
              purpose="signup-confirm-secret"
              required
              value={form.confirm_password}
            />
          </label>
          <label>
            One-time invite code
            <input
              autoComplete="one-time-code"
              data-1p-ignore="true"
              data-bwignore="true"
              data-lpignore="true"
              name="invite-code"
              onChange={(event) => updateField("invite_code", event.target.value)}
              required
              type="text"
              value={form.invite_code}
            />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
          <button className="primary-button" disabled={pending} type="submit">
            {pending ? "Creating..." : "Create account / sign in"}
          </button>
          <div className="login-links">
            <Link to="/login">Back to sign in</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
