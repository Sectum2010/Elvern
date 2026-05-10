export const ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS = 2600;

function isFiniteNonNegative(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function padTwo(value) {
  return String(value).padStart(2, "0");
}

export function formatPlaybackTime(seconds) {
  if (!isFiniteNonNegative(Number(seconds))) {
    return "0:00";
  }
  const total = Math.max(0, Math.round(Number(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}:${padTwo(minutes)}:${padTwo(secs)}`;
  }
  return `${minutes}:${padTwo(secs)}`;
}

export function formatPlaybackDuration(seconds) {
  if (!isFiniteNonNegative(Number(seconds)) || Number(seconds) <= 0) {
    return "--:--";
  }
  return formatPlaybackTime(seconds);
}

export const ELVERN_OVERLAY_LAYOUT_VARIANTS = Object.freeze({
  PHONE: "phone",
  TABLET: "tablet",
  DESKTOP: "desktop",
});

export function resolveOverlayLayoutVariant(deviceClass) {
  const normalized = typeof deviceClass === "string" ? deviceClass.toLowerCase() : "";
  if (normalized === "phone" || normalized === "iphone" || normalized === "android-phone") {
    return ELVERN_OVERLAY_LAYOUT_VARIANTS.PHONE;
  }
  if (normalized === "tablet" || normalized === "ipad") {
    return ELVERN_OVERLAY_LAYOUT_VARIANTS.TABLET;
  }
  if (normalized === "desktop" || normalized === "laptop") {
    return ELVERN_OVERLAY_LAYOUT_VARIANTS.DESKTOP;
  }
  return ELVERN_OVERLAY_LAYOUT_VARIANTS.DESKTOP;
}

export function resolveOverlayLayoutCapabilities(deviceClass) {
  const variant = resolveOverlayLayoutVariant(deviceClass);
  if (variant === ELVERN_OVERLAY_LAYOUT_VARIANTS.PHONE) {
    return Object.freeze({
      variant,
      showInlineVolumeSlider: false,
      showInlineMuteToggle: false,
      showInlineSpeed: false,
      showInlineCaptions: false,
      showInlineAudio: false,
      showInlinePip: false,
      useMoreMenu: true,
      compactCenterHint: true,
    });
  }
  if (variant === ELVERN_OVERLAY_LAYOUT_VARIANTS.TABLET) {
    return Object.freeze({
      variant,
      showInlineVolumeSlider: false,
      showInlineMuteToggle: true,
      showInlineSpeed: true,
      showInlineCaptions: true,
      showInlineAudio: false,
      showInlinePip: false,
      useMoreMenu: true,
      compactCenterHint: false,
    });
  }
  return Object.freeze({
    variant,
    showInlineVolumeSlider: true,
    showInlineMuteToggle: true,
    showInlineSpeed: true,
    showInlineCaptions: true,
    showInlineAudio: true,
    showInlinePip: true,
    useMoreMenu: false,
    compactCenterHint: false,
  });
}

const AUTOPLAY_BLOCKED_ERROR_NAMES = new Set([
  "NotAllowedError",
  "NotAllowed",
  "AbortError",
  "SecurityError",
]);

export function isAutoplayBlockedError(error) {
  if (!error) {
    return false;
  }
  const name = typeof error.name === "string" ? error.name : "";
  if (AUTOPLAY_BLOCKED_ERROR_NAMES.has(name)) {
    return true;
  }
  const message = typeof error.message === "string" ? error.message.toLowerCase() : "";
  if (!message) {
    return false;
  }
  return (
    message.includes("user gesture")
    || message.includes("user didn't interact")
    || message.includes("user did not interact")
    || message.includes("autoplay")
    || message.includes("not allowed by user agent")
  );
}

export function describeReattachAutoplayPrompt({
  isAutoplayBlocked = false,
  reattachReason = "",
} = {}) {
  if (!isAutoplayBlocked) {
    return null;
  }
  if (reattachReason === "native_hls_window_slide" || String(reattachReason).startsWith("native_hls_window_")) {
    return "Tap play to resume from where you were.";
  }
  return "Tap play to resume.";
}

export function shouldOverlayBeVisible({
  isPlaying = false,
  preparing = false,
  hasError = false,
  isDraggingTimeline = false,
  anyMenuOpen = false,
  controlsFocused = false,
  lastInteractionAtMs = 0,
  nowMs = 0,
  idleHideDelayMs = ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS,
} = {}) {
  if (!isPlaying) {
    return true;
  }
  if (preparing) {
    return true;
  }
  if (hasError) {
    return true;
  }
  if (isDraggingTimeline) {
    return true;
  }
  if (anyMenuOpen) {
    return true;
  }
  if (controlsFocused) {
    return true;
  }
  if (!Number.isFinite(lastInteractionAtMs) || !Number.isFinite(nowMs) || idleHideDelayMs <= 0) {
    return false;
  }
  const elapsed = nowMs - lastInteractionAtMs;
  return elapsed < idleHideDelayMs;
}
