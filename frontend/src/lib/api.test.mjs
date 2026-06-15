import { afterEach, test, vi } from "vitest";
import assert from "node:assert/strict";

import {
  apiRequest,
  extractApiErrorMessage,
  isMaintenanceModeError,
  MAINTENANCE_MODE_BLOCKED_EVENT,
  MAINTENANCE_MODE_MESSAGE,
} from "./api.js";

afterEach(() => {
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
