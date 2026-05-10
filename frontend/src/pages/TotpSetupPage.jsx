import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";


export function TotpSetupPage() {
  const navigate = useNavigate();
  const { user, refreshAuth } = useAuth();
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState([]);
  const [showManual, setShowManual] = useState(false);

  useEffect(() => {
    if (!user) {
      return;
    }
    let cancelled = false;
    apiRequest("/api/auth/totp/setup", { method: "POST" })
      .then((payload) => {
        if (!cancelled) {
          setSetup(payload);
        }
      })
      .catch((requestError) => setError(requestError.message || "Unable to start 2FA setup"));
    return () => {
      cancelled = true;
    };
  }, [user]);

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  async function handleVerify(event) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const payload = await apiRequest("/api/auth/totp/setup/verify", {
        method: "POST",
        data: { code },
      });
      setRecoveryCodes(payload.recovery_codes || []);
      await refreshAuth();
    } catch (requestError) {
      setError(requestError.message || "Invalid authenticator code");
    } finally {
      setPending(false);
    }
  }

  async function handleSkip() {
    setError("");
    try {
      await apiRequest("/api/auth/totp/skip", { method: "POST" });
      await refreshAuth();
      navigate("/library", { replace: true });
    } catch (requestError) {
      setError(requestError.message || "Unable to skip setup");
    }
  }

  function downloadCodes() {
    const blob = new Blob([recoveryCodes.join("\n") + "\n"], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "elvern-recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="settings-page">
      <section className="settings-card totp-setup-card">
        <div className="totp-setup-card__intro">
          <p className="eyebrow">TWO-FACTOR AUTHENTICATION</p>
          <h1>Secure your admin account</h1>
          <p className="page-subnote">
            Scan the QR code with an authenticator app like Google Authenticator, 1Password, or Apple Passwords.
          </p>
        </div>
        <div className="totp-setup-card__qr-panel">
          <div className="totp-setup-card__qr">
            {setup?.qr_svg ? <div dangerouslySetInnerHTML={{ __html: setup.qr_svg }} /> : <p>Loading QR...</p>}
          </div>
          <button className="totp-text-link totp-skip-link" onClick={handleSkip} type="button">
            Skip for now
          </button>
        </div>
        <form className="admin-inline-form" onSubmit={handleVerify}>
          <h2>1. Scan QR code</h2>
          <p className="page-subnote">2. Enter the 6-digit code from your app.</p>
          <input
            autoComplete="one-time-code"
            inputMode="numeric"
            maxLength={6}
            onChange={(event) => setCode(event.target.value)}
            pattern="[0-9]{6}"
            placeholder="000000"
            required
            type="text"
            value={code}
          />
          <div className="totp-setup-card__actions">
            <button className="ghost-button ghost-button--inline" onClick={() => setShowManual((current) => !current)} type="button">
              {showManual ? "Hide manual code" : "Enter manually"}
            </button>
            <button className="primary-button" disabled={pending || !setup} type="submit">
              {pending ? "Verifying..." : "Verify and enable"}
            </button>
          </div>
          {showManual ? <p className="admin-diagnostic-id-modal__value">{setup?.secret}</p> : null}
          {error ? <p className="action-feedback action-feedback--error">{error}</p> : null}
        </form>
      </section>
      {recoveryCodes.length > 0 ? (
        <div className="browser-resume-modal" role="presentation">
          <div className="browser-resume-modal__card detail-info-modal__card">
            <div className="detail-info-modal__copy">
              <h2>Save your recovery codes</h2>
              <p className="page-subnote">
                These codes can be used once each if you lose your authenticator. Store them somewhere safe.
                They will not be shown again.
              </p>
            </div>
            <pre className="admin-diagnostic-id-modal__value">{recoveryCodes.join("\n")}</pre>
            <div className="browser-resume-modal__actions">
              <button className="ghost-button" onClick={() => navigator.clipboard?.writeText(recoveryCodes.join("\n"))} type="button">
                Copy all
              </button>
              <button className="ghost-button" onClick={downloadCodes} type="button">
                Download as .txt
              </button>
              <button className="primary-button" onClick={() => navigate("/library", { replace: true })} type="button">
                I've saved them
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
