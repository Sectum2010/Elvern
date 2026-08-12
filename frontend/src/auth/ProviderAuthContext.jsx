import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ProviderReconnectModal } from "../components/ProviderReconnectModal";
import { apiRequest, isAbortError, isTransientNetworkError } from "../lib/api";
import { CONNECTIVITY_RECOVERED_EVENT } from "../lib/connectivityRecoveryStore";
import { PAGE_RESUME_EVENT } from "../lib/pageResume";
import {
  buildProviderAuthReturnPath,
  clearProviderAuthIntent,
  createProviderAuthOperationId,
  getProviderAuthIdentity,
  getProviderAuthRequirementFromStatus,
  isProviderAuthReconnectCapable,
  navigateToProviderAuthorization,
  PROVIDER_RECONNECT_CANCELLED_MESSAGE,
  readProviderAuthIntent,
  saveProviderAuthIntent,
  shouldShowProviderAuthBootstrapModal,
  shouldUseProviderAuthPassiveNotice,
  startGoogleDriveReconnect,
} from "../lib/providerAuth";
import {
  beginExternalNavigation,
  clearExternalNavigationForIdentityChange,
  completeExternalNavigation,
  markExternalNavigationNavigating,
  markExternalNavigationReconciling,
  prepareExternalNavigation,
  createExternalNavigationAwareRequestOwner,
} from "../lib/externalNavigationCoordinator";
import { useAuth } from "./AuthContext";


const ProviderAuthContext = createContext(null);
const IDLE_TRANSACTION = Object.freeze({
  state: "idle",
  message: "",
  candidate: null,
  result: null,
  operationId: "",
  outcomeId: "",
  identity: "",
  operationContext: null,
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
  const { loading: authLoading, user } = useAuth();
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
  const externalNavigationHandleRef = useRef(null);
  const handledResumeGenerationsRef = useRef(new Set());

  const updateTransaction = useCallback((nextTransaction) => {
    transactionRef.current = nextTransaction;
    setTransaction(nextTransaction);
  }, []);

  identityRef.current = identity;

  const refreshProviderAuthStatus = useCallback(async ({
    signal,
    ignoreDismissal = false,
    ownExternalNavigation = true,
  } = {}) => {
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
      const requestOwner = ownExternalNavigation
        ? createExternalNavigationAwareRequestOwner({
          identity: requestIdentity,
          resource: "provider_auth_status",
        })
        : undefined;
      const payload = await apiRequest("/api/cloud-libraries/google/provider-auth-status", {
        signal,
        requestOwner,
      });
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
      if (!isAbortError(requestError)) {
        console.error("Failed to load Google Drive provider auth status", requestError);
      }
      return requirementRef.current;
    }
  }, [identity, user]);

  const finishTransaction = useCallback((nextState, message, result = null, operation = null) => {
    const intent = operation || readProviderAuthIntent({ identity: identityRef.current });
    const operationId = String(intent?.operationId || transactionRef.current.operationId || "");
    const operationIdentity = String(intent?.identity || identityRef.current || "");
    clearProviderAuthIntent();
    if (externalNavigationHandleRef.current) {
      completeExternalNavigation(externalNavigationHandleRef.current);
      externalNavigationHandleRef.current = null;
    }
    updateTransaction({
      state: nextState,
      message,
      candidate: null,
      result,
      operationId,
      outcomeId: operationId ? `${operationIdentity}:${operationId}` : "",
      identity: operationIdentity,
      operationContext: intent ? {
        actionType: String(intent.actionType || ""),
        mediaItemId: Number(intent.mediaItemId || 0) || null,
        platform: String(intent.platform || "") || null,
        returnPath: String(intent.returnPath || ""),
      } : null,
    });
  }, [updateTransaction]);

  const acknowledgeProviderAuthOutcome = useCallback((outcomeId) => {
    if (!outcomeId || transactionRef.current.outcomeId !== outcomeId) {
      return false;
    }
    updateTransaction(IDLE_TRANSACTION);
    return true;
  }, [updateTransaction]);

  const reconcileProviderAuthTransaction = useCallback(async ({
    resumeGeneration = 0,
    resumeSource = "resume",
  } = {}) => {
    if (!user || !identity) {
      return null;
    }
    const intent = readProviderAuthIntent({ identity });
    if (!intent?.operationId) {
      return null;
    }
    const normalizedGeneration = Number(resumeGeneration) || 0;
    const reconciliationKey = `${identity}:${intent.operationId}:${resumeSource}:${normalizedGeneration}`;
    if (handledResumeGenerationsRef.current.has(reconciliationKey)) {
      return transactionRef.current;
    }
    const currentInFlight = reconcileInFlightRef.current;
    if (
      currentInFlight?.identity === identity
      && currentInFlight?.operationId === intent.operationId
    ) {
      return currentInFlight.promise;
    }
    if (currentInFlight) {
      currentInFlight.controller.abort();
    }
    const controller = new AbortController();
    handledResumeGenerationsRef.current.add(reconciliationKey);
    if (handledResumeGenerationsRef.current.size > 128) {
      handledResumeGenerationsRef.current.delete(handledResumeGenerationsRef.current.values().next().value);
    }
    const navigationHandle = beginExternalNavigation({
      identity,
      provider: intent.provider || "google_drive",
      operationId: intent.operationId,
    });
    externalNavigationHandleRef.current = navigationHandle;
    markExternalNavigationReconciling(navigationHandle);
    let task;
    task = (async () => {
      const operationIdentity = identity;
      const operationId = intent.operationId;
      updateTransaction({
        state: "reconciling_return",
        message: "",
        candidate: null,
        result: null,
        operationId,
        outcomeId: "",
        identity: operationIdentity,
      });
      const callbackMessage = getGoogleDriveMessageFromLocation(window.location);
      let operation;
      try {
        operation = await apiRequest("/api/cloud-libraries/google/operation/status", {
          method: "POST",
          data: { operation_id: operationId },
          signal: controller.signal,
        });
      } catch (requestError) {
        if (
          isAbortError(requestError)
          || identityRef.current !== operationIdentity
          || readProviderAuthIntent({ identity: operationIdentity })?.operationId !== operationId
        ) {
          return transactionRef.current;
        }
        updateTransaction({
          state: "unknown",
          message: "Google Drive reconnect status is temporarily unknown. Elvern will check again.",
          candidate: null,
          result: null,
          operationId,
          outcomeId: "",
          identity: operationIdentity,
        });
        removeGoogleDriveCallbackParams();
        return transactionRef.current;
      }
      if (
        identityRef.current !== operationIdentity
        || readProviderAuthIntent({ identity: operationIdentity })?.operationId !== operationId
      ) {
        return transactionRef.current;
      }

      if (operation?.status === "account_mismatch") {
        try {
          const candidate = await apiRequest("/api/cloud-libraries/google/account-candidate/status", {
            method: "POST",
            data: { operation_id: intent.operationId },
            signal: controller.signal,
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
            operationId,
            outcomeId: "",
            identity: operationIdentity,
          };
          updateTransaction(next);
          removeGoogleDriveCallbackParams();
          return next;
        } catch (requestError) {
          if (
            isAbortError(requestError)
            || identityRef.current !== operationIdentity
            || readProviderAuthIntent({ identity: operationIdentity })?.operationId !== operationId
          ) {
            return transactionRef.current;
          }
          if (isTransientNetworkError(requestError)) {
            updateTransaction({
              state: "unknown",
              message: "Google Drive reconnect status is temporarily unknown. Elvern will check again.",
              candidate: null,
              result: null,
              operationId,
              outcomeId: "",
              identity: operationIdentity,
              operationContext: null,
            });
          } else {
            finishTransaction(
              "error",
              requestError.message || "Google Drive account replacement expired.",
              null,
              intent,
            );
          }
          removeGoogleDriveCallbackParams();
          return transactionRef.current;
        }
      }

      if (operation?.status === "connected") {
        const nextRequirement = await refreshProviderAuthStatus({ ownExternalNavigation: false });
        if (
          identityRef.current !== operationIdentity
          || readProviderAuthIntent({ identity: operationIdentity })?.operationId !== operationId
        ) {
          return transactionRef.current;
        }
        finishTransaction(
          "connected",
          callbackMessage || "Google Drive connected.",
          { requirement: nextRequirement },
          intent,
        );
        removeGoogleDriveCallbackParams();
        return transactionRef.current;
      }

      if (operation?.status === "pending") {
        let cancellation;
        try {
          cancellation = await apiRequest("/api/cloud-libraries/google/operation/cancel", {
            method: "POST",
            data: { operation_id: operationId },
            signal: controller.signal,
          });
        } catch (requestError) {
          if (
            isAbortError(requestError)
            || identityRef.current !== operationIdentity
            || readProviderAuthIntent({ identity: operationIdentity })?.operationId !== operationId
          ) {
            return transactionRef.current;
          }
          updateTransaction({
            state: "pending_confirmation",
            message: "Google Drive reconnect cancellation is awaiting confirmation.",
            candidate: null,
            result: null,
            operationId,
            outcomeId: "",
            identity: operationIdentity,
          });
          removeGoogleDriveCallbackParams();
          return transactionRef.current;
        }
        if (
          identityRef.current !== operationIdentity
          || readProviderAuthIntent({ identity: operationIdentity })?.operationId !== operationId
        ) {
          return transactionRef.current;
        }
        if (!["cancelled", "expired"].includes(String(cancellation?.status || ""))) {
          updateTransaction({
            state: "pending_confirmation",
            message: "Google Drive reconnect cancellation is awaiting confirmation.",
            candidate: null,
            result: null,
            operationId,
            outcomeId: "",
            identity: operationIdentity,
          });
          removeGoogleDriveCallbackParams();
          return transactionRef.current;
        }
        finishTransaction("cancelled_or_incomplete", PROVIDER_RECONNECT_CANCELLED_MESSAGE, null, intent);
        removeGoogleDriveCallbackParams();
        return transactionRef.current;
      }

      if (["cancelled", "expired"].includes(String(operation?.status || ""))) {
        finishTransaction(
          "cancelled_or_incomplete",
          PROVIDER_RECONNECT_CANCELLED_MESSAGE,
          null,
          intent,
        );
      } else if (operation?.status === "error") {
        finishTransaction(
          "error",
          operation.message || callbackMessage || "Google Drive reconnect failed.",
          null,
          intent,
        );
      } else {
        updateTransaction({
          state: "pending_confirmation",
          message: "Google Drive reconnect status is awaiting confirmation.",
          candidate: null,
          result: null,
          operationId,
          outcomeId: "",
          identity: operationIdentity,
          operationContext: null,
        });
      }
      removeGoogleDriveCallbackParams();
      return transactionRef.current;
    })().finally(() => {
      if (externalNavigationHandleRef.current === navigationHandle) {
        completeExternalNavigation(navigationHandle);
        externalNavigationHandleRef.current = null;
      }
      if (reconcileInFlightRef.current?.promise === task) {
        reconcileInFlightRef.current = null;
      }
    });
    reconcileInFlightRef.current = {
      identity,
      operationId: intent.operationId,
      controller,
      promise: task,
    };
    return task;
  }, [finishTransaction, identity, refreshProviderAuthStatus, updateTransaction, user]);

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

  async function startProviderReconnect({
    allowWithoutRequirement = false,
    returnPath: requestedReturnPath = "",
    intentMetadata = null,
  } = {}) {
    const currentRequirement = requirementRef.current;
    const currentState = transactionRef.current.state;
    if (
      ["starting", "preparing_external_navigation", "navigating_external", "reconciling_return", "pending_confirmation", "unknown"].includes(currentState)
      || (!allowWithoutRequirement && !currentRequirement)
      || currentRequirement?.allowReconnect === false
      || !user
    ) {
      return null;
    }
    const operationId = createProviderAuthOperationId();
    const returnPath = String(requestedReturnPath || "").trim()
      || buildProviderAuthReturnPath(window.location);
    const intent = {
      provider: "google_drive",
      operationId,
      identity,
      returnPath,
      state: "starting",
      ...(intentMetadata && typeof intentMetadata === "object" ? intentMetadata : {}),
    };
    if (!saveProviderAuthIntent(intent)) {
      const message = "Google Drive reconnect could not be started because this browser could not save the operation.";
      updateTransaction({
        ...IDLE_TRANSACTION,
        state: "error",
        message,
        operationId,
        outcomeId: `${identity}:${operationId}`,
        identity,
        operationContext: null,
      });
      setModalError(message);
      return null;
    }
    updateTransaction({
      state: "starting",
      message: "",
      candidate: null,
      result: null,
      operationId,
      outcomeId: "",
      identity,
      operationContext: null,
    });
    setModalError("");
    let operationStarted = false;
    try {
      const payload = await startGoogleDriveReconnect({
        operationId,
        returnPath: intent.returnPath,
      });
      operationStarted = true;
      if (
        identityRef.current !== identity
        || readProviderAuthIntent({ identity })?.operationId !== operationId
      ) {
        return null;
      }
      const navigationHandle = beginExternalNavigation({
        identity,
        operationContext: null,
        provider: "google_drive",
        operationId,
      });
      externalNavigationHandleRef.current = navigationHandle;
      saveProviderAuthIntent({ ...intent, state: "preparing_external_navigation" });
      updateTransaction({
        state: "preparing_external_navigation",
        message: "",
        candidate: null,
        result: null,
        operationId,
        outcomeId: "",
        identity,
        operationContext: null,
      });
      prepareExternalNavigation(navigationHandle);
      saveProviderAuthIntent({ ...intent, state: "navigating_external" });
      updateTransaction({
        state: "navigating_external",
        message: "",
        candidate: null,
        result: null,
        operationId,
        outcomeId: "",
        identity,
      });
      markExternalNavigationNavigating(navigationHandle);
      navigateToProviderAuthorization(payload.authorization_url);
      return payload;
    } catch (requestError) {
      if (identityRef.current !== identity) {
        return null;
      }
      if (!operationStarted) {
        finishTransaction(
          "error",
          requestError.message || "Failed to start Google Drive reconnect.",
          null,
          intent,
        );
        setModalError(requestError.message || "Failed to start Google Drive reconnect.");
        return null;
      }
      if (externalNavigationHandleRef.current) {
        completeExternalNavigation(externalNavigationHandleRef.current);
        externalNavigationHandleRef.current = null;
      }
      let cancellationStatus = "";
      try {
        const cancellation = await apiRequest("/api/cloud-libraries/google/operation/cancel", {
          method: "POST",
          data: { operation_id: operationId },
        });
        cancellationStatus = String(cancellation?.status || "");
      } catch {
        // Keep the identity-bound intent so a later confirmed recovery can reconcile it.
      }
      const message = requestError.message || "Failed to start Google Drive reconnect.";
      if (["cancelled", "expired", "error"].includes(cancellationStatus)) {
        finishTransaction("error", message, null, intent);
        setModalError(message);
        return null;
      }
      updateTransaction({
        state: "pending_confirmation",
        message,
        candidate: null,
        result: null,
        operationId,
        outcomeId: "",
        identity,
        operationContext: null,
      });
      setModalError(message);
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
    if (authLoading) {
      return undefined;
    }
    if (reconcileInFlightRef.current?.identity !== identity) {
      reconcileInFlightRef.current?.controller.abort();
      reconcileInFlightRef.current = null;
    }
    clearExternalNavigationForIdentityChange(identity);
    handledResumeGenerationsRef.current.clear();
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
      void reconcileProviderAuthTransaction({ resumeGeneration: 0, resumeSource: "initial" });
    }
    return () => controller.abort();
  }, [authLoading, identity, reconcileProviderAuthTransaction, refreshProviderAuthStatus, updateTransaction, user]);

  useEffect(() => {
    if (!user || typeof window === "undefined") {
      return undefined;
    }
    function reconcileAfterResume(event) {
      if (document.visibilityState === "hidden" || !readProviderAuthIntent({ identity })) {
        return;
      }
      void reconcileProviderAuthTransaction({
        resumeGeneration: Number(event?.detail?.generation) || 0,
        resumeSource: "page_resume",
      });
    }
    window.addEventListener(PAGE_RESUME_EVENT, reconcileAfterResume);
    return () => {
      window.removeEventListener(PAGE_RESUME_EVENT, reconcileAfterResume);
    };
  }, [identity, reconcileProviderAuthTransaction, user]);

  useEffect(() => {
    if (!user || typeof window === "undefined") {
      return undefined;
    }
    function reconcileAfterConnectivityRecovery(event) {
      if (
        !["unknown", "pending_confirmation"].includes(transactionRef.current.state)
        || !readProviderAuthIntent({ identity })
      ) {
        return;
      }
      void reconcileProviderAuthTransaction({
        resumeGeneration: Number(event?.detail?.generation) || 0,
        resumeSource: "connectivity_recovery",
      });
    }
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, reconcileAfterConnectivityRecovery);
    return () => {
      window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, reconcileAfterConnectivityRecovery);
    };
  }, [identity, reconcileProviderAuthTransaction, user]);

  useEffect(() => {
    if (requirement && shouldShowProviderAuthBootstrapModal({ requirement, dismissed: dismissedThisSession })) {
      laterContinuationRef.current = null;
      setModalOpen(true);
    }
  }, [dismissedThisSession, requirement]);

  const reconnectPending = [
    "starting",
    "preparing_external_navigation",
    "navigating_external",
    "reconciling_return",
    "pending_confirmation",
    "unknown",
  ].includes(transaction.state);
  const contextValue = {
    providerAuthControllerAvailable: true,
    providerAuthRequirement: requirement,
    providerAuthDismissedThisSession: dismissedThisSession,
    providerAuthReconnectPending: reconnectPending,
    providerAuthTransaction: transaction,
    acknowledgeProviderAuthOutcome,
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
    acknowledgeProviderAuthOutcome: () => false,
    startProviderReconnect: async () => null,
    cancelAccountReplacement: async () => undefined,
    confirmAccountReplacement: async () => null,
  };
}
