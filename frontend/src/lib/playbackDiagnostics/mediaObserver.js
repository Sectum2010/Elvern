import {
  PLAYBACK_DIAGNOSTICS_AGGREGATE_MS,
  PLAYBACK_DIAGNOSTICS_CLIENT_SAMPLE_MS,
  PLAYBACK_DIAGNOSTICS_INCIDENT_CHUNK_TARGET_BYTES,
  PLAYBACK_DIAGNOSTICS_INCIDENT_FRAME_CHUNK,
  PLAYBACK_DIAGNOSTICS_INCIDENT_MAX_RANGES,
  PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS,
  PLAYBACK_DIAGNOSTICS_INCIDENT_PRE_SECONDS,
  PLAYBACK_DIAGNOSTICS_INCIDENT_SAMPLE_CHUNK,
  PLAYBACK_DIAGNOSTICS_INCIDENT_TASK_BUDGET_MS,
  PLAYBACK_DIAGNOSTICS_MEDIA_EVENTS,
  PLAYBACK_DIAGNOSTICS_STALL_CONFIRM_MS,
} from "./constants";
import { diagnosticUrlIdentity } from "./privacy";
import { DiagnosticRingBuffer } from "./ringBuffer";
import { createDiagnosticId } from "./schema";

const ACTION_ORIGIN_MEDIA_EVENTS = new Set([
  "play", "pause", "seeking", "seeked", "ratechange", "volumechange",
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
const FRAME_RING_MAX_ENTRIES = PLAYBACK_DIAGNOSTICS_INCIDENT_PRE_SECONDS * 240;
const SAMPLE_RING_MAX_ENTRIES = Math.ceil(
  PLAYBACK_DIAGNOSTICS_INCIDENT_PRE_SECONDS * 1_000
  / PLAYBACK_DIAGNOSTICS_CLIENT_SAMPLE_MS,
) + 8;

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function monotonicNow(windowRef = globalThis.window) {
  return windowRef?.performance?.now?.() ?? globalThis.performance?.now?.() ?? Date.now();
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
    buffer_slope: previous && elapsedMs > 0
      ? (buffer.buffered_ahead_ms - previous.buffered_ahead_ms) / elapsedMs
      : null,
    playhead_advancement_rate: previous && elapsedMs > 0 ? playheadDeltaMs / elapsedMs : null,
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
  if (
    Array.isArray(sample?.buffer_hole_sizes_ms)
    && sample.buffer_hole_sizes_ms.length > PLAYBACK_DIAGNOSTICS_INCIDENT_MAX_RANGES
  ) {
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
  constructor({
    video,
    record,
    actionOrigin = () => "unknown",
    windowRef = globalThis.window,
    documentRef = globalThis.document,
  }) {
    this.video = video;
    this.record = record;
    this.actionOrigin = actionOrigin;
    this.windowRef = windowRef;
    this.documentRef = documentRef;
    const windowMs = PLAYBACK_DIAGNOSTICS_INCIDENT_PRE_SECONDS * 1_000;
    this.sampleRing = new DiagnosticRingBuffer(SAMPLE_RING_MAX_ENTRIES, { windowMs });
    this.frameRing = new DiagnosticRingBuffer(FRAME_RING_MAX_ENTRIES, {
      windowMs,
      timestamp: (sample) => finite(sample?.callback_monotonic_ms),
    });
    this.aggregateSamples = [];
    this.frameAggregateSamples = [];
    this.previousSample = null;
    this.previousFrameQuality = null;
    this.sampleTimer = null;
    this.aggregateTimer = null;
    this.frameRequest = null;
    this.activeCandidate = null;
    this.stallTimer = null;
    this.postIncidentTimers = new Map();
    this.lastConfirmedIncident = null;
    this.snapshotJobs = new Set();
    this.firstFrameSeen = false;
    this.firstFrameFallbackSeen = false;
    this.frameCallbackSupported = typeof video?.requestVideoFrameCallback === "function";
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
    this.handlers.forEach((handler, eventName) => this.video.removeEventListener(eventName, handler));
    this.handlers.clear();
    if (this.sampleTimer != null) this.windowRef.clearInterval(this.sampleTimer);
    if (this.aggregateTimer != null) this.windowRef.clearInterval(this.aggregateTimer);
    if (this.stallTimer != null) this.windowRef.clearTimeout(this.stallTimer);
    this.postIncidentTimers.forEach((timer) => this.windowRef.clearTimeout(timer));
    this.postIncidentTimers.clear();
    this.snapshotJobs.forEach((job) => job.cancel());
    this.snapshotJobs.clear();
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
    const snapshot = readMediaDiagnosticSnapshot(
      this.video,
      this.previousSample,
      monotonicNow(this.windowRef),
    );
    this.previousSample = snapshot;
    this.sampleRing.push(snapshot);
    this.aggregateSamples.push(snapshot);
    if (this.activeCandidate) this.maybeRecoverCandidate("sample", snapshot);
  }

  flushAggregate() {
    if (this.aggregateSamples.length) {
      const samples = this.aggregateSamples.splice(0);
      const aggregate = aggregateSamples(samples);
      this.record("media_aggregate", {
        payload: aggregate,
        sampleWindowMs: PLAYBACK_DIAGNOSTICS_AGGREGATE_MS,
      });
      this.postIncidentTimers.forEach((_timer, incidentId) => {
        this.record("client_incident_post_aggregate", {
          incidentId,
          payload: aggregate,
          sampleWindowMs: PLAYBACK_DIAGNOSTICS_AGGREGATE_MS,
        });
      });
    }
    this.flushFrameAggregate();
  }

  handleMediaEvent(eventName, event) {
    const snapshot = readMediaDiagnosticSnapshot(
      this.video,
      this.previousSample,
      monotonicNow(this.windowRef),
    );
    const actionOrigin = ACTION_ORIGIN_MEDIA_EVENTS.has(eventName)
      ? this.actionOrigin(eventName, event)
      : "browser";
    const payload = { ...snapshot, action_origin: actionOrigin };
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
      if (!this.frameCallbackSupported && !this.firstFrameFallbackSeen) {
        this.firstFrameFallbackSeen = true;
        this.firstFrameSeen = true;
        this.record("first_video_frame_inferred", {
          priority: "high",
          observationKind: "inferred",
          measurementMethod: "html_media_playing_proxy",
          measurementUncertainty: "Playing does not prove compositor presentation time.",
          payload: snapshot,
        });
      }
    }
    if (PICTURE_IN_PICTURE_EVENTS[eventName]) {
      this.record(PICTURE_IN_PICTURE_EVENTS[eventName], {
        priority: "high",
        payload: { active: eventName === "enterpictureinpicture", action_origin: "browser" },
      });
    }
    if (["waiting", "stalled"].includes(eventName)) this.beginStallCandidate(eventName, snapshot);
    if (["playing", "ended", "pause", "seeking"].includes(eventName)) {
      this.maybeRecoverCandidate(eventName, snapshot);
    }
    if (eventName === "ended") {
      this.record("completed", { priority: "critical", payload: { action_origin: "browser" } });
    }
  }

  eligibleForStall(snapshot) {
    return this.firstFrameSeen
      && !snapshot.paused
      && !snapshot.seeking
      && !snapshot.ended
      && this.documentRef?.visibilityState !== "hidden";
  }

  beginStallCandidate(reason, snapshot) {
    if (this.activeCandidate || !this.eligibleForStall(snapshot)) {
      this.record("stall_candidate_ignored", {
        observationKind: "inferred",
        payload: {
          reason: !this.firstFrameSeen ? "startup_waiting" : (
            snapshot.seeking ? "seeking" : (snapshot.paused ? "paused" : "background_or_existing")
          ),
          stall_reason: reason,
        },
      });
      return;
    }
    const incidentId = createDiagnosticId("incident");
    const now = monotonicNow(this.windowRef);
    const recurrence = this.lastConfirmedIncident
      && now - this.lastConfirmedIncident.ended_at_ms
        <= PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS * 1_000
      ? this.lastConfirmedIncident.incident_id
      : null;
    this.activeCandidate = {
      incident_id: incidentId,
      started_at_ms: now,
      start_playhead_ms: snapshot.current_time_ms,
      reason,
      confirmed: false,
      recurrence_group_id: recurrence || incidentId,
      previous_incident_id: recurrence,
    };
    this.record("stall_candidate", {
      incidentId,
      priority: "high",
      payload: {
        candidate_time_ns: String(Math.round(Date.now() * 1_000_000)),
        stall_reason: reason,
        ...snapshot,
        ring_complete: this.sampleRing.complete,
        frame_ring_complete: this.frameRing.complete,
        previous_incident_id: recurrence,
        recurrence_group_id: recurrence || incidentId,
      },
    });
    this.persistIncidentPreWindow(incidentId);
    this.stallTimer = this.windowRef.setTimeout(
      () => this.confirmStall(incidentId),
      PLAYBACK_DIAGNOSTICS_STALL_CONFIRM_MS,
    );
  }

  confirmStall(incidentId) {
    const candidate = this.activeCandidate;
    if (!candidate || candidate.incident_id !== incidentId || candidate.confirmed) return;
    const snapshot = readMediaDiagnosticSnapshot(
      this.video,
      this.previousSample,
      monotonicNow(this.windowRef),
    );
    const playheadProgress = snapshot.current_time_ms - candidate.start_playhead_ms;
    const relevantEvidence = snapshot.ready_state < 3 || snapshot.buffered_ahead_ms < 250;
    if (!this.eligibleForStall(snapshot) || playheadProgress >= 100 || !relevantEvidence) {
      this.finishCandidate("candidate_recovered_before_threshold", snapshot);
      return;
    }
    candidate.confirmed = true;
    this.record("stall_confirmed", {
      incidentId,
      priority: "critical",
      payload: {
        stall_reason: candidate.reason,
        recurrence_group_id: candidate.recurrence_group_id,
        previous_incident_id: candidate.previous_incident_id,
        ...snapshot,
      },
    });
    this.record("recovery_waiting", {
      incidentId,
      priority: "high",
      observationKind: "measured_client",
      payload: { reason: "observer_has_no_control_action" },
    });
  }

  maybeRecoverCandidate(reason, snapshot) {
    const candidate = this.activeCandidate;
    if (!candidate) return;
    const progressed = snapshot.current_time_ms - candidate.start_playhead_ms >= 100;
    const intentionallyStopped = snapshot.paused || snapshot.seeking || reason === "ended";
    if (!progressed && !intentionallyStopped && reason !== "playing") return;
    this.finishCandidate(reason, snapshot);
  }

  finishCandidate(reason, snapshot) {
    const incident = this.activeCandidate;
    if (!incident) return;
    if (this.stallTimer != null) this.windowRef.clearTimeout(this.stallTimer);
    this.stallTimer = null;
    this.activeCandidate = null;
    const durationMs = Math.max(0, monotonicNow(this.windowRef) - incident.started_at_ms);
    if (!incident.confirmed) {
      this.record("stall_candidate_recovered", {
        incidentId: incident.incident_id,
        priority: "high",
        payload: { actual_duration_ms: durationMs, reason, stall_reason: incident.reason },
      });
      return;
    }
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
    const endedAt = monotonicNow(this.windowRef);
    this.lastConfirmedIncident = { incident_id: incident.incident_id, ended_at_ms: endedAt };
    const timer = this.windowRef.setTimeout(() => {
      this.postIncidentTimers.delete(incident.incident_id);
      this.record("post_recovery_observation", {
        incidentId: incident.incident_id,
        payload: { actual_duration_ms: PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS * 1_000 },
      });
    }, PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS * 1_000);
    this.postIncidentTimers.set(incident.incident_id, timer);
    if (this.postIncidentTimers.size > 4) {
      const oldestId = this.postIncidentTimers.keys().next().value;
      this.windowRef.clearTimeout(this.postIncidentTimers.get(oldestId));
      this.postIncidentTimers.delete(oldestId);
    }
    void snapshot;
  }

  persistIncidentPreWindow(incidentId) {
    this.scheduleSnapshot({
      incidentId,
      ring: this.sampleRing,
      eventName: "client_incident_pre_samples",
      payloadKey: "samples",
      chunkEntries: PLAYBACK_DIAGNOSTICS_INCIDENT_SAMPLE_CHUNK,
      completeKey: "ring_complete",
      complete: this.sampleRing.complete,
      transform: boundedIncidentSample,
    });
    this.scheduleSnapshot({
      incidentId,
      ring: this.frameRing,
      eventName: "client_incident_pre_frames",
      payloadKey: "frame_samples",
      chunkEntries: PLAYBACK_DIAGNOSTICS_INCIDENT_FRAME_CHUNK,
      completeKey: "frame_ring_complete",
      complete: this.frameRing.complete,
      transform: (value) => value,
    });
  }

  scheduleSnapshot({
    incidentId,
    ring,
    eventName,
    payloadKey,
    chunkEntries,
    completeKey,
    complete,
    transform,
  }) {
    const cursor = ring.createSnapshotCursor();
    let handle = null;
    let chunkIndex = 0;
    let cancelled = false;
    const cancelSchedule = () => {
      if (handle == null) return;
      if (this.windowRef?.cancelIdleCallback) this.windowRef.cancelIdleCallback(handle);
      else this.windowRef?.clearTimeout?.(handle);
      handle = null;
    };
    const job = {
      cancel: () => {
        cancelled = true;
        cancelSchedule();
        cursor.cancel();
      },
    };
    const schedule = () => {
      if (cancelled) return;
      if (this.windowRef?.requestIdleCallback) {
        handle = this.windowRef.requestIdleCallback(run, { timeout: 50 });
      } else {
        handle = this.windowRef?.setTimeout?.(run, 0);
      }
    };
    const run = () => {
      handle = null;
      if (cancelled) return;
      const startedAt = monotonicNow(this.windowRef);
      const values = [];
      let bytes = 0;
      while (!cursor.done && values.length < chunkEntries) {
        const candidate = cursor.read(1).map(transform)[0];
        if (candidate === undefined) continue;
        const candidateBytes = encodedBytes(candidate);
        if (values.length && bytes + candidateBytes > PLAYBACK_DIAGNOSTICS_INCIDENT_CHUNK_TARGET_BYTES) {
          break;
        }
        values.push(candidate);
        bytes += candidateBytes;
        if (monotonicNow(this.windowRef) - startedAt >= PLAYBACK_DIAGNOSTICS_INCIDENT_TASK_BUDGET_MS) {
          break;
        }
      }
      if (values.length) {
        chunkIndex += 1;
        this.record(eventName, {
          incidentId,
          priority: "high",
          payload: {
            [payloadKey]: values,
            chunk_sequence: chunkIndex,
            final: cursor.done,
            [completeKey]: complete,
            serialized_bytes: bytes,
          },
        });
      }
      if (cursor.done) {
        this.snapshotJobs.delete(job);
        return;
      }
      schedule();
    };
    this.snapshotJobs.add(job);
    schedule();
  }

  startFrameLoop() {
    if (!this.frameCallbackSupported) return;
    let previous = null;
    const callback = (now, metadata) => {
      const sample = {
        callback_monotonic_ms: finite(now),
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
        this.record("first_video_frame_presented", {
          priority: "high",
          observationKind: "measured_client",
          measurementMethod: "request_video_frame_callback",
          payload: sample,
        });
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
    const cadence = frames
      .map((frame) => finite(frame.frame_cadence_ms))
      .filter((value) => value != null);
    const cumulativeDropped = finite(quality?.droppedVideoFrames);
    const cumulativeTotal = finite(quality?.totalVideoFrames);
    const previous = this.previousFrameQuality;
    const reset = previous != null && (
      cumulativeDropped < previous.dropped || cumulativeTotal < previous.total
    );
    const deltaDropped = previous != null && !reset && cumulativeDropped != null
      ? cumulativeDropped - previous.dropped
      : null;
    const deltaTotal = previous != null && !reset && cumulativeTotal != null
      ? cumulativeTotal - previous.total
      : null;
    if (cumulativeDropped != null && cumulativeTotal != null) {
      this.previousFrameQuality = { dropped: cumulativeDropped, total: cumulativeTotal };
    }
    this.record("frame_aggregate", {
      payload: {
        ...latest,
        sample_count: frames.length,
        frame_cadence_ms: cadence.length
          ? cadence.reduce((sum, value) => sum + value, 0) / cadence.length
          : null,
        cumulative_total_frames: cumulativeTotal,
        cumulative_dropped_frames: cumulativeDropped,
        delta_total_frames: deltaTotal,
        delta_dropped_frames: deltaDropped,
        window_dropped_frame_ratio: deltaTotal > 0 && deltaDropped != null
          ? deltaDropped / deltaTotal
          : null,
        frame_counter_reset: reset,
        corrupted_frames: finite(quality?.corruptedVideoFrames),
      },
      sampleWindowMs: PLAYBACK_DIAGNOSTICS_AGGREGATE_MS,
    });
  }
}
