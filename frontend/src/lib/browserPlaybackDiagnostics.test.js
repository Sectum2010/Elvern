import { describe, expect, test, vi } from "vitest";

import {
  buildBrowserPlaybackDiagnosticPayload,
  logBrowserPlaybackDiagnostic,
} from "./browserPlaybackDiagnostics";

describe("browser playback diagnostics", () => {
  test("builds a safe snapshot with backend and client gate fields", () => {
    const session = {
      active_epoch_id: "epoch-1",
      active_epoch_state: "warming",
      active_manifest_url: "/api/browser-playback/session/secret-token/master.m3u8",
      attach_ready: true,
      attach_revision: 4,
      client_attach_revision: 3,
      duration_seconds: 7877,
      engine_mode: "route2",
      gate_reason: "lite_undersupply_below_realtime",
      lite_required_runway_seconds: 180,
      lite_required_runway_source: "lite_undersupply",
      lite_undersupply_detected: true,
      lite_undersupply_reason: "mature_supply_below_1_0",
      playback_mode: "lite",
      ready_end_seconds: 174,
      ready_start_seconds: 0,
      required_startup_runway_seconds: 180,
      actual_startup_runway_seconds: 174,
      session_id: "session-1",
      source_path: "/media/private/movie.mkv",
      supply_observation_seconds: 12,
      supply_rate_x: 0.72,
    };
    const video = {
      currentTime: 5,
      networkState: 2,
      paused: true,
      readyState: 1,
      videoHeight: 0,
      videoWidth: 0,
    };

    const payload = buildBrowserPlaybackDiagnosticPayload({
      eventReason: "client_release_gate",
      session,
      video,
      releaseGate: {
        backendPreparedAheadSeconds: 174,
        clientBufferedAheadSeconds: 3,
        clientReady: false,
        configuredClientBufferSeconds: 180,
        ready: false,
        requiredClientBufferSeconds: 180,
        serverReady: false,
      },
      livenessSample: {
        bufferedAheadSeconds: 3,
        networkState: 2,
        readyState: 1,
        timeAdvancing: false,
      },
      mobileLifecycleState: "prewarming",
      mobilePlayerCanPlay: false,
      firstFrameReady: false,
      loadedDataSeen: false,
      canPlaySeen: false,
      frameReady: false,
    });

    expect(payload).toMatchObject({
      active_manifest_url_exists: true,
      actual_startup_runway_seconds: 174,
      backend_prepared_ahead_seconds: 174,
      client_attach_revision: 3,
      client_buffered_ahead_seconds: 3,
      client_ready_state: 1,
      client_time_advancing: false,
      duration_seconds: 7877,
      engine_mode: "route2",
      event_reason: "client_release_gate",
      firstFrameReady: false,
      gate_reason: "lite_undersupply_below_realtime",
      lite_required_runway_seconds: 180,
      lite_required_runway_source: "lite_undersupply",
      lite_undersupply_detected: true,
      lite_undersupply_reason: "mature_supply_below_1_0",
      mobileLifecycleState: "prewarming",
      mobilePlayerCanPlay: false,
      playback_mode: "lite",
      prepared_through_seconds: 174,
      ready_end_seconds: 174,
      releaseGateReady: false,
      releaseGateReason: "backend_prepared_ahead_below_required",
      required_client_buffer_seconds: 180,
      required_startup_runway_seconds: 180,
      session_id: "session-1",
      supply_rate_x: 0.72,
      video_current_time_seconds: 5,
    });
    expect(JSON.stringify(payload)).not.toContain("secret-token");
    expect(JSON.stringify(payload)).not.toContain("/media/private");
  });

  test("rate-limits repeated logs without mutating the payload", () => {
    const payload = {
      event_reason: "client_release_gate",
      session_id: "session-1",
      gate_reason: "lite_undersupply_below_realtime",
    };
    const snapshotBefore = JSON.stringify(payload);
    const lastLogMap = new Map();
    const consoleRef = { debug: vi.fn() };

    expect(logBrowserPlaybackDiagnostic({
      eventName: "elvern:ios_playback_release_blocked",
      payload,
      lastLogMap,
      nowMs: 10000,
      minIntervalMs: 7000,
      consoleRef,
    })).toBe(true);
    expect(logBrowserPlaybackDiagnostic({
      eventName: "elvern:ios_playback_release_blocked",
      payload,
      lastLogMap,
      nowMs: 12000,
      minIntervalMs: 7000,
      consoleRef,
    })).toBe(false);
    expect(logBrowserPlaybackDiagnostic({
      eventName: "elvern:ios_playback_release_blocked",
      payload,
      lastLogMap,
      nowMs: 18000,
      minIntervalMs: 7000,
      consoleRef,
    })).toBe(true);

    expect(consoleRef.debug).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(payload)).toBe(snapshotBefore);
  });

  test("captures recovery decision fields without exposing transport or path details", () => {
    const payload = buildBrowserPlaybackDiagnosticPayload({
      eventReason: "native_hls_playlist_stale",
      session: {
        active_manifest_url: "/api/browser-playback/session/token-value/native.m3u8",
        ahead_runway_seconds: 18,
        playback_mode: "lite",
        session_id: "session-2",
        source_path: "/private/media/Pacific Rim.mkv",
      },
      livenessSample: {
        bufferedAheadSeconds: 0,
        currentTimeDeltaSeconds: 0,
        elapsedMs: 2400,
        stallReason: "native_hls_playlist_stale",
        timeAdvancing: false,
      },
      recoveryDecision: {
        start: true,
        reason: "native_hls_playlist_starved",
      },
      staleNativePlaylistStall: true,
      video: {
        currentSrc: "/api/browser-playback/session/token-value/native.m3u8",
        currentTime: 181,
        networkState: 2,
        paused: false,
        readyState: 2,
      },
    });

    expect(payload).toMatchObject({
      active_manifest_url_exists: true,
      backend_prepared_ahead_seconds: 18,
      client_buffered_ahead_seconds: 0,
      client_playback_stall_reason: "native_hls_playlist_stale",
      client_time_advancing: false,
      event_reason: "native_hls_playlist_stale",
      recovery_decision_reason: "native_hls_playlist_starved",
      recovery_decision_start: true,
      stale_native_playlist_stall: true,
      video_current_time_seconds: 181,
    });
    const serialized = JSON.stringify(payload);
    expect(serialized).not.toContain("token-value");
    expect(serialized).not.toContain("Pacific Rim");
    expect(serialized).not.toContain("/private/media");
    expect(serialized).not.toContain("currentSrc");
  });
});
