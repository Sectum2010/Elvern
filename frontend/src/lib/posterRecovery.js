import {
  getConnectivityIncidentAfterFailure,
  getConnectivityRecoverySnapshot,
  resetConnectivityRecoveryStoreForTests,
  subscribeConnectivityRecovery,
  wasConnectivityIncidentRecovered,
} from "./connectivityRecoveryStore.js";


export const POSTER_RECOVERY_COOLDOWN_MS = 500;


export function getPosterRecoveryAttachContext() {
  const snapshot = getConnectivityRecoverySnapshot();
  return snapshot.active
    ? {
      eligible: true,
      incident: snapshot.activeIncidentId,
      failureId: snapshot.latestFailureId,
      attachFailureWatermark: snapshot.latestFailureId,
    }
    : {
      eligible: false,
      incident: 0,
      failureId: 0,
      attachFailureWatermark: snapshot.latestFailureId,
    };
}


// Kept as a compatibility alias for focused callers. Eligibility is now
// captured when the image request attaches, not when onError happens.
export const getPosterRecoveryCandidate = getPosterRecoveryAttachContext;


export function resolvePosterRecoveryErrorContext(context) {
  if (context?.eligible) {
    return context;
  }
  const incident = getConnectivityIncidentAfterFailure(
    context?.attachFailureWatermark,
  );
  if (!incident) {
    return context;
  }
  return {
    ...context,
    eligible: true,
    incident: incident.incidentId,
    failureId: incident.latestFailureId,
  };
}


export function isPosterAttachContextRecovered(context) {
  return Boolean(
    context?.eligible
    && Number(context.incident) > 0
    && wasConnectivityIncidentRecovered(context.incident, context.failureId),
  );
}


export function subscribePosterRecoveryEvents(subscriber) {
  if (typeof subscriber !== "function") {
    return () => {};
  }
  return subscribeConnectivityRecovery(() => {
    subscriber({
      kind: "recovered",
      detail: getConnectivityRecoverySnapshot(),
    });
  });
}


export function resetPosterRecoveryStateForTests() {
  resetConnectivityRecoveryStoreForTests();
}
