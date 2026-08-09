import { PanelRightClose, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "../auth/AuthContext.jsx";
import { apiRequest } from "../lib/api.js";
import { fetchControlCenterResource } from "../lib/controlCenterQueries.js";
import { getOrCreateDeviceId } from "../lib/device.js";
import { detectClientPlatform } from "../lib/platformDetection.js";
import { queryClient } from "../lib/queryClient.js";
import {
  buildUserSettingsQueryKey,
  USER_SETTINGS_QUERY_GC_TIME_MS,
  USER_SETTINGS_QUERY_STALE_TIME_MS,
} from "../lib/userSettingsQueries.js";
import { useControlCenterSession } from "./ControlCenterSessionContext.jsx";

export const SYSTEM_STATUS_RAIL_REFRESH_MS = 30_000;

const RAIL_RESOURCES = Object.freeze([
  "system",
  "cloudLibraries",
  "googleDriveSetup",
  "personalHidden",
  "globalHidden",
]);

function readablePlatform(platform) {
  return { windows: "Windows", mac: "macOS", linux: "Linux" }[platform] || "Unknown";
}

function countItems(payload) {
  return Array.isArray(payload?.items) ? payload.items.length : null;
}

function helperVlcValue(helper) {
  if (!helper) return "Unavailable";
  if (helper.vlc_detection_state === "installed") return "Detected";
  if (helper.vlc_detection_state === "not_detected") return "Not found";
  return helper.helper_required === false ? "Not detected" : "Not verified";
}

function googleDriveValue(cloudLibraries) {
  const google = cloudLibraries?.google;
  if (!google) return "Unavailable";
  if (google.connected) return "Connected";
  if (google.reconnect_required) return "Needs attention";
  return google.enabled ? "Not connected" : "Disabled";
}

function formatCallback(helper) {
  if (!helper) return "Unavailable";
  if (helper.helper_required === false) return "Not required";
  if (!helper.last_seen_helper_at) return "Never";
  const candidate = new Date(helper.last_seen_helper_at);
  return Number.isNaN(candidate.getTime()) ? "Never" : candidate.toLocaleString();
}

export function buildSystemStatusRailRows({ payloads, platform, deviceId }) {
  const personalCount = countItems(payloads.personalHidden);
  const globalCount = countItems(payloads.globalHidden);
  const hiddenCount = personalCount === null || globalCount === null
    ? "Unavailable"
    : `${personalCount} personal + ${globalCount} global`;
  const helper = payloads.desktopHelper;
  const helperLabel = platform === "linux" && helper?.same_host
    ? "VLC on host"
    : "VLC on device";
  const posterWidth = payloads.userSettings?.poster_card_display_max_width;

  return [
    ["Google Drive", googleDriveValue(payloads.cloudLibraries)],
    ["OAuth setup", payloads.googleDriveSetup?.configuration_label
      || payloads.googleDriveSetup?.configuration_state
      || "Unavailable"],
    [helperLabel, helperVlcValue(helper)],
    ["Platform", readablePlatform(platform)],
    ["Titles indexed", Number.isFinite(payloads.system?.total_media_items)
      ? String(payloads.system.total_media_items)
      : "Unavailable"],
    ["Hidden titles", hiddenCount],
    ["Poster width", posterWidth === "original" ? "Original" : posterWidth ? `${posterWidth}px` : "Unavailable"],
    ["Island", payloads.userSettings
      ? (payloads.userSettings.desktop_floating_island_position === "bottom" ? "Bottom" : "Top")
      : "Unavailable"],
    ["Device ID", deviceId || "Unavailable"],
    ["Last callback", formatCallback(helper)],
  ];
}

async function fetchRailUserSettings(user, force) {
  return queryClient.fetchQuery({
    queryKey: buildUserSettingsQueryKey({ userId: user?.id, role: user?.role }),
    queryFn: ({ signal }) => apiRequest("/api/user-settings", { signal, abortOnPageHide: true }),
    staleTime: force ? 0 : USER_SETTINGS_QUERY_STALE_TIME_MS,
    gcTime: USER_SETTINGS_QUERY_GC_TIME_MS,
    retry: false,
  });
}

export function SystemStatusRail() {
  const { user } = useAuth();
  const { statusRailOpen, setStatusRailOpen } = useControlCenterSession();
  const platform = useMemo(() => detectClientPlatform(), []);
  const [deviceId] = useState(getOrCreateDeviceId);
  const [payloads, setPayloads] = useState({});
  const [staleResources, setStaleResources] = useState([]);
  const [loading, setLoading] = useState(false);
  const generationRef = useRef(0);
  const inFlightRef = useRef(false);

  const load = useCallback(async ({ force = false } = {}) => {
    if (!statusRailOpen || user?.role !== "admin" || inFlightRef.current) return;
    inFlightRef.current = true;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    setLoading(true);
    const requests = [
      ...RAIL_RESOURCES.map((resource) => [resource, fetchControlCenterResource({
        userId: user.id,
        role: user.role,
        resource,
        force,
      })]),
      ["desktopHelper", fetchControlCenterResource({
        userId: user.id,
        role: user.role,
        resource: "desktopHelper",
        platform,
        deviceId,
        force,
      })],
      ["userSettings", fetchRailUserSettings(user, force)],
    ];
    const results = await Promise.allSettled(requests.map(([, request]) => request));
    if (generation !== generationRef.current) {
      inFlightRef.current = false;
      return;
    }
    const failed = [];
    setPayloads((current) => {
      const next = { ...current };
      results.forEach((result, index) => {
        const resource = requests[index][0];
        if (result.status === "fulfilled") next[resource] = result.value;
        else if (result.reason?.name !== "AbortError") failed.push(resource);
      });
      return next;
    });
    setStaleResources(failed);
    setLoading(false);
    inFlightRef.current = false;
  }, [deviceId, platform, statusRailOpen, user]);

  useEffect(() => {
    if (!statusRailOpen || user?.role !== "admin") {
      generationRef.current += 1;
      setLoading(false);
      return undefined;
    }
    void load();
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === "visible") void load({ force: true });
    }, SYSTEM_STATUS_RAIL_REFRESH_MS);
    function handleVisibilityChange() {
      if (document.visibilityState === "visible") void load({ force: true });
    }
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [load, statusRailOpen, user?.role]);

  useEffect(() => {
    if (!statusRailOpen) return undefined;
    function handleKeyDown(event) {
      if (event.key === "Escape") setStatusRailOpen(false);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [setStatusRailOpen, statusRailOpen]);

  if (user?.role !== "admin" || !statusRailOpen) return null;

  const rows = buildSystemStatusRailRows({ payloads, platform, deviceId });
  return (
    <aside aria-label="System status" className="control-center-status-rail">
      <header className="control-center-status-rail__header">
        <div><span>System</span><h2>Status</h2></div>
        <div className="control-center-status-rail__actions">
          <button aria-label="Refresh system status" disabled={loading} onClick={() => void load({ force: true })} type="button">
            <RefreshCw aria-hidden="true" className={loading ? "is-spinning" : ""} size={16} />
          </button>
          <button aria-label="Close system status" onClick={() => setStatusRailOpen(false)} type="button">
            <X aria-hidden="true" size={18} />
          </button>
        </div>
      </header>
      {staleResources.length > 0 ? (
        <p aria-live="polite" className="control-center-status-rail__stale">
          Some values are stale. Last known values are preserved.
        </p>
      ) : null}
      <dl className="control-center-status-rail__list">
        {rows.map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd title={value}>{value}</dd></div>
        ))}
      </dl>
      <PanelRightClose aria-hidden="true" className="control-center-status-rail__watermark" size={72} />
    </aside>
  );
}
