const OFFLINE_SHELL_REVISION = "__ELVERN_OFFLINE_SHELL_REVISION__";
const OFFLINE_CACHE_FAMILY = "elvern-offline-shell-";
const LEGACY_CACHE_FAMILY = "elvern-shell";
const NAVIGATION_HANDOFF_TIMEOUT_MS = 8_000;
const RECOVERY_NAVIGATION_TIMEOUT_MS = 15_000;
const RECOVERY_NAVIGATION_ARM_TTL_MS = 15_000;
const RECOVERY_NAVIGATION_MESSAGE = "ELVERN_ARM_RECOVERY_NAVIGATION";
const RECOVERY_NAVIGATION_ACK = "ELVERN_RECOVERY_NAVIGATION_ARMED";
const APP_SHELL_HEADER = "X-Elvern-App-Shell";
const OFFLINE_SHELL_HEADER = "X-Elvern-Offline-Shell";
const scopePath = new URL(self.registration.scope).pathname;
const cacheScopeIdentity = encodeURIComponent(scopePath);
const OFFLINE_CACHE_NAME = `${OFFLINE_CACHE_FAMILY}${OFFLINE_SHELL_REVISION}:${cacheScopeIdentity}`;
const OFFLINE_URL = new URL("offline.html", self.registration.scope).href;
const recoveryNavigationByClientId = new Map();


async function cacheOfflineShell() {
  const cache = await caches.open(OFFLINE_CACHE_NAME);
  await cache.add(new Request(OFFLINE_URL, { cache: "reload", credentials: "same-origin" }));
}


function isCurrentScopeOfflineCache(key) {
  return key.startsWith(OFFLINE_CACHE_FAMILY) && key.endsWith(`:${cacheScopeIdentity}`);
}


async function clearLegacyAndOldOfflineCaches() {
  const keys = await caches.keys();
  await Promise.all(keys
    .filter((key) => key.startsWith(LEGACY_CACHE_FAMILY)
      || (isCurrentScopeOfflineCache(key) && key !== OFFLINE_CACHE_NAME))
    .map((key) => caches.delete(key)));
}


function navigationIsEligible(request) {
  if (request.method !== "GET" || request.mode !== "navigate") return false;
  const requestUrl = new URL(request.url);
  if (requestUrl.origin !== self.location.origin) return false;
  const relativePath = requestUrl.pathname.startsWith(scopePath)
    ? requestUrl.pathname.slice(scopePath.length)
    : requestUrl.pathname;
  return !relativePath.startsWith("api/")
    && requestUrl.pathname !== "/_elvern/frontend-health"
    && relativePath !== "health"
    && relativePath !== "sw.js"
    && relativePath !== "manifest.webmanifest"
    && !relativePath.startsWith("assets/")
    && !relativePath.startsWith("icons/");
}


async function offlineShellResponse() {
  const cached = await caches.match(OFFLINE_URL, { cacheName: OFFLINE_CACHE_NAME });
  if (!cached) return null;
  const headers = new Headers(cached.headers);
  headers.set(OFFLINE_SHELL_HEADER, "1");
  return new Response(cached.body, {
    status: cached.status,
    statusText: cached.statusText,
    headers,
  });
}


function fetchResult(request) {
  return fetch(request).then(
    (response) => ({ kind: "response", response }),
    (error) => ({ kind: "failure", error }),
  );
}


function timeoutResult(timeoutMs) {
  let timeoutId = 0;
  const promise = new Promise((resolve) => {
    timeoutId = setTimeout(() => resolve({ kind: "timeout" }), timeoutMs);
  });
  return { promise, cancel: () => clearTimeout(timeoutId) };
}


async function networkFirstNavigation(request) {
  const networkResult = fetchResult(request);
  const timeout = timeoutResult(NAVIGATION_HANDOFF_TIMEOUT_MS);
  const result = await Promise.race([networkResult, timeout.promise]);
  timeout.cancel();
  if (result.kind === "response") return result.response;
  const offline = await offlineShellResponse();
  if (offline) return offline;
  const lateResult = result.kind === "timeout" ? await networkResult : result;
  if (lateResult.kind === "response") return lateResult.response;
  throw lateResult.error || new TypeError("Elvern navigation failed");
}


async function recoveryNavigation(request) {
  const recoveryRequest = new Request(request, { cache: "no-store" });
  const networkResult = fetchResult(recoveryRequest);
  const timeout = timeoutResult(RECOVERY_NAVIGATION_TIMEOUT_MS);
  const result = await Promise.race([networkResult, timeout.promise]);
  timeout.cancel();
  if (
    result.kind === "response"
    && result.response.ok
    && result.response.headers.get(APP_SHELL_HEADER) === "1"
    && result.response.headers.get(OFFLINE_SHELL_HEADER) !== "1"
  ) {
    return result.response;
  }
  const offline = await offlineShellResponse();
  if (offline) return offline;
  const lateResult = result.kind === "timeout" ? await networkResult : result;
  if (lateResult.kind === "response") return lateResult.response;
  throw lateResult.error || new TypeError("Elvern recovery navigation failed");
}


function consumeRecoveryArm(clientId) {
  if (!clientId) return false;
  const arm = recoveryNavigationByClientId.get(clientId);
  recoveryNavigationByClientId.delete(clientId);
  return Boolean(arm && arm.expiresAt > Date.now());
}


self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    await cacheOfflineShell();
    await self.skipWaiting();
  })());
});


self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    await clearLegacyAndOldOfflineCaches();
    await self.clients.claim();
  })());
});


self.addEventListener("message", (event) => {
  const sourceClientId = event.source?.id;
  const payload = event.data;
  const expiresAt = Number(payload?.expires_at);
  const nonce = typeof payload?.nonce === "string" ? payload.nonce : "";
  const replyPort = event.ports?.[0];
  const acknowledge = (accepted) => {
    replyPort?.postMessage({
      type: RECOVERY_NAVIGATION_ACK,
      schema_version: 1,
      nonce,
      accepted,
    });
  };
  if (
    !sourceClientId
    || payload?.type !== RECOVERY_NAVIGATION_MESSAGE
    || payload?.schema_version !== 1
    || nonce.length < 16
    || nonce.length > 128
    || !Number.isFinite(expiresAt)
    || expiresAt <= Date.now()
    || expiresAt > Date.now() + RECOVERY_NAVIGATION_ARM_TTL_MS
  ) {
    acknowledge(false);
    return;
  }
  recoveryNavigationByClientId.set(sourceClientId, { nonce, expiresAt });
  acknowledge(true);
  setTimeout(() => {
    const current = recoveryNavigationByClientId.get(sourceClientId);
    if (current?.nonce === nonce && current.expiresAt <= Date.now()) {
      recoveryNavigationByClientId.delete(sourceClientId);
    }
  }, Math.max(0, expiresAt - Date.now()));
});


self.addEventListener("fetch", (event) => {
  if (!navigationIsEligible(event.request)) return;
  const navigationClientId = event.replacesClientId || event.clientId;
  const armed = consumeRecoveryArm(navigationClientId);
  event.respondWith(armed
    ? recoveryNavigation(event.request)
    : networkFirstNavigation(event.request));
});
