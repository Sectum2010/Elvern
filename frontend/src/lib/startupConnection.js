import {
  createPublicConnectivityProbeRunner,
  PUBLIC_PROBE_CONFIRMATION_DELAY_MS,
  resolvePublicConnectivityProbeRegistry,
} from "./publicConnectivityProbes.js";
import { detectClientPlatform, isDesktopClientPlatform } from "./platformDetection.js";


export const STARTUP_UNREACHABLE_DELAY_MS = 60_000;
export const STARTUP_HEALTH_PROBE_INTERVAL_MS = 10_000;
export const STARTUP_HEALTH_PROBE_TIMEOUT_MS = 5_000;
export const DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS = 8_000;
export const PUBLIC_CONNECTIVITY_CONFIRMATION_DELAY_MS = PUBLIC_PROBE_CONFIRMATION_DELAY_MS;
export const STARTUP_SHELL_REVEAL_DELAY_MS = 400;
export const NO_INTERNET_REAPPEAR_MS = 10_000;
export const STARTUP_CONNECTIVITY_FAILURE_EVENT = "elvern:connectivity-failure";
export const STARTUP_APPLICATION_READY_EVENT = "elvern:application-response";
export const FRONTEND_HEALTH_PATH = "/_elvern/frontend-health";
export const BACKEND_HEALTH_PATH = "/health";
export const CONNECTIVITY_INTERNET_OFFLINE = "internet_offline";
export const CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE = "frontend_or_vpn_unreachable";
export const CONNECTIVITY_BACKEND_UNREACHABLE = "backend_unreachable";
export const CONNECTIVITY_EVIDENCE_INSUFFICIENT = "connectivity_evidence_insufficient";
export const CONNECTIVITY_HEALTHY = "healthy";
export const CONNECTION_OOPS_TITLE = "Oops!";
export const CONNECTION_SERVER_OOPS_COPY = "Seems like the server has been bamboozled, we will fix it as soon as possible.";
export const CONNECTION_VPN_OOPS_COPY = "Elvern could not be reached, check your VPN connection and try again.";
export const CONNECTION_OFFLINE_OOPS_COPY = "It looks like you're offline. Please check your connection and try again.";
export const CONNECTION_GENERIC_OOPS_COPY = "Elvern could not be reached at the moment, please check your connection and try again.";
export const CONNECTION_OOPS_COPY = CONNECTION_VPN_OOPS_COPY;
export const CONNECTION_STATUS_WORDS = Object.freeze([
  "Flibbertigibbeting...",
  "Ruminating...",
  "Conjuring...",
  "Recombobulating...",
  "Scrying...",
  "Divining...",
  "Wayfinding...",
  "Enchanting...",
]);
export const CONNECTION_FAMILIARS = Object.freeze(["raven", "wisp", "horned", "gargoyle", "keeper"]);
export const CONNECTION_FAMILIAR_ROTATION_MS = 7_000;


function isSuccessfulHealthResponse(response) {
  return Boolean(response && response.ok);
}


function deriveConnectivityClassification({
  internetState,
  internetOutageLatched,
  frontendState,
  backendState,
}) {
  if (internetState === "offline" || internetOutageLatched) {
    return CONNECTIVITY_INTERNET_OFFLINE;
  }
  if (frontendState === "reachable" && backendState === "unreachable") {
    return CONNECTIVITY_BACKEND_UNREACHABLE;
  }
  if (frontendState === "unreachable") {
    return internetState === "online"
      ? CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE
      : CONNECTIVITY_EVIDENCE_INSUFFICIENT;
  }
  if (frontendState === "reachable" && backendState === "reachable") {
    return CONNECTIVITY_HEALTHY;
  }
  return CONNECTIVITY_EVIDENCE_INSUFFICIENT;
}


function isDebugEnabled(windowObject) {
  try {
    return windowObject?.localStorage?.getItem("elvern_connection_shell_debug") === "1";
  } catch {
    return false;
  }
}


export function getConnectionOopsCopy(classification) {
  if (classification === CONNECTIVITY_BACKEND_UNREACHABLE) {
    return CONNECTION_SERVER_OOPS_COPY;
  }
  if (classification === CONNECTIVITY_INTERNET_OFFLINE) {
    return CONNECTION_OFFLINE_OOPS_COPY;
  }
  if (classification === CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE) {
    return CONNECTION_VPN_OOPS_COPY;
  }
  return CONNECTION_GENERIC_OOPS_COPY;
}


export function dispatchStartupConnectivityFailure(detail = null) {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }
  window.dispatchEvent(new CustomEvent(STARTUP_CONNECTIVITY_FAILURE_EVENT, { detail }));
}


export function dispatchStartupApplicationReady() {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }
  window.__elvernRuntimeReady = true;
  window.__elvernBootstrapPhase = "runtime_ready";
  window.dispatchEvent(new CustomEvent(STARTUP_APPLICATION_READY_EVENT));
}


export function createStartupConnectionController({
  fetchImpl = globalThis.fetch?.bind(globalThis),
  windowObject = globalThis.window,
  documentObject = globalThis.document,
  navigatorObject = globalThis.navigator,
  requireApplicationReady = false,
  initialOutageStartedAt = Number(windowObject?.__elvernConnectionStartedAt) || 0,
  platform = detectClientPlatform(),
  publicConnectivityProbes = null,
  publicConnectivityProbeUrl = "",
  publicProbeConfirmationDelayMs = PUBLIC_CONNECTIVITY_CONFIRMATION_DELAY_MS,
  publicProbeStorage = windowObject?.localStorage,
} = {}) {
  const initiallyOffline = navigatorObject?.onLine === false;
  const probes = publicConnectivityProbes || (publicConnectivityProbeUrl
    ? resolvePublicConnectivityProbeRegistry({ pluralValue: "", singularValue: publicConnectivityProbeUrl })
    : resolvePublicConnectivityProbeRegistry());
  const publicRunner = createPublicConnectivityProbeRunner({
    fetchImpl,
    probes,
    storage: publicProbeStorage,
    setTimeoutImpl: windowObject?.setTimeout?.bind(windowObject),
    clearTimeoutImpl: windowObject?.clearTimeout?.bind(windowObject),
    debug: isDebugEnabled(windowObject)
      ? ({ endpointId, success, status, elapsedMs }) => console.debug("Elvern public connectivity probe", {
        endpointId,
        success,
        status,
        elapsedMs,
      })
      : null,
  });
  const initialTrust = publicRunner.getTrustState();
  let applicationReady = !requireApplicationReady;
  let runtimeReady = false;
  let offlineOopsRequired = initiallyOffline;
  let forceOfflineOopsPending = false;
  let started = false;
  let lifecycleGeneration = 0;
  let initialProbeCompleted = false;
  let outageStartedAt = Number.isFinite(initialOutageStartedAt) && initialOutageStartedAt > 0
    ? initialOutageStartedAt
    : Date.now();
  let unreachableTimer = 0;
  let scheduledProbeTimer = 0;
  let inFlightProbe = null;
  const activeHealthControllers = new Set();
  const listeners = new Set();
  const desktopWatchdogEnabled = isDesktopClientPlatform(platform);
  let snapshot = {
    internetState: initiallyOffline ? "offline" : "unknown",
    internetOutageLatched: initiallyOffline,
    publicProbeTrusted: initialTrust.trusted,
    frontendState: "unknown",
    backendState: "unknown",
    status: "connecting",
    serviceReachable: false,
    runtimeReady: false,
    offlineOopsRequired,
    classification: initiallyOffline
      ? CONNECTIVITY_INTERNET_OFFLINE
      : CONNECTIVITY_EVIDENCE_INSUFFICIENT,
  };

  function emit(next = {}) {
    const candidate = {
      ...snapshot,
      ...next,
      runtimeReady,
      offlineOopsRequired,
    };
    candidate.classification = deriveConnectivityClassification(candidate);
    const keys = [
      "internetState",
      "internetOutageLatched",
      "publicProbeTrusted",
      "frontendState",
      "backendState",
      "status",
      "serviceReachable",
      "runtimeReady",
      "offlineOopsRequired",
      "classification",
    ];
    if (keys.every((key) => snapshot[key] === candidate[key])) {
      return;
    }
    snapshot = candidate;
    listeners.forEach((listener) => listener());
  }

  function clearUnreachableTimer() {
    if (unreachableTimer) {
      windowObject?.clearTimeout?.(unreachableTimer);
      unreachableTimer = 0;
    }
  }

  function clearScheduledProbe() {
    if (scheduledProbeTimer) {
      windowObject?.clearTimeout?.(scheduledProbeTimer);
      scheduledProbeTimer = 0;
    }
  }

  function shouldSuppressRuntimeOops() {
    return runtimeReady && snapshot.internetOutageLatched && !offlineOopsRequired;
  }

  function scheduleUnreachable({ reset = false } = {}) {
    if (reset) {
      outageStartedAt = Date.now();
      clearUnreachableTimer();
    }
    if (unreachableTimer || shouldSuppressRuntimeOops()) {
      return;
    }
    const remaining = Math.max(0, STARTUP_UNREACHABLE_DELAY_MS - Math.max(0, Date.now() - outageStartedAt));
    unreachableTimer = windowObject?.setTimeout?.(() => {
      unreachableTimer = 0;
      if (!snapshot.serviceReachable || !applicationReady || offlineOopsRequired) {
        emit({ status: "unreachable" });
      }
    }, remaining) || 0;
  }

  function scheduleNextProbe() {
    clearScheduledProbe();
    if (!started || documentObject?.visibilityState === "hidden") {
      return;
    }
    const needsRecovery = !snapshot.serviceReachable
      || !applicationReady
      || snapshot.internetOutageLatched
      || offlineOopsRequired;
    if (!desktopWatchdogEnabled && !needsRecovery) {
      return;
    }
    const delayMs = desktopWatchdogEnabled && runtimeReady
      ? DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS
      : STARTUP_HEALTH_PROBE_INTERVAL_MS;
    scheduledProbeTimer = windowObject?.setTimeout?.(() => {
      scheduledProbeTimer = 0;
      void probe();
    }, delayMs) || 0;
  }

  function markInternetOffline({ initialCycle = false } = {}) {
    if (initialCycle || forceOfflineOopsPending) {
      offlineOopsRequired = true;
    }
    forceOfflineOopsPending = false;
    emit({
      internetState: "offline",
      internetOutageLatched: true,
    });
    if (shouldSuppressRuntimeOops()) {
      clearUnreachableTimer();
      if (snapshot.serviceReachable && applicationReady) {
        emit({ status: "connected" });
      }
      return;
    }
    emit({ status: snapshot.status === "unreachable" ? "unreachable" : "connecting" });
    scheduleUnreachable();
  }

  function applyInternetEvidence(result, { initialCycle = false } = {}) {
    if (result.internetState === "online") {
      offlineOopsRequired = false;
      forceOfflineOopsPending = false;
      emit({
        internetState: "online",
        internetOutageLatched: false,
        publicProbeTrusted: true,
      });
      if (snapshot.serviceReachable && applicationReady) {
        clearUnreachableTimer();
        outageStartedAt = 0;
        emit({ status: "connected" });
      }
      return;
    }
    if (result.internetState === "offline") {
      emit({ publicProbeTrusted: Boolean(result.trusted) });
      markInternetOffline({ initialCycle });
      return;
    }
    emit({
      internetState: snapshot.internetOutageLatched ? "offline" : "unknown",
      publicProbeTrusted: Boolean(result.trusted),
    });
  }

  function markServiceHealthy() {
    const canConnect = applicationReady && !offlineOopsRequired;
    if (canConnect) {
      runtimeReady = true;
      clearUnreachableTimer();
      outageStartedAt = 0;
    }
    emit({
      frontendState: "reachable",
      backendState: "reachable",
      serviceReachable: true,
      status: canConnect ? "connected" : (snapshot.status === "unreachable" ? "unreachable" : "connecting"),
    });
  }

  function markServiceFailure({ frontendState, backendState }) {
    const wasConnected = snapshot.status === "connected";
    if (wasConnected || !outageStartedAt) {
      outageStartedAt = Date.now();
    }
    emit({
      frontendState,
      backendState,
      serviceReachable: false,
      status: shouldSuppressRuntimeOops() ? "connecting" : (snapshot.status === "unreachable" ? "unreachable" : "connecting"),
    });
    if (shouldSuppressRuntimeOops()) {
      clearUnreachableTimer();
    } else {
      scheduleUnreachable();
    }
  }

  async function requestHealth(path, generation) {
    const abortController = new AbortController();
    activeHealthControllers.add(abortController);
    const timeoutId = windowObject?.setTimeout?.(
      () => abortController.abort(),
      STARTUP_HEALTH_PROBE_TIMEOUT_MS,
    ) || 0;
    try {
      const response = await fetchImpl(path, {
        cache: "no-store",
        credentials: "same-origin",
        signal: abortController.signal,
      });
      return generation === lifecycleGeneration && isSuccessfulHealthResponse(response);
    } catch {
      return false;
    } finally {
      if (timeoutId) {
        windowObject?.clearTimeout?.(timeoutId);
      }
      activeHealthControllers.delete(abortController);
    }
  }

  async function collectServiceEvidence(generation) {
    const frontendReachable = await requestHealth(FRONTEND_HEALTH_PATH, generation);
    if (!frontendReachable) {
      return { frontendState: "unreachable", backendState: "unknown" };
    }
    const backendReachable = await requestHealth(BACKEND_HEALTH_PATH, generation);
    return {
      frontendState: "reachable",
      backendState: backendReachable ? "reachable" : "unreachable",
    };
  }

  function probe() {
    if (inFlightProbe || typeof fetchImpl !== "function") {
      return inFlightProbe || Promise.resolve(false);
    }
    clearScheduledProbe();
    const generation = lifecycleGeneration;
    const initialCycle = !initialProbeCompleted;
    const publicEvidence = navigatorObject?.onLine === false
      ? Promise.resolve({
        internetState: "offline",
        trusted: snapshot.publicProbeTrusted,
        endpointId: null,
        rounds: 0,
      })
      : publicRunner.probeConfirmed({ confirmationDelayMs: publicProbeConfirmationDelayMs });
    const serviceEvidence = collectServiceEvidence(generation);

    const publicOperation = publicEvidence.then((result) => {
      if (started && generation === lifecycleGeneration) {
        applyInternetEvidence(result, { initialCycle });
      }
      return result;
    });
    const serviceOperation = serviceEvidence.then((result) => {
      if (!started || generation !== lifecycleGeneration) {
        return result;
      }
      if (result.frontendState === "reachable" && result.backendState === "reachable") {
        markServiceHealthy();
      } else {
        markServiceFailure(result);
      }
      return result;
    });

    const operation = Promise.all([publicOperation, serviceOperation]).then(([internet, service]) => (
      internet.internetState === "online"
      && service.frontendState === "reachable"
      && service.backendState === "reachable"
    ));
    const tracked = operation.finally(() => {
      if (generation === lifecycleGeneration) {
        initialProbeCompleted = true;
      }
      if (inFlightProbe === tracked) {
        inFlightProbe = null;
      }
      if (started && generation === lifecycleGeneration) {
        scheduleNextProbe();
      }
    });
    inFlightProbe = tracked;
    return tracked;
  }

  function handleVisibilityChange() {
    if (documentObject?.visibilityState === "hidden") {
      clearScheduledProbe();
      return;
    }
    void probe();
  }

  function handleOnline() {
    void probe();
  }

  function handleOffline() {
    markInternetOffline({ initialCycle: !initialProbeCompleted });
    void probe();
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    lifecycleGeneration += 1;
    if (!outageStartedAt) {
      outageStartedAt = Date.now();
    }
    if (initiallyOffline || navigatorObject?.onLine === false) {
      markInternetOffline({ initialCycle: true });
    }
    scheduleUnreachable();
    windowObject?.addEventListener?.("online", handleOnline);
    windowObject?.addEventListener?.("offline", handleOffline);
    documentObject?.addEventListener?.("visibilitychange", handleVisibilityChange);
    void probe();
  }

  function stop() {
    if (!started) {
      return;
    }
    started = false;
    lifecycleGeneration += 1;
    clearUnreachableTimer();
    clearScheduledProbe();
    activeHealthControllers.forEach((controller) => controller.abort());
    activeHealthControllers.clear();
    publicRunner.abort();
    inFlightProbe = null;
    windowObject?.removeEventListener?.("online", handleOnline);
    windowObject?.removeEventListener?.("offline", handleOffline);
    documentObject?.removeEventListener?.("visibilitychange", handleVisibilityChange);
  }

  function reportFailure({ forceOfflineOops = false } = {}) {
    if (forceOfflineOops) {
      forceOfflineOopsPending = true;
      if (snapshot.internetOutageLatched || navigatorObject?.onLine === false) {
        offlineOopsRequired = true;
        emit({ status: "connecting" });
        scheduleUnreachable({ reset: true });
      }
    }
    if (navigatorObject?.onLine === false) {
      markInternetOffline();
    } else if (!snapshot.serviceReachable) {
      markServiceFailure({ frontendState: snapshot.frontendState, backendState: snapshot.backendState });
    }
    return probe();
  }

  function reportApplicationReady() {
    applicationReady = true;
    runtimeReady = true;
    if (windowObject) {
      windowObject.__elvernRuntimeReady = true;
      windowObject.__elvernBootstrapPhase = "runtime_ready";
    }
    if (snapshot.serviceReachable && !offlineOopsRequired) {
      clearUnreachableTimer();
      outageStartedAt = 0;
      emit({ status: "connected" });
    } else {
      emit({ status: snapshot.status === "unreachable" ? "unreachable" : "connecting" });
      scheduleUnreachable();
    }
    scheduleNextProbe();
  }

  function retry() {
    outageStartedAt = Date.now();
    clearUnreachableTimer();
    emit({ status: "connecting" });
    scheduleUnreachable();
    return probe();
  }

  return {
    getSnapshot: () => snapshot,
    probe,
    reportApplicationReady,
    reportFailure,
    retry,
    start,
    stop,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
