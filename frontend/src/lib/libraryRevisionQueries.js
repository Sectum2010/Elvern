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
export const LIBRARY_REVISION_CHECK_EVENT = "elvern:library-revision-check";
export const LIBRARY_REVISION_QUERY_PREFIX = Object.freeze(["library-revision", "v1"]);
const REVISION_FIELDS = Object.freeze([
  "catalog",
  "presentation",
  "permission",
  "user_overlay",
  "progress",
  "combined_library",
]);


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
    if (!String(payload[field] || "").trim()) {
      throw new Error(`Invalid Library revision field: ${field}`);
    }
  }
  return payload;
}


export async function applyLibraryRevisionChange({ previous, current, refreshAuth }) {
  if (!previous) return { baseline: true, changedLayers: [] };
  const changedLayers = REVISION_FIELDS.filter((field) => previous[field] !== current[field]);
  if (changedLayers.includes("permission")) {
    await refreshAuth?.({ notifyOnFailure: true });
  }
  if (changedLayers.includes("presentation")) {
    await queryClient.invalidateQueries({ queryKey: ["user-settings"], refetchType: "active" });
  }
  if (changedLayers.some((field) => ["catalog", "presentation", "permission", "user_overlay"].includes(field))) {
    await invalidateLibraryQueries({ refetchType: "active" });
  }
  if (changedLayers.includes("progress")) {
    const progressState = await apiRequest("/api/library/v2/progress-state", { cache: "no-store" });
    await patchLibraryProgressStateCaches(progressState);
  }
  return { baseline: false, changedLayers };
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
    if (mode !== "on" || !user) return undefined;

    let active = true;
    const queryKey = buildLibraryRevisionQueryKey(user);

    function clearTimer() {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = 0;
    }

    function schedule() {
      clearTimer();
      if (!active || document.visibilityState === "hidden") return;
      timerRef.current = window.setTimeout(() => {
        timerRef.current = 0;
        void check();
      }, LIBRARY_REVISION_VISIBLE_INTERVAL_MS);
    }

    async function check() {
      if (!active || document.visibilityState === "hidden") return false;
      if (inFlightRef.current) return inFlightRef.current;
      const operation = (async () => {
        try {
          const current = await queryClient.fetchQuery({
            queryKey,
            queryFn: ({ signal }) => apiRequest("/api/library/v2/revision", { signal, cache: "no-store" })
              .then(validateLibraryRevisionPayload),
            staleTime: 0,
            gcTime: LIBRARY_QUERY_GC_TIME_MS,
          });
          if (!active) return false;
          await applyLibraryRevisionChange({
            previous: baselineRef.current,
            current,
            refreshAuth,
          });
          baselineRef.current = current;
          return true;
        } catch {
          return false;
        } finally {
          if (inFlightRef.current === operation) inFlightRef.current = null;
          schedule();
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
