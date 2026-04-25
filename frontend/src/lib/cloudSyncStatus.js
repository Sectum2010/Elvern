const LIBRARY_CLOUD_RECONNECT_DISMISSED_KEY = "elvern:library-cloud-reconnect-dismissed";


export function hasCloudSyncWarning(cloudSync) {
  const status = typeof cloudSync?.status === "string" ? cloudSync.status : "";
  return status === "failed" || status === "partial_failure";
}


export function formatRescanBannerText(payload, fallbackMessage = "Library scan started.") {
  const baseMessage = typeof payload?.message === "string" && payload.message.trim()
    ? payload.message.trim()
    : fallbackMessage;
  if (!hasCloudSyncWarning(payload?.cloud_sync)) {
    return baseMessage;
  }
  const cloudMessage = typeof payload?.cloud_sync?.message === "string" && payload.cloud_sync.message.trim()
    ? payload.cloud_sync.message.trim()
    : baseMessage;
  const localMessage = payload?.running ? "Local scan started." : "Local scan updated.";
  if (cloudMessage.startsWith(localMessage)) {
    return cloudMessage;
  }
  return `${localMessage} ${cloudMessage}`;
}


export function formatCompletedRescanWarning(cloudSyncMessage, fallbackPrefix = "Local scan completed.") {
  const message = typeof cloudSyncMessage === "string" ? cloudSyncMessage.trim() : "";
  return message ? `${fallbackPrefix} ${message}` : fallbackPrefix;
}


export function isCloudReconnectRequired(cloudLibraries) {
  const google = cloudLibraries?.google;
  if (!google || typeof google !== "object") {
    return false;
  }
  return (
    google.reconnect_required === true
    || google.provider_auth_required === true
    || google.connection_status === "reconnect_required"
  );
}


export function getCloudReconnectPrompt(cloudLibraries) {
  if (!isCloudReconnectRequired(cloudLibraries)) {
    return null;
  }
  return {
    title: "Reconnect Google Drive",
    message: "Google Drive reconnect is required. Cloud movies may be stale until you reconnect.",
  };
}


export function formatGoogleDriveSetupLabel(configurationState, configurationLabel) {
  const state = typeof configurationState === "string" ? configurationState : "";
  const label = typeof configurationLabel === "string" && configurationLabel.trim()
    ? configurationLabel.trim()
    : "Not configured";
  if (state === "ready") {
    return "OAuth Ready";
  }
  if (state === "partially_configured") {
    return "OAuth Partially configured";
  }
  if (state === "not_configured") {
    return "OAuth Not configured";
  }
  return `OAuth ${label}`;
}


export function formatGoogleConnectionHealthLabel(google) {
  const status = typeof google?.connection_status === "string" ? google.connection_status : "";
  switch (status) {
    case "connected":
      return "Connected";
    case "reconnect_required":
      return "Reconnect required";
    case "error":
      return "Stale or error";
    case "not_connected":
      return "Not connected";
    case "not_configured":
    default:
      return "Not configured";
  }
}


export function readLibraryCloudReconnectDismissed() {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.sessionStorage.getItem(LIBRARY_CLOUD_RECONNECT_DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}


export function dismissLibraryCloudReconnectPrompt() {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.setItem(LIBRARY_CLOUD_RECONNECT_DISMISSED_KEY, "1");
  } catch {
    // Ignore sessionStorage failures and keep the prompt visible.
  }
}


export function clearLibraryCloudReconnectDismissal() {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.removeItem(LIBRARY_CLOUD_RECONNECT_DISMISSED_KEY);
  } catch {
    // Ignore sessionStorage cleanup failures.
  }
}
