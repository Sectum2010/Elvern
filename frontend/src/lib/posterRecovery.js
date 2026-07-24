import {
  getConnectivityIncidentAfterFailure,
  getConnectivityRecoverySnapshot,
  resetConnectivityRecoveryStoreForTests,
  subscribeConnectivityRecovery,
  wasConnectivityIncidentRecovered,
} from "./connectivityRecoveryStore.js";


export const POSTER_RECOVERY_COOLDOWN_MS = 500;
export const MAX_POSTER_RECOVERY_ATTEMPTED_INCIDENTS = 6;


export function getPosterRecoveryAttachContext() {
  const snapshot = getConnectivityRecoverySnapshot();
  return {
    attachFailureWatermark: snapshot.latestFailureId,
    boundIncidentId: snapshot.active ? snapshot.activeIncidentId : 0,
    boundFailureId: snapshot.active ? snapshot.latestFailureId : 0,
  };
}


// Kept as a compatibility alias for focused callers. Eligibility is now
// captured when the image request attaches, not when onError happens.
export const getPosterRecoveryCandidate = getPosterRecoveryAttachContext;


export function resolvePosterRecoveryErrorContext(context) {
  const snapshot = getConnectivityRecoverySnapshot();
  const boundIncidentId = Number(context?.boundIncidentId || 0);
  const boundFailureId = Number(context?.boundFailureId || 0);
  const searchWatermark = Math.max(
    Number(context?.attachFailureWatermark || 0),
    boundFailureId,
  );
  const newerIncident = getConnectivityIncidentAfterFailure(
    searchWatermark,
    snapshot.latestFailureId,
  );
  if (newerIncident) {
    return {
      ...context,
      boundIncidentId: newerIncident.incidentId,
      boundFailureId: newerIncident.latestFailureId,
    };
  }
  if (
    boundIncidentId > 0
    && snapshot.active
    && snapshot.activeIncidentId === boundIncidentId
    && snapshot.latestFailureId > boundFailureId
  ) {
    return {
      ...context,
      boundFailureId: snapshot.latestFailureId,
    };
  }
  if (boundIncidentId > 0) {
    return context;
  }
  const incident = getConnectivityIncidentAfterFailure(
    context?.attachFailureWatermark,
    snapshot.latestFailureId,
  );
  return incident
    ? {
      ...context,
      boundIncidentId: incident.incidentId,
      boundFailureId: incident.latestFailureId,
    }
    : context;
}


export function isPosterAttachContextRecovered(context) {
  return Boolean(
    Number(context?.boundIncidentId) > 0
    && wasConnectivityIncidentRecovered(
      context.boundIncidentId,
      context.boundFailureId,
    ),
  );
}


export function hasPosterRecoveryAttempt(context, incidentId) {
  const normalizedIncidentId = Number(incidentId || 0);
  return normalizedIncidentId > 0
    && Array.isArray(context?.attemptedIncidentIds)
    && context.attemptedIncidentIds.includes(normalizedIncidentId);
}


export function markPosterRecoveryAttempt(context, incidentId) {
  const normalizedIncidentId = Number(incidentId || 0);
  if (normalizedIncidentId <= 0 || hasPosterRecoveryAttempt(context, normalizedIncidentId)) {
    return context;
  }
  const attemptedIncidentIds = [
    ...(Array.isArray(context?.attemptedIncidentIds)
      ? context.attemptedIncidentIds
      : []),
    normalizedIncidentId,
  ].slice(-MAX_POSTER_RECOVERY_ATTEMPTED_INCIDENTS);
  return {
    ...context,
    attemptedIncidentIds,
  };
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
