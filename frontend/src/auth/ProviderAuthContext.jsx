import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { ProviderReconnectModal } from "../components/ProviderReconnectModal";
import { apiRequest } from "../lib/api";
import {
  buildProviderAuthReturnPath,
  getGoogleDriveStatusFromLocation,
  getProviderAuthRequirementFromStatus,
  PROVIDER_RECONNECT_CANCELLED_MESSAGE,
  PROVIDER_RECONNECT_PENDING_RESET_MS,
  shouldShowProviderAuthBootstrapModal,
  shouldResetProviderReconnectPending,
  startGoogleDriveReconnect,
} from "../lib/providerAuth";
import { useAuth } from "./AuthContext";


const ProviderAuthContext = createContext(null);


export function ProviderAuthProvider({ children }) {
  const { user } = useAuth();
  const location = useLocation();
  const [requirement, setRequirement] = useState(null);
  const [dismissedThisSession, setDismissedThisSession] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [reconnectPending, setReconnectPending] = useState(false);
  const [modalError, setModalError] = useState("");
  const laterContinuationRef = useRef(null);
  const requirementRef = useRef(null);
  const reconnectPendingRef = useRef(false);

  useEffect(() => {
    requirementRef.current = requirement;
  }, [requirement]);

  useEffect(() => {
    reconnectPendingRef.current = reconnectPending;
  }, [reconnectPending]);

  function resetReconnectPendingAfterReturn() {
    if (typeof window === "undefined") {
      return;
    }
    const googleDriveStatus = getGoogleDriveStatusFromLocation(window.location);
    if (googleDriveStatus === "connected") {
      reconnectPendingRef.current = false;
      setReconnectPending(false);
      setModalError("");
      return;
    }
    const visibilityState = typeof document === "undefined"
      ? "visible"
      : document.visibilityState;
    if (!shouldResetProviderReconnectPending({
      reconnectPending: reconnectPendingRef.current,
      googleDriveStatus,
      visibilityState,
    })) {
      return;
    }
    reconnectPendingRef.current = false;
    setReconnectPending(false);
    if (requirementRef.current) {
      setModalOpen(true);
      setModalError(PROVIDER_RECONNECT_CANCELLED_MESSAGE);
    }
  }

  async function refreshProviderAuthStatus({ signal, ignoreDismissal = false } = {}) {
    if (!user) {
      setRequirement(null);
      setDismissedThisSession(false);
      setModalOpen(false);
      return null;
    }
    try {
      const payload = await apiRequest("/api/cloud-libraries/google/provider-auth-status", { signal });
      const nextRequirement = getProviderAuthRequirementFromStatus(payload);
      setRequirement(nextRequirement);
      requirementRef.current = nextRequirement;
      if (!nextRequirement) {
        setDismissedThisSession(false);
        setModalOpen(false);
        setModalError("");
        return null;
      }
      if (shouldShowProviderAuthBootstrapModal({
        requirement: nextRequirement,
        dismissed: ignoreDismissal ? false : dismissedThisSession,
      })) {
        laterContinuationRef.current = null;
        setModalError("");
        setModalOpen(true);
      }
      return nextRequirement;
    } catch (requestError) {
      if (requestError?.name !== "AbortError") {
        console.error("Failed to load Google Drive provider auth status", requestError);
      }
      return requirementRef.current;
    }
  }

  function showProviderAuthPrompt(nextRequirement = requirementRef.current, { onLater = null } = {}) {
    if (!nextRequirement) {
      return false;
    }
    setRequirement(nextRequirement);
    requirementRef.current = nextRequirement;
    laterContinuationRef.current = typeof onLater === "function" ? onLater : null;
    setModalError("");
    setModalOpen(true);
    return true;
  }

  function dismissProviderAuthPrompt() {
    const continuation = laterContinuationRef.current;
    laterContinuationRef.current = null;
    setDismissedThisSession(true);
    setModalOpen(false);
    setModalError("");
    if (continuation) {
      void continuation();
    }
  }

  async function startProviderReconnect() {
    const currentRequirement = requirementRef.current;
    if (reconnectPending || !currentRequirement || currentRequirement.allowReconnect === false) {
      return;
    }
    reconnectPendingRef.current = true;
    setReconnectPending(true);
    setModalError("");
    try {
      await startGoogleDriveReconnect({
        returnPath: buildProviderAuthReturnPath(window.location),
      });
    } catch (requestError) {
      setModalError(requestError.message || "Failed to start Google Drive reconnect.");
      reconnectPendingRef.current = false;
      setReconnectPending(false);
    }
  }

  async function handleReconnect() {
    await startProviderReconnect();
  }

  useEffect(() => {
    const controller = new AbortController();
    setDismissedThisSession(false);
    void refreshProviderAuthStatus({ signal: controller.signal, ignoreDismissal: true });
    return () => {
      controller.abort();
    };
  }, [user?.id]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }
    const handlePageReturn = () => {
      resetReconnectPendingAfterReturn();
    };
    const handleVisibilityChange = () => {
      if (typeof document === "undefined" || document.visibilityState === "visible") {
        resetReconnectPendingAfterReturn();
      }
    };
    window.addEventListener("pageshow", handlePageReturn);
    window.addEventListener("focus", handlePageReturn);
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }
    return () => {
      window.removeEventListener("pageshow", handlePageReturn);
      window.removeEventListener("focus", handlePageReturn);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
      }
    };
  }, []);

  useEffect(() => {
    if (!reconnectPending || typeof window === "undefined") {
      return undefined;
    }
    const resetTimer = window.setTimeout(() => {
      resetReconnectPendingAfterReturn();
    }, PROVIDER_RECONNECT_PENDING_RESET_MS);
    return () => {
      window.clearTimeout(resetTimer);
    };
  }, [reconnectPending]);

  useEffect(() => {
    if (!requirement) {
      return;
    }
    if (shouldShowProviderAuthBootstrapModal({ requirement, dismissed: dismissedThisSession })) {
      laterContinuationRef.current = null;
      setModalOpen(true);
    }
  }, [dismissedThisSession, requirement]);

  const contextValue = {
    providerAuthRequirement: requirement,
    providerAuthDismissedThisSession: dismissedThisSession,
    providerAuthReconnectPending: reconnectPending,
    refreshProviderAuthStatus,
    showProviderAuthPrompt,
    dismissProviderAuthPrompt,
    startProviderReconnect,
  };

  return (
    <ProviderAuthContext.Provider value={contextValue}>
      {children}
      <ProviderReconnectModal
        allowReconnect={requirement?.allowReconnect !== false}
        message={requirement?.message || ""}
        onClose={dismissProviderAuthPrompt}
        onReconnect={handleReconnect}
        onSecondary={dismissProviderAuthPrompt}
        open={modalOpen && Boolean(requirement)}
        reconnectLabel="Reconnect"
        reconnectPending={reconnectPending}
        secondaryLabel="Later"
        title={requirement?.title || "Google Drive connection expired"}
        errorMessage={modalError}
      />
    </ProviderAuthContext.Provider>
  );
}


export function useProviderAuth() {
  const context = useContext(ProviderAuthContext);
  if (!context) {
    throw new Error("useProviderAuth must be used inside ProviderAuthProvider");
  }
  return context;
}
