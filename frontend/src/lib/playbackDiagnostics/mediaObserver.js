import {
  PLAYBACK_DIAGNOSTICS_AGGREGATE_MS,
  PLAYBACK_DIAGNOSTICS_CLIENT_SAMPLE_MS,
  PLAYBACK_DIAGNOSTICS_INCIDENT_CHUNK_TARGET_BYTES,
  PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS,
  PLAYBACK_DIAGNOSTICS_INCIDENT_FRAME_CHUNK,
  PLAYBACK_DIAGNOSTICS_INCIDENT_MAX_RANGES,
  PLAYBACK_DIAGNOSTICS_INCIDENT_PRE_SECONDS,
  PLAYBACK_DIAGNOSTICS_INCIDENT_SAMPLE_CHUNK,
  PLAYBACK_DIAGNOSTICS_MEDIA_EVENTS,
} from "./constants";
import { diagnosticUrlIdentity } from "./privacy";
import { DiagnosticRingBuffer } from "./ringBuffer";
import { createDiagnosticId } from "./schema";

const ACTION_ORIGIN_MEDIA_EVENTS = new Set([
  "play",
  "pause",
  "seeking",
  "seeked",
  "ratechange",
  "volumechange",
]);

const MEDIA_SEMANTIC_EVENTS = Object.freeze({
  play: "play_requested",
  pause: "pause_started",
  seeking: "seek_started",
  seeked: "seek_completed",
  ratechange: "playback_rate_changed",
});

const PICTURE_IN_PICTURE_EVENTS = Object.freeze({
  enterpictureinpicture: "picture_in_picture_entered",
  leavepictureinpicture: "picture_in_picture_exited",
});

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function readDiagnosticTimeRanges(ranges) {
  const result = [];
  if (!ranges) return result;
  for (let index = 0; index < ranges.length; index += 1) {
    try {
      result.push({
        start_ms: Math.max(0, ranges.start(index) * 1_000),
        end_ms: Math.max(0, ranges.end(index) * 1_000),
      });
    } catch {
      break;
    }
  }
  return result;
}

function summarizeRanges(ranges, playheadMs) {
  let contiguousAheadMs = 0;
  let behindMs = 0;
  let totalMs = 0;
  let holes = 0;
  const holeSizesMs = [];
  ranges.forEach((range, index) => {
    totalMs += Math.max(0, range.end_ms - range.start_ms);
    if (playheadMs >= range.start_ms - 50 && playheadMs <= range.end_ms + 50) {
      contiguousAheadMs = Math.max(0, range.end_ms - playheadMs);
      behindMs = Math.max(0, playheadMs - range.start_ms);
    }
    if (index > 0) {
      const gap = Math.max(0, range.start_ms - ranges[index - 1].end_ms);
      if (gap > 0) {
        holes += 1;
        holeSizesMs.push(gap);
      }
    }
  });
  return {
    contiguous_buffered_ahead_ms: contiguousAheadMs,
    buffered_ahead_ms: contiguousAheadMs,
    buffered_behind_ms: behindMs,
    total_buffered_ms: totalMs,
    buffer_hole_count: holes,
    buffer_hole_sizes_ms: holeSizesMs,
  };
}

export function readMediaDiagnosticSnapshot(video, previous = null, nowMs = performance.now()) {
  const currentTimeMs = Math.max(0, (finite(video?.currentTime) || 0) * 1_000);
  const durationSeconds = finite(video?.duration);
  const bufferedRanges = readDiagnosticTimeRanges(video?.buffered);
  const elapsedMs = previous ? Math.max(0, nowMs - previous.sample_monotonic_ms) : 0;
  const playheadDeltaMs = previous ? currentTimeMs - previous.current_time_ms : 0;
  const buffer = summarizeRanges(bufferedRanges, currentTimeMs);
  const bufferSlope = previous && elapsedMs > 0
    ? (buffer.buffered_ahead_ms - previous.buffered_ahead_ms) / elapsedMs
    : null;
  const playheadSlope = previous && elapsedMs > 0 ? playheadDeltaMs / elapsedMs : null;
  return {
    sample_monotonic_ms: nowMs,
    current_time_ms: currentTimeMs,
    duration_ms: durationSeconds != null && durationSeconds > 0 ? durationSeconds * 1_000 : null,
    paused: Boolean(video?.paused),
    ended: Boolean(video?.ended),
    seeking: Boolean(video?.seeking),
    playback_rate: finite(video?.playbackRate),
    ready_state: finite(video?.readyState),
    network_state: finite(video?.networkState),
    width: finite(video?.videoWidth),
    height: finite(video?.videoHeight),
    muted: Boolean(video?.muted),
    volume: finite(video?.volume),
    buffered_ranges: bufferedRanges,
    seekable_ranges: readDiagnosticTimeRanges(video?.seekable),
    played_ranges: readDiagnosticTimeRanges(video?.played),
    ...buffer,
    buffer_slope: bufferSlope,
    playhead_advancement_rate: playheadSlope,
    ...diagnosticUrlIdentity(video?.currentSrc || video?.src || ""),
  };
}

function aggregateSamples(samples) {
  if (!samples.length) return {};
  const latest = samples.at(-1);
  const numbers = (key) => samples
    .map((sample) => finite(sample[key]))
    .filter((value) => value != null);
  const average = (values) => (
    values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
  );
  const bufferSamples = numbers("buffered_ahead_ms");
  return {
    ...latest,
    sample_count: samples.length,
    sample_window_ms: Math.max(0, latest.sample_monotonic_ms - samples[0].sample_monotonic_ms),
    buffer_slope: average(numbers("buffer_slope")),
    playhead_advancement_rate: average(numbers("playhead_advancement_rate")),
    minimum_buffer_ms: bufferSamples.length ? Math.min(...bufferSamples) : null,
  };
}

function encodedBytes(value) {
  const encoded = JSON.stringify(value);
  if (typeof TextEncoder === "function") return new TextEncoder().encode(encoded).byteLength;
  return encoded.length * 2;
}

function chunks(values, size, maxBytes) {
  const result = [];
  let current = [];
  let currentBytes = 0;
  values.forEach((value) => {
    const valueBytes = encodedBytes(value);
    if (current.length && (current.length >= size || currentBytes + valueBytes > maxBytes)) {
      result.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push(value);
    currentBytes += valueBytes;
  });
  if (current.length) {
    result.push(current);
  }
  return result;
}

function boundedIncidentSample(sample) {
  const result = { ...sample };
  let truncated = false;
  ["buffered_ranges", "seekable_ranges", "played_ranges"].forEach((field) => {
    if (!Array.isArray(sample?.[field])) return;
    result[field.replace("ranges", "range_count")] = sample[field].length;
    if (sample[field].length > PLAYBACK_DIAGNOSTICS_INCIDENT_MAX_RANGES) {
      result[field] = sample[field].slice(0, PLAYBACK_DIAGNOSTICS_INCIDENT_MAX_RANGES);
      truncated = true;
    }
  });
  if (Array.isArray(sample?.buffer_hole_sizes_ms)
      && sample.buffer_hole_sizes_ms.length > PLAYBACK_DIAGNOSTICS_INCIDENT_MAX_RANGES) {
    result.buffer_hole_sizes_ms = sample.buffer_hole_sizes_ms.slice(
      0,
      PLAYBACK_DIAGNOSTICS_INCIDENT_MAX_RANGES,
    );
    truncated = true;
  }
  result.ranges_truncated = truncated;
  return result;
}

export class MediaElementDiagnosticObserver {
  constructor({ video, record, actionOrigin = () => "unknown", windowRef = globalThis.window }) {
    this.video = video;
    this.record = record;
    this.actionOrigin = actionOrigin;
    this.windowRef = windowRef;
    const sampleRingEntries = Math.ceil(
      PLAYBACK_DIAGNOSTICS_INCIDENT_PRE_SECONDS * 1_000
      / PLAYBACK_DIAGNOSTICS_CLIENT_SAMPLE_MS,
    );
    const frameRingEntries = PLAYBACK_DIAGNOSTICS_INCIDENT_PRE_SECONDS * 120;
    this.sampleRing = new DiagnosticRingBuffer(sampleRingEntries);
    this.frameRing = new DiagnosticRingBuffer(frameRingEntries);
    this.aggregateSamples = [];
    this.frameAggregateSamples = [];
    this.previousSample = null;
    this.sampleTimer = null;
    this.aggregateTimer = null;
    this.frameRequest = null;
    this.activeIncident = null;
    this.stallTimer = null;
    this.postRecoveryTimers = new Set();
    this.firstFrameSeen = false;
    this.hasStartedPlayback = false;
    this.handlers = new Map();
  }

  start() {
    PLAYBACK_DIAGNOSTICS_MEDIA_EVENTS.forEach((eventName) => {
      const handler = (event) => this.handleMediaEvent(eventName, event);
      this.handlers.set(eventName, handler);
      this.video.addEventListener(eventName, handler);
    });
    Object.keys(PICTURE_IN_PICTURE_EVENTS).forEach((eventName) => {
      const handler = (event) => this.handleMediaEvent(eventName, event);
      this.handlers.set(eventName, handler);
      this.video.addEventListener(eventName, handler);
    });
    this.sampleTimer = this.windowRef.setInterval(
      () => this.sample(),
      PLAYBACK_DIAGNOSTICS_CLIENT_SAMPLE_MS,
    );
    this.aggregateTimer = this.windowRef.setInterval(
      () => this.flushAggregate(),
      PLAYBACK_DIAGNOSTICS_AGGREGATE_MS,
    );
    this.startFrameLoop();
    this.sample();
  }

  stop() {
    this.handlers.forEach((handler, eventName) => {
      this.video.removeEventListener(eventName, handler);
    });
    this.handlers.clear();
    if (this.sampleTimer != null) this.windowRef.clearInterval(this.sampleTimer);
    if (this.aggregateTimer != null) this.windowRef.clearInterval(this.aggregateTimer);
    if (this.stallTimer != null) this.windowRef.clearTimeout(this.stallTimer);
    this.postRecoveryTimers.forEach((timer) => this.windowRef.clearTimeout(timer));
    this.postRecoveryTimers.clear();
    if (this.frameRequest != null && typeof this.video.cancelVideoFrameCallback === "function") {
      this.video.cancelVideoFrameCallback(this.frameRequest);
    }
    this.sampleTimer = null;
    this.aggregateTimer = null;
    this.stallTimer = null;
    this.frameRequest = null;
    this.flushAggregate();
  }

  sample() {
    const snapshot = readMediaDiagnosticSnapshot(this.video, this.previousSample);
    this.previousSample = snapshot;
    this.sampleRing.push(snapshot);
    this.aggregateSamples.push(snapshot);
    if (this.activeIncident && performance.now() <= this.activeIncident.post_until_ms) {
      this.record("client_incident_sample", {
        priority: "high",
        incidentId: this.activeIncident.incident_id,
        payload: snapshot,
      });
    }
    if (this.activeIncident) this.maybeEndStall("sample", snapshot);
  }

  flushAggregate() {
    if (!this.aggregateSamples.length) return;
    const samples = this.aggregateSamples.splice(0);
    this.record("media_aggregate", {
      payload: aggregateSamples(samples),
      sampleWindowMs: PLAYBACK_DIAGNOSTICS_AGGREGATE_MS,
    });
    this.flushFrameAggregate();
  }

  handleMediaEvent(eventName, event) {
    const snapshot = readMediaDiagnosticSnapshot(this.video, this.previousSample);
    const actionOrigin = ACTION_ORIGIN_MEDIA_EVENTS.has(eventName)
      ? this.actionOrigin(eventName, event)
      : "browser";
    const payload = {
      ...snapshot,
      action_origin: actionOrigin,
    };
    if (eventName === "error") {
      payload.error_code = finite(this.video?.error?.code);
      payload.error_class = this.video?.error?.name || "MediaError";
    }
    this.record(`media_${eventName}`, {
      priority: ["error", "stalled", "waiting", "ended"].includes(eventName) ? "high" : "normal",
      severity: eventName === "error" ? "error" : "info",
      payload,
      playheadMs: snapshot.current_time_ms,
      mediaElementTimeMs: snapshot.current_time_ms,
      durationMs: snapshot.duration_ms,
    });
    const semanticEvent = MEDIA_SEMANTIC_EVENTS[eventName];
    if (semanticEvent) {
      this.record(semanticEvent, {
        priority: "high",
        payload,
        playheadMs: snapshot.current_time_ms,
        mediaElementTimeMs: snapshot.current_time_ms,
        durationMs: snapshot.duration_ms,
      });
    }
    if (eventName === "volumechange") {
      this.record("volume_changed", { payload });
      this.record(snapshot.muted ? "muted" : "unmuted", { payload });
    }
    if (eventName === "playing") {
      this.record(this.hasStartedPlayback ? "resume_started" : "play_started", {
        priority: "high",
        payload,
        playheadMs: snapshot.current_time_ms,
        mediaElementTimeMs: snapshot.current_time_ms,
        durationMs: snapshot.duration_ms,
      });
      this.hasStartedPlayback = true;
    }
    if (PICTURE_IN_PICTURE_EVENTS[eventName]) {
      this.record(PICTURE_IN_PICTURE_EVENTS[eventName], {
        priority: "high",
        payload: {
          active: eventName === "enterpictureinpicture",
          action_origin: "browser",
        },
      });
    }
    if (["waiting", "stalled"].includes(eventName)) this.beginStallCandidate(eventName, snapshot);
    if (["playing", "timeupdate", "ended"].includes(eventName)) this.maybeEndStall(eventName, snapshot);
    if (eventName === "playing" && !this.firstFrameSeen) {
      this.firstFrameSeen = true;
      this.record("first_frame", { priority: "high", payload: snapshot });
    }
    if (eventName === "ended") {
      this.record("completed", { priority: "critical", payload: { action_origin: "browser" } });
    }
  }

  beginStallCandidate(reason, snapshot) {
    if (this.activeIncident) return;
    const incidentId = createDiagnosticId("incident");
    this.activeIncident = {
      incident_id: incidentId,
      started_at_ms: performance.now(),
      post_until_ms: Number.POSITIVE_INFINITY,
      reason,
      recovered: false,
    };
    this.record("stall_candidate", {
      incidentId,
      priority: "high",
      observationKind: "measured_client",
      payload: {
        candidate_time_ns: String(Math.round(Date.now() * 1_000_000)),
        stall_reason: reason,
        ...snapshot,
        ring_complete: this.sampleRing.complete,
        frame_ring_complete: this.frameRing.complete,
      },
    });
    this.persistIncidentPreWindow(incidentId);
    this.stallTimer = this.windowRef.setTimeout(() => {
      if (!this.activeIncident || this.activeIncident.incident_id !== incidentId) return;
      this.record("stall_confirmed", {
        incidentId,
        priority: "critical",
        payload: { stall_reason: reason, ...readMediaDiagnosticSnapshot(this.video, this.previousSample) },
      });
      this.record("recovery_started", {
        incidentId,
        priority: "high",
        observationKind: "inferred",
        payload: { recovery_action: "awaiting_existing_playback_recovery" },
      });
    }, 500);
  }

  persistIncidentPreWindow(incidentId) {
    const sampleChunks = chunks(
      this.sampleRing.snapshot().map(boundedIncidentSample),
      PLAYBACK_DIAGNOSTICS_INCIDENT_SAMPLE_CHUNK,
      PLAYBACK_DIAGNOSTICS_INCIDENT_CHUNK_TARGET_BYTES,
    );
    sampleChunks.forEach((samples, index) => {
      this.record("client_incident_pre_samples", {
        incidentId,
        priority: "high",
        payload: {
          samples,
          current: index + 1,
          count: sampleChunks.length,
          ring_complete: this.sampleRing.complete,
        },
      });
    });
    const frameChunks = chunks(
      this.frameRing.snapshot(),
      PLAYBACK_DIAGNOSTICS_INCIDENT_FRAME_CHUNK,
      PLAYBACK_DIAGNOSTICS_INCIDENT_CHUNK_TARGET_BYTES,
    );
    frameChunks.forEach((frameSamples, index) => {
      this.record("client_incident_pre_frames", {
        incidentId,
        priority: "high",
        payload: {
          frame_samples: frameSamples,
          current: index + 1,
          count: frameChunks.length,
          frame_ring_complete: this.frameRing.complete,
        },
      });
    });
  }

  maybeEndStall(reason, snapshot) {
    if (!this.activeIncident || this.activeIncident.recovered) return;
    const incident = this.activeIncident;
    if (reason !== "ended" && (this.video.paused || snapshot.playhead_advancement_rate === 0)) return;
    incident.recovered = true;
    if (this.stallTimer != null) this.windowRef.clearTimeout(this.stallTimer);
    this.stallTimer = null;
    const durationMs = Math.max(0, performance.now() - incident.started_at_ms);
    this.record("playhead_progress_resumed", {
      incidentId: incident.incident_id,
      priority: "high",
      payload: { actual_duration_ms: durationMs, stall_reason: incident.reason },
    });
    this.record("stall_ended", {
      incidentId: incident.incident_id,
      priority: "critical",
      payload: { stall_duration_ms: durationMs, reason },
    });
    incident.post_until_ms = performance.now() + PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS * 1_000;
    const postRecoveryTimer = this.windowRef.setTimeout(() => {
      this.postRecoveryTimers.delete(postRecoveryTimer);
      if (this.activeIncident?.incident_id !== incident.incident_id) return;
      this.record("post_recovery_observation", {
        incidentId: incident.incident_id,
        payload: { actual_duration_ms: PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS * 1_000 },
      });
      this.activeIncident = null;
    }, PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS * 1_000);
    this.postRecoveryTimers.add(postRecoveryTimer);
  }

  startFrameLoop() {
    if (typeof this.video.requestVideoFrameCallback !== "function") return;
    let previous = null;
    const callback = (now, metadata) => {
      const sample = {
        presentation_time_ms: finite(metadata?.presentationTime),
        expected_display_time_ms: finite(metadata?.expectedDisplayTime),
        media_element_time_ms: finite(metadata?.mediaTime) != null ? metadata.mediaTime * 1_000 : null,
        presented_frames: finite(metadata?.presentedFrames),
        decoder_delay_ms: finite(metadata?.processingDuration) != null
          ? metadata.processingDuration * 1_000
          : null,
        callback_lateness_ms: finite(metadata?.expectedDisplayTime) != null
          ? Math.max(0, now - metadata.expectedDisplayTime)
          : null,
        frame_cadence_ms: previous != null ? Math.max(0, now - previous) : null,
      };
      previous = now;
      this.frameRing.push(sample);
      this.frameAggregateSamples.push(sample);
      if (!this.firstFrameSeen) {
        this.firstFrameSeen = true;
        this.record("first_frame", { priority: "high", payload: sample });
      }
      this.frameRequest = this.video.requestVideoFrameCallback(callback);
    };
    this.frameRequest = this.video.requestVideoFrameCallback(callback);
  }

  flushFrameAggregate() {
    const frames = this.frameAggregateSamples.splice(0);
    if (!frames.length) return;
    const quality = typeof this.video.getVideoPlaybackQuality === "function"
      ? this.video.getVideoPlaybackQuality()
      : null;
    const latest = frames.at(-1);
    const cadence = frames.map((frame) => finite(frame.frame_cadence_ms)).filter((value) => value != null);
    const dropped = finite(quality?.droppedVideoFrames);
    const total = finite(quality?.totalVideoFrames);
    this.record("frame_aggregate", {
      payload: {
        ...latest,
        sample_count: frames.length,
        frame_cadence_ms: cadence.length
          ? cadence.reduce((sum, value) => sum + value, 0) / cadence.length
          : null,
        total_frames: total,
        dropped_frames: dropped,
        corrupted_frames: finite(quality?.corruptedVideoFrames),
        dropped_frame_ratio: total > 0 && dropped != null ? dropped / total : null,
      },
      sampleWindowMs: PLAYBACK_DIAGNOSTICS_AGGREGATE_MS,
    });
  }
}
