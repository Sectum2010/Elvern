import { useEffect, useRef } from "react";

import { useAuth } from "../auth/AuthContext.jsx";
import { apiRequest } from "./api.js";
import {
  invalidateLibraryQueriesForIdentity,
  LIBRARY_QUERY_GC_TIME_MS,
  patchLibraryProgressStateCaches,
} from "./libraryQueries.js";
import { queryClient } from "./queryClient.js";
import { PAGE_RESUME_EVENT } from "./pageResume.js";


export const LIBRARY_REVISION_VISIBLE_INTERVAL_MS = 60_000;
export const LIBRARY_PROGRESS_REVISION_IMMEDIATE_RETRY_MAX = 2;
export const LIBRARY_REVISION_CHECK_EVENT = "elvern:library-revision-check";
export const LIBRARY_REVISION_QUERY_PREFIX = Object.freeze(["library-revision", "v1"]);
const OPAQUE_REVISION_TOKEN_PATTERN = /^[0-9a-f]{64}$/;
const PROGRESS_STATE_TOP_LEVEL_FIELDS = new Set(["schema_version", "progress_revision", "items"]);
const PROGRESS_STATE_ITEM_FIELDS = new Set([
  "id",
  "progress_seconds",
  "progress_duration_seconds",
  "completed",
]);
export const REVISION_PAYLOAD_FIELDS = Object.freeze([
  "schema_version",
  "catalog",
  "presentation",
  "permission",
  "user_overlay",
  "progress",
  "combined_library",
]);
const REVISION_TOKEN_FIELDS = REVISION_PAYLOAD_FIELDS.filter((field) => field !== "schema_version");
const REVISION_FIELDS = REVISION_TOKEN_FIELDS;
const SUMMARY_REFRESH_LAYER_FIELDS = Object.freeze([
  "catalog",
  "presentation",
  "permission",
  "user_overlay",
]);


export class LibraryRevisionContractError extends Error {
  constructor(message) {
    super(message);
    this.name = "LibraryRevisionContractError";
  }
}


export class LibraryProgressStateContractError extends Error {
  constructor(message) {
    super(message);
    this.name = "LibraryProgressStateContractError";
  }
}


export class LibraryProgressRevisionRaceError extends Error {
  constructor() {
    super("Library progress revision changed while the progress snapshot was loading");
    this.name = "LibraryProgressRevisionRaceError";
  }
}


export class LibraryRevisionOperationStaleError extends Error {
  constructor() {
    super("Library revision operation no longer belongs to the current identity");
    this.name = "LibraryRevisionOperationStaleError";
  }
}


export function resolveLibraryRevisionMode(rawValue = import.meta.env?.VITE_ELVERN_LIBRARY_REVISION_MODE) {
  const normalized = String(rawValue || "").trim().toLowerCase();
  if (!normalized) return "on";
  return normalized === "on" ? "on" : "off";
}


export function buildLibraryRevisionQueryKey(user) {
  return [
    ...LIBRARY_REVISION_QUERY_PREFIX,
    {
      userId: String(user?.id ?? "").trim(),
      role: String(user?.role || "").trim().toLowerCase(),
      permissionIdentity: JSON.stringify({
        ageCredential: Number(user?.age_credential ?? 18),
        assistantBetaEnabled: Boolean(user?.assistant_beta_enabled),
      }),
    },
  ];
}


export function validateLibraryRevisionPayload(payload) {
  if (
    !payload
    || typeof payload !== "object"
    || Array.isArray(payload)
    || ![Object.prototype, null].includes(Object.getPrototypeOf(payload))
  ) {
    throw new LibraryRevisionContractError("Invalid Library revision payload");
  }
  const payloadFields = Object.keys(payload);
  if (
    payloadFields.length !== REVISION_PAYLOAD_FIELDS.length
    || !REVISION_PAYLOAD_FIELDS.every((field) => Object.hasOwn(payload, field))
    || !payloadFields.every((field) => REVISION_PAYLOAD_FIELDS.includes(field))
  ) {
    throw new LibraryRevisionContractError("Invalid Library revision field set");
  }
  if (payload?.schema_version !== "library-revision-v1") {
    throw new LibraryRevisionContractError("Invalid Library revision schema");
  }
  for (const field of REVISION_TOKEN_FIELDS) {
    if (typeof payload[field] !== "string" || !OPAQUE_REVISION_TOKEN_PATTERN.test(payload[field])) {
      throw new LibraryRevisionContractError(`Invalid Library revision field: ${field}`);
    }
  }
  return payload;
}


function hasOnlyFields(value, allowedFields) {
  return Object.keys(value).every((field) => allowedFields.has(field));
}


export function validateLibraryProgressStatePayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new LibraryProgressStateContractError("Invalid Library progress-state payload");
  }
  if (!hasOnlyFields(payload, PROGRESS_STATE_TOP_LEVEL_FIELDS)) {
    throw new LibraryProgressStateContractError("Unexpected Library progress-state field");
  }
  if (payload.schema_version !== "library-progress-state-v1") {
    throw new LibraryProgressStateContractError("Invalid Library progress-state schema");
  }
  if (!OPAQUE_REVISION_TOKEN_PATTERN.test(payload.progress_revision)) {
    throw new LibraryProgressStateContractError("Invalid Library progress revision token");
  }
  if (!Array.isArray(payload.items)) {
    throw new LibraryProgressStateContractError("Library progress-state items must be an array");
  }

  const seenIds = new Set();
  for (const item of payload.items) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new LibraryProgressStateContractError("Invalid Library progress-state item");
    }
    if (!hasOnlyFields(item, PROGRESS_STATE_ITEM_FIELDS)) {
      throw new LibraryProgressStateContractError("Unexpected Library progress-state item field");
    }
    if (![...PROGRESS_STATE_ITEM_FIELDS].every((field) => Object.hasOwn(item, field))) {
      throw new LibraryProgressStateContractError("Missing Library progress-state item field");
    }
    if (!Number.isInteger(item.id) || item.id <= 0 || seenIds.has(item.id)) {
      throw new LibraryProgressStateContractError("Invalid or duplicate Library progress-state item id");
    }
    if (!Number.isFinite(item.progress_seconds) || item.progress_seconds < 0) {
      throw new LibraryProgressStateContractError("Invalid Library progress seconds");
    }
    if (
      item.progress_duration_seconds !== null
      && (!Number.isFinite(item.progress_duration_seconds) || item.progress_duration_seconds < 0)
    ) {
      throw new LibraryProgressStateContractError("Invalid Library progress duration");
    }
    if (typeof item.completed !== "boolean") {
      throw new LibraryProgressStateContractError("Invalid Library progress completion state");
    }
    seenIds.add(item.id);
  }
  return payload;
}


function errorDetailCode(error) {
  const detail = error?.detail ?? error?.payload?.detail;
  return detail?.code;
}


export function isLibraryRevisionGloballyDisabledError(error) {
  return Number(error?.status) === 503 && errorDetailCode(error) === "library_revision_disabled";
}


export function isLibraryRevisionEndpointCapabilityUnavailableError(error) {
  return Number(error?.status) === 404 || isLibraryRevisionGloballyDisabledError(error);
}


export function isLibraryProgressStateSubCapabilityUnavailableError(error) {
  if (Number(error?.status) === 404) return true;
  return Number(error?.status) === 503 && errorDetailCode(error) === "library_progress_state_disabled";
}


function assertOperationCurrent(isOperationCurrent) {
  if (!isOperationCurrent()) throw new LibraryRevisionOperationStaleError();
}


function buildApplyResult({
  baseline = false,
  changedLayers = [],
  nextBaseline,
  immediateRetryRequired = false,
  progressError = null,
  progressCapabilityUnavailable = false,
  revisionCapabilityUnavailable = false,
  summaryRefreshReasons = [],
}) {
  return {
    baseline,
    changedLayers,
    nextBaseline,
    immediateRetryRequired,
    progressError,
    progressCapabilityUnavailable,
    revisionCapabilityUnavailable,
    summaryRefreshRequired: summaryRefreshReasons.length > 0,
    summaryRefreshReasons,
  };
}


export async function applyLibraryRevisionChange({
  previous,
  current,
  refreshAuth,
  identity,
  progressStateCapabilityUnavailable = false,
  isOperationCurrent = () => true,
}) {
  assertOperationCurrent(isOperationCurrent);
  if (!previous) {
    return buildApplyResult({
      baseline: true,
      nextBaseline: current,
      progressCapabilityUnavailable: progressStateCapabilityUnavailable,
    });
  }
  const changedLayers = REVISION_FIELDS.filter((field) => previous[field] !== current[field]);
  const summaryRefreshReasons = SUMMARY_REFRESH_LAYER_FIELDS.filter((field) => changedLayers.includes(field));
  let progressState = null;
  let progressError = null;
  let progressCapabilityUnavailable = progressStateCapabilityUnavailable;
  let revisionCapabilityUnavailable = false;
  if (changedLayers.includes("progress")) {
    if (progressStateCapabilityUnavailable) {
      summaryRefreshReasons.push("progress_capability_fallback");
    } else {
      try {
        const progressPayload = await apiRequest("/api/library/v2/progress-state", {
          cache: "no-store",
          abortOnPageHide: true,
        });
        assertOperationCurrent(isOperationCurrent);
        progressState = validateLibraryProgressStatePayload(progressPayload);
        if (progressState.progress_revision !== current.progress) {
          throw new LibraryProgressRevisionRaceError();
        }
      } catch (error) {
        if (error instanceof LibraryRevisionOperationStaleError) throw error;
        progressState = null;
        progressError = error;
        if (isLibraryRevisionGloballyDisabledError(error)) {
          revisionCapabilityUnavailable = true;
        } else if (isLibraryProgressStateSubCapabilityUnavailableError(error)) {
          progressCapabilityUnavailable = true;
          summaryRefreshReasons.push("progress_capability_fallback");
        }
      }
    }
  }

  if (revisionCapabilityUnavailable) {
    const nextBaseline = { ...current, progress: previous.progress };
    return buildApplyResult({
      changedLayers,
      nextBaseline,
      progressError,
      progressCapabilityUnavailable: false,
      revisionCapabilityUnavailable: true,
    });
  }

  if (changedLayers.includes("permission")) {
    assertOperationCurrent(isOperationCurrent);
    await refreshAuth?.({ notifyOnFailure: true });
    assertOperationCurrent(isOperationCurrent);
  }
  if (changedLayers.includes("presentation")) {
    assertOperationCurrent(isOperationCurrent);
    await queryClient.invalidateQueries({ queryKey: ["user-settings"], refetchType: "active" });
    assertOperationCurrent(isOperationCurrent);
  }
  if (progressState) {
    assertOperationCurrent(isOperationCurrent);
    const patchResult = await patchLibraryProgressStateCaches(progressState, identity);
    assertOperationCurrent(isOperationCurrent);
    if (patchResult.membershipMayHaveChanged) {
      summaryRefreshReasons.push("progress_membership");
    }
  }
  if (summaryRefreshReasons.length > 0) {
    assertOperationCurrent(isOperationCurrent);
    await invalidateLibraryQueriesForIdentity({ ...identity, refetchType: "active" });
    assertOperationCurrent(isOperationCurrent);
  }
  const nextBaseline = { ...current };
  if (progressError && !progressCapabilityUnavailable) {
    nextBaseline.progress = previous.progress;
  }
  return buildApplyResult({
    changedLayers,
    nextBaseline,
    immediateRetryRequired: progressError instanceof LibraryProgressRevisionRaceError,
    progressError,
    progressCapabilityUnavailable,
    summaryRefreshReasons,
  });
}


export function LibraryRevisionSynchronizer() {
  const { user, refreshAuth } = useAuth();
  const baselineRef = useRef(null);
  const inFlightRef = useRef(null);
  const timerRef = useRef(0);
  const lifecycleGenerationRef = useRef(0);
  const mode = resolveLibraryRevisionMode();
  const identity = user ? JSON.stringify(buildLibraryRevisionQueryKey(user)[2]) : "";

  useEffect(() => {
    const lifecycleGeneration = lifecycleGenerationRef.current + 1;
    lifecycleGenerationRef.current = lifecycleGeneration;
    baselineRef.current = null;
    inFlightRef.current = null;
    if (mode !== "on" || !user) return undefined;

    let active = true;
    let revisionCapabilityUnavailable = false;
    let progressStateCapabilityUnavailable = false;
    const queryKey = buildLibraryRevisionQueryKey(user);
    const protectedIdentity = {
      userId: queryKey[2].userId,
      role: queryKey[2].role,
    };
    const isOperationCurrent = () => (
      active && lifecycleGenerationRef.current === lifecycleGeneration
    );

    function clearTimer() {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = 0;
    }

    function schedule() {
      clearTimer();
      if (!isOperationCurrent() || revisionCapabilityUnavailable || document.visibilityState === "hidden") return;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = 0;
        void check();
      }, LIBRARY_REVISION_VISIBLE_INTERVAL_MS);
    }

    async function check(immediateRetryAttempt = 0) {
      if (!isOperationCurrent() || revisionCapabilityUnavailable || document.visibilityState === "hidden") return false;
      if (inFlightRef.current) return inFlightRef.current;
      let retryImmediately = false;
      const operation = (async () => {
        try {
          let current;
          try {
            current = await queryClient.fetchQuery({
              queryKey,
              queryFn: ({ signal }) => apiRequest("/api/library/v2/revision", {
                signal,
                cache: "no-store",
                abortOnPageHide: true,
              })
                .then(validateLibraryRevisionPayload),
              staleTime: 0,
              gcTime: LIBRARY_QUERY_GC_TIME_MS,
              retry: false,
            });
          } catch (error) {
            if (isLibraryRevisionEndpointCapabilityUnavailableError(error)) {
              revisionCapabilityUnavailable = true;
              clearTimer();
            }
            return false;
          }
          if (!isOperationCurrent()) return false;
          const result = await applyLibraryRevisionChange({
            previous: baselineRef.current,
            current,
            refreshAuth,
            identity: protectedIdentity,
            progressStateCapabilityUnavailable,
            isOperationCurrent,
          });
          if (!isOperationCurrent()) return false;
          progressStateCapabilityUnavailable = result.progressCapabilityUnavailable;
          if (result.revisionCapabilityUnavailable) {
            revisionCapabilityUnavailable = true;
            clearTimer();
            return false;
          }
          if (!isOperationCurrent()) return false;
          baselineRef.current = result.nextBaseline;
          retryImmediately = result.immediateRetryRequired
            && immediateRetryAttempt < LIBRARY_PROGRESS_REVISION_IMMEDIATE_RETRY_MAX;
          return true;
        } catch (error) {
          if (error instanceof LibraryRevisionOperationStaleError) return false;
          return false;
        } finally {
          if (inFlightRef.current === operation) inFlightRef.current = null;
          if (retryImmediately && isOperationCurrent() && !revisionCapabilityUnavailable) {
            void Promise.resolve().then(() => check(immediateRetryAttempt + 1));
          } else {
            schedule();
          }
        }
      })();
      inFlightRef.current = operation;
      return operation;
    }

    function checkWhenVisible() {
      if (document.visibilityState !== "hidden") void check();
    }

    function handleVisibility() {
      if (document.visibilityState === "hidden") clearTimer();
      else void check();
    }

    window.addEventListener(PAGE_RESUME_EVENT, checkWhenVisible);
    window.addEventListener("online", checkWhenVisible);
    window.addEventListener(LIBRARY_REVISION_CHECK_EVENT, checkWhenVisible);
    document.addEventListener("visibilitychange", handleVisibility);
    void check();

    return () => {
      active = false;
      clearTimer();
      void queryClient.cancelQueries({ queryKey });
      window.removeEventListener(PAGE_RESUME_EVENT, checkWhenVisible);
      window.removeEventListener("online", checkWhenVisible);
      window.removeEventListener(LIBRARY_REVISION_CHECK_EVENT, checkWhenVisible);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [identity, mode, refreshAuth]);

  return null;
}
