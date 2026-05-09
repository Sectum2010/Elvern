import { useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";


export function TotpChallengePage() {
  const navigate = useNavigate();
  const { user, refreshAuth } = useAuth();
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());

  const challengeToken = sessionStorage.getItem("elvern_totp_challenge") || "";
  const expiresAt = Number(sessionStorage.getItem("elvern_totp_expires") || 0);
  const remainingSeconds = Math.max(0, Math.floor((expiresAt - now) / 1000));
  const countdown = useMemo(() => {
    const minutes = Math.floor(remainingSeconds / 60);
    const seconds = String(remainingSeconds % 60).padStart(2, "0");
    return `${minutes}:${seconds}`;
  }, [remainingSeconds]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  if (user) {
    return <Navigate to="/library" replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      await apiRequest("/api/auth/login/totp", {
        method: "POST",
        data: { challenge_token: challengeToken, code },
      });
      sessionStorage.removeItem("elvern_totp_challenge");
      sessionStorage.removeItem("elvern_totp_expires");
      await refreshAuth();
      navigate("/library", { replace: true });
    } catch (requestError) {
      setError(requestError.message || "Two-factor verification failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <p className="eyebrow">ELVERN</p>
        <h1>Two-factor authentication</h1>
        <p className="login-copy">Enter the 6-digit code from your authenticator app.</p>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            {useRecovery ? "Recovery code" : "Authenticator code"}
            <input
              autoComplete="one-time-code"
              inputMode={useRecovery ? "text" : "numeric"}
              maxLength={useRecovery ? 24 : 6}
              onChange={(event) => setCode(event.target.value)}
              placeholder={useRecovery ? "elvn-xxxx-xxxx-xxxx" : "000000"}
              required
              type="text"
              value={code}
            />
          </label>
          <p className="page-subnote">Code expires in {countdown}</p>
          {error ? <p className="form-error">{error}</p> : null}
          <button className="primary-button login-button" disabled={pending || !challengeToken} type="submit">
            {pending ? "Verifying..." : (useRecovery ? "Use recovery code" : "Verify")}
          </button>
          <button className="totp-text-link totp-challenge-recovery-toggle" onClick={() => setUseRecovery((current) => !current)} type="button">
            {useRecovery ? "Use authenticator code" : "Use a recovery code"}
          </button>
        </form>
      </div>
    </div>
  );
}
