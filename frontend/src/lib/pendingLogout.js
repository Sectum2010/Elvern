export const PENDING_LOGOUT_STORAGE_KEY = "elvern:auth:logout-pending:v1";


function getStorage() {
  return typeof window === "undefined" ? null : window.localStorage;
}


export function createPendingLogoutMarker(user) {
  return {
    version: 1,
    userId: String(user?.id ?? ""),
    sessionId: String(user?.session_id ?? ""),
    createdAt: new Date().toISOString(),
  };
}


export function readPendingLogoutMarker(storage = getStorage()) {
  try {
    const parsed = JSON.parse(storage?.getItem?.(PENDING_LOGOUT_STORAGE_KEY) || "null");
    if (
      parsed?.version !== 1
      || typeof parsed.userId !== "string"
      || typeof parsed.sessionId !== "string"
      || !parsed.userId
      || !parsed.sessionId
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}


export function writePendingLogoutMarker(marker, storage = getStorage()) {
  storage?.setItem?.(PENDING_LOGOUT_STORAGE_KEY, JSON.stringify(marker));
}


export function clearPendingLogoutMarker(storage = getStorage()) {
  storage?.removeItem?.(PENDING_LOGOUT_STORAGE_KEY);
}
