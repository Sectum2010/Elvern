import { clearProtectedQueryCache } from "./queryClient";
import {
  dispatchStartupApplicationReady,
  dispatchStartupConnectivityFailure,
} from "./startupConnection.js";


function joinMessages(values) {
  const messages = values
    .map((value) => (typeof value === "string" ? value.trim() : ""))
    .filter(Boolean);
  return messages.length > 0 ? messages.join("; ") : null;
}

export const MAINTENANCE_MODE_MESSAGE = "The server is currently under construction, please try again later";
export const MAINTENANCE_MODE_BLOCKED_EVENT = "elvern:maintenance-mode-blocked";
export const AUTH_REVALIDATION_REQUESTED_EVENT = "elvern:auth-revalidation-requested";

function isAuthRequestPath(path) {
  const pathname = String(path || "").split("?", 1)[0];
  return pathname.startsWith("/api/auth/");
}

function dispatchAuthRevalidationRequested(path, status) {
  if (
    status !== 403
    || isAuthRequestPath(path)
    || typeof window === "undefined"
    || typeof window.dispatchEvent !== "function"
  ) {
    return;
  }
  window.dispatchEvent(new CustomEvent(AUTH_REVALIDATION_REQUESTED_EVENT));
}

function extractDetailMessage(detail) {
  if (typeof detail === "string") {
    return detail.trim() || null;
  }
  if (Array.isArray(detail)) {
    return joinMessages(detail.map((entry) => {
      if (typeof entry === "string") {
        return entry;
      }
      if (entry && typeof entry === "object") {
        const field = Array.isArray(entry.loc)
          ? entry.loc.filter((part) => typeof part === "string" || typeof part === "number").join(".")
          : "";
        const message = typeof entry.msg === "string" ? entry.msg.trim() : "";
        if (field && message) {
          return `${field}: ${message}`;
        }
        return message || (typeof entry.message === "string" ? entry.message : "");
      }
      return "";
    }));
  }
  if (detail && typeof detail === "object") {
    return joinMessages([
      typeof detail.message === "string" ? detail.message : "",
      typeof detail.title === "string" ? detail.title : "",
      typeof detail.error === "string" ? detail.error : "",
      typeof detail.reason === "string" ? detail.reason : "",
    ]);
  }
  return null;
}

function isExactMaintenanceModeMessage(value) {
  return typeof value === "string" && value.trim() === MAINTENANCE_MODE_MESSAGE;
}

export function extractApiErrorMessage(payload, fallback = "Request failed") {
  const detail =
    typeof payload === "object" && payload && "detail" in payload
      ? payload.detail
      : null;
  const detailMessage = extractDetailMessage(detail);
  if (detailMessage) {
    return detailMessage;
  }
  if (typeof payload === "string") {
    const trimmed = payload.trim();
    if (trimmed) {
      return trimmed;
    }
    return fallback;
  }
  if (payload && typeof payload === "object") {
    return joinMessages([
      typeof payload.message === "string" ? payload.message : "",
      typeof payload.error === "string" ? payload.error : "",
      typeof payload.title === "string" ? payload.title : "",
    ]) || fallback;
  }
  return fallback;
}

export function isMaintenanceModeError(error) {
  if (!error || Number(error.status) !== 503) {
    return false;
  }
  if (isExactMaintenanceModeMessage(error.message)) {
    return true;
  }
  if (isExactMaintenanceModeMessage(extractDetailMessage(error.detail))) {
    return true;
  }
  if (typeof error === "object" && "payload" in error) {
    return isExactMaintenanceModeMessage(extractApiErrorMessage(error.payload, ""));
  }
  return false;
}

function dispatchMaintenanceModeBlocked(error) {
  if (
    typeof window === "undefined"
    || typeof window.dispatchEvent !== "function"
    || !isMaintenanceModeError(error)
  ) {
    return;
  }
  window.dispatchEvent(new CustomEvent(MAINTENANCE_MODE_BLOCKED_EVENT, {
    detail: { message: MAINTENANCE_MODE_MESSAGE },
  }));
}

export async function apiRequest(path, options = {}) {
  const { data, headers = {}, signal, method = "GET" } = options;
  const requestHeaders = { ...headers };

  let body;
  if (data !== undefined) {
    if (typeof FormData !== "undefined" && data instanceof FormData) {
      body = data;
    } else {
      requestHeaders["Content-Type"] = "application/json";
      body = JSON.stringify(data);
    }
  }

  let response;
  try {
    response = await fetch(path, {
      method,
      headers: requestHeaders,
      body,
      signal,
      credentials: "include",
    });
  } catch (error) {
    if (error?.name !== "AbortError") {
      dispatchStartupConnectivityFailure({ path });
    }
    throw error;
  }
  dispatchStartupApplicationReady();

  const contentType = response.headers.get("content-type") || "";
  if (response.headers.get("x-elvern-totp-setup-required") === "true" && typeof window !== "undefined") {
    const segments = window.location.pathname.split("/").filter(Boolean);
    const prefixCandidate = segments[0] || "";
    const base = /^[a-hjkmnp-z2-9]{8,24}$/.test(prefixCandidate) ? `/${prefixCandidate}` : "";
    if (!window.location.pathname.endsWith("/setup/totp")) {
      window.location.assign(`${window.location.origin}${base}/setup/totp`);
    }
  }
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload && "detail" in payload
        ? payload.detail
        : null;
    const message = extractApiErrorMessage(payload);
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    error.detail = detail;
    if (response.status === 401) {
      clearProtectedQueryCache();
    }
    dispatchAuthRevalidationRequested(path, response.status);
    dispatchMaintenanceModeBlocked(error);
    throw error;
  }

  return payload;
}
