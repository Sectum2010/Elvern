function readNavigatorPlatformSnapshot() {
  if (typeof navigator === "undefined") {
    return {
      userAgent: "",
      platform: "",
      maxTouchPoints: 0,
    };
  }
  return {
    userAgent: navigator.userAgent || "",
    platform: navigator.platform || "",
    maxTouchPoints: Number(navigator.maxTouchPoints || 0),
  };
}

function normalizePlatformInput({
  userAgent = "",
  platform = "",
  maxTouchPoints = 0,
} = {}) {
  return {
    userAgent: String(userAgent || "").trim().toLowerCase(),
    platform: String(platform || "").trim().toLowerCase(),
    maxTouchPoints: Number.isFinite(Number(maxTouchPoints))
      ? Math.max(0, Number(maxTouchPoints))
      : 0,
  };
}

export function detectClientPlatform(input = null) {
  const {
    userAgent,
    platform,
    maxTouchPoints,
  } = normalizePlatformInput(input || readNavigatorPlatformSnapshot());
  const iPadDesktopClassAgent =
    maxTouchPoints > 1
    && (userAgent.includes("macintosh") || platform.includes("mac"));

  if (userAgent.includes("iphone") || userAgent.includes("ipod")) {
    return "iphone";
  }
  if (userAgent.includes("ipad") || iPadDesktopClassAgent) {
    return "ipad";
  }
  if (userAgent.includes("android")) {
    return "android";
  }
  if (userAgent.includes("windows")) {
    return "windows";
  }
  if (
    userAgent.includes("macintosh")
    || (userAgent.includes("mac os x") && !userAgent.includes("iphone") && !userAgent.includes("ipad"))
  ) {
    return "mac";
  }
  if (userAgent.includes("linux") || platform.includes("linux") || userAgent.includes("x11")) {
    return "linux";
  }
  return "unknown";
}

export function detectClientDeviceClass(input = null) {
  const snapshot = normalizePlatformInput(input || readNavigatorPlatformSnapshot());
  const platform = detectClientPlatform(snapshot);

  if (platform === "iphone") {
    return "phone";
  }
  if (platform === "ipad") {
    return "tablet";
  }
  if (platform === "android") {
    return snapshot.userAgent.includes("mobile") ? "phone" : "tablet";
  }
  if (isDesktopClientPlatform(platform)) {
    return "desktop";
  }
  return "unknown";
}

export function isIOSClientPlatform(platform) {
  return platform === "iphone" || platform === "ipad";
}

export function isIOSLikeBrowser(input = null) {
  return isIOSClientPlatform(detectClientPlatform(input));
}

export function isDesktopClientPlatform(platform) {
  return platform === "windows" || platform === "mac" || platform === "linux";
}

export function detectDesktopPlatform(input = null) {
  const platform = detectClientPlatform(input);
  return isDesktopClientPlatform(platform) ? platform : null;
}
