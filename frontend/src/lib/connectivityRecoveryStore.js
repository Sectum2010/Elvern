export const CONNECTIVITY_RECOVERED_EVENT = "elvern:connectivity-recovered";
export const CONNECTIVITY_MANUAL_RETRY_EVENT = "elvern:connectivity-manual-retry";

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
  prolonged: false,
};
const listeners = new Set();
const incidents = new Map();


function emit() {
  listeners.forEach((listener) => listener());
}


function rememberIncident(record) {
  incidents.delete(record.incidentId);
  incidents.set(record.incidentId, record);
  while (incidents.size > MAX_RECOVERED_INCIDENTS) {
    incidents.delete(incidents.keys().next().value);
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
  const existing = incidents.get(incidentId);
  rememberIncident({
    incidentId,
    firstFailureId: existing?.firstFailureId || failureId,
    latestFailureId: failureId,
    prolonged: false,
    active: true,
    recovered: false,
    recoveryGeneration: 0,
  });
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
    prolonged: false,
  };
  const activeIncident = incidents.get(incidentId);
  rememberIncident({
    incidentId,
    firstFailureId: activeIncident?.firstFailureId || normalizedFailureId,
    latestFailureId: normalizedFailureId,
    active: false,
    recovered: true,
    recoveryGeneration,
  });
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
  return getConnectivityIncidentRecoveryGeneration(incidentId, failureId) > 0;
}


export function getConnectivityIncidentRecoveryGeneration(incidentId, failureId = 0) {
  const normalizedFailureId = Number(failureId || 0);
  const incident = incidents.get(Number(incidentId));
  if (
    incident
    && incident.recovered
    && incident.latestFailureId >= normalizedFailureId
  ) {
    return incident.recoveryGeneration;
  }
  if (
    !incident
    && normalizedFailureId > 0
    && snapshot.latestRecoveredFailureId >= normalizedFailureId
  ) {
    return snapshot.latestRecoveryGeneration;
  }
  return 0;
}


export function getConnectivityIncidentAfterFailure(
  failureWatermark,
  throughFailureId = snapshot.latestFailureId,
) {
  const lowerBound = Number(failureWatermark || 0);
  const upperBound = Number(throughFailureId || 0);
  const records = Array.from(incidents.values());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const incident = records[index];
    if (
      incident.firstFailureId > lowerBound
      && incident.firstFailureId <= upperBound
    ) {
      return { ...incident };
    }
  }
  return null;
}


export function subscribeConnectivityRecovery(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}


export function setConnectivityIncidentProlonged(prolonged) {
  const nextValue = snapshot.active && prolonged === true;
  if (snapshot.prolonged === nextValue) {
    return;
  }
  snapshot = { ...snapshot, prolonged: nextValue };
  emit();
}


export function requestConnectivityManualRetry() {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return false;
  }
  window.dispatchEvent(new CustomEvent(CONNECTIVITY_MANUAL_RETRY_EVENT));
  return true;
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
    prolonged: false,
  };
  incidents.clear();
  emit();
}
