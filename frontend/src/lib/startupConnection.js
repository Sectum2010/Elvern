export const STARTUP_UNREACHABLE_DELAY_MS = 60_000;
export const STARTUP_HEALTH_PROBE_INTERVAL_MS = 10_000;
export const STARTUP_HEALTH_PROBE_TIMEOUT_MS = 5_000;
export const STARTUP_SHELL_REVEAL_DELAY_MS = 400;
export const STARTUP_CONNECTIVITY_FAILURE_EVENT = "elvern:connectivity-failure";
export const STARTUP_APPLICATION_READY_EVENT = "elvern:application-response";
export const CONNECTION_OOPS_TITLE = "Oops!";
export const CONNECTION_OOPS_COPY = "Elvern could not be reached. Check your connection and try again.";
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
  requireApplicationReady = false,
  initialOutageStartedAt = Number(windowObject?.__elvernConnectionStartedAt) || 0,
} = {}) {
  let snapshot = { status: "connecting", serviceReachable: false };
  let applicationReady = !requireApplicationReady;
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

  function emit(nextStatus, serviceReachable = snapshot.serviceReachable) {
    if (snapshot.status === nextStatus && snapshot.serviceReachable === serviceReachable) {
      return;
    }
    snapshot = { status: nextStatus, serviceReachable };
    listeners.forEach((listener) => listener());
  }

  function clearUnreachableTimer() {
    if (unreachableTimer) {
      windowObject?.clearTimeout?.(unreachableTimer);
      unreachableTimer = 0;
    }
  }

  function scheduleUnreachable() {
    clearUnreachableTimer();
    const elapsed = Math.max(0, Date.now() - outageStartedAt);
    const remaining = Math.max(0, STARTUP_UNREACHABLE_DELAY_MS - elapsed);
    unreachableTimer = windowObject?.setTimeout?.(() => {
      unreachableTimer = 0;
      if (snapshot.status === "connecting") {
        emit("unreachable", snapshot.serviceReachable);
      }
    }, remaining) || 0;
  }

  function beginConnecting({ serviceReachable = false } = {}) {
    if (snapshot.status === "unreachable") {
      emit("unreachable", serviceReachable);
      return;
    }
    if (snapshot.status !== "connecting") {
      outageStartedAt = Date.now();
      emit("connecting", serviceReachable);
      scheduleUnreachable();
      return;
    }
    emit("connecting", serviceReachable);
  }

  function handleProbeFailure() {
    applicationReady = !requireApplicationReady;
    if (snapshot.status === "connected") {
      beginConnecting();
    } else {
      emit(snapshot.status, false);
    }
  }

  function probe() {
    if (inFlightProbe || typeof fetchImpl !== "function") {
      return inFlightProbe || Promise.resolve(false);
    }
    activeAbortController = new AbortController();
    probeTimeout = windowObject?.setTimeout?.(() => {
      activeAbortController?.abort();
    }, STARTUP_HEALTH_PROBE_TIMEOUT_MS) || 0;
    inFlightProbe = Promise.resolve(fetchImpl("/health", {
      cache: "no-store",
      credentials: "same-origin",
      signal: activeAbortController.signal,
    }))
      .then((response) => {
        if (!isSuccessfulHealthResponse(response)) {
          handleProbeFailure();
          return false;
        }
        if (applicationReady) {
          clearUnreachableTimer();
          emit("connected", true);
        } else {
          emit(snapshot.status, true);
        }
        return true;
      })
      .catch(() => {
        handleProbeFailure();
        return false;
      })
      .finally(() => {
        if (probeTimeout) {
          windowObject?.clearTimeout?.(probeTimeout);
          probeTimeout = 0;
        }
        activeAbortController = null;
        inFlightProbe = null;
      });
    return inFlightProbe;
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
    if (snapshot.status === "connected") {
      beginConnecting();
    }
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    if (snapshot.status !== "connected") {
      snapshot = { status: "connecting", serviceReachable: false };
      if (!outageStartedAt) {
        outageStartedAt = Date.now();
      }
      scheduleUnreachable();
    }
    void probe();
    probeInterval = windowObject?.setInterval?.(() => {
      void probe();
    }, STARTUP_HEALTH_PROBE_INTERVAL_MS) || 0;
    windowObject?.addEventListener?.("online", handleOnline);
    windowObject?.addEventListener?.("offline", handleOffline);
    documentObject?.addEventListener?.("visibilitychange", handleVisibilityChange);
  }

  function stop() {
    started = false;
    clearUnreachableTimer();
    if (probeInterval) {
      windowObject?.clearInterval?.(probeInterval);
      probeInterval = 0;
    }
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
    handleProbeFailure();
  }

  function reportApplicationReady() {
    applicationReady = true;
    if (snapshot.serviceReachable) {
      clearUnreachableTimer();
      emit("connected", true);
    }
  }

  function retry() {
    outageStartedAt = Date.now();
    applicationReady = !requireApplicationReady;
    emit("connecting", false);
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
