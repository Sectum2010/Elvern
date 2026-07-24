import { hashKey } from "@tanstack/react-query";
import { useEffect } from "react";

import {
  getConnectivityIncidentRecoveryGeneration,
  getConnectivityRecoverySnapshot,
  subscribeConnectivityRecovery,
} from "./connectivityRecoveryStore.js";
import { queryClient } from "./queryClient.js";


const MAX_RECOVERY_CLAIMS = 512;
const recoveryClaims = new Map();
let queryCacheSubscriptionInstalled = false;


function isTransientTransportError(error) {
  return error?.transient === true && Number(error?.failureId) > 0;
}


function recoveryGenerationForError(error) {
  if (!isTransientTransportError(error)) {
    return 0;
  }
  if (Number(error.incidentId) > 0) {
    return getConnectivityIncidentRecoveryGeneration(
      error.incidentId,
      error.failureId,
    );
  }
  const snapshot = getConnectivityRecoverySnapshot();
  return snapshot.latestRecoveredFailureId >= Number(error.failureId)
    ? snapshot.latestRecoveryGeneration
    : 0;
}


function rememberClaim(claimKey) {
  recoveryClaims.delete(claimKey);
  recoveryClaims.set(claimKey, true);
  while (recoveryClaims.size > MAX_RECOVERY_CLAIMS) {
    recoveryClaims.delete(recoveryClaims.keys().next().value);
  }
}


function clearClaimsForQueryHash(queryHash) {
  const suffix = `:${queryHash}`;
  for (const claimKey of recoveryClaims.keys()) {
    if (claimKey.endsWith(suffix)) {
      recoveryClaims.delete(claimKey);
    }
  }
}


function installQueryCacheCleanup() {
  if (queryCacheSubscriptionInstalled) {
    return;
  }
  queryCacheSubscriptionInstalled = true;
  queryClient.getQueryCache().subscribe((event) => {
    if (event?.type === "removed" && event.query?.queryHash) {
      clearClaimsForQueryHash(event.query.queryHash);
    }
  });
}


export function clearBoundedQueryRecoveryBookkeeping() {
  recoveryClaims.clear();
}


export function requestBoundedQueryRecovery({ error, queryKey, refetch }) {
  if (!Array.isArray(queryKey) || typeof refetch !== "function") {
    return false;
  }
  const generation = recoveryGenerationForError(error);
  if (generation <= 0) {
    return false;
  }
  const queryHash = hashKey(queryKey);
  const claimKey = `${generation}:${queryHash}`;
  if (recoveryClaims.has(claimKey)) {
    return false;
  }
  rememberClaim(claimKey);
  void refetch();
  return true;
}


export function useBoundedQueryRecovery(query, { enabled = true, queryKey } = {}) {
  const error = query?.error;
  const refetch = typeof query?.refetch === "function" ? query.refetch : null;

  useEffect(() => {
    installQueryCacheCleanup();
    if (!enabled || !isTransientTransportError(error) || !refetch) {
      return undefined;
    }
    const recover = () => {
      requestBoundedQueryRecovery({ error, queryKey, refetch });
    };
    const unsubscribe = subscribeConnectivityRecovery(recover);
    recover();
    return unsubscribe;
  }, [enabled, error, queryKey, refetch]);
}


export function resetBoundedQueryRecoveryForTests() {
  recoveryClaims.clear();
}
