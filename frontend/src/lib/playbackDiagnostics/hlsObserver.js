import { diagnosticUrlIdentity } from "./privacy";

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function loaderStats(data) {
  const stats = data?.stats || data?.frag?.stats || {};
  const loading = stats.loading || {};
  const requestStart = finite(loading.start);
  const first = finite(loading.first);
  const end = finite(loading.end);
  const bytes = finite(stats.loaded ?? stats.total);
  const durationMs = requestStart != null && end != null ? Math.max(0, end - requestStart) : null;
  return {
    request_start_ms: requestStart,
    first_byte_ms: requestStart != null && first != null ? Math.max(0, first - requestStart) : null,
    last_byte_ms: durationMs,
    bytes,
    retries: finite(stats.retry),
    client_throughput_bps: bytes != null && durationMs > 0 ? bytes * 8_000 / durationMs : null,
  };
}

function fragmentPayload(eventName, data) {
  const fragment = data?.frag || {};
  const url = fragment.url || fragment.relurl || data?.url || "";
  return {
    hls_event: eventName,
    hls_level: finite(fragment.level ?? data?.level),
    segment_index: finite(fragment.sn),
    segment_media_start_ms: finite(fragment.start) != null ? fragment.start * 1_000 : null,
    segment_duration_ms: finite(fragment.duration) != null ? fragment.duration * 1_000 : null,
    audio_stream_index: finite(fragment.audioTrackId ?? data?.id),
    error_class: data?.type ? String(data.type).slice(0, 128) : null,
    error_code: data?.details ? String(data.details).slice(0, 128) : null,
    fatal: Boolean(data?.fatal),
    ...loaderStats(data),
    ...(url ? diagnosticUrlIdentity(url) : {}),
  };
}

const EVENT_MAP = Object.freeze({
  MANIFEST_LOADING: "hls_manifest_loading",
  MANIFEST_LOADED: "hls_manifest_loaded",
  MANIFEST_PARSED: "hls_manifest_parsed",
  LEVEL_LOADING: "hls_level_loading",
  LEVEL_LOADED: "hls_level_loaded",
  FRAG_LOADING: "hls_fragment_loading",
  FRAG_LOAD_EMERGENCY_ABORTED: "hls_fragment_emergency_abort",
  FRAG_LOADED: "hls_fragment_loaded",
  FRAG_BUFFERED: "hls_fragment_buffered",
  BUFFER_APPENDING: "hls_buffer_appending",
  BUFFER_APPENDED: "hls_buffer_appended",
  BUFFER_FLUSHING: "hls_buffer_flushing",
  BUFFER_FLUSHED: "hls_buffer_flushed",
  AUDIO_TRACK_SWITCHING: "hls_audio_track_switching",
  AUDIO_TRACK_SWITCHED: "hls_audio_track_switched",
  SUBTITLE_TRACK_SWITCH: "hls_subtitle_track_switching",
  SUBTITLE_TRACK_LOADED: "hls_subtitle_track_loaded",
  FPS_DROP: "hls_fps_drop",
  FPS_DROP_LEVEL_CAPPING: "hls_fps_drop_level_capping",
  ERROR: "hls_error",
});

export class HlsJsDiagnosticObserver {
  constructor({ hls, events, record }) {
    this.hls = hls;
    this.events = events || {};
    this.record = record;
    this.handlers = [];
  }

  start() {
    Object.entries(EVENT_MAP).forEach(([constantName, eventName]) => {
      const hlsEvent = this.events[constantName];
      if (!hlsEvent) return;
      const handler = (_event, data) => {
        const payload = fragmentPayload(eventName, data);
        this.record(eventName, {
          priority: payload.fatal || eventName.includes("error") ? "high" : "normal",
          severity: payload.fatal ? "error" : "info",
          payload,
        });
      };
      this.hls.on(hlsEvent, handler);
      this.handlers.push({ hlsEvent, handler });
    });
  }

  stop() {
    this.handlers.forEach(({ hlsEvent, handler }) => this.hls.off(hlsEvent, handler));
    this.handlers = [];
  }
}
