import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";

import {
  collectPlaybackDiagnosticCapabilities,
  unsupportedWebCapabilities,
} from "./capabilities.js";
import { HlsJsDiagnosticObserver } from "./hlsObserver.js";
import { PlaybackLifecycleDiagnosticObserver } from "./lifecycleObserver.js";
import {
  MediaElementDiagnosticObserver,
  readDiagnosticTimeRanges,
  readMediaDiagnosticSnapshot,
} from "./mediaObserver.js";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function ranges(values) {
  return {
    length: values.length,
    start: (index) => values[index][0],
    end: (index) => values[index][1],
  };
}

test("media snapshot reports ranges, holes, and URL identity without full source URL", () => {
  assert.deepEqual(readDiagnosticTimeRanges(ranges([[0, 2], [3, 5]])), [
    { start_ms: 0, end_ms: 2_000 },
    { start_ms: 3_000, end_ms: 5_000 },
  ]);
  const snapshot = readMediaDiagnosticSnapshot({
    currentTime: 1,
    duration: 10,
    paused: false,
    ended: false,
    seeking: false,
    playbackRate: 1,
    readyState: 4,
    networkState: 2,
    videoWidth: 1920,
    videoHeight: 1080,
    muted: false,
    volume: 0.8,
    buffered: ranges([[0, 2], [3, 5]]),
    seekable: ranges([[0, 10]]),
    played: ranges([[0, 1]]),
    currentSrc: "https://elvern.invalid/api/browser-playback/epochs/abcdef0123456789abcdef01/index.m3u8?token=secret",
  }, null, 100);

  assert.equal(snapshot.buffered_ahead_ms, 1_000);
  assert.equal(snapshot.buffer_hole_count, 1);
  assert.deepEqual(snapshot.buffer_hole_sizes_ms, [1_000]);
  assert.equal(JSON.stringify(snapshot).includes("token=secret"), false);
});

test("hls.js adapter maps events and strips fragment URLs", () => {
  const handlers = new Map();
  const hls = {
    on: vi.fn((name, handler) => handlers.set(name, handler)),
    off: vi.fn(),
  };
  const record = vi.fn();
  const observer = new HlsJsDiagnosticObserver({
    hls,
    events: { FRAG_LOADED: "fragLoaded", ERROR: "error" },
    record,
  });
  observer.start();
  handlers.get("fragLoaded")("fragLoaded", {
    frag: {
      sn: 7,
      start: 4,
      duration: 4,
      url: "https://provider.invalid/segment.m4s?resourcekey=secret",
      stats: { loaded: 1_024, loading: { start: 10, first: 12, end: 20 } },
    },
  });
  const payload = record.mock.calls[0][1].payload;
  assert.equal(payload.segment_index, 7);
  assert.equal(payload.bytes, 1_024);
  assert.equal(JSON.stringify(payload).includes("resourcekey"), false);
  observer.stop();
  assert.equal(hls.off.mock.calls.length, 2);
});

test("lifecycle hidden state is inferred and never labeled as a Home action", () => {
  const record = vi.fn();
  const recalibrateClock = vi.fn();
  const documentRef = new EventTarget();
  documentRef.visibilityState = "visible";
  documentRef.hasFocus = () => true;
  documentRef.wasDiscarded = false;
  const windowRef = new EventTarget();
  windowRef.screen = { orientation: new EventTarget() };
  windowRef.screen.orientation.type = "portrait-primary";
  windowRef.matchMedia = () => ({ matches: false });
  const observer = new PlaybackLifecycleDiagnosticObserver({
    record,
    recalibrateClock,
    windowRef,
    documentRef,
    navigatorRef: { onLine: true },
  });
  observer.start();
  documentRef.visibilityState = "hidden";
  documentRef.dispatchEvent(new Event("visibilitychange"));
  documentRef.visibilityState = "visible";
  documentRef.dispatchEvent(new Event("visibilitychange"));

  const inferred = record.mock.calls.find(([name]) => name === "background_suspension_suspected");
  assert.equal(inferred[1].observationKind, "inferred");
  assert.equal(JSON.stringify(record.mock.calls).toLowerCase().includes("home"), false);
  assert.equal(recalibrateClock.mock.calls.length, 1);
  observer.stop();
});

test("capability matrix reports unsupported Web-only measurements honestly", () => {
  const capabilities = collectPlaybackDiagnosticCapabilities({
    windowRef: { performance: {}, PerformanceObserver: undefined },
    documentRef: {},
    navigatorRef: {},
    video: {},
  });
  const unsupported = unsupportedWebCapabilities(capabilities);
  assert.equal(capabilities.native_hls_internal_cache, false);
  assert.equal(capabilities.client_fragment_loader_detail, false);
  assert.equal(unsupported.includes("safari_process_rss"), true);
  assert.equal(unsupported.includes("exact_browser_cpu_percent"), true);
});

test("media observer emits semantic actions and listens for PiP on the video element", () => {
  vi.useFakeTimers();
  const video = new EventTarget();
  Object.assign(video, {
    currentTime: 2,
    duration: 10,
    paused: false,
    ended: false,
    seeking: false,
    playbackRate: 1,
    readyState: 4,
    networkState: 2,
    videoWidth: 1_920,
    videoHeight: 1_080,
    muted: false,
    volume: 0.5,
    buffered: ranges([[0, 5]]),
    seekable: ranges([[0, 10]]),
    played: ranges([[0, 2]]),
    currentSrc: "/api/browser-playback/epochs/synthetic/index.m3u8",
  });
  const record = vi.fn();
  const actionOrigin = vi.fn(() => "user");
  const observer = new MediaElementDiagnosticObserver({
    video,
    record,
    actionOrigin,
    windowRef: window,
  });
  observer.start();

  video.dispatchEvent(new Event("play"));
  video.dispatchEvent(new Event("playing"));
  video.dispatchEvent(new Event("pause"));
  video.dispatchEvent(new Event("enterpictureinpicture"));

  const names = record.mock.calls.map(([name]) => name);
  assert.equal(names.includes("play_requested"), true);
  assert.equal(names.includes("play_started"), true);
  assert.equal(names.includes("pause_started"), true);
  assert.equal(names.includes("picture_in_picture_entered"), true);
  assert.equal(actionOrigin.mock.calls.length, 2);
  observer.stop();
});

test("incident pre-window is byte-bounded, chunked, and excludes the ring from stall_candidate", () => {
  vi.useFakeTimers();
  const video = new EventTarget();
  Object.assign(video, {
    currentTime: 2,
    duration: 10,
    paused: false,
    ended: false,
    seeking: false,
    playbackRate: 1,
    readyState: 2,
    networkState: 2,
    muted: false,
    volume: 1,
    buffered: ranges([[0, 2]]),
    seekable: ranges([[0, 10]]),
    played: ranges([[0, 2]]),
  });
  const record = vi.fn();
  const observer = new MediaElementDiagnosticObserver({ video, record, windowRef: window });
  const oversizedRanges = Array.from({ length: 100 }, (_, index) => ({
    start_ms: index * 1_000,
    end_ms: index * 1_000 + 500,
  }));
  for (let index = 0; index < 240; index += 1) {
    observer.sampleRing.push({
      sample_monotonic_ms: index * 250,
      current_time_ms: index * 250,
      buffered_ranges: oversizedRanges,
      seekable_ranges: oversizedRanges,
      played_ranges: oversizedRanges,
      buffer_hole_sizes_ms: Array.from({ length: 100 }, () => 500),
    });
  }

  observer.beginStallCandidate("waiting", readMediaDiagnosticSnapshot(video));

  const candidate = record.mock.calls.find(([name]) => name === "stall_candidate")[1];
  assert.equal("samples" in candidate.payload, false);
  const sampleChunks = record.mock.calls.filter(
    ([name]) => name === "client_incident_pre_samples",
  );
  assert.equal(sampleChunks.length > 1, true);
  sampleChunks.forEach(([, options]) => {
    assert.equal(options.payload.samples.length <= 64, true);
    assert.equal(new TextEncoder().encode(JSON.stringify(options.payload)).byteLength < 64_000, true);
    assert.equal(options.payload.samples[0].buffered_ranges.length, 64);
    assert.equal(options.payload.samples[0].buffered_range_count, 100);
    assert.equal(options.payload.samples[0].ranges_truncated, true);
  });
});

test("incident recovery is recorded once while post-recovery sampling continues", () => {
  vi.useFakeTimers();
  const video = new EventTarget();
  Object.assign(video, {
    currentTime: 2,
    duration: 10,
    paused: false,
    ended: false,
    seeking: false,
    playbackRate: 1,
    readyState: 4,
    networkState: 2,
    muted: false,
    volume: 1,
    buffered: ranges([[0, 8]]),
    seekable: ranges([[0, 10]]),
    played: ranges([[0, 2]]),
  });
  const record = vi.fn();
  const observer = new MediaElementDiagnosticObserver({ video, record, windowRef: window });

  observer.beginStallCandidate("waiting", readMediaDiagnosticSnapshot(video));
  video.currentTime = 2.25;
  vi.advanceTimersByTime(250);
  observer.sample();

  for (let index = 0; index < 4; index += 1) {
    video.currentTime += 0.25;
    vi.advanceTimersByTime(250);
    observer.sample();
  }

  const eventNames = record.mock.calls.map(([name]) => name);
  assert.equal(eventNames.filter((name) => name === "playhead_progress_resumed").length, 1);
  assert.equal(eventNames.filter((name) => name === "stall_ended").length, 1);
  assert.equal(eventNames.filter((name) => name === "client_incident_sample").length, 5);
  assert.equal(observer.postRecoveryTimers.size, 1);

  vi.advanceTimersByTime(120_000);
  assert.equal(
    record.mock.calls.filter(([name]) => name === "post_recovery_observation").length,
    1,
  );
  assert.equal(observer.activeIncident, null);
});
