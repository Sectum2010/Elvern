import { clearProtectedQueryCache } from "./queryClient";
import {
  dispatchStartupApplicationReady,
  dispatchStartupConnectivityFailure,
} from "./startupConnection.js";
import {
  combineAbortSignals,
  getPageLifecycleSignal,
} from "./pageLifecycle.js";
import {
  getExternalNavigationRequestOwnerSignal,
  isExpectedExternalNavigationReason,
  releaseExternalNavigationAwareRequestOwner,
} from "./externalNavigationCoordinator.js";


function joinMessages(values) {
  const messages = values
    .map((value) => (typeof value === "string" ? value.trim() : ""))
    .filter(Boolean);
  return messages.length > 0 ? messages.join("; ") : null;
}

export const MAINTENANCE_MODE_MESSAGE = "The server is currently under construction, please try again later";
export const MAINTENANCE_MODE_BLOCKED_EVENT = "elvern:maintenance-mode-blocked";
export const AUTH_REVALIDATION_REQUESTED_EVENT = "elvern:auth-revalidation-requested";


export class ApiNetworkError extends Error {
  constructor(
    message = "Elvern could not complete the request.",
    { cause, failureId = 0, incidentId = 0, requestClass = "api" } = {},
  ) {
    super(message, { cause });
    this.name = "ApiNetworkError";
    this.transient = true;
    this.category = "transport";
    this.failureId = Number(failureId) || 0;
    this.incidentId = Number(incidentId) || 0;
    this.requestClass = requestClass;
  }
}


export class ApiResponseError extends Error {
  constructor(status, { cause } = {}) {
    super("Elvern received an unreadable response from the server.", { cause });
    this.name = "ApiResponseError";
    this.transient = false;
    this.category = "protocol";
    this.status = Number.isInteger(status) ? status : null;
    this.responseStatus = this.status;
  }
}


export class ApiCancellationError extends Error {
  constructor(reason) {
    super("The request was cancelled for external navigation.");
    this.name = "ApiCancellationError";
    this.category = "cancellation";
    this.cancellationReason = reason?.reason || "expected_external_navigation";
  }
}


// A malformed body surfaces as a SyntaxError (JSON.parse) — the server DID
// respond, so this is a protocol/response failure, never a transport outage
// and never an abort. Everything else thrown while consuming the body stream
// (Firefox "NetworkError when attempting to fetch resource", DOMException,
// TypeError) is a genuine transport failure.
function isMalformedBodyError(error) {
  return error?.name === "SyntaxError";
}


export function isAbortError(error) {
  return error?.name === "AbortError" || error?.category === "cancellation";
}


export function isExpectedExternalNavigationCancellation(error) {
  return error?.category === "cancellation"
    && error?.cancellationReason === "expected_external_navigation";
}


export function isTransientNetworkError(error) {
  return error instanceof ApiNetworkError || error?.transient === true;
}


export function isHttpError(error) {
  return Number.isInteger(Number(error?.status));
}


function createAbortError(reason = null) {
  if (isExpectedExternalNavigationReason(reason)) {
    return new ApiCancellationError(reason);
  }
  if (typeof DOMException === "function") {
    return new DOMException("The request was aborted.", "AbortError");
  }
  const error = new Error("The request was aborted.");
  error.name = "AbortError";
  return error;
}

function isAuthRequestPath(path) {
  const pathname = String(path || "").split("?", 1)[0];
  return pathname.startsWith("/api/auth/");
}

export function classifyApiRequest(path) {
  const pathname = String(path || "").split("?", 1)[0];
  if (pathname === "/api/auth/login") return "auth_login";
  if (pathname.startsWith("/api/auth/")) return "auth";
  if (pathname.startsWith("/api/library")) return "library";
  if (pathname.startsWith("/api/user-settings")) return "user_settings";
  if (pathname.startsWith("/api/desktop-helper")) return "desktop_helper";
  if (
    pathname.startsWith("/api/playback")
    || pathname.startsWith("/api/progress")
    || pathname.startsWith("/api/browser-playback")
    || pathname.startsWith("/api/desktop-playback")
  ) {
    return "playback";
  }
  return "api";
}

function createNetworkError(path, cause) {
  const requestClass = classifyApiRequest(path);
  const recoveryIdentity = dispatchStartupConnectivityFailure({
    classification: "transport",
    requestClass,
  });
  return new ApiNetworkError(undefined, {
    cause,
    requestClass,
    ...recoveryIdentity,
  });
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
  const {
    abortOnPageHide = false,
    cache,
    data,
    headers = {},
    keepalive,
    method = "GET",
    mode,
    redirect,
    referrerPolicy,
    requestOwner,
    signal,
  } = options;
  const requestOwnerSignal = getExternalNavigationRequestOwnerSignal(requestOwner);
  if (requestOwner && !requestOwnerSignal) {
    throw new TypeError("apiRequest received an invalid request owner.");
  }
  const requestHeaders = { ...headers };
  const combinedSignal = combineAbortSignals([
    signal,
    requestOwnerSignal,
    abortOnPageHide ? getPageLifecycleSignal() : null,
  ]);

  let body;
  if (data !== undefined) {
    if (typeof FormData !== "undefined" && data instanceof FormData) {
      body = data;
    } else {
      requestHeaders["Content-Type"] = "application/json";
      body = JSON.stringify(data);
    }
  }

  // One error boundary spans the complete request transaction: fetch, header
  // receipt, body read/parse, and HTTP classification. AbortSignal listeners
  // are cleaned up in `finally` only after every stage (including body reading)
  // has settled, so a listener is never removed while the body is still being
  // consumed.
  let response;
  let payload;
  try {
    // Stage 1-3: construct the request, execute fetch, receive headers.
    try {
      response = await fetch(path, {
        method,
        headers: requestHeaders,
        body,
        signal: combinedSignal.signal,
        credentials: "include",
        ...(cache === undefined ? {} : { cache }),
        ...(keepalive === undefined ? {} : { keepalive }),
        ...(mode === undefined ? {} : { mode }),
        ...(redirect === undefined ? {} : { redirect }),
        ...(referrerPolicy === undefined ? {} : { referrerPolicy }),
      });
    } catch (error) {
      if (combinedSignal.signal?.aborted || isAbortError(error)) {
        throw createAbortError(combinedSignal.signal?.reason);
      }
      throw createNetworkError(path, error);
    }

    // Headers have arrived: an actual HTTP response exists. The application is
    // proven reachable even if the body later fails to read or parse.
    dispatchStartupApplicationReady();

    // Authentication security actions depend only on the HTTP status and must
    // happen even when the response body is malformed or its stream fails.
    if (response.status === 401) {
      clearProtectedQueryCache();
    }
    dispatchAuthRevalidationRequested(path, response.status);

    const contentType = response.headers.get("content-type") || "";
    if (response.headers.get("x-elvern-totp-setup-required") === "true" && typeof window !== "undefined") {
      const segments = window.location.pathname.split("/").filter(Boolean);
      const prefixCandidate = segments[0] || "";
      const base = /^[a-hjkmnp-z2-9]{8,24}$/.test(prefixCandidate) ? `/${prefixCandidate}` : "";
      if (!window.location.pathname.endsWith("/setup/totp")) {
        window.location.assign(`${window.location.origin}${base}/setup/totp`);
      }
    }

    // Stage 4-5: consume and parse the body inside the same boundary so a
    // body-stream failure is classified rather than escaping as a raw browser
    // error.
    try {
      payload = contentType.includes("application/json")
        ? await response.json()
        : await response.text();
    } catch (error) {
      if (combinedSignal.signal?.aborted || isAbortError(error)) {
        throw createAbortError(combinedSignal.signal?.reason);
      }
      if (isMalformedBodyError(error)) {
        // The server responded but the body is unreadable: a protocol/response
        // failure, not a transport outage and not an abort.
        throw new ApiResponseError(response.status, { cause: error });
      }
      throw createNetworkError(path, error);
    }

    // Stage 6: classify HTTP status now that the full body is available.
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
      dispatchMaintenanceModeBlocked(error);
      throw error;
    }
  } finally {
    // Stage 7: remove the combined AbortSignal listeners only after all body
    // work has completed.
    combinedSignal.cleanup();
    if (requestOwner) {
      releaseExternalNavigationAwareRequestOwner(requestOwner);
    }
  }

  return payload;
}
