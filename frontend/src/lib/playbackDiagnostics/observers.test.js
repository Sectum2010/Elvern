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
import { PlaybackPerformanceDiagnosticObserver } from "./performanceObserver.js";
import { DiagnosticRingBuffer } from "./ringBuffer.js";

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

test("hls request attempt identity remains stable through completion and changes on retry", () => {
  const handlers = new Map();
  const hls = {
    on: vi.fn((name, handler) => handlers.set(name, handler)),
    off: vi.fn(),
  };
  const record = vi.fn();
  const observer = new HlsJsDiagnosticObserver({
    hls,
    events: { FRAG_LOADING: "fragLoading", FRAG_LOADED: "fragLoaded" },
    record,
  });
  observer.start();
  const fragment = {
    sn: 9,
    level: 1,
    cc: 0,
    url: "/api/browser-playback/epochs/abcdef0123456789abcdef01/segments/9.m4s",
  };

  handlers.get("fragLoading")("fragLoading", { frag: fragment });
  handlers.get("fragLoaded")("fragLoaded", { frag: fragment });
  handlers.get("fragLoading")("fragLoading", { frag: fragment });
  handlers.get("fragLoaded")("fragLoaded", { frag: fragment });

  const attempts = record.mock.calls.map(([, options]) => options.payload);
  assert.equal(attempts[0].request_attempt, 1);
  assert.equal(attempts[1].request_attempt_id, attempts[0].request_attempt_id);
  assert.equal(attempts[2].request_attempt, 2);
  assert.notEqual(attempts[2].request_attempt_id, attempts[0].request_attempt_id);
  assert.equal(attempts[3].request_attempt_id, attempts[2].request_attempt_id);
});

test("ring buffer is bounded, time-windowed, and preserves an incremental snapshot", () => {
  const ring = new DiagnosticRingBuffer(8, { windowMs: 1_000 });
  [0, 100, 800, 1_100].forEach((sample_monotonic_ms) => {
    ring.push({ sample_monotonic_ms });
  });
  assert.deepEqual(
    ring.snapshot().map((sample) => sample.sample_monotonic_ms),
    [100, 800, 1_100],
  );
  assert.equal(ring.complete, true);

  const cursor = ring.createSnapshotCursor();
  for (let index = 0; index < 12; index += 1) {
    ring.push({ sample_monotonic_ms: 1_200 + index * 100 });
  }
  const preserved = [];
  while (!cursor.done) preserved.push(...cursor.read(1));
  assert.deepEqual(
    preserved.map((sample) => sample.sample_monotonic_ms),
    [100, 800, 1_100],
  );
  assert.equal(ring.length <= 8, true);
});

test("lifecycle hidden time is measured without claiming suspension or a Home action", () => {
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

  const hiddenStart = record.mock.calls.find(([name]) => name === "page_hidden_started");
  const hiddenEnd = record.mock.calls.find(([name]) => name === "page_hidden_ended");
  assert.equal(hiddenStart[1].payload.page_state, "hidden");
  assert.equal(hiddenEnd[1].payload.page_state, "visible");
  assert.equal(Number.isFinite(hiddenEnd[1].payload.hidden_duration_ms), true);
  assert.equal(
    record.mock.calls.some(([name]) => name === "background_suspension_suspected"),
    false,
  );
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
  assert.equal(capabilities.native_hls_internal_cache, "api_absent");
  assert.equal(capabilities.client_fragment_loader_detail, "not_applicable");
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
  assert.equal(names.includes("first_video_frame_inferred"), true);
  assert.equal(names.includes("first_video_frame_presented"), false);
  assert.equal(names.includes("pause_started"), true);
  assert.equal(names.includes("picture_in_picture_entered"), true);
  assert.equal(actionOrigin.mock.calls.length, 2);
  observer.stop();
});

test("RVFC is authoritative for measured first frame and playing remains only a semantic start", () => {
  let frameCallback = null;
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
    buffered: ranges([[0, 5]]),
    seekable: ranges([[0, 10]]),
    played: ranges([[0, 2]]),
    requestVideoFrameCallback: (callback) => {
      frameCallback = callback;
      return 1;
    },
    cancelVideoFrameCallback: vi.fn(),
  });
  const record = vi.fn();
  const observer = new MediaElementDiagnosticObserver({ video, record, windowRef: window });
  observer.startFrameLoop();
  observer.handleMediaEvent("playing", new Event("playing"));

  assert.equal(
    record.mock.calls.some(([name]) => name === "first_video_frame_inferred"),
    false,
  );
  assert.equal(
    record.mock.calls.some(([name]) => name === "first_video_frame_presented"),
    false,
  );

  frameCallback(125, {
    presentationTime: 124,
    expectedDisplayTime: 125,
    mediaTime: 2,
    presentedFrames: 1,
    processingDuration: 0.002,
  });
  const firstFrame = record.mock.calls.find(
    ([name]) => name === "first_video_frame_presented",
  );
  assert.equal(firstFrame[1].observationKind, "measured_client");
  assert.equal(firstFrame[1].measurementMethod, "request_video_frame_callback");
  observer.stop();
});

test("startup, seeking, paused, and hidden waiting never become confirmed stalls", () => {
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
  const documentRef = { visibilityState: "visible" };
  const observer = new MediaElementDiagnosticObserver({
    video,
    record,
    windowRef: window,
    documentRef,
  });

  observer.beginStallCandidate("waiting", readMediaDiagnosticSnapshot(video));
  observer.firstFrameSeen = true;
  video.seeking = true;
  observer.beginStallCandidate("waiting", readMediaDiagnosticSnapshot(video));
  video.seeking = false;
  video.paused = true;
  observer.beginStallCandidate("waiting", readMediaDiagnosticSnapshot(video));
  video.paused = false;
  documentRef.visibilityState = "hidden";
  observer.beginStallCandidate("waiting", readMediaDiagnosticSnapshot(video));

  vi.advanceTimersByTime(1_000);
  const names = record.mock.calls.map(([name]) => name);
  assert.equal(names.filter((name) => name === "stall_candidate_ignored").length, 4);
  assert.equal(names.includes("stall_confirmed"), false);
  observer.stop();
});

test("incident pre-window is byte-bounded, chunked, and excludes the ring from stall_candidate", async () => {
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
  observer.firstFrameSeen = true;
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
  await vi.advanceTimersByTimeAsync(0);
  while (observer.snapshotJobs.size) {
    await vi.advanceTimersByTimeAsync(1);
  }

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
  observer.firstFrameSeen = true;

  observer.beginStallCandidate("waiting", readMediaDiagnosticSnapshot(video));
  vi.advanceTimersByTime(500);
  video.readyState = 4;
  video.buffered = ranges([[0, 8]]);
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
  assert.equal(eventNames.includes("recovery_started"), false);
  observer.flushAggregate();
  assert.equal(eventNames.filter((name) => name === "client_incident_post_aggregate").length, 0);
  assert.equal(
    record.mock.calls.filter(([name]) => name === "client_incident_post_aggregate").length,
    1,
  );
  assert.equal(observer.postIncidentTimers.size, 1);

  vi.advanceTimersByTime(120_000);
  assert.equal(
    record.mock.calls.filter(([name]) => name === "post_recovery_observation").length,
    1,
  );
  assert.equal(observer.activeCandidate, null);
});

test("a new stall is not swallowed while an earlier incident post-window is active", () => {
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
  observer.firstFrameSeen = true;

  observer.beginStallCandidate("waiting", readMediaDiagnosticSnapshot(video));
  vi.advanceTimersByTime(500);
  video.currentTime = 2.25;
  video.readyState = 4;
  video.buffered = ranges([[0, 8]]);
  observer.sample();
  const firstIncidentId = observer.lastConfirmedIncident.incident_id;
  assert.equal(observer.postIncidentTimers.size, 1);

  video.readyState = 2;
  video.buffered = ranges([[0, video.currentTime]]);
  observer.beginStallCandidate("stalled", readMediaDiagnosticSnapshot(video));
  assert.equal(observer.activeCandidate.previous_incident_id, firstIncidentId);
  assert.notEqual(observer.activeCandidate.incident_id, firstIncidentId);
  vi.advanceTimersByTime(500);
  assert.equal(
    record.mock.calls.filter(([name]) => name === "stall_confirmed").length,
    2,
  );
});

test("frame aggregate reports interval deltas and detects cumulative counter reset", () => {
  let quality = { droppedVideoFrames: 10, totalVideoFrames: 100, corruptedVideoFrames: 0 };
  const video = {
    getVideoPlaybackQuality: () => quality,
  };
  const record = vi.fn();
  const observer = new MediaElementDiagnosticObserver({ video, record, windowRef: window });
  observer.frameAggregateSamples = [{ frame_cadence_ms: 16 }];
  observer.flushFrameAggregate();

  quality = { droppedVideoFrames: 11, totalVideoFrames: 200, corruptedVideoFrames: 0 };
  observer.frameAggregateSamples = [{ frame_cadence_ms: 17 }];
  observer.flushFrameAggregate();
  const interval = record.mock.calls.at(-1)[1].payload;
  assert.equal(interval.delta_dropped_frames, 1);
  assert.equal(interval.delta_total_frames, 100);
  assert.equal(interval.window_dropped_frame_ratio, 0.01);

  quality = { droppedVideoFrames: 1, totalVideoFrames: 5, corruptedVideoFrames: 0 };
  observer.frameAggregateSamples = [{ frame_cadence_ms: 18 }];
  observer.flushFrameAggregate();
  const reset = record.mock.calls.at(-1)[1].payload;
  assert.equal(reset.frame_counter_reset, true);
  assert.equal(reset.window_dropped_frame_ratio, null);
});

test("performance observer ignores pre-session buffered entries and keeps same-time resources distinct", () => {
  let callback;
  class FakePerformanceObserver {
    static supportedEntryTypes = ["resource"];

    constructor(nextCallback) {
      callback = nextCallback;
    }

    observe() {}

    disconnect() {}
  }
  const record = vi.fn();
  const observer = new PlaybackPerformanceDiagnosticObserver({
    record,
    windowRef: {
      PerformanceObserver: FakePerformanceObserver,
      performance: { now: () => 100 },
      setInterval: () => 1,
      clearInterval: () => {},
    },
    documentRef: { visibilityState: "visible" },
    navigatorRef: {},
  });
  observer.start();
  callback({
    getEntries: () => [
      { name: "/api/browser-playback/epochs/old/index.m3u8", startTime: 99, duration: 1 },
      {
        name: "/api/browser-playback/epochs/abcdef0123456789abcdef01/segments/1.m4s",
        startTime: 101,
        duration: 5,
        transferSize: 100,
        encodedBodySize: 90,
      },
      {
        name: "/api/browser-playback/epochs/abcdef0123456789abcdef01/segments/1.m4s",
        startTime: 101,
        duration: 7,
        transferSize: 120,
        encodedBodySize: 110,
      },
    ],
  });

  const resourceEvents = record.mock.calls.filter(([name]) => name === "client_resource_timing");
  assert.equal(resourceEvents.length, 2);
  assert.deepEqual(
    resourceEvents.map(([, options]) => options.payload.transfer_bytes),
    [100, 120],
  );
  observer.stop();
});
