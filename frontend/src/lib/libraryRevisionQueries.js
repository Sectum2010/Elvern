import { useEffect, useRef } from "react";

import { useAuth } from "../auth/AuthContext.jsx";
import { apiRequest } from "./api.js";
import {
  invalidateLibraryQueries,
  LIBRARY_QUERY_GC_TIME_MS,
  patchLibraryProgressStateCaches,
} from "./libraryQueries.js";
import { queryClient } from "./queryClient.js";


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
const REVISION_FIELDS = Object.freeze([
  "catalog",
  "presentation",
  "permission",
  "user_overlay",
  "progress",
  "combined_library",
]);


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
  if (payload?.schema_version !== "library-revision-v1") {
    throw new Error("Invalid Library revision schema");
  }
  for (const field of REVISION_FIELDS) {
    if (!OPAQUE_REVISION_TOKEN_PATTERN.test(payload[field])) {
      throw new Error(`Invalid Library revision field: ${field}`);
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


export function isLibraryRevisionCapabilityUnavailableError(error) {
  const status = Number(error?.status);
  if (status === 404) return true;
  if (status !== 503) return false;
  const detail = error?.detail ?? error?.payload?.detail;
  return detail?.code === "library_revision_disabled";
}


export async function applyLibraryRevisionChange({ previous, current, refreshAuth }) {
  if (!previous) {
    return {
      baseline: true,
      changedLayers: [],
      nextBaseline: current,
      immediateRetryRequired: false,
      progressError: null,
      capabilityUnavailable: false,
    };
  }
  const changedLayers = REVISION_FIELDS.filter((field) => previous[field] !== current[field]);
  let progressState = null;
  let progressError = null;
  if (changedLayers.includes("progress")) {
    try {
      progressState = validateLibraryProgressStatePayload(
        await apiRequest("/api/library/v2/progress-state", { cache: "no-store" }),
      );
      if (progressState.progress_revision !== current.progress) {
        throw new LibraryProgressRevisionRaceError();
      }
    } catch (error) {
      progressState = null;
      progressError = error;
    }
  }

  if (changedLayers.includes("permission")) {
    await refreshAuth?.({ notifyOnFailure: true });
  }
  if (changedLayers.includes("presentation")) {
    await queryClient.invalidateQueries({ queryKey: ["user-settings"], refetchType: "active" });
  }
  if (changedLayers.some((field) => ["catalog", "presentation", "permission", "user_overlay"].includes(field))) {
    await invalidateLibraryQueries({ refetchType: "active" });
  }
  if (progressState) {
    await patchLibraryProgressStateCaches(progressState);
  }
  const nextBaseline = { ...current };
  if (progressError) {
    nextBaseline.progress = previous.progress;
  }
  return {
    baseline: false,
    changedLayers,
    nextBaseline,
    immediateRetryRequired: progressError instanceof LibraryProgressRevisionRaceError,
    progressError,
    capabilityUnavailable: isLibraryRevisionCapabilityUnavailableError(progressError),
  };
}


export function LibraryRevisionSynchronizer() {
  const { user, refreshAuth } = useAuth();
  const baselineRef = useRef(null);
  const inFlightRef = useRef(null);
  const timerRef = useRef(0);
  const mode = resolveLibraryRevisionMode();
  const identity = user ? JSON.stringify(buildLibraryRevisionQueryKey(user)[2]) : "";

  useEffect(() => {
    baselineRef.current = null;
    inFlightRef.current = null;
    if (mode !== "on" || !user) return undefined;

    let active = true;
    let capabilityUnavailable = false;
    const queryKey = buildLibraryRevisionQueryKey(user);

    function clearTimer() {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = 0;
    }

    function schedule() {
      clearTimer();
      if (!active || capabilityUnavailable || document.visibilityState === "hidden") return;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = 0;
        void check();
      }, LIBRARY_REVISION_VISIBLE_INTERVAL_MS);
    }

    async function check(immediateRetryAttempt = 0) {
      if (!active || capabilityUnavailable || document.visibilityState === "hidden") return false;
      if (inFlightRef.current) return inFlightRef.current;
      let retryImmediately = false;
      const operation = (async () => {
        try {
          const current = await queryClient.fetchQuery({
            queryKey,
            queryFn: ({ signal }) => apiRequest("/api/library/v2/revision", { signal, cache: "no-store" })
              .then(validateLibraryRevisionPayload),
            staleTime: 0,
            gcTime: LIBRARY_QUERY_GC_TIME_MS,
            retry: false,
          });
          if (!active) return false;
          const result = await applyLibraryRevisionChange({
            previous: baselineRef.current,
            current,
            refreshAuth,
          });
          baselineRef.current = result.nextBaseline;
          if (result.capabilityUnavailable) {
            capabilityUnavailable = true;
            clearTimer();
            return false;
          }
          retryImmediately = result.immediateRetryRequired
            && immediateRetryAttempt < LIBRARY_PROGRESS_REVISION_IMMEDIATE_RETRY_MAX;
          return true;
        } catch (error) {
          if (isLibraryRevisionCapabilityUnavailableError(error)) {
            capabilityUnavailable = true;
            clearTimer();
          }
          return false;
        } finally {
          if (inFlightRef.current === operation) inFlightRef.current = null;
          if (retryImmediately && active && !capabilityUnavailable) {
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

    window.addEventListener("focus", checkWhenVisible);
    window.addEventListener("pageshow", checkWhenVisible);
    window.addEventListener("online", checkWhenVisible);
    window.addEventListener(LIBRARY_REVISION_CHECK_EVENT, checkWhenVisible);
    document.addEventListener("visibilitychange", handleVisibility);
    void check();

    return () => {
      active = false;
      clearTimer();
      void queryClient.cancelQueries({ queryKey });
      window.removeEventListener("focus", checkWhenVisible);
      window.removeEventListener("pageshow", checkWhenVisible);
      window.removeEventListener("online", checkWhenVisible);
      window.removeEventListener(LIBRARY_REVISION_CHECK_EVENT, checkWhenVisible);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [identity, mode, refreshAuth]);

  return null;
}
