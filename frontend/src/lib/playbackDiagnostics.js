const DEBUG_ENABLED_VALUES = new Set(["1", "true", "yes", "on"]);
const DEBUG_DISABLED_VALUES = new Set(["0", "false", "no", "off"]);
const PLAYBACK_DEBUG_STORAGE_KEY = "elvernPlaybackDebug";
const TOKEN_PARAM_PATTERN = /(token|access_token|auth|authorization|signature|sig|key|secret)/i;

function normalizeDebugValue(value) {
  return String(value || "").trim().toLowerCase();
}

function readStorageValue(storage, key) {
  try {
    return storage?.getItem?.(key) || "";
  } catch {
    return "";
  }
}

export function isPlaybackDebugEnabled(search = "", storage = null) {
  const querySearch = search || (typeof window !== "undefined" ? window.location.search : "");
  const params = new URLSearchParams(querySearch || "");
  const queryValue = normalizeDebugValue(params.get(PLAYBACK_DEBUG_STORAGE_KEY));
  if (DEBUG_ENABLED_VALUES.has(queryValue)) {
    return true;
  }
  if (DEBUG_DISABLED_VALUES.has(queryValue)) {
    return false;
  }

  const storageSource = storage || (typeof window !== "undefined" ? window.localStorage : null);
  return DEBUG_ENABLED_VALUES.has(normalizeDebugValue(readStorageValue(storageSource, PLAYBACK_DEBUG_STORAGE_KEY)));
}

export function redactDiagnosticUrl(value) {
  if (!value || typeof value !== "string") {
    return "";
  }
  const baseUrl = typeof window !== "undefined" ? window.location.origin : "http://elvern.local";
  try {
    const parsed = new URL(value, baseUrl);
    parsed.searchParams.forEach((_paramValue, key) => {
      if (TOKEN_PARAM_PATTERN.test(key)) {
        parsed.searchParams.set(key, "[redacted]");
      }
    });
    if (!/^[a-z][a-z0-9+.-]*:/i.test(value)) {
      return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
    return parsed.toString();
  } catch {
    return value.replace(/([?&][^=]*(?:token|access_token|auth|signature|sig|key|secret)[^=]*=)[^&]+/gi, "$1[redacted]");
  }
}

export function serializeDiagnosticNumber(value) {
  if (Number.isFinite(value)) {
    return value;
  }
  if (Number.isNaN(value)) {
    return "NaN";
  }
  if (value === Infinity) {
    return "Infinity";
  }
  if (value === -Infinity) {
    return "-Infinity";
  }
  return value ?? null;
}

export function serializeTimeRanges(ranges) {
  const length = ranges?.length || 0;
  const serialized = [];
  for (let index = 0; index < length; index += 1) {
    try {
      const start = ranges.start(index);
      const end = ranges.end(index);
      serialized.push({
        index,
        start: serializeDiagnosticNumber(start),
        end: serializeDiagnosticNumber(end),
        duration: Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, end - start) : null,
      });
    } catch (error) {
      serialized.push({
        index,
        error: error?.message || "Failed to read time range",
      });
    }
  }
  return {
    length,
    ranges: serialized,
  };
}

export function classifyManifestSnapshot(manifestText = "") {
  const lines = String(manifestText || "").split(/\r?\n/);
  const firstLines = lines.slice(0, 40).map((line) => (
    line.replace(/([?&][^=]*(?:token|access_token|auth|authorization|signature|sig|key|secret)[^=]*=)[^&\s"']+/gi, "$1[redacted]")
  ));
  const playlistTypeLine = lines.find((line) => line.startsWith("#EXT-X-PLAYLIST-TYPE:")) || "";
  const playlistType = playlistTypeLine.split(":")[1]?.trim() || "";
  const mediaSequenceLine = lines.find((line) => line.startsWith("#EXT-X-MEDIA-SEQUENCE:")) || "";
  const targetDurationLine = lines.find((line) => line.startsWith("#EXT-X-TARGETDURATION:")) || "";
  const hasEndlist = lines.includes("#EXT-X-ENDLIST");
  const containsEvent = playlistType.toUpperCase() === "EVENT";
  const containsVod = playlistType.toUpperCase() === "VOD";
  let classification = "unknown";
  if (containsVod && hasEndlist) {
    classification = "vod_complete";
  } else if (containsVod) {
    classification = "vod_without_endlist";
  } else if (containsEvent && hasEndlist) {
    classification = "event_complete";
  } else if (containsEvent) {
    classification = "event_open";
  } else if (!hasEndlist) {
    classification = "live_or_open";
  }

  return {
    first_lines: firstLines,
    playlist_type: playlistType || null,
    contains_event_playlist_type: containsEvent,
    contains_vod_playlist_type: containsVod,
    contains_endlist: hasEndlist,
    media_sequence: mediaSequenceLine ? mediaSequenceLine.split(":")[1]?.trim() || null : null,
    target_duration: targetDurationLine ? targetDurationLine.split(":")[1]?.trim() || null : null,
    classification,
  };
}

export function isManifestUrlSafeToFetch(url) {
  if (!url || typeof url !== "string") {
    return false;
  }
  try {
    const currentOrigin = typeof window !== "undefined" ? window.location.origin : "http://elvern.local";
    const parsed = new URL(url, currentOrigin);
    return parsed.origin === currentOrigin && parsed.pathname.toLowerCase().endsWith(".m3u8");
  } catch {
    return false;
  }
}

function readElementIdentity(element) {
  if (!element) {
    return null;
  }
  return {
    tag_name: element.tagName || "",
    id: element.id || "",
    class_name: typeof element.className === "string" ? element.className : String(element.className || ""),
  };
}

export function readComputedElementDiagnostics(element) {
  if (!element || typeof window === "undefined" || typeof window.getComputedStyle !== "function") {
    return null;
  }
  const style = window.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return {
    ...readElementIdentity(element),
    rect: {
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      left: rect.left,
    },
    display: style.display,
    position: style.position,
    width: style.width,
    height: style.height,
    overflow: style.overflow,
    overflow_x: style.overflowX,
    overflow_y: style.overflowY,
    z_index: style.zIndex,
    pointer_events: style.pointerEvents,
    border_radius: style.borderRadius,
    transform: style.transform,
    object_fit: style.objectFit,
    padding_bottom: style.paddingBottom,
    margin_bottom: style.marginBottom,
  };
}

export function readElementFromVideoPoint(video, { yRatio = 0.92 } = {}) {
  if (!video || typeof document === "undefined" || typeof document.elementFromPoint !== "function") {
    return null;
  }
  const rect = video.getBoundingClientRect();
  if (!rect.width || !rect.height) {
    return null;
  }
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height * yRatio;
  const element = document.elementFromPoint(x, y);
  return {
    point: { x, y, y_ratio: yRatio },
    element: readElementIdentity(element),
  };
}

export async function fetchManifestDiagnostics(url) {
  if (!url) {
    return {
      url: "",
      fetched: false,
      reason: "no_manifest_url",
    };
  }
  if (!isManifestUrlSafeToFetch(url)) {
    return {
      url: redactDiagnosticUrl(url),
      fetched: false,
      reason: "manifest_url_not_same_origin_m3u8",
    };
  }
  try {
    const response = await fetch(url, {
      cache: "no-store",
      credentials: "include",
    });
    const text = await response.text();
    return {
      url: redactDiagnosticUrl(url),
      fetched: true,
      http_status: response.status,
      ok: response.ok,
      ...classifyManifestSnapshot(text),
    };
  } catch (error) {
    return {
      url: redactDiagnosticUrl(url),
      fetched: false,
      error: error?.message || "Failed to fetch manifest",
    };
  }
}
