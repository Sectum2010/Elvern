import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ProviderReconnectModal } from "../components/ProviderReconnectModal";
import { apiRequest } from "../lib/api";
import { PAGE_RESUME_EVENT } from "../lib/pageResume";
import {
  buildProviderAuthReturnPath,
  clearProviderAuthIntent,
  createProviderAuthOperationId,
  getGoogleDriveStatusFromLocation,
  getProviderAuthIdentity,
  getProviderAuthRequirementFromStatus,
  isProviderAuthReconnectCapable,
  PROVIDER_RECONNECT_CANCELLED_MESSAGE,
  PROVIDER_RECONNECT_PENDING_RESET_MS,
  readProviderAuthIntent,
  saveProviderAuthIntent,
  shouldShowProviderAuthBootstrapModal,
  shouldUseProviderAuthPassiveNotice,
  startGoogleDriveReconnect,
} from "../lib/providerAuth";
import { useAuth } from "./AuthContext";


const ProviderAuthContext = createContext(null);
const IDLE_TRANSACTION = Object.freeze({
  state: "idle",
  message: "",
  candidate: null,
  result: null,
});


function getGoogleDriveMessageFromLocation(currentLocation) {
  const searchParams = new URLSearchParams(currentLocation?.search || "");
  return searchParams.get("googleDriveMessage") || "";
}


function removeGoogleDriveCallbackParams() {
  if (typeof window === "undefined") {
    return;
  }
  const url = new URL(window.location.href);
  const hadCallback = url.searchParams.has("googleDriveStatus")
    || url.searchParams.has("googleDriveMessage");
  if (!hadCallback) {
    return;
  }
  url.searchParams.delete("googleDriveStatus");
  url.searchParams.delete("googleDriveMessage");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}


export function ProviderAuthProvider({ children }) {
  const { user } = useAuth();
  const identity = getProviderAuthIdentity(user);
  const [requirement, setRequirement] = useState(null);
  const [dismissedThisSession, setDismissedThisSession] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalError, setModalError] = useState("");
  const [transaction, setTransaction] = useState(IDLE_TRANSACTION);
  const laterContinuationRef = useRef(null);
  const requirementRef = useRef(null);
  const dismissedThisSessionRef = useRef(false);
  const identityRef = useRef(identity);
  const transactionRef = useRef(IDLE_TRANSACTION);
  const reconcileInFlightRef = useRef(null);
  const boundedCheckTimerRef = useRef(0);
  const lastResumeCheckAtRef = useRef(0);

  const updateTransaction = useCallback((nextTransaction) => {
    transactionRef.current = nextTransaction;
    setTransaction(nextTransaction);
  }, []);

  identityRef.current = identity;

  const refreshProviderAuthStatus = useCallback(async ({ signal, ignoreDismissal = false } = {}) => {
    if (!user) {
      setRequirement(null);
      requirementRef.current = null;
      setDismissedThisSession(false);
      dismissedThisSessionRef.current = false;
      setModalOpen(false);
      return null;
    }
    const requestIdentity = identity;
    try {
      const payload = await apiRequest("/api/cloud-libraries/google/provider-auth-status", { signal });
      if (identityRef.current !== requestIdentity) {
        return null;
      }
      const nextRequirement = getProviderAuthRequirementFromStatus(payload);
      setRequirement(nextRequirement);
      requirementRef.current = nextRequirement;
      if (!nextRequirement) {
        setDismissedThisSession(false);
        dismissedThisSessionRef.current = false;
        setModalOpen(false);
        setModalError("");
        return null;
      }
      if (shouldShowProviderAuthBootstrapModal({
        requirement: nextRequirement,
        dismissed: ignoreDismissal ? false : dismissedThisSessionRef.current,
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
  }, [identity, user]);

  const finishTransaction = useCallback((nextState, message, result = null) => {
    clearProviderAuthIntent();
    if (boundedCheckTimerRef.current) {
      window.clearTimeout(boundedCheckTimerRef.current);
      boundedCheckTimerRef.current = 0;
    }
    updateTransaction({ state: nextState, message, candidate: null, result });
  }, [updateTransaction]);

  const reconcileProviderAuthTransaction = useCallback(async ({ bounded = false } = {}) => {
    if (!user || !identity) {
      return null;
    }
    const intent = readProviderAuthIntent({ identity });
    if (!intent?.operationId) {
      return null;
    }
    if (reconcileInFlightRef.current) {
      return reconcileInFlightRef.current;
    }
    const task = (async () => {
      const operationIdentity = identity;
      const operationId = intent.operationId;
      updateTransaction({ state: "reconciling", message: "", candidate: null, result: null });
      const callbackStatus = getGoogleDriveStatusFromLocation(window.location);
      const callbackMessage = getGoogleDriveMessageFromLocation(window.location);
      if (callbackStatus === "cancelled" || callbackStatus === "error") {
        const message = callbackMessage || PROVIDER_RECONNECT_CANCELLED_MESSAGE;
        finishTransaction(
          callbackStatus === "error" ? "error" : "cancelled_or_incomplete",
          message,
        );
        removeGoogleDriveCallbackParams();
        if (requirementRef.current) {
          setModalOpen(true);
          setModalError(message);
        }
        return transactionRef.current;
      }
      if (callbackStatus === "account_mismatch") {
        try {
          const candidate = await apiRequest("/api/cloud-libraries/google/account-candidate/status", {
            method: "POST",
            data: { operation_id: intent.operationId },
          });
          if (
            identityRef.current !== operationIdentity
            || readProviderAuthIntent({ identity: operationIdentity })?.operationId !== operationId
          ) {
            return transactionRef.current;
          }
          const next = {
            state: "account_mismatch",
            message: callbackMessage,
            candidate,
            result: null,
          };
          updateTransaction(next);
          removeGoogleDriveCallbackParams();
          return next;
        } catch (requestError) {
          if (identityRef.current !== operationIdentity) {
            return transactionRef.current;
          }
          finishTransaction("error", requestError.message || "Google Drive account replacement expired.");
          removeGoogleDriveCallbackParams();
          return transactionRef.current;
        }
      }

      const [providerResult, cloudResult] = await Promise.allSettled([
        apiRequest("/api/cloud-libraries/google/provider-auth-status"),
        apiRequest("/api/cloud-libraries"),
      ]);
      if (
        identityRef.current !== operationIdentity
        || readProviderAuthIntent({ identity: operationIdentity })?.operationId !== operationId
      ) {
        return transactionRef.current;
      }
      if (providerResult.status === "fulfilled") {
        const nextRequirement = getProviderAuthRequirementFromStatus(providerResult.value);
        setRequirement(nextRequirement);
        requirementRef.current = nextRequirement;
      }
      const google = cloudResult.status === "fulfilled" ? cloudResult.value?.google : null;
      const connected = callbackStatus === "connected"
        || Boolean(google?.connected && !google?.reconnect_required);
      if (connected) {
        finishTransaction("connected", callbackMessage || "Google Drive connected.", google);
        removeGoogleDriveCallbackParams();
        return transactionRef.current;
      }
      if (bounded) {
        finishTransaction("cancelled_or_incomplete", PROVIDER_RECONNECT_CANCELLED_MESSAGE);
        if (requirementRef.current) {
          setModalOpen(true);
          setModalError(PROVIDER_RECONNECT_CANCELLED_MESSAGE);
        }
        return transactionRef.current;
      }
      if (!boundedCheckTimerRef.current) {
        boundedCheckTimerRef.current = window.setTimeout(() => {
          boundedCheckTimerRef.current = 0;
          void reconcileProviderAuthTransaction({ bounded: true });
        }, PROVIDER_RECONNECT_PENDING_RESET_MS);
      }
      return transactionRef.current;
    })().finally(() => {
      reconcileInFlightRef.current = null;
    });
    reconcileInFlightRef.current = task;
    return task;
  }, [finishTransaction, identity, updateTransaction, user]);

  function showProviderAuthPrompt(nextRequirement = requirementRef.current, { onLater = null } = {}) {
    if (!nextRequirement) {
      return false;
    }
    setRequirement(nextRequirement);
    requirementRef.current = nextRequirement;
    if (shouldUseProviderAuthPassiveNotice(nextRequirement)) {
      laterContinuationRef.current = null;
      setModalOpen(false);
      setModalError("");
      return false;
    }
    laterContinuationRef.current = typeof onLater === "function" ? onLater : null;
    setModalError("");
    setModalOpen(true);
    return true;
  }

  function dismissProviderAuthPrompt() {
    const continuation = laterContinuationRef.current;
    laterContinuationRef.current = null;
    setDismissedThisSession(true);
    dismissedThisSessionRef.current = true;
    setModalOpen(false);
    setModalError("");
    if (continuation) {
      void continuation();
    }
  }

  async function startProviderReconnect({ allowWithoutRequirement = false } = {}) {
    const currentRequirement = requirementRef.current;
    const currentState = transactionRef.current.state;
    if (
      ["starting", "navigating_external", "reconciling"].includes(currentState)
      || (!allowWithoutRequirement && !currentRequirement)
      || currentRequirement?.allowReconnect === false
      || !user
    ) {
      return null;
    }
    const operationId = createProviderAuthOperationId();
    const intent = {
      provider: "google_drive",
      operationId,
      identity,
      returnPath: buildProviderAuthReturnPath(window.location),
      state: "starting",
    };
    saveProviderAuthIntent(intent);
    updateTransaction({ state: "starting", message: "", candidate: null, result: null });
    setModalError("");
    try {
      const payload = await startGoogleDriveReconnect({
        operationId,
        returnPath: intent.returnPath,
      });
      if (
        identityRef.current !== identity
        || readProviderAuthIntent({ identity })?.operationId !== operationId
      ) {
        return null;
      }
      updateTransaction({ state: "navigating_external", message: "", candidate: null, result: null });
      return payload;
    } catch (requestError) {
      if (identityRef.current !== identity) {
        return null;
      }
      finishTransaction("error", requestError.message || "Failed to start Google Drive reconnect.");
      setModalError(requestError.message || "Failed to start Google Drive reconnect.");
      return null;
    }
  }

  async function cancelAccountReplacement() {
    const intent = readProviderAuthIntent({ identity });
    if (!intent?.operationId || transactionRef.current.state !== "account_mismatch") {
      return;
    }
    await apiRequest("/api/cloud-libraries/google/account-candidate/cancel", {
      method: "POST",
      data: { operation_id: intent.operationId },
    });
    finishTransaction("cancelled_or_incomplete", "Google Drive account replacement cancelled.");
  }

  async function confirmAccountReplacement() {
    const intent = readProviderAuthIntent({ identity });
    if (!intent?.operationId || transactionRef.current.state !== "account_mismatch") {
      return null;
    }
    updateTransaction({ ...transactionRef.current, state: "reconciling" });
    try {
      const result = await apiRequest("/api/cloud-libraries/google/account-candidate/replace", {
        method: "POST",
        data: { operation_id: intent.operationId },
      });
      finishTransaction("connected", "Google Drive account replaced.", result);
      await refreshProviderAuthStatus();
      return result;
    } catch (requestError) {
      updateTransaction({
        ...transactionRef.current,
        state: "account_mismatch",
        message: requestError.message || "Failed to replace the Google Drive account.",
      });
      throw requestError;
    }
  }

  useEffect(() => {
    if (!user || !identity) {
      clearProviderAuthIntent();
      updateTransaction(IDLE_TRANSACTION);
      setRequirement(null);
      requirementRef.current = null;
      setDismissedThisSession(false);
      dismissedThisSessionRef.current = false;
      setModalOpen(false);
      return undefined;
    }
    const controller = new AbortController();
    setDismissedThisSession(false);
    dismissedThisSessionRef.current = false;
    void refreshProviderAuthStatus({ signal: controller.signal, ignoreDismissal: true });
    if (readProviderAuthIntent({ identity })) {
      void reconcileProviderAuthTransaction();
    }
    return () => controller.abort();
  }, [identity, reconcileProviderAuthTransaction, refreshProviderAuthStatus, updateTransaction, user]);

  useEffect(() => {
    if (!user || typeof window === "undefined") {
      return undefined;
    }
    function reconcileAfterResume() {
      if (document.visibilityState === "hidden" || !readProviderAuthIntent({ identity })) {
        return;
      }
      const now = Date.now();
      if (now - lastResumeCheckAtRef.current < 500) {
        return;
      }
      lastResumeCheckAtRef.current = now;
      void reconcileProviderAuthTransaction();
    }
    function handleVisibilityChange() {
      if (document.visibilityState === "visible") {
        reconcileAfterResume();
      }
    }
    window.addEventListener(PAGE_RESUME_EVENT, reconcileAfterResume);
    window.addEventListener("pageshow", reconcileAfterResume);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener(PAGE_RESUME_EVENT, reconcileAfterResume);
      window.removeEventListener("pageshow", reconcileAfterResume);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [identity, reconcileProviderAuthTransaction, user]);

  useEffect(() => () => {
    if (boundedCheckTimerRef.current) {
      window.clearTimeout(boundedCheckTimerRef.current);
    }
  }, []);

  useEffect(() => {
    if (requirement && shouldShowProviderAuthBootstrapModal({ requirement, dismissed: dismissedThisSession })) {
      laterContinuationRef.current = null;
      setModalOpen(true);
    }
  }, [dismissedThisSession, requirement]);

  const reconnectPending = ["starting", "navigating_external", "reconciling"].includes(transaction.state);
  const contextValue = {
    providerAuthControllerAvailable: true,
    providerAuthRequirement: requirement,
    providerAuthDismissedThisSession: dismissedThisSession,
    providerAuthReconnectPending: reconnectPending,
    providerAuthTransaction: transaction,
    refreshProviderAuthStatus,
    reconcileProviderAuthTransaction,
    showProviderAuthPrompt,
    dismissProviderAuthPrompt,
    startProviderReconnect,
    cancelAccountReplacement,
    confirmAccountReplacement,
  };

  return (
    <ProviderAuthContext.Provider value={contextValue}>
      {children}
      <ProviderReconnectModal
        allowReconnect={requirement?.allowReconnect !== false}
        message={requirement?.message || ""}
        onClose={dismissProviderAuthPrompt}
        onReconnect={() => startProviderReconnect()}
        onSecondary={dismissProviderAuthPrompt}
        open={modalOpen && isProviderAuthReconnectCapable(requirement)}
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


export function useOptionalProviderAuth() {
  const context = useContext(ProviderAuthContext);
  return context || {
    providerAuthControllerAvailable: false,
    providerAuthReconnectPending: false,
    providerAuthTransaction: IDLE_TRANSACTION,
    startProviderReconnect: async () => null,
    cancelAccountReplacement: async () => undefined,
    confirmAccountReplacement: async () => null,
  };
}
