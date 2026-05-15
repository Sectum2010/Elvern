import { test, expect } from "vitest";

import { softResumeRequiresHardReattach } from "./useOptimizedPlaybackSession.js";

test("soft resume ignores manifest revision-only URL changes", () => {
  const payload = {
    active_epoch_id: "epoch-a",
    active_manifest_url: "/api/browser-playback/sessions/session-a/route2.m3u8?attach_revision=9&manifest_revision=41",
  };

  expect(softResumeRequiresHardReattach({
    payload,
    attachedIdentity: "epoch-a",
    attachedManifestUrl: "/api/browser-playback/sessions/session-a/route2.m3u8?attach_revision=7&manifest_revision=39",
    streamSourceUrl: "/api/browser-playback/sessions/session-a/route2.m3u8?attach_revision=7&manifest_revision=39",
  })).toBe(false);
});

test("soft resume hard reattaches for a real epoch identity change", () => {
  expect(softResumeRequiresHardReattach({
    payload: {
      active_epoch_id: "epoch-b",
      active_manifest_url: "/api/browser-playback/sessions/session-a/route2.m3u8",
    },
    attachedIdentity: "epoch-a",
    attachedManifestUrl: "/api/browser-playback/sessions/session-a/route2.m3u8",
  })).toBe(true);
});

test("soft resume hard reattaches when active manifest path truly changes", () => {
  expect(softResumeRequiresHardReattach({
    payload: {
      active_epoch_id: "epoch-a",
      active_manifest_url: "/api/browser-playback/sessions/session-a/replacement.m3u8",
    },
    attachedIdentity: "epoch-a",
    attachedManifestUrl: "/api/browser-playback/sessions/session-a/route2.m3u8",
    streamSourceUrl: "/api/browser-playback/sessions/session-a/route2.m3u8",
  })).toBe(true);
});

test("soft resume uses current stream source when attached URL ref is empty", () => {
  const payload = {
    active_epoch_id: "epoch-a",
    active_manifest_url: "/api/browser-playback/sessions/session-a/route2.m3u8?manifest_revision=3",
  };

  expect(softResumeRequiresHardReattach({
    payload,
    attachedIdentity: "epoch-a",
    attachedManifestUrl: "",
    streamSourceUrl: "/api/browser-playback/sessions/session-a/route2.m3u8?manifest_revision=1",
  })).toBe(false);

  expect(softResumeRequiresHardReattach({
    payload,
    attachedIdentity: "epoch-a",
    attachedManifestUrl: "",
    streamSourceUrl: "/api/browser-playback/sessions/session-a/replacement.m3u8",
  })).toBe(true);
});
