import {
  CONNECTIVITY_RECOVERED_EVENT,
  STARTUP_CONNECTIVITY_FAILURE_EVENT,
} from "./startupConnection";


export const POSTER_RECOVERY_FAILURE_WINDOW_MS = 30_000;
export const POSTER_RECOVERY_COOLDOWN_MS = 500;

let listenerTarget = null;
let failureSequence = 0;
let lastFailureAt = 0;
const subscribers = new Set();


function notifySubscribers(kind, detail) {
  subscribers.forEach((subscriber) => {
    subscriber({ kind, detail });
  });
}


function handleConnectivityFailure(event) {
  failureSequence += 1;
  lastFailureAt = Date.now();
  notifySubscribers("failure", {
    failureSequence,
    classification: event.detail?.classification || "transport",
  });
}


function handleConnectivityRecovered(event) {
  notifySubscribers("recovered", {
    failureSequence,
    generation: Number(event.detail?.generation || 0),
  });
}


function installListeners() {
  if (listenerTarget || typeof window === "undefined") {
    return;
  }
  listenerTarget = window;
  listenerTarget.addEventListener(
    STARTUP_CONNECTIVITY_FAILURE_EVENT,
    handleConnectivityFailure,
  );
  listenerTarget.addEventListener(
    CONNECTIVITY_RECOVERED_EVENT,
    handleConnectivityRecovered,
  );
}


function removeListenersIfUnused() {
  if (!listenerTarget || subscribers.size > 0) {
    return;
  }
  listenerTarget.removeEventListener(
    STARTUP_CONNECTIVITY_FAILURE_EVENT,
    handleConnectivityFailure,
  );
  listenerTarget.removeEventListener(
    CONNECTIVITY_RECOVERED_EVENT,
    handleConnectivityRecovered,
  );
  listenerTarget = null;
}


export function getPosterRecoveryCandidate({
  now = Date.now(),
  navigatorObject = globalThis.navigator,
} = {}) {
  const browserOffline = navigatorObject?.onLine === false;
  const recentTransportFailure = (
    lastFailureAt > 0
    && now - lastFailureAt <= POSTER_RECOVERY_FAILURE_WINDOW_MS
  );
  return {
    eligible: browserOffline || recentTransportFailure,
    failureSequence,
  };
}


export function subscribePosterRecoveryEvents(subscriber) {
  if (typeof subscriber !== "function") {
    return () => {};
  }
  subscribers.add(subscriber);
  installListeners();
  return () => {
    subscribers.delete(subscriber);
    removeListenersIfUnused();
  };
}


export function resetPosterRecoveryStateForTests() {
  failureSequence = 0;
  lastFailureAt = 0;
}
