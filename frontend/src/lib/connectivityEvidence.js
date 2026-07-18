export const DEFAULT_PUBLIC_PROBE_TIMEOUT_MS = 5_000;


export function normalizePublicConnectivityProbeUrl(value) {
  const raw = String(value || "").trim();
  if (!raw || raw === "__ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL__") {
    return "";
  }
  try {
    const parsed = new URL(raw);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
  } catch {
    return "";
  }
}


export function classifyConnectivityEvidence({
  browserOffline = false,
  frontendReachable = null,
  backendReachable = null,
  publicInternetReachable = null,
} = {}) {
  if (browserOffline) {
    return "internet_offline";
  }
  if (frontendReachable === false) {
    return publicInternetReachable === false
      ? "internet_offline"
      : "frontend_or_vpn_unreachable";
  }
  if (frontendReachable === true && backendReachable === false) {
    return "backend_unreachable";
  }
  if (frontendReachable === true && backendReachable === true) {
    return "healthy";
  }
  return "frontend_or_vpn_unreachable";
}


export async function probePublicConnectivity({
  fetchImpl = globalThis.fetch?.bind(globalThis),
  url,
  timeoutMs = DEFAULT_PUBLIC_PROBE_TIMEOUT_MS,
  abortController = new AbortController(),
  setTimeoutImpl = globalThis.setTimeout?.bind(globalThis),
  clearTimeoutImpl = globalThis.clearTimeout?.bind(globalThis),
} = {}) {
  const normalizedUrl = normalizePublicConnectivityProbeUrl(url);
  if (!normalizedUrl || typeof fetchImpl !== "function") {
    return false;
  }
  const timeoutId = setTimeoutImpl?.(() => abortController.abort(), timeoutMs) || 0;
  try {
    const response = await fetchImpl(normalizedUrl, {
      cache: "no-store",
      credentials: "omit",
      mode: "cors",
      referrerPolicy: "no-referrer",
      signal: abortController.signal,
    });
    return Boolean(response?.ok);
  } catch {
    return false;
  } finally {
    if (timeoutId) {
      clearTimeoutImpl?.(timeoutId);
    }
  }
}
