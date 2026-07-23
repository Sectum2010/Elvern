import {
  CONNECTIVITY_RECOVERED_EVENT,
  STARTUP_CONNECTIVITY_FAILURE_EVENT,
} from "./startupConnection";


export const POSTER_RECOVERY_FAILURE_WINDOW_MS = 30_000;
export const POSTER_RECOVERY_COOLDOWN_MS = 500;

let listenerTarget = null;
// A transport incident opens on the first connectivity failure and stays open
// (coalescing further failures) until a recovery closes it. Posters bind to the
// incident that was open when they failed, so a later, unrelated incident can
// never retroactively retry a poster that failed before it.
let incidentSequence = 0;
let currentIncident = 0;
let lastFailureAt = 0;
const subscribers = new Set();


function notifySubscribers(kind, detail) {
  subscribers.forEach((subscriber) => {
    subscriber({ kind, detail });
  });
}


function handleConnectivityFailure(event) {
  if (currentIncident === 0) {
    incidentSequence += 1;
    currentIncident = incidentSequence;
  }
  lastFailureAt = Date.now();
  notifySubscribers("failure", {
    incident: currentIncident,
    classification: event.detail?.classification || "transport",
  });
}


function handleConnectivityRecovered(event) {
  const incident = currentIncident;
  currentIncident = 0;
  notifySubscribers("recovered", {
    incident,
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
  const incidentRecent = (
    currentIncident !== 0
    && lastFailureAt > 0
    && now - lastFailureAt <= POSTER_RECOVERY_FAILURE_WINDOW_MS
  );
  if (incidentRecent || browserOffline) {
    if (currentIncident === 0) {
      // The poster failed under an explicit browser-offline condition before any
      // API-driven incident opened. Open one now so the eventual recovery can
      // bind to it.
      incidentSequence += 1;
      currentIncident = incidentSequence;
      lastFailureAt = now;
    }
    return { eligible: true, incident: currentIncident };
  }
  return { eligible: false, incident: 0 };
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
  incidentSequence = 0;
  currentIncident = 0;
  lastFailureAt = 0;
}
