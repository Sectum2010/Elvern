const DEVICE_ID_STORAGE_KEY = "elvern_device_id";


function generateFallbackDeviceId() {
  return `device-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}


export function getOrCreateDeviceId() {
  if (typeof window === "undefined") {
    return null;
  }

  const existing = window.localStorage.getItem(DEVICE_ID_STORAGE_KEY);
  if (existing) {
    return existing;
  }

  const nextId = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : generateFallbackDeviceId();
  window.localStorage.setItem(DEVICE_ID_STORAGE_KEY, nextId);
  return nextId;
}
