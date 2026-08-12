import { getPageLifecycleGeneration } from "./pageLifecycle.js";


export const EXTERNAL_NAVIGATION_MAX_AGE_MS = 15 * 60 * 1000;
export const EXPECTED_EXTERNAL_NAVIGATION_REASON = "expected_external_navigation";

const handleRecords = new WeakMap();
const requestOwnerRecords = new WeakMap();
const requestOwners = new Set();
const listeners = new Set();

let currentNavigation = null;
let expiryTimer = 0;
let snapshot = Object.freeze({
  active: false,
  identity: "",
  provider: "",
  operationId: "",
  phase: "idle",
  lifecycleGeneration: 0,
  expiresAt: 0,
});


function emit() {
  listeners.forEach((listener) => listener());
}


function publish(record = null) {
  snapshot = Object.freeze(record ? {
    active: true,
    identity: record.identity,
    provider: record.provider,
    operationId: record.operationId,
    phase: record.phase,
    lifecycleGeneration: record.lifecycleGeneration,
    expiresAt: record.expiresAt,
  } : {
    active: false,
    identity: "",
    provider: "",
    operationId: "",
    phase: "idle",
    lifecycleGeneration: getPageLifecycleGeneration(),
    expiresAt: 0,
  });
  emit();
}


function clearExpiryTimer() {
  if (expiryTimer && typeof window !== "undefined") {
    window.clearTimeout(expiryTimer);
  }
  expiryTimer = 0;
}


function expireCurrentNavigation(record) {
  if (currentNavigation !== record) {
    return;
  }
  currentNavigation = null;
  clearExpiryTimer();
  publish(null);
}


function scheduleExpiry(record) {
  clearExpiryTimer();
  if (typeof window === "undefined") {
    return;
  }
  expiryTimer = window.setTimeout(
    () => expireCurrentNavigation(record),
    Math.max(0, record.expiresAt - Date.now()),
  );
}


function requireHandle(handle) {
  const record = handleRecords.get(handle);
  return record && currentNavigation === record ? record : null;
}


function expectedNavigationReason(record, resource) {
  return Object.freeze({
    category: "cancellation",
    reason: EXPECTED_EXTERNAL_NAVIGATION_REASON,
    identity: record.identity,
    provider: record.provider,
    operationId: record.operationId,
    lifecycleGeneration: record.lifecycleGeneration,
    resource,
  });
}


function abortOwnedReads(record) {
  requestOwners.forEach((ownerRecord) => {
    if (
      ownerRecord.identity === record.identity
      && !ownerRecord.controller.signal.aborted
    ) {
      ownerRecord.controller.abort(expectedNavigationReason(record, ownerRecord.resource));
    }
  });
}


export function beginExternalNavigation({ identity, provider, operationId } = {}) {
  const normalizedIdentity = String(identity || "").trim();
  const normalizedProvider = String(provider || "").trim().toLowerCase();
  const normalizedOperationId = String(operationId || "").trim();
  if (!normalizedIdentity || !normalizedProvider || !normalizedOperationId) {
    throw new TypeError("External navigation requires identity, provider, and operation ID.");
  }
  if (
    currentNavigation
    && currentNavigation.identity === normalizedIdentity
    && currentNavigation.provider === normalizedProvider
    && currentNavigation.operationId === normalizedOperationId
  ) {
    return currentNavigation.handle;
  }
  if (currentNavigation) {
    expireCurrentNavigation(currentNavigation);
  }
  const handle = Object.freeze({});
  const record = {
    handle,
    identity: normalizedIdentity,
    provider: normalizedProvider,
    operationId: normalizedOperationId,
    phase: "starting",
    lifecycleGeneration: getPageLifecycleGeneration(),
    expiresAt: Date.now() + EXTERNAL_NAVIGATION_MAX_AGE_MS,
  };
  handleRecords.set(handle, record);
  currentNavigation = record;
  publish(record);
  scheduleExpiry(record);
  return handle;
}


export function prepareExternalNavigation(handle) {
  const record = requireHandle(handle);
  if (!record) {
    return false;
  }
  record.phase = "preparing_external_navigation";
  publish(record);
  abortOwnedReads(record);
  return true;
}


export function markExternalNavigationNavigating(handle) {
  const record = requireHandle(handle);
  if (!record) {
    return false;
  }
  record.phase = "navigating_external";
  publish(record);
  return true;
}


export function markExternalNavigationReconciling(handle) {
  const record = requireHandle(handle);
  if (!record) {
    return false;
  }
  record.phase = "reconciling_return";
  publish(record);
  abortOwnedReads(record);
  return true;
}


export function completeExternalNavigation(handle) {
  const record = requireHandle(handle);
  if (!record) {
    return false;
  }
  expireCurrentNavigation(record);
  return true;
}


export function clearExternalNavigationForIdentityChange(nextIdentity = "") {
  if (!currentNavigation) {
    return false;
  }
  const normalizedIdentity = String(nextIdentity || "").trim();
  if (normalizedIdentity && currentNavigation.identity === normalizedIdentity) {
    return false;
  }
  expireCurrentNavigation(currentNavigation);
  return true;
}


export function createExternalNavigationAwareRequestOwner({ identity, resource } = {}) {
  const controller = new AbortController();
  const owner = Object.freeze({ signal: controller.signal });
  const ownerRecord = {
    owner,
    controller,
    identity: String(identity || "").trim(),
    resource: String(resource || "").trim().toLowerCase(),
  };
  requestOwnerRecords.set(owner, ownerRecord);
  requestOwners.add(ownerRecord);
  if (
    currentNavigation
    && currentNavigation.identity === ownerRecord.identity
    && ["preparing_external_navigation", "navigating_external", "reconciling_return"]
      .includes(currentNavigation.phase)
  ) {
    controller.abort(expectedNavigationReason(currentNavigation, ownerRecord.resource));
  }
  return owner;
}


export function getExternalNavigationRequestOwnerSignal(owner) {
  return requestOwnerRecords.get(owner)?.controller.signal || null;
}


export function releaseExternalNavigationAwareRequestOwner(owner) {
  const record = requestOwnerRecords.get(owner);
  if (!record) {
    return false;
  }
  requestOwnerRecords.delete(owner);
  requestOwners.delete(record);
  return true;
}


export function isExpectedExternalNavigationReason(reason) {
  return Boolean(reason)
    && reason.category === "cancellation"
    && reason.reason === EXPECTED_EXTERNAL_NAVIGATION_REASON;
}


export function isExternalNavigationSuspendedForIdentity(identity) {
  return Boolean(currentNavigation)
    && currentNavigation.identity === String(identity || "").trim()
    && ["preparing_external_navigation", "navigating_external", "reconciling_return"]
      .includes(currentNavigation.phase);
}


export function getExternalNavigationSnapshot() {
  return snapshot;
}


export function subscribeExternalNavigation(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}


export function resetExternalNavigationCoordinatorForTests() {
  clearExpiryTimer();
  currentNavigation = null;
  requestOwners.forEach((record) => {
    if (!record.controller.signal.aborted) {
      record.controller.abort();
    }
  });
  requestOwners.clear();
  publish(null);
}
