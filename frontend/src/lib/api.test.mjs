import { afterEach, test, vi } from "vitest";
import assert from "node:assert/strict";

import {
  ApiNetworkError,
  ApiResponseError,
  apiRequest,
  AUTH_REVALIDATION_REQUESTED_EVENT,
  extractApiErrorMessage,
  isMaintenanceModeError,
  MAINTENANCE_MODE_BLOCKED_EVENT,
  MAINTENANCE_MODE_MESSAGE,
} from "./api.js";
import { resetPageLifecycleForTests } from "./pageLifecycle.js";
import {
  buildLibraryQueryKey,
  buildLibraryShadowV2QueryKey,
  buildLibraryV2QueryKey,
} from "./libraryQueries.js";
import { queryClient } from "./queryClient.js";
import { buildUserSettingsQueryKey } from "./userSettingsQueries.js";
import {
  STARTUP_APPLICATION_READY_EVENT,
  STARTUP_CONNECTIVITY_FAILURE_EVENT,
} from "./startupConnection.js";
import { resetConnectivityRecoveryStoreForTests } from "./connectivityRecoveryStore.js";

afterEach(() => {
  resetPageLifecycleForTests();
  resetConnectivityRecoveryStoreForTests();
  queryClient.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("extractApiErrorMessage returns string detail directly", () => {
  assert.equal(
    extractApiErrorMessage({ detail: "Google OAuth Client Secret must not contain spaces." }),
    "Google OAuth Client Secret must not contain spaces.",
  );
});

test("extractApiErrorMessage uses object detail message", () => {
  assert.equal(
    extractApiErrorMessage({ detail: { message: "Reconnect Google Drive to continue this action." } }),
    "Reconnect Google Drive to continue this action.",
  );
});

test("extractApiErrorMessage joins FastAPI validation detail entries", () => {
  assert.equal(
    extractApiErrorMessage({
      detail: [
        { loc: ["body", "resource_id"], msg: "String should have at least 2 characters" },
        { loc: ["body", "resource_type"], msg: "Input should be 'folder' or 'shared_drive'" },
      ],
    }),
    "body.resource_id: String should have at least 2 characters; body.resource_type: Input should be 'folder' or 'shared_drive'",
  );
});

test("extractApiErrorMessage falls back to plain text error bodies", () => {
  assert.equal(
    extractApiErrorMessage("Cloud libraries refresh failed upstream."),
    "Cloud libraries refresh failed upstream.",
  );
});

test("extractApiErrorMessage uses object-level message when detail is absent", () => {
  assert.equal(
    extractApiErrorMessage({ message: "Cloud libraries could not refresh." }),
    "Cloud libraries could not refresh.",
  );
});

test("apiRequest dispatches maintenance mode event for the exact maintenance 503", async () => {
  const events = [];
  function handleMaintenanceModeBlocked(event) {
    events.push(event.detail?.message || "");
  }
  window.addEventListener(MAINTENANCE_MODE_BLOCKED_EVENT, handleMaintenanceModeBlocked);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ detail: MAINTENANCE_MODE_MESSAGE }),
    { status: 503, headers: { "content-type": "application/json" } },
  )));

  try {
    await assert.rejects(
      () => apiRequest("/api/library"),
      (error) => {
        assert.equal(isMaintenanceModeError(error), true);
        assert.equal(error.message, MAINTENANCE_MODE_MESSAGE);
        return true;
      },
    );

    assert.deepEqual(events, [MAINTENANCE_MODE_MESSAGE]);
  } finally {
    window.removeEventListener(MAINTENANCE_MODE_BLOCKED_EVENT, handleMaintenanceModeBlocked);
  }
});

test("apiRequest does not dispatch maintenance mode event for generic 503 errors", async () => {
  const events = [];
  function handleMaintenanceModeBlocked(event) {
    events.push(event.detail?.message || "");
  }
  window.addEventListener(MAINTENANCE_MODE_BLOCKED_EVENT, handleMaintenanceModeBlocked);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ detail: "Service unavailable" }),
    { status: 503, headers: { "content-type": "application/json" } },
  )));

  try {
    await assert.rejects(
      () => apiRequest("/api/library"),
      (error) => {
        assert.equal(isMaintenanceModeError(error), false);
        assert.equal(error.message, "Service unavailable");
        return true;
      },
    );

    assert.deepEqual(events, []);
  } finally {
    window.removeEventListener(MAINTENANCE_MODE_BLOCKED_EVENT, handleMaintenanceModeBlocked);
  }
});

test("apiRequest immediately clears protected library cache on 401", async () => {
  const identity = {
    userId: 2,
    role: "standard_user",
    category: "movies",
  };
  const libraryKey = buildLibraryQueryKey(identity);
  const v2LibraryKey = buildLibraryV2QueryKey(identity);
  const shadowLibraryKey = buildLibraryShadowV2QueryKey(identity);
  const settingsKey = buildUserSettingsQueryKey({ userId: 2, role: "standard_user" });
  queryClient.setQueryData(libraryKey, { items: [{ id: 401 }] });
  queryClient.setQueryData(v2LibraryKey, { items_by_id: { "401": { id: 401 } } });
  queryClient.setQueryData(shadowLibraryKey, { items_by_id: { "401": { id: 401 } } });
  queryClient.setQueryData(settingsKey, { poster_card_display_max_width: "800" });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ detail: "Authentication required" }),
    { status: 401, headers: { "content-type": "application/json" } },
  )));

  await assert.rejects(() => apiRequest("/api/library"));

  assert.equal(queryClient.getQueryData(libraryKey), undefined);
  assert.equal(queryClient.getQueryData(v2LibraryKey), undefined);
  assert.equal(queryClient.getQueryData(shadowLibraryKey), undefined);
  assert.equal(queryClient.getQueryData(settingsKey), undefined);
});

test("apiRequest preserves protected cache and requests auth revalidation on a business 403", async () => {
  const identity = {
    userId: 2,
    role: "standard_user",
    category: "movies",
  };
  const libraryKey = buildLibraryQueryKey(identity);
  const v2LibraryKey = buildLibraryV2QueryKey(identity);
  const settingsKey = buildUserSettingsQueryKey({ userId: 2, role: "standard_user" });
  const events = [];
  const handleRevalidation = () => events.push("requested");
  window.addEventListener(AUTH_REVALIDATION_REQUESTED_EVENT, handleRevalidation);
  queryClient.setQueryData(libraryKey, { items: [{ id: 403 }] });
  queryClient.setQueryData(v2LibraryKey, { items_by_id: { "403": { id: 403 } } });
  queryClient.setQueryData(settingsKey, { poster_card_display_max_width: "800" });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ detail: "This action is not allowed" }),
    { status: 403, headers: { "content-type": "application/json" } },
  )));

  try {
    await assert.rejects(() => apiRequest("/api/assistant/requests"));
    assert.deepEqual(queryClient.getQueryData(libraryKey), { items: [{ id: 403 }] });
    assert.deepEqual(queryClient.getQueryData(v2LibraryKey), {
      items_by_id: { "403": { id: 403 } },
    });
    assert.deepEqual(queryClient.getQueryData(settingsKey), { poster_card_display_max_width: "800" });
    assert.deepEqual(events, ["requested"]);
  } finally {
    window.removeEventListener(AUTH_REVALIDATION_REQUESTED_EVENT, handleRevalidation);
  }
});

test("auth verification 403 does not recursively request another auth revalidation", async () => {
  const events = [];
  const handleRevalidation = () => events.push("requested");
  window.addEventListener(AUTH_REVALIDATION_REQUESTED_EVENT, handleRevalidation);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ detail: "Session invalid" }),
    { status: 403, headers: { "content-type": "application/json" } },
  )));

  try {
    await assert.rejects(() => apiRequest("/api/auth/me"));
    assert.deepEqual(events, []);
  } finally {
    window.removeEventListener(AUTH_REVALIDATION_REQUESTED_EVENT, handleRevalidation);
  }
});

test("network failure requests startup connection recovery but AbortError does not", async () => {
  const events = [];
  const handleFailure = (event) => events.push(event.detail?.classification);
  window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  try {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new TypeError("offline")));
    await assert.rejects(() => apiRequest("/api/auth/me"));
    assert.deepEqual(events, ["transport"]);

    const abortError = new Error("cancelled");
    abortError.name = "AbortError";
    globalThis.fetch.mockRejectedValueOnce(abortError);
    await assert.rejects(() => apiRequest("/api/library"));
    assert.deepEqual(events, ["transport"]);
  } finally {
    window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  }
});

test("pagehide abort normalizes Firefox NetworkError without reporting connectivity failure", async () => {
  const events = [];
  const handleFailure = () => events.push("failure");
  window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  vi.stubGlobal("fetch", vi.fn((_path, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => {
      reject(new TypeError("NetworkError when attempting to fetch resource"));
    }, { once: true });
  })));

  try {
    const request = apiRequest("/api/library", { abortOnPageHide: true });
    window.dispatchEvent(new Event("pagehide"));
    await assert.rejects(request, (error) => {
      assert.equal(error.name, "AbortError");
      assert.doesNotMatch(error.message, /NetworkError/);
      return true;
    });
    assert.deepEqual(events, []);
  } finally {
    window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  }
});

test("real Firefox-style NetworkError becomes a stable ApiNetworkError", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("NetworkError when attempting to fetch resource")),
  );

  await assert.rejects(() => apiRequest("/api/library"), (error) => {
    assert.equal(error instanceof ApiNetworkError, true);
    assert.equal(error.name, "ApiNetworkError");
    assert.equal(error.category, "transport");
    assert.doesNotMatch(error.message, /NetworkError/);
    return true;
  });
});

test("apiRequest forwards the allowed cache option without spreading unknown options", async () => {
  const fetchMock = vi.fn(async () => new Response(
    JSON.stringify({ ok: true }),
    { status: 200, headers: { "content-type": "application/json" } },
  ));
  vi.stubGlobal("fetch", fetchMock);

  await apiRequest("/health", {
    cache: "no-store",
    unknownPrivateOption: "must-not-leak",
  });

  assert.equal(fetchMock.mock.calls[0][1].cache, "no-store");
  assert.equal("unknownPrivateOption" in fetchMock.mock.calls[0][1], false);
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function fakeResponse({ status = 200, contentType = "application/json", ok, json, text } = {}) {
  const headers = new Map();
  if (contentType) {
    headers.set("content-type", contentType);
  }
  return {
    ok: ok === undefined ? status >= 200 && status < 300 : ok,
    status,
    headers: { get: (name) => headers.get(String(name).toLowerCase()) ?? null },
    json: json || (async () => ({})),
    text: text || (async () => ""),
  };
}

test("body reader rejecting with a Firefox NetworkError becomes ApiNetworkError with one connectivity failure", async () => {
  const failures = [];
  const readyEvents = [];
  const handleFailure = () => failures.push("failure");
  const handleReady = () => readyEvents.push("ready");
  window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  window.addEventListener(STARTUP_APPLICATION_READY_EVENT, handleReady);
  vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({
    json: () => Promise.reject(new TypeError("NetworkError when attempting to fetch resource")),
  })));

  try {
    await assert.rejects(() => apiRequest("/api/library"), (error) => {
      assert.equal(error instanceof ApiNetworkError, true);
      assert.equal(error.category, "transport");
      assert.equal(error.transient, true);
      assert.doesNotMatch(error.message, /NetworkError/);
      return true;
    });
    // Headers arrived, so the application is proven reachable, and the body
    // transport failure is reported exactly once.
    assert.deepEqual(readyEvents, ["ready"]);
    assert.deepEqual(failures, ["failure"]);
  } finally {
    window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
    window.removeEventListener(STARTUP_APPLICATION_READY_EVENT, handleReady);
  }
});

test("pagehide abort while response.json() is pending normalizes to AbortError without connectivity failure", async () => {
  const failures = [];
  const handleFailure = () => failures.push("failure");
  window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  const body = deferred();
  vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({ json: () => body.promise })));

  try {
    const request = apiRequest("/api/library", { abortOnPageHide: true });
    await Promise.resolve();
    window.dispatchEvent(new Event("pagehide"));
    body.reject(new TypeError("NetworkError when attempting to fetch resource"));
    await assert.rejects(request, (error) => {
      assert.equal(error.name, "AbortError");
      assert.doesNotMatch(error.message, /NetworkError/);
      return true;
    });
    assert.deepEqual(failures, []);
  } finally {
    window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  }
});

test("caller abort while response.text() is pending normalizes to AbortError", async () => {
  const failures = [];
  const handleFailure = () => failures.push("failure");
  window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  const controller = new AbortController();
  const body = deferred();
  vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({
    contentType: "text/plain",
    text: () => body.promise,
  })));

  try {
    const request = apiRequest("/api/library", { signal: controller.signal });
    await Promise.resolve();
    controller.abort();
    body.reject(new TypeError("NetworkError when attempting to fetch resource"));
    await assert.rejects(request, (error) => {
      assert.equal(error.name, "AbortError");
      return true;
    });
    assert.deepEqual(failures, []);
  } finally {
    window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  }
});

test("malformed JSON produces a sanitized ApiResponseError, not a raw SyntaxError or abort", async () => {
  const failures = [];
  const readyEvents = [];
  const handleFailure = () => failures.push("failure");
  const handleReady = () => readyEvents.push("ready");
  window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  window.addEventListener(STARTUP_APPLICATION_READY_EVENT, handleReady);
  vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({
    json: () => Promise.reject(new SyntaxError("Unexpected token < in JSON at position 0")),
  })));

  try {
    await assert.rejects(() => apiRequest("/api/library"), (error) => {
      assert.equal(error instanceof ApiResponseError, true);
      assert.equal(error.name, "ApiResponseError");
      assert.equal(error.category, "protocol");
      assert.equal(error.transient, false);
      assert.equal(error.status, 200);
      assert.equal(error.responseStatus, 200);
      assert.equal(error.cause?.name, "SyntaxError");
      assert.notEqual(error.name, "AbortError");
      assert.doesNotMatch(error.message, /SyntaxError|Unexpected token|NetworkError/);
      return true;
    });
    // The server responded (headers arrived), so a body parse failure still
    // proves reachability and never reports a transport outage.
    assert.deepEqual(readyEvents, ["ready"]);
    assert.deepEqual(failures, []);
  } finally {
    window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
    window.removeEventListener(STARTUP_APPLICATION_READY_EVENT, handleReady);
  }
});

test("malformed 401 still clears protected cache before body parsing fails", async () => {
  const key = buildLibraryQueryKey({
    userId: 2,
    role: "standard_user",
    category: "movies",
  });
  queryClient.setQueryData(key, { items: [{ id: 401 }] });
  vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({
    status: 401,
    json: () => Promise.reject(new SyntaxError("malformed")),
  })));

  await assert.rejects(() => apiRequest("/api/library"), (error) => {
    assert.equal(error instanceof ApiResponseError, true);
    assert.equal(error.status, 401);
    return true;
  });
  assert.equal(queryClient.getQueryData(key), undefined);
});

test("malformed business 403 still requests auth revalidation before body parsing fails", async () => {
  const events = [];
  const handler = () => events.push("revalidate");
  window.addEventListener(AUTH_REVALIDATION_REQUESTED_EVENT, handler);
  vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({
    status: 403,
    json: () => Promise.reject(new SyntaxError("malformed")),
  })));

  try {
    await assert.rejects(() => apiRequest("/api/assistant/requests"), (error) => {
      assert.equal(error instanceof ApiResponseError, true);
      assert.equal(error.status, 403);
      return true;
    });
    assert.deepEqual(events, ["revalidate"]);
  } finally {
    window.removeEventListener(AUTH_REVALIDATION_REQUESTED_EVENT, handler);
  }
});

test("malformed 503 is a protocol error and never impersonates maintenance mode", async () => {
  const events = [];
  const handler = () => events.push("maintenance");
  window.addEventListener(MAINTENANCE_MODE_BLOCKED_EVENT, handler);
  vi.stubGlobal("fetch", vi.fn(async () => fakeResponse({
    status: 503,
    json: () => Promise.reject(new SyntaxError("malformed")),
  })));

  try {
    await assert.rejects(() => apiRequest("/api/library"), (error) => {
      assert.equal(error instanceof ApiResponseError, true);
      assert.equal(error.status, 503);
      assert.equal(isMaintenanceModeError(error), false);
      return true;
    });
    assert.deepEqual(events, []);
  } finally {
    window.removeEventListener(MAINTENANCE_MODE_BLOCKED_EVENT, handler);
  }
});

test("transport diagnostics expose only a generic request class, never a raw path", async () => {
  const details = [];
  const handler = (event) => details.push(event.detail);
  window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handler);
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

  try {
    await assert.rejects(
      () => apiRequest("/api/library/search?q=private-title"),
      (error) => {
        assert.equal(error instanceof ApiNetworkError, true);
        assert.equal(error.requestClass, "library");
        assert.equal(error.failureId > 0, true);
        assert.equal(error.incidentId > 0, true);
        return true;
      },
    );
    assert.equal(details.length, 1);
    assert.equal(details[0].requestClass, "library");
    assert.equal(JSON.stringify(details[0]).includes("private-title"), false);
  } finally {
    window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handler);
  }
});

test("an HTTP 401 proves the application responded instead of reporting a connection failure", async () => {
  const events = [];
  const handleReady = () => events.push("ready");
  const handleFailure = () => events.push("failure");
  window.addEventListener(STARTUP_APPLICATION_READY_EVENT, handleReady);
  window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ detail: "Authentication required" }),
    { status: 401, headers: { "content-type": "application/json" } },
  )));

  try {
    await assert.rejects(() => apiRequest("/api/auth/me"));
    assert.deepEqual(events, ["ready"]);
  } finally {
    window.removeEventListener(STARTUP_APPLICATION_READY_EVENT, handleReady);
    window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleFailure);
  }
});
