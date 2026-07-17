export const STARTUP_UNREACHABLE_DELAY_MS = 60_000;
export const STARTUP_HEALTH_PROBE_INTERVAL_MS = 10_000;
export const STARTUP_HEALTH_PROBE_TIMEOUT_MS = 5_000;
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
export const CONNECTION_SERVER_OOPS_COPY = "Seems like the server has been bamboozled, please try again later.";
export const CONNECTION_VPN_OOPS_COPY = "Elvern could not be reached, check your VPN connection and try again.";
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


export function dispatchStartupConnectivityFailure() {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }
  window.dispatchEvent(new CustomEvent(STARTUP_CONNECTIVITY_FAILURE_EVENT));
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
} = {}) {
  const initiallyOffline = navigatorObject?.onLine === false;
  let snapshot = {
    status: "connecting",
    serviceReachable: false,
    runtimeReady: false,
    classification: initiallyOffline
      ? CONNECTIVITY_INTERNET_OFFLINE
      : CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
  };
  let applicationReady = !requireApplicationReady;
  let runtimeReady = false;
  let started = false;
  let outageStartedAt = Number.isFinite(initialOutageStartedAt) && initialOutageStartedAt > 0
    ? initialOutageStartedAt
    : 0;
  let unreachableTimer = 0;
  let probeInterval = 0;
  let probeTimeout = 0;
  let activeAbortController = null;
  let inFlightProbe = null;
  const listeners = new Set();

  function emit(next) {
    const nextSnapshot = {
      ...snapshot,
      ...next,
      runtimeReady,
    };
    if (
      snapshot.status === nextSnapshot.status
      && snapshot.serviceReachable === nextSnapshot.serviceReachable
      && snapshot.runtimeReady === nextSnapshot.runtimeReady
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
    if (!probeInterval) {
      return;
    }
    windowObject?.clearInterval?.(probeInterval);
    probeInterval = 0;
  }

  function ensureRecoveryInterval() {
    if (probeInterval) {
      return;
    }
    probeInterval = windowObject?.setInterval?.(() => {
      void probe();
    }, STARTUP_HEALTH_PROBE_INTERVAL_MS) || 0;
  }

  function scheduleUnreachable() {
    if (unreachableTimer) {
      return;
    }
    if (snapshot.classification === CONNECTIVITY_INTERNET_OFFLINE) {
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
    if (!outageStartedAt || snapshot.status === "connected") {
      outageStartedAt = Date.now();
    }
    const status = preserveUnreachable && snapshot.status === "unreachable"
      ? "unreachable"
      : "connecting";
    emit({
      status,
      serviceReachable: false,
      classification,
    });
    if (classification === CONNECTIVITY_INTERNET_OFFLINE) {
      clearUnreachableTimer();
    } else if (status === "connecting") {
      scheduleUnreachable();
    }
    ensureRecoveryInterval();
  }

  function markHealthy() {
    if (applicationReady) {
      outageStartedAt = 0;
      runtimeReady = true;
      clearUnreachableTimer();
      clearRecoveryInterval();
      emit({
        status: "connected",
        serviceReachable: true,
        classification: CONNECTIVITY_HEALTHY,
      });
      return;
    }
    emit({
      status: snapshot.status === "unreachable" ? "unreachable" : "connecting",
      serviceReachable: true,
      classification: CONNECTIVITY_HEALTHY,
    });
  }

  async function probe() {
    if (inFlightProbe || typeof fetchImpl !== "function") {
      return inFlightProbe || Promise.resolve(false);
    }
    if (navigatorObject?.onLine === false) {
      beginOutage(CONNECTIVITY_INTERNET_OFFLINE);
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
      try {
        const frontendResponse = await fetchImpl(FRONTEND_HEALTH_PATH, {
          cache: "no-store",
          credentials: "same-origin",
          signal: probeAbortController.signal,
        });
        if (!isSuccessfulHealthResponse(frontendResponse)) {
          beginOutage(CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE, { preserveUnreachable: wasUnreachable });
          return false;
        }
      } catch {
        beginOutage(CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE, { preserveUnreachable: wasUnreachable });
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
    if (documentObject?.visibilityState === "visible") {
      void probe();
    }
  }

  function handleOnline() {
    void probe();
  }

  function handleOffline() {
    beginOutage(CONNECTIVITY_INTERNET_OFFLINE);
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    if (!outageStartedAt) {
      outageStartedAt = Date.now();
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
    if (probeTimeout) {
      windowObject?.clearTimeout?.(probeTimeout);
      probeTimeout = 0;
    }
    activeAbortController?.abort();
    activeAbortController = null;
    inFlightProbe = null;
    windowObject?.removeEventListener?.("online", handleOnline);
    windowObject?.removeEventListener?.("offline", handleOffline);
    documentObject?.removeEventListener?.("visibilitychange", handleVisibilityChange);
  }

  function reportFailure() {
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
    clearUnreachableTimer();
    clearRecoveryInterval();
    outageStartedAt = 0;
    emit({
      status: "connected",
      serviceReachable: true,
      classification: CONNECTIVITY_HEALTHY,
    });
  }

  function retry() {
    if (runtimeReady) {
      return probe();
    }
    outageStartedAt = Date.now();
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
