const OFFLINE_SHELL_REVISION = "__ELVERN_OFFLINE_SHELL_REVISION__";
const OFFLINE_CACHE_FAMILY = "elvern-offline-shell-";
const LEGACY_CACHE_FAMILY = "elvern-shell";
const NAVIGATION_HANDOFF_TIMEOUT_MS = 3_500;
const scopePath = new URL(self.registration.scope).pathname;
const cacheScopeIdentity = encodeURIComponent(scopePath);
const OFFLINE_CACHE_NAME = `${OFFLINE_CACHE_FAMILY}${OFFLINE_SHELL_REVISION}:${cacheScopeIdentity}`;
const OFFLINE_URL = new URL("offline.html", self.registration.scope).href;


function isElvernOfflineCache(key) {
  return key.startsWith(OFFLINE_CACHE_FAMILY);
}


async function cacheOfflineShell() {
  const cache = await caches.open(OFFLINE_CACHE_NAME);
  await cache.add(new Request(OFFLINE_URL, { cache: "reload", credentials: "same-origin" }));
}


async function clearLegacyAndOldOfflineCaches() {
  const keys = await caches.keys();
  await Promise.all(keys
    .filter((key) => key.startsWith(LEGACY_CACHE_FAMILY) || (isElvernOfflineCache(key) && key !== OFFLINE_CACHE_NAME))
    .map((key) => caches.delete(key)));
}


function navigationIsEligible(request) {
  if (request.method !== "GET" || request.mode !== "navigate") {
    return false;
  }
  const requestUrl = new URL(request.url);
  if (requestUrl.origin !== self.location.origin) {
    return false;
  }
  const relativePath = requestUrl.pathname.startsWith(scopePath)
    ? requestUrl.pathname.slice(scopePath.length)
    : requestUrl.pathname;
  return !relativePath.startsWith("api/")
    && relativePath !== "health"
    && relativePath !== "sw.js"
    && relativePath !== "manifest.webmanifest"
    && !relativePath.startsWith("assets/")
    && !relativePath.startsWith("icons/");
}


async function networkFirstNavigation(request) {
  const networkRequest = fetch(request);
  let timeoutId = 0;
  const handoffTimeout = new Promise((resolve) => {
    timeoutId = setTimeout(() => resolve(null), NAVIGATION_HANDOFF_TIMEOUT_MS);
  });
  try {
    const response = await Promise.race([networkRequest, handoffTimeout]);
    if (response) {
      clearTimeout(timeoutId);
      return response;
    }
  } catch {
    clearTimeout(timeoutId);
  }
  const cachedOfflineShell = await caches.match(OFFLINE_URL, { cacheName: OFFLINE_CACHE_NAME });
  if (cachedOfflineShell) {
    return cachedOfflineShell;
  }
  return networkRequest;
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


self.addEventListener("fetch", (event) => {
  if (!navigationIsEligible(event.request)) {
    return;
  }
  event.respondWith(networkFirstNavigation(event.request));
});
