import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext.jsx";
import { apiRequest } from "../../lib/api.js";
import { resolveBrowserPlaybackSessionRoot } from "../../lib/browserPlayback.js";


export function usePlaybackAwareLogout({ onBeforeLogout } = {}) {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [workerChoice, setWorkerChoice] = useState(null);
  const [pendingChoice, setPendingChoice] = useState("");
  const [error, setError] = useState("");

  const completeLogout = useCallback(async () => {
    onBeforeLogout?.();
    const confirmed = await logout();
    if (confirmed) {
      navigate("/login", { replace: true });
    }
    return confirmed;
  }, [logout, navigate, onBeforeLogout]);

  const requestLogout = useCallback(async () => {
    setError("");
    try {
      const sessionRoot = resolveBrowserPlaybackSessionRoot();
      const activeSession = await apiRequest(`${sessionRoot}/active`);
      if (!activeSession?.session_id) {
        await completeLogout();
        return;
      }
      let movieTitle = "This movie";
      try {
        const itemPayload = await apiRequest(
          `/api/library/item/${encodeURIComponent(activeSession.media_item_id)}`,
        );
        if (typeof itemPayload?.title === "string" && itemPayload.title.trim()) {
          movieTitle = itemPayload.title.trim();
        }
      } catch {
        // The generic title keeps logout available if the optional title lookup fails.
      }
      setWorkerChoice({
        movieTitle,
        sessionId: String(activeSession.session_id),
        stopUrl: typeof activeSession.stop_url === "string" ? activeSession.stop_url : "",
        sessionRoot,
      });
    } catch {
      await completeLogout();
    }
  }, [completeLogout]);

  const closeWorkerChoice = useCallback(() => {
    if (pendingChoice) return;
    setWorkerChoice(null);
    setError("");
  }, [pendingChoice]);

  const keepPreparing = useCallback(async () => {
    if (!workerChoice || pendingChoice) return;
    setPendingChoice("keep");
    setError("");
    try {
      const confirmed = await completeLogout();
      if (!confirmed) setWorkerChoice((current) => current || workerChoice);
    } catch (requestError) {
      setWorkerChoice((current) => current || workerChoice);
      setError(requestError.message || "Failed to log out");
    } finally {
      setPendingChoice("");
    }
  }, [completeLogout, pendingChoice, workerChoice]);

  const terminateAndLogout = useCallback(async () => {
    if (!workerChoice?.sessionId || pendingChoice) return;
    setPendingChoice("terminate");
    setError("");
    const stopUrl = workerChoice.stopUrl
      || `${workerChoice.sessionRoot}/sessions/${encodeURIComponent(workerChoice.sessionId)}/stop`;
    try {
      await apiRequest(stopUrl, { method: "POST" });
    } catch {
      // Explicit sign-out must remain available when worker termination itself fails.
    }
    try {
      const confirmed = await completeLogout();
      if (!confirmed) setWorkerChoice((current) => current || workerChoice);
    } catch (requestError) {
      setWorkerChoice((current) => current || workerChoice);
      setError(requestError.message || "Failed to log out");
    } finally {
      setPendingChoice("");
    }
  }, [completeLogout, pendingChoice, workerChoice]);

  useEffect(() => {
    if (!workerChoice) return undefined;
    function handleKeyDown(event) {
      if (event.key === "Escape") closeWorkerChoice();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [closeWorkerChoice, workerChoice]);

  return {
    closeWorkerChoice,
    error,
    keepPreparing,
    pendingChoice,
    requestLogout,
    terminateAndLogout,
    workerChoice,
  };
}
