import { useEffect, useState } from "react";

import { apiRequest } from "./api";


export function isIOSMobileBrowser() {
  if (typeof navigator === "undefined") {
    return false;
  }
  return /iphone|ipad|ipod/i.test(navigator.userAgent || "");
}

export function resolveBrowserPlaybackSessionRoot() {
  return isIOSMobileBrowser() ? "/api/mobile-playback" : "/api/browser-playback";
}

export function isRoute2SessionPayload(payload) {
  return payload?.engine_mode === "route2";
}

export function getPlaybackMode(mode = "lite") {
  return mode === "full" ? "full" : "lite";
}

export function getPlaybackModeTitle(mode = "lite") {
  return getPlaybackMode(mode) === "full" ? "Full Playback" : "Lite Playback";
}

export function getPlaybackModeLabel(mode = "lite") {
  return getPlaybackModeTitle(mode).toLowerCase();
}

export function getSessionModeEstimateSeconds(payload) {
  if (typeof payload?.mode_estimate_seconds === "number" && Number.isFinite(payload.mode_estimate_seconds)) {
    return payload.mode_estimate_seconds;
  }
  if (typeof payload?.prepare_estimate_seconds === "number" && Number.isFinite(payload.prepare_estimate_seconds)) {
    return payload.prepare_estimate_seconds;
  }
  return null;
}

export function buildRoute2ProbeSegmentUrl(payload) {
  if (!isRoute2SessionPayload(payload) || !payload?.active_manifest_url) {
    return "";
  }
  const segmentIndex = Number(payload?.manifest_end_segment);
  if (!Number.isFinite(segmentIndex) || segmentIndex < 0) {
    return "";
  }
  const manifestUrl = String(payload.active_manifest_url);
  const prefix = manifestUrl.replace(/index\.m3u8(?:\?.*)?$/i, "");
  if (!prefix) {
    return "";
  }
  const separator = prefix.includes("?") ? "&" : "?";
  return `${prefix}segments/${segmentIndex}.m4s${separator}probe=${Date.now()}`;
}

export function buildFullPlaybackReadyKey(payload) {
  if (!payload?.session_id) {
    return "";
  }
  return `${payload.session_id}:${payload.playback_mode || "lite"}:${payload.attach_revision || 0}`;
}

export function useActiveBrowserPlaybackItemId() {
  const [activeItemId, setActiveItemId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let timerId = 0;
    const sessionRoot = resolveBrowserPlaybackSessionRoot();

    async function pollActiveSession() {
      try {
        const payload = await apiRequest(`${sessionRoot}/active`);
        if (cancelled) {
          return;
        }
        setActiveItemId(payload?.media_item_id ?? null);
      } catch {
        if (!cancelled) {
          setActiveItemId(null);
        }
      }
      if (!cancelled) {
        timerId = window.setTimeout(pollActiveSession, 5000);
      }
    }

    pollActiveSession().catch(() => {
      if (!cancelled) {
        timerId = window.setTimeout(pollActiveSession, 5000);
      }
    });
    return () => {
      cancelled = true;
      window.clearTimeout(timerId);
    };
  }, []);

  return activeItemId;
}
