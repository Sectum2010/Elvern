import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyManifestSnapshot,
  isManifestUrlSafeToFetch,
  isPlaybackDebugEnabled,
  redactDiagnosticUrl,
  serializeTimeRanges,
} from "./playbackDiagnostics.js";

test("playback diagnostics are gated by query string or localStorage-style storage", () => {
  assert.equal(isPlaybackDebugEnabled("?elvernPlaybackDebug=1"), true);
  assert.equal(isPlaybackDebugEnabled("?elvernPlaybackDebug=false", {
    getItem: () => "1",
  }), false);
  assert.equal(isPlaybackDebugEnabled("", {
    getItem: (key) => (key === "elvernPlaybackDebug" ? "true" : ""),
  }), true);
  assert.equal(isPlaybackDebugEnabled("", {
    getItem: () => "",
  }), false);
});

test("diagnostic URL redaction keeps route shape but removes secrets", () => {
  const redacted = redactDiagnosticUrl("/api/native-playback/session/abc/stream?token=secret&plain=ok");

  assert.equal(redacted, "/api/native-playback/session/abc/stream?token=%5Bredacted%5D&plain=ok");
});

test("manifest classification distinguishes open event playlists from complete VOD playlists", () => {
  const eventManifest = [
    "#EXTM3U",
    "#EXT-X-VERSION:7",
    "#EXT-X-TARGETDURATION:4",
    "#EXT-X-MEDIA-SEQUENCE:0",
    "#EXT-X-PLAYLIST-TYPE:EVENT",
    "#EXTINF:4.000,",
    "segments/0.m4s",
  ].join("\n");
  const vodManifest = `${eventManifest.replace("#EXT-X-PLAYLIST-TYPE:EVENT", "#EXT-X-PLAYLIST-TYPE:VOD")}\n#EXT-X-ENDLIST`;

  assert.equal(classifyManifestSnapshot(eventManifest).classification, "event_open");
  assert.equal(classifyManifestSnapshot(eventManifest).contains_endlist, false);
  assert.equal(classifyManifestSnapshot(vodManifest).classification, "vod_complete");
  assert.equal(classifyManifestSnapshot(vodManifest).target_duration, "4");

  const tokenizedManifest = `${eventManifest}\nsegments/0.m4s?token=secret`;
  assert.equal(
    classifyManifestSnapshot(tokenizedManifest).first_lines.at(-1),
    "segments/0.m4s?token=[redacted]",
  );
});

test("same-origin m3u8 manifest URLs are considered safe to fetch", () => {
  globalThis.window = { location: { origin: "https://elvern.test" } };
  try {
    assert.equal(isManifestUrlSafeToFetch("/api/browser-playback/epochs/e1/index.m3u8"), true);
    assert.equal(isManifestUrlSafeToFetch("https://elvern.test/api/browser-playback/epochs/e1/index.m3u8"), true);
    assert.equal(isManifestUrlSafeToFetch("https://other.test/api/browser-playback/epochs/e1/index.m3u8"), false);
    assert.equal(isManifestUrlSafeToFetch("/api/native-playback/session/s1/stream?token=abc"), false);
  } finally {
    delete globalThis.window;
  }
});

test("time ranges are serialized without assuming browser-specific shape", () => {
  const serialized = serializeTimeRanges({
    length: 2,
    start: (index) => [0, 10][index],
    end: (index) => [4, 20][index],
  });

  assert.deepEqual(serialized, {
    length: 2,
    ranges: [
      { index: 0, start: 0, end: 4, duration: 4 },
      { index: 1, start: 10, end: 20, duration: 10 },
    ],
  });
});
