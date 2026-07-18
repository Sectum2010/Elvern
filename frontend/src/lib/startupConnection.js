import {
  classifyConnectivityEvidence,
  normalizePublicConnectivityProbeUrl,
  probePublicConnectivity,
} from "./connectivityEvidence.js";
import { detectClientPlatform, isDesktopClientPlatform } from "./platformDetection.js";


export const STARTUP_UNREACHABLE_DELAY_MS = 60_000;
export const STARTUP_HEALTH_PROBE_INTERVAL_MS = 10_000;
export const STARTUP_HEALTH_PROBE_TIMEOUT_MS = 5_000;
export const DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS = 8_000;
export const PUBLIC_CONNECTIVITY_CONFIRMATION_DELAY_MS = 250;
export const STARTUP_SHELL_REVEAL_DELAY_MS = 400;
export const NO_INTERNET_REAPPEAR_MS = 10_000;
export const STARTUP_CONNECTIVITY_FAILURE_EVENT = "elvern:connectivity-failure";
export const STARTUP_APPLICATION_READY_EVENT = "elvern:application-response";
export const FRONTEND_HEALTH_PATH = "/_elvern/frontend-health";
export const BACKEND_HEALTH_PATH = "/health";
export const CONNECTIVITY_INTERNET_OFFLINE = "internet_offline";
export const CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE = "frontend_or_vpn_unreachable";
export const CONNECTIVITY_BACKEND_UNREACHABLE = "backend_unreachable";
export const CONNECTIVITY_HEALTHY = "healthy";
export const CONNECTION_OOPS_TITLE = "Oops!";
export const CONNECTION_SERVER_OOPS_COPY = "Seems like the server has been bamboozled, we will fix it as soon as possible.";
export const CONNECTION_VPN_OOPS_COPY = "Elvern could not be reached, check your VPN connection and try again.";
export const CONNECTION_OFFLINE_OOPS_COPY = "It looks like you're offline. Please check your connection and try again.";
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


function configuredPublicConnectivityProbeUrl() {
  return normalizePublicConnectivityProbeUrl(
    import.meta.env?.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL,
  );
}


export function getConnectionOopsCopy(classification) {
  if (classification === CONNECTIVITY_BACKEND_UNREACHABLE) {
    return CONNECTION_SERVER_OOPS_COPY;
  }
  if (classification === CONNECTIVITY_INTERNET_OFFLINE) {
    return CONNECTION_OFFLINE_OOPS_COPY;
  }
  return CONNECTION_VPN_OOPS_COPY;
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
  publicConnectivityProbeUrl = configuredPublicConnectivityProbeUrl(),
  publicProbeConfirmationDelayMs = PUBLIC_CONNECTIVITY_CONFIRMATION_DELAY_MS,
} = {}) {
  const initiallyOffline = navigatorObject?.onLine === false;
  let snapshot = {
    status: "connecting",
    serviceReachable: false,
    runtimeReady: false,
    offlineOopsRequired: false,
    classification: initiallyOffline
      ? CONNECTIVITY_INTERNET_OFFLINE
      : CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
  };
  let applicationReady = !requireApplicationReady;
  let runtimeReady = false;
  let offlineOopsRequired = false;
  let forceOfflineOopsPending = false;
  let started = false;
  let outageStarted = Number.isFinite(initialOutageStartedAt) && initialOutageStartedAt > 0;
  let outageStartedAt = outageStarted
    ? initialOutageStartedAt
    : 0;
  let unreachableTimer = 0;
  let recoveryInterval = 0;
  let watchdogInterval = 0;
  let probeTimeout = 0;
  let activeAbortController = null;
  const activePublicAbortControllers = new Set();
  let inFlightProbe = null;
  let warnedMissingPublicProbe = false;
  const normalizedPublicProbeUrl = normalizePublicConnectivityProbeUrl(publicConnectivityProbeUrl);
  const desktopWatchdogEnabled = isDesktopClientPlatform(platform);
  const listeners = new Set();

  function emit(next) {
    const nextSnapshot = {
      ...snapshot,
      ...next,
      runtimeReady,
      offlineOopsRequired,
    };
    if (
      snapshot.status === nextSnapshot.status
      && snapshot.serviceReachable === nextSnapshot.serviceReachable
      && snapshot.runtimeReady === nextSnapshot.runtimeReady
      && snapshot.offlineOopsRequired === nextSnapshot.offlineOopsRequired
      && snapshot.classification === nextSnapshot.classification
    ) {
      return;
    }
    snapshot = nextSnapshot;
    listeners.forEach((listener) => listener());
  }

  function clearUnreachableTimer() {
    if (!unreachableTimer) {
      return;
    }
    windowObject?.clearTimeout?.(unreachableTimer);
    unreachableTimer = 0;
  }

  function clearRecoveryInterval() {
    if (!recoveryInterval) {
      return;
    }
    windowObject?.clearInterval?.(recoveryInterval);
    recoveryInterval = 0;
  }

  function ensureRecoveryInterval() {
    if (recoveryInterval) {
      return;
    }
    recoveryInterval = windowObject?.setInterval?.(() => {
      void probe();
    }, STARTUP_HEALTH_PROBE_INTERVAL_MS) || 0;
  }

  function clearWatchdogInterval() {
    if (!watchdogInterval) {
      return;
    }
    windowObject?.clearInterval?.(watchdogInterval);
    watchdogInterval = 0;
  }

  function ensureWatchdogInterval() {
    if (
      watchdogInterval
      || !desktopWatchdogEnabled
      || !runtimeReady
      || snapshot.status !== "connected"
      || snapshot.classification !== CONNECTIVITY_HEALTHY
      || documentObject?.visibilityState === "hidden"
    ) {
      return;
    }
    watchdogInterval = windowObject?.setInterval?.(() => {
      void probe();
    }, DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS) || 0;
  }

  function scheduleUnreachable() {
    if (unreachableTimer) {
      return;
    }
    const elapsed = Math.max(0, Date.now() - outageStartedAt);
    const remaining = Math.max(0, STARTUP_UNREACHABLE_DELAY_MS - elapsed);
    unreachableTimer = windowObject?.setTimeout?.(() => {
      unreachableTimer = 0;
      if (snapshot.status === "connecting") {
        emit({ status: "unreachable" });
      }
    }, remaining) || 0;
  }

  function beginOutage(classification, { preserveUnreachable = false } = {}) {
    clearWatchdogInterval();
    if (classification === CONNECTIVITY_INTERNET_OFFLINE && forceOfflineOopsPending && runtimeReady) {
      offlineOopsRequired = true;
    } else if (classification !== CONNECTIVITY_INTERNET_OFFLINE) {
      offlineOopsRequired = false;
    }
    forceOfflineOopsPending = false;
    if (runtimeReady && classification === CONNECTIVITY_INTERNET_OFFLINE && !offlineOopsRequired) {
      outageStartedAt = 0;
      outageStarted = false;
      clearUnreachableTimer();
      emit({
        status: "connecting",
        serviceReachable: false,
        classification,
      });
      ensureRecoveryInterval();
      return;
    }
    if (!outageStarted || snapshot.status === "connected") {
      outageStartedAt = Date.now();
      outageStarted = true;
    }
    const status = preserveUnreachable && snapshot.status === "unreachable"
      ? "unreachable"
      : "connecting";
    emit({
      status,
      serviceReachable: false,
      classification,
    });
    if (status === "connecting") {
      scheduleUnreachable();
    }
    ensureRecoveryInterval();
  }

  function markHealthy() {
    if (applicationReady) {
      outageStartedAt = 0;
      outageStarted = false;
      runtimeReady = true;
      offlineOopsRequired = false;
      forceOfflineOopsPending = false;
      clearUnreachableTimer();
      clearRecoveryInterval();
      emit({
        status: "connected",
        serviceReachable: true,
        classification: CONNECTIVITY_HEALTHY,
      });
      ensureWatchdogInterval();
      return;
    }
    emit({
      status: snapshot.status === "unreachable" ? "unreachable" : "connecting",
      serviceReachable: true,
      classification: CONNECTIVITY_HEALTHY,
    });
  }

  function waitForPublicProbeConfirmation() {
    return new Promise((resolve) => {
      windowObject?.setTimeout?.(resolve, Math.max(0, Number(publicProbeConfirmationDelayMs) || 0));
    });
  }

  async function runPublicProbeAttempt() {
    const publicAbortController = new AbortController();
    activePublicAbortControllers.add(publicAbortController);
    try {
      return await probePublicConnectivity({
        fetchImpl,
        url: normalizedPublicProbeUrl,
        timeoutMs: STARTUP_HEALTH_PROBE_TIMEOUT_MS,
        abortController: publicAbortController,
        setTimeoutImpl: windowObject?.setTimeout?.bind(windowObject),
        clearTimeoutImpl: windowObject?.clearTimeout?.bind(windowObject),
      });
    } finally {
      activePublicAbortControllers.delete(publicAbortController);
    }
  }

  async function classifyFrontendFailure() {
    if (!normalizedPublicProbeUrl) {
      if (!warnedMissingPublicProbe) {
        warnedMissingPublicProbe = true;
        console.warn(
          "VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL is not configured; frontend failures cannot be distinguished from VPN/origin failures.",
        );
      }
      return CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE;
    }
    if (await runPublicProbeAttempt()) {
      return CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE;
    }
    await waitForPublicProbeConfirmation();
    return classifyConnectivityEvidence({
      frontendReachable: false,
      publicInternetReachable: await runPublicProbeAttempt(),
    });
  }

  async function probe() {
    if (inFlightProbe || typeof fetchImpl !== "function") {
      return inFlightProbe || Promise.resolve(false);
    }
    if (navigatorObject?.onLine === false) {
      beginOutage(CONNECTIVITY_INTERNET_OFFLINE, {
        preserveUnreachable: snapshot.status === "unreachable",
      });
      return false;
    }

    const probeAbortController = new AbortController();
    activeAbortController = probeAbortController;
    const currentProbeTimeout = windowObject?.setTimeout?.(() => {
      probeAbortController.abort();
    }, STARTUP_HEALTH_PROBE_TIMEOUT_MS) || 0;
    probeTimeout = currentProbeTimeout;
    const wasUnreachable = snapshot.status === "unreachable";
    const probeOperation = (async () => {
      let frontendReachable = false;
      try {
        const frontendResponse = await fetchImpl(FRONTEND_HEALTH_PATH, {
          cache: "no-store",
          credentials: "same-origin",
          signal: probeAbortController.signal,
        });
        frontendReachable = isSuccessfulHealthResponse(frontendResponse);
      } catch {
        frontendReachable = false;
      }
      if (!frontendReachable) {
        const classification = await classifyFrontendFailure();
        beginOutage(classification, { preserveUnreachable: wasUnreachable });
        return false;
      }

      try {
        const backendResponse = await fetchImpl(BACKEND_HEALTH_PATH, {
          cache: "no-store",
          credentials: "same-origin",
          signal: probeAbortController.signal,
        });
        if (!isSuccessfulHealthResponse(backendResponse)) {
          beginOutage(CONNECTIVITY_BACKEND_UNREACHABLE, { preserveUnreachable: wasUnreachable });
          return false;
        }
      } catch {
        beginOutage(CONNECTIVITY_BACKEND_UNREACHABLE, { preserveUnreachable: wasUnreachable });
        return false;
      }

      markHealthy();
      return true;
    })();
    const trackedProbe = probeOperation.finally(() => {
      if (currentProbeTimeout) {
        windowObject?.clearTimeout?.(currentProbeTimeout);
      }
      if (probeTimeout === currentProbeTimeout) {
        probeTimeout = 0;
      }
      if (activeAbortController === probeAbortController) {
        activeAbortController = null;
      }
      if (inFlightProbe === trackedProbe) {
        inFlightProbe = null;
      }
    });
    inFlightProbe = trackedProbe;
    return trackedProbe;
  }

  function handleVisibilityChange() {
    if (documentObject?.visibilityState === "hidden") {
      clearWatchdogInterval();
      return;
    }
    void probe();
  }

  function handleOnline() {
    void probe();
  }

  function handleOffline() {
    beginOutage(CONNECTIVITY_INTERNET_OFFLINE, {
      preserveUnreachable: snapshot.status === "unreachable",
    });
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    if (!outageStarted) {
      outageStartedAt = Date.now();
      outageStarted = true;
    }
    if (initiallyOffline || navigatorObject?.onLine === false) {
      beginOutage(CONNECTIVITY_INTERNET_OFFLINE);
    } else {
      scheduleUnreachable();
      ensureRecoveryInterval();
      void probe();
    }
    windowObject?.addEventListener?.("online", handleOnline);
    windowObject?.addEventListener?.("offline", handleOffline);
    documentObject?.addEventListener?.("visibilitychange", handleVisibilityChange);
  }

  function stop() {
    started = false;
    clearUnreachableTimer();
    clearRecoveryInterval();
    clearWatchdogInterval();
    if (probeTimeout) {
      windowObject?.clearTimeout?.(probeTimeout);
      probeTimeout = 0;
    }
    activeAbortController?.abort();
    activeAbortController = null;
    activePublicAbortControllers.forEach((controller) => controller.abort());
    activePublicAbortControllers.clear();
    inFlightProbe = null;
    windowObject?.removeEventListener?.("online", handleOnline);
    windowObject?.removeEventListener?.("offline", handleOffline);
    documentObject?.removeEventListener?.("visibilitychange", handleVisibilityChange);
  }

  function reportFailure({ forceOfflineOops = false } = {}) {
    if (runtimeReady && forceOfflineOops) {
      forceOfflineOopsPending = true;
    }
    if (navigatorObject?.onLine !== false && forceOfflineOops) {
      return probe();
    }
    beginOutage(
      navigatorObject?.onLine === false
        ? CONNECTIVITY_INTERNET_OFFLINE
        : CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
    );
    return probe();
  }

  function reportApplicationReady() {
    applicationReady = true;
    runtimeReady = true;
    offlineOopsRequired = false;
    forceOfflineOopsPending = false;
    clearUnreachableTimer();
    clearRecoveryInterval();
    outageStartedAt = 0;
    outageStarted = false;
    emit({
      status: "connected",
      serviceReachable: true,
      classification: CONNECTIVITY_HEALTHY,
    });
    ensureWatchdogInterval();
  }

  function retry() {
    if (runtimeReady) {
      if (offlineOopsRequired) {
        outageStartedAt = Date.now();
        outageStarted = true;
        clearUnreachableTimer();
        emit({
          status: "connecting",
          serviceReachable: false,
          classification: CONNECTIVITY_INTERNET_OFFLINE,
        });
        scheduleUnreachable();
        ensureRecoveryInterval();
      }
      return probe();
    }
    outageStartedAt = Date.now();
    outageStarted = true;
    clearUnreachableTimer();
    applicationReady = !requireApplicationReady;
    emit({
      status: "connecting",
      serviceReachable: false,
      classification: navigatorObject?.onLine === false
        ? CONNECTIVITY_INTERNET_OFFLINE
        : CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
    });
    scheduleUnreachable();
    ensureRecoveryInterval();
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
