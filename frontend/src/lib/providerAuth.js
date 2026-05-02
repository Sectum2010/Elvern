import { apiRequest } from "./api.js";


const PROVIDER_AUTH_INTENT_KEY = "elvern:provider-auth-intent";
const PROVIDER_AUTH_INTENT_MAX_AGE_MS = 30 * 60 * 1000;
export const PROVIDER_AUTH_ADMIN_NOTICE_MESSAGE = "Your cloud provider token has expired. Contact an admin to stream cloud-stored movies.";
export const PROVIDER_RECONNECT_CANCELLED_MESSAGE = "Reconnect was not completed.";
export const PROVIDER_RECONNECT_PENDING_RESET_MS = 10000;


export function normalizeProviderAuthDetail(detail) {
  if (!detail || typeof detail !== "object") {
    return null;
  }
  if (detail.code !== "provider_auth_required" || !detail.provider) {
    return null;
  }
  return {
    code: detail.code,
    provider: String(detail.provider),
    providerReason: String(detail.provider_reason || detail.reason || ""),
    title: String(detail.title || "Provider connection expired"),
    message: String(detail.message || "Reconnect the provider to continue this action."),
    allowReconnect: detail.allow_reconnect !== false,
    requiresAdmin: detail.requires_admin === true,
  };
}


export function getProviderAuthRequirement(error) {
  if (!error || typeof error !== "object") {
    return null;
  }
  return normalizeProviderAuthDetail(error.detail || error?.payload?.detail || null);
}


export function getProviderAuthRequirementFromStatus(status) {
  if (!status || typeof status !== "object") {
    return null;
  }
  const detail = status.requirement || status.detail || null;
  if (detail) {
    return normalizeProviderAuthDetail(detail);
  }
  if (status.provider_auth_required === true || status.reconnect_required === true) {
    return normalizeProviderAuthDetail({
      code: "provider_auth_required",
      provider: status.provider || "google_drive",
      provider_reason: status.provider_reason || status.reason || "",
      title: status.title || "Google Drive connection expired",
      message: status.message || "Reconnect Google Drive to continue cloud playback.",
      allow_reconnect: status.allow_reconnect,
      requires_admin: status.requires_admin,
    });
  }
  return null;
}


export function isProviderAuthReconnectCapable(requirement) {
  return Boolean(requirement)
    && requirement.requiresAdmin !== true
    && requirement.allowReconnect !== false;
}


export function shouldUseProviderAuthPassiveNotice(requirement) {
  return Boolean(requirement)
    && (
      requirement.requiresAdmin === true
      || requirement.allowReconnect === false
    );
}


export function getProviderAuthPassiveNoticeMessage(requirement) {
  if (!shouldUseProviderAuthPassiveNotice(requirement)) {
    return "";
  }
  return PROVIDER_AUTH_ADMIN_NOTICE_MESSAGE;
}


export function shouldShowProviderAuthBootstrapModal({ requirement, dismissed }) {
  return isProviderAuthReconnectCapable(requirement) && dismissed !== true;
}


export function shouldShowProviderAuthActionModal({ itemSourceKind, requirement }) {
  return shouldGuardGoogleDriveAction({
    itemSourceKind,
    reconnectRequired: isProviderAuthReconnectCapable(requirement),
  });
}


export function buildProviderAuthReturnPath(currentLocation) {
  if (typeof currentLocation === "string") {
    try {
      currentLocation = new URL(currentLocation, "http://elvern.local");
    } catch {
      return "/";
    }
  }
  const pathname = currentLocation?.pathname || "/";
  const searchParams = new URLSearchParams(currentLocation?.search || "");
  searchParams.delete("googleDriveStatus");
  searchParams.delete("googleDriveMessage");
  const search = searchParams.toString();
  const hash = currentLocation?.hash || "";
  return `${pathname}${search ? `?${search}` : ""}${hash}`;
}


export function getGoogleDriveStatusFromLocation(currentLocation) {
  if (typeof currentLocation === "string") {
    try {
      currentLocation = new URL(currentLocation, "http://elvern.local");
    } catch {
      return "";
    }
  }
  const searchParams = new URLSearchParams(currentLocation?.search || "");
  return searchParams.get("googleDriveStatus") || "";
}


export function shouldResetProviderReconnectPending({
  reconnectPending,
  googleDriveStatus = "",
  visibilityState = "visible",
} = {}) {
  return (
    reconnectPending === true
    && googleDriveStatus !== "connected"
    && visibilityState !== "hidden"
  );
}


export function saveProviderAuthIntent(intent) {
  if (typeof window === "undefined" || !intent || typeof intent !== "object") {
    return;
  }
  try {
    window.sessionStorage.setItem(
      PROVIDER_AUTH_INTENT_KEY,
      JSON.stringify({
        ...intent,
        savedAt: Date.now(),
      }),
    );
  } catch {
    // Ignore sessionStorage failures and let the current action fail visibly.
  }
}


export function readProviderAuthIntent() {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(PROVIDER_AUTH_INTENT_KEY);
    if (!raw) {
      return null;
    }
    const payload = JSON.parse(raw);
    if (!payload || typeof payload !== "object") {
      return null;
    }
    if (Date.now() - Number(payload.savedAt || 0) > PROVIDER_AUTH_INTENT_MAX_AGE_MS) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}


export function clearProviderAuthIntent() {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.removeItem(PROVIDER_AUTH_INTENT_KEY);
  } catch {
    // Ignore sessionStorage cleanup failures.
  }
}


export function shouldGuardGoogleDriveAction({ itemSourceKind, reconnectRequired }) {
  return (itemSourceKind || "local") === "cloud" && reconnectRequired === true;
}


export async function startGoogleDriveReconnect({ returnPath } = {}) {
  const requestData = returnPath
    ? { return_path: returnPath }
    : undefined;
  const payload = await apiRequest("/api/cloud-libraries/google/connect", {
    method: "POST",
    data: requestData,
  });
  if (!payload?.authorization_url) {
    throw new Error("Google Drive reconnect did not return an authorization URL.");
  }
  if (typeof window !== "undefined") {
    window.location.assign(payload.authorization_url);
  }
  return payload;
}
