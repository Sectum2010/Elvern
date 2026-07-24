export const CONNECTIVITY_RECOVERED_EVENT = "elvern:connectivity-recovered";

const MAX_RECOVERED_INCIDENTS = 64;

let incidentSequence = 0;
let recoveryGeneration = 0;
let snapshot = {
  active: false,
  activeIncidentId: 0,
  latestFailureId: 0,
  latestRecoveredFailureId: 0,
  latestRecoveredIncidentId: 0,
  latestRecoveryGeneration: 0,
};
const listeners = new Set();
const recoveredIncidents = new Map();


function emit() {
  listeners.forEach((listener) => listener());
}


function rememberRecoveredIncident(incidentId, failureId, generation) {
  recoveredIncidents.delete(incidentId);
  recoveredIncidents.set(incidentId, { failureId, generation });
  while (recoveredIncidents.size > MAX_RECOVERED_INCIDENTS) {
    recoveredIncidents.delete(recoveredIncidents.keys().next().value);
  }
}


export function registerConnectivityFailure() {
  const failureId = snapshot.latestFailureId + 1;
  const incidentId = snapshot.active
    ? snapshot.activeIncidentId
    : incidentSequence + 1;
  if (!snapshot.active) {
    incidentSequence = incidentId;
  }
  snapshot = {
    ...snapshot,
    active: true,
    activeIncidentId: incidentId,
    latestFailureId: failureId,
  };
  emit();
  return { failureId, incidentId };
}


export function publishConnectivityRecovery({
  generation: requestedGeneration = 0,
  previousClassification = "service_unreachable",
  recoveredThroughFailureId,
} = {}) {
  const normalizedFailureId = Number(recoveredThroughFailureId);
  if (
    !snapshot.active
    || !Number.isInteger(normalizedFailureId)
    || normalizedFailureId < snapshot.latestFailureId
  ) {
    return null;
  }

  const incidentId = snapshot.activeIncidentId;
  recoveryGeneration = Math.max(
    recoveryGeneration + 1,
    Number(requestedGeneration) || 0,
  );
  snapshot = {
    ...snapshot,
    active: false,
    latestRecoveredFailureId: normalizedFailureId,
    latestRecoveredIncidentId: incidentId,
    latestRecoveryGeneration: recoveryGeneration,
  };
  rememberRecoveredIncident(incidentId, normalizedFailureId, recoveryGeneration);
  emit();

  const detail = {
    generation: recoveryGeneration,
    incidentId,
    previousClassification,
    recoveredThroughFailureId: normalizedFailureId,
  };
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new CustomEvent(CONNECTIVITY_RECOVERED_EVENT, { detail }));
  }
  return detail;
}


export function getConnectivityRecoverySnapshot() {
  return snapshot;
}


export function wasConnectivityIncidentRecovered(incidentId, failureId = 0) {
  const recovered = recoveredIncidents.get(Number(incidentId));
  return Boolean(
    recovered
    && recovered.failureId >= Number(failureId || 0),
  );
}


export function getConnectivityIncidentRecoveryGeneration(incidentId, failureId = 0) {
  const recovered = recoveredIncidents.get(Number(incidentId));
  return recovered && recovered.failureId >= Number(failureId || 0)
    ? recovered.generation
    : 0;
}


export function subscribeConnectivityRecovery(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}


export function resetConnectivityRecoveryStoreForTests() {
  incidentSequence = 0;
  recoveryGeneration = 0;
  snapshot = {
    active: false,
    activeIncidentId: 0,
    latestFailureId: 0,
    latestRecoveredFailureId: 0,
    latestRecoveredIncidentId: 0,
    latestRecoveryGeneration: 0,
  };
  recoveredIncidents.clear();
  emit();
}
