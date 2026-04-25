import test from "node:test";
import assert from "node:assert/strict";

import {
  clearLibraryCloudReconnectDismissal,
  dismissLibraryCloudReconnectPrompt,
  formatCompletedRescanWarning,
  formatGoogleConnectionHealthLabel,
  formatGoogleDriveSetupLabel,
  formatRescanBannerText,
  getCloudReconnectPrompt,
  hasCloudSyncWarning,
  isCloudReconnectRequired,
  readLibraryCloudReconnectDismissed,
} from "./cloudSyncStatus.js";
import { shouldGuardGoogleDriveAction } from "./providerAuth.js";


function createSessionStorageMock() {
  const store = new Map();
  return {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
  };
}


test("failed cloud sync produces a warning banner message", () => {
  const payload = {
    running: true,
    message: "Recent Watched is already current. Local scan started.",
    cloud_sync: {
      status: "failed",
      message: "Google Drive reconnect is required. Cloud library was not refreshed and may be stale.",
    },
  };

  assert.equal(hasCloudSyncWarning(payload.cloud_sync), true);
  assert.equal(
    formatRescanBannerText(payload),
    "Local scan started. Google Drive reconnect is required. Cloud library was not refreshed and may be stale.",
  );
});


test("successful cloud sync keeps the backend message", () => {
  const payload = {
    running: true,
    message: "Recent Watched is already current. Cloud refresh completed: 1 source(s) synced, 3 media row(s) refreshed. Local scan started.",
    cloud_sync: {
      status: "success",
      message: "Cloud refresh completed: 1 source(s) synced, 3 media row(s) refreshed.",
    },
  };

  assert.equal(hasCloudSyncWarning(payload.cloud_sync), false);
  assert.equal(formatRescanBannerText(payload), payload.message);
});


test("completed scan warning keeps the stale-state explanation", () => {
  assert.equal(
    formatCompletedRescanWarning("Google Drive reconnect is required. Cloud library was not refreshed and may be stale."),
    "Local scan completed. Google Drive reconnect is required. Cloud library was not refreshed and may be stale.",
  );
});


test("cloud reconnect helper detects reconnect-required provider health", () => {
  const payload = {
    google: {
      connection_status: "reconnect_required",
      reconnect_required: true,
      provider_auth_required: true,
    },
  };

  assert.equal(isCloudReconnectRequired(payload), true);
  assert.deepEqual(getCloudReconnectPrompt(payload), {
    title: "Reconnect Google Drive",
    message: "Google Drive reconnect is required. Cloud movies may be stale until you reconnect.",
  });
});


test("settings labels distinguish oauth setup from account reconnect health", () => {
  assert.equal(formatGoogleDriveSetupLabel("ready", "Ready"), "OAuth Ready");
  assert.equal(
    formatGoogleConnectionHealthLabel({ connection_status: "reconnect_required" }),
    "Reconnect required",
  );
});


test("library reconnect dismissal is stored in session storage", () => {
  const originalWindow = global.window;
  global.window = { sessionStorage: createSessionStorageMock() };

  try {
    assert.equal(readLibraryCloudReconnectDismissed(), false);
    dismissLibraryCloudReconnectPrompt();
    assert.equal(readLibraryCloudReconnectDismissed(), true);
    clearLibraryCloudReconnectDismissal();
    assert.equal(readLibraryCloudReconnectDismissed(), false);
  } finally {
    if (originalWindow === undefined) {
      delete global.window;
    } else {
      global.window = originalWindow;
    }
  }
});


test("cloud action guard only blocks cloud items when reconnect is required", () => {
  assert.equal(
    shouldGuardGoogleDriveAction({ itemSourceKind: "local", reconnectRequired: true }),
    false,
  );
  assert.equal(
    shouldGuardGoogleDriveAction({ itemSourceKind: "cloud", reconnectRequired: true }),
    true,
  );
  assert.equal(
    shouldGuardGoogleDriveAction({ itemSourceKind: "cloud", reconnectRequired: false }),
    false,
  );
});
