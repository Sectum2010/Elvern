function normalizeUserAgent(userAgent) {
  return typeof userAgent === "string" ? userAgent.trim().toLowerCase() : "";
}

export function detectBrowserPlaybackDeviceClass({
  userAgent = "",
  maxTouchPoints = 0,
} = {}) {
  const normalizedUserAgent = normalizeUserAgent(userAgent);
  const touchPoints = Number.isFinite(Number(maxTouchPoints))
    ? Math.max(0, Number(maxTouchPoints))
    : 0;

  if (!normalizedUserAgent) {
    return "unknown";
  }
  if (normalizedUserAgent.includes("iphone") || normalizedUserAgent.includes("ipod")) {
    return "phone";
  }
  if (normalizedUserAgent.includes("ipad")) {
    return "tablet";
  }
  if (normalizedUserAgent.includes("android")) {
    return normalizedUserAgent.includes("mobile") ? "phone" : "tablet";
  }
  if (normalizedUserAgent.includes("macintosh") && touchPoints > 1) {
    return "tablet";
  }
  if (
    normalizedUserAgent.includes("windows nt")
    || normalizedUserAgent.includes("macintosh")
    || normalizedUserAgent.includes("x11; linux")
    || normalizedUserAgent.includes("linux x86_64")
    || normalizedUserAgent.includes("cros")
  ) {
    return "desktop";
  }
  if (normalizedUserAgent.includes("tablet")) {
    return "tablet";
  }
  return "unknown";
}

export function capBrowserPlaybackProfileForDeviceClass({
  deviceClass = "unknown",
  requestedProfile = "mobile_2160p",
} = {}) {
  const normalizedProfile = requestedProfile === "mobile_2160p"
    ? "mobile_2160p"
    : "mobile_1080p";
  if (normalizedProfile !== "mobile_2160p") {
    return normalizedProfile;
  }
  return deviceClass === "desktop" || deviceClass === "tablet"
    ? "mobile_2160p"
    : "mobile_1080p";
}
