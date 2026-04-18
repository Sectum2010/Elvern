const PROVIDER_AUTH_INTENT_KEY = "elvern:provider-auth-intent";
const PROVIDER_AUTH_INTENT_MAX_AGE_MS = 30 * 60 * 1000;


function normalizeProviderAuthDetail(detail) {
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
