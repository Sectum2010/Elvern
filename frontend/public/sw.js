const OFFLINE_SHELL_REVISION = "__ELVERN_OFFLINE_SHELL_REVISION__";
const OFFLINE_CACHE_FAMILY = "elvern-offline-shell-";
const LEGACY_CACHE_FAMILY = "elvern-shell";
const NAVIGATION_HANDOFF_TIMEOUT_MS = 8_000;
const RECOVERY_NAVIGATION_TIMEOUT_MS = 15_000;
const RECOVERY_NAVIGATION_ARM_TTL_MS = 15_000;
const RECOVERY_NAVIGATION_ARM_MAX_RECORDS = 32;
const RECOVERY_ARM_DATABASE_NAME = "elvern-service-worker-state-v1";
const RECOVERY_ARM_STORE_NAME = "recovery_arms";
const RECOVERY_ARM_DATABASE_VERSION = 1;
const RECOVERY_NAVIGATION_MESSAGE = "ELVERN_ARM_RECOVERY_NAVIGATION";
const RECOVERY_NAVIGATION_ACK = "ELVERN_RECOVERY_NAVIGATION_ARMED";
const APP_SHELL_HEADER = "X-Elvern-App-Shell";
const OFFLINE_SHELL_HEADER = "X-Elvern-Offline-Shell";
const scopePath = new URL(self.registration.scope).pathname;
const cacheScopeIdentity = encodeURIComponent(scopePath);
const OFFLINE_CACHE_NAME = `${OFFLINE_CACHE_FAMILY}${OFFLINE_SHELL_REVISION}:${cacheScopeIdentity}`;
const OFFLINE_URL = new URL("offline.html", self.registration.scope).href;
let recoveryDatabasePromise = null;
let recoveryStoreWarningIssued = false;


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


function warnRecoveryStoreUnavailable() {
  if (recoveryStoreWarningIssued) return;
  recoveryStoreWarningIssued = true;
  console.warn("Elvern recovery arm storage is unavailable; using normal navigation recovery.");
}


function recoveryArmKey(scopeIdentity, clientId) {
  return `${scopeIdentity}::${clientId}`;
}


function openRecoveryDatabase() {
  if (recoveryDatabasePromise) return recoveryDatabasePromise;
  recoveryDatabasePromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB is unavailable"));
      return;
    }
    const request = indexedDB.open(RECOVERY_ARM_DATABASE_NAME, RECOVERY_ARM_DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(RECOVERY_ARM_STORE_NAME)) {
        database.createObjectStore(RECOVERY_ARM_STORE_NAME);
      }
    };
    request.onerror = () => reject(request.error || new Error("Unable to open recovery arm storage"));
    request.onsuccess = () => resolve(request.result);
  }).catch((error) => {
    recoveryDatabasePromise = null;
    throw error;
  });
  return recoveryDatabasePromise;
}


async function writeDurableRecoveryArm({ sourceClientId, nonce, expiresAt }) {
  const database = await openRecoveryDatabase();
  const now = Date.now();
  const key = recoveryArmKey(scopePath, sourceClientId);
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(RECOVERY_ARM_STORE_NAME, "readwrite");
    const store = transaction.objectStore(RECOVERY_ARM_STORE_NAME);
    const request = store.getAll();
    let operationReady = false;
    request.onsuccess = () => {
      const retained = [];
      for (const record of request.result || []) {
        const recordKey = recoveryArmKey(record.scope_identity, record.source_client_id);
        if (Number(record.expires_at) <= now || recordKey === key) {
          store.delete(recordKey);
        } else {
          retained.push(record);
        }
      }
      retained.push({
        schema_version: 1,
        scope_identity: scopePath,
        source_client_id: sourceClientId,
        nonce,
        expires_at: expiresAt,
        created_at: now,
      });
      retained.sort((left, right) => Number(left.created_at) - Number(right.created_at));
      while (retained.length > RECOVERY_NAVIGATION_ARM_MAX_RECORDS) {
        const oldest = retained.shift();
        store.delete(recoveryArmKey(oldest.scope_identity, oldest.source_client_id));
      }
      store.put(retained[retained.length - 1], key);
      operationReady = true;
    };
    request.onerror = () => reject(request.error || new Error("Unable to read recovery arm storage"));
    transaction.oncomplete = () => operationReady
      ? resolve(true)
      : reject(new Error("Recovery arm transaction completed without a write"));
    transaction.onerror = () => reject(transaction.error || new Error("Recovery arm transaction failed"));
    transaction.onabort = () => reject(transaction.error || new Error("Recovery arm transaction aborted"));
  });
}


async function consumeDurableRecoveryArm(clientId) {
  if (!clientId) return false;
  try {
    const database = await openRecoveryDatabase();
    const now = Date.now();
    const key = recoveryArmKey(scopePath, clientId);
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(RECOVERY_ARM_STORE_NAME, "readwrite");
      const store = transaction.objectStore(RECOVERY_ARM_STORE_NAME);
      const request = store.getAll();
      let accepted = false;
      request.onsuccess = () => {
        for (const record of request.result || []) {
          const recordKey = recoveryArmKey(record.scope_identity, record.source_client_id);
          const expired = Number(record.expires_at) <= now;
          if (expired || recordKey === key) store.delete(recordKey);
          if (
            recordKey === key
            && !expired
            && record.schema_version === 1
            && record.scope_identity === scopePath
            && record.source_client_id === clientId
            && typeof record.nonce === "string"
            && record.nonce.length >= 16
          ) {
            accepted = true;
          }
        }
      };
      request.onerror = () => reject(request.error || new Error("Unable to read recovery arm storage"));
      transaction.oncomplete = () => resolve(accepted);
      transaction.onerror = () => reject(transaction.error || new Error("Recovery arm transaction failed"));
      transaction.onabort = () => reject(transaction.error || new Error("Recovery arm transaction aborted"));
    });
  } catch {
    warnRecoveryStoreUnavailable();
    return false;
  }
}


async function cleanupExpiredRecoveryArms() {
  const database = await openRecoveryDatabase();
  const now = Date.now();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(RECOVERY_ARM_STORE_NAME, "readwrite");
    const store = transaction.objectStore(RECOVERY_ARM_STORE_NAME);
    const request = store.getAll();
    request.onsuccess = () => {
      for (const record of request.result || []) {
        if (Number(record.expires_at) <= now) {
          store.delete(recoveryArmKey(record.scope_identity, record.source_client_id));
        }
      }
    };
    request.onerror = () => reject(request.error || new Error("Unable to read recovery arm storage"));
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("Recovery arm cleanup failed"));
    transaction.onabort = () => reject(transaction.error || new Error("Recovery arm cleanup aborted"));
  });
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


self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    await cacheOfflineShell();
    await self.skipWaiting();
  })());
});


self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    await clearLegacyAndOldOfflineCaches();
    try {
      await cleanupExpiredRecoveryArms();
    } catch {
      warnRecoveryStoreUnavailable();
    }
    await self.clients.claim();
  })());
});


self.addEventListener("message", (event) => {
  const sourceClientId = event.source?.id;
  const payload = event.data;
  const expiresAt = Number(payload?.expires_at);
  const nonce = typeof payload?.nonce === "string" ? payload.nonce : "";
  const replyPort = event.ports?.[0];
  const acknowledge = (accepted, durability = "unavailable") => {
    replyPort?.postMessage({
      type: RECOVERY_NAVIGATION_ACK,
      schema_version: 1,
      nonce,
      accepted,
      durability,
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
  const operation = writeDurableRecoveryArm({ sourceClientId, nonce, expiresAt }).then(
    () => acknowledge(true, "durable"),
    () => {
      warnRecoveryStoreUnavailable();
      acknowledge(false);
    },
  );
  event.waitUntil(operation);
});


self.addEventListener("fetch", (event) => {
  if (!navigationIsEligible(event.request)) return;
  const navigationClientId = event.replacesClientId || event.clientId;
  event.respondWith((async () => (
    await consumeDurableRecoveryArm(navigationClientId)
      ? recoveryNavigation(event.request)
      : networkFirstNavigation(event.request)
  ))());
});
