import assert from "node:assert/strict";
import { describe, test } from "vitest";

import {
  buildHlsConfig,
  classifyManifestWindowState,
  classifyPlaybackStall,
  deriveBufferTargetsFromSession,
  evaluateClientPlaybackReleaseGate,
  hasVideoFirstFrameForPlaybackRelease,
  muteVideoForClientPrewarm,
  readClientBufferedAheadSeconds,
  readClientPlaybackLiveness,
  restoreVideoAfterClientPrewarm,
  resolveClientPlaybackReleaseBufferSeconds,
  resolvePlaybackRecoveryTargetSeconds,
  retuneHlsInstance,
  shouldStartClientBufferPrewarm,
  shouldRecoverNativeHlsStalePlaylist,
  shouldDisarmFirstFrameStallMonitor,
  shouldStartVisibleHlsSupplyRecovery,
} from "./browserPlaybackBufferPolicy.js";

const MB = 1024 * 1024;
const GB = 1024 * 1024 * 1024;

function makeBufferedRange(ranges) {
  return {
    length: ranges.length,
    start(index) {
      return ranges[index][0];
    },
    end(index) {
      return ranges[index][1];
    },
  };
}

describe("deriveBufferTargetsFromSession", () => {
  test("maps lite tiers to locked targets", () => {
    assert.equal(deriveBufferTargetsFromSession({ playback_mode: "lite", buffer_tier: "lite_fast" }, "phone").forwardBufferSeconds, 15);
    assert.equal(deriveBufferTargetsFromSession({ playback_mode: "lite", buffer_tier: "lite_uncertain" }, "phone").forwardBufferSeconds, 45);
    assert.equal(deriveBufferTargetsFromSession({ playback_mode: "lite", buffer_tier: "lite_undersupply" }, "phone").forwardBufferSeconds, 180);
  });

  test("maps full tiers to locked targets", () => {
    assert.equal(deriveBufferTargetsFromSession({ playback_mode: "full", buffer_tier: "full_healthy" }, "desktop").forwardBufferSeconds, 120);
    assert.equal(deriveBufferTargetsFromSession({ playback_mode: "full", buffer_tier: "full_bad_condition" }, "desktop").forwardBufferSeconds, 900);
  });

  test("uses universal back buffer and byte ceilings without device seconds caps", () => {
    const phone = deriveBufferTargetsFromSession({ buffer_tier: "full_bad_condition" }, "phone");
    const tablet = deriveBufferTargetsFromSession({ buffer_tier: "full_bad_condition" }, "tablet");
    const desktop = deriveBufferTargetsFromSession({ buffer_tier: "full_bad_condition" }, "desktop");
    const laptop = deriveBufferTargetsFromSession({ buffer_tier: "full_bad_condition" }, "laptop");
    assert.equal(phone.forwardBufferSeconds, 900);
    assert.equal(tablet.forwardBufferSeconds, 900);
    assert.equal(desktop.forwardBufferSeconds, 900);
    assert.equal(laptop.forwardBufferSeconds, 900);
    assert.equal(phone.backBufferSeconds, 120);
    assert.equal(tablet.backBufferSeconds, 120);
    assert.equal(desktop.backBufferSeconds, 120);
    assert.equal(laptop.backBufferSeconds, 120);
    assert.equal(phone.maxBufferSizeBytes, 250 * MB);
    assert.equal(tablet.maxBufferSizeBytes, 300 * MB);
    assert.equal(desktop.maxBufferSizeBytes, 3 * GB);
    assert.equal(laptop.maxBufferSizeBytes, 3 * GB);
  });

  test("falls back to lite required runway for uncertain lite sessions", () => {
    const targets = deriveBufferTargetsFromSession({
      playback_mode: "lite",
      lite_required_runway_seconds: 45,
    }, "phone");
    assert.equal(targets.forwardBufferSeconds, 45);
  });
});

describe("client playback release gates", () => {
  test("locked thresholds are client-buffer release thresholds", () => {
    assert.equal(resolveClientPlaybackReleaseBufferSeconds({
      playback_mode: "lite",
      buffer_tier: "lite_fast",
    }, "phone"), 15);
    assert.equal(resolveClientPlaybackReleaseBufferSeconds({
      playback_mode: "lite",
      buffer_tier: "lite_uncertain",
    }, "phone"), 45);
    assert.equal(resolveClientPlaybackReleaseBufferSeconds({
      playback_mode: "lite",
      buffer_tier: "lite_undersupply",
    }, "phone"), 180);
    assert.equal(resolveClientPlaybackReleaseBufferSeconds({
      playback_mode: "full",
      buffer_tier: "full_healthy",
    }, "phone"), 120);
    assert.equal(resolveClientPlaybackReleaseBufferSeconds({
      playback_mode: "full",
      buffer_tier: "full_bad_condition",
    }, "phone"), 900);
  });

  test("server prepared runway alone does not release Lite fast playback", () => {
    const gate = evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "lite", buffer_tier: "lite_fast" },
      backendPreparedAheadSeconds: 20,
      clientBufferedAheadSeconds: 4,
      deviceClass: "phone",
    });

    assert.equal(gate.requiredClientBufferSeconds, 15);
    assert.equal(gate.serverReady, true);
    assert.equal(gate.clientReady, false);
    assert.equal(gate.ready, false);
  });

  test("Lite releases only when client contiguous buffer reaches the required tier", () => {
    assert.equal(evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "lite", buffer_tier: "lite_fast" },
      backendPreparedAheadSeconds: 20,
      clientBufferedAheadSeconds: 15,
      deviceClass: "phone",
    }).ready, true);
    assert.equal(evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "lite", buffer_tier: "lite_uncertain" },
      backendPreparedAheadSeconds: 60,
      clientBufferedAheadSeconds: 15,
      deviceClass: "phone",
    }).ready, false);
    assert.equal(evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "lite", buffer_tier: "lite_undersupply" },
      backendPreparedAheadSeconds: 220,
      clientBufferedAheadSeconds: 45,
      deviceClass: "phone",
    }).ready, false);
  });

  test("Full releases only when client contiguous buffer reaches normal or reserve tier", () => {
    assert.equal(evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "full", buffer_tier: "full_healthy" },
      backendPreparedAheadSeconds: 140,
      clientBufferedAheadSeconds: 119,
      deviceClass: "phone",
    }).ready, false);
    assert.equal(evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "full", buffer_tier: "full_healthy" },
      backendPreparedAheadSeconds: 140,
      clientBufferedAheadSeconds: 120,
      deviceClass: "phone",
    }).ready, true);
    assert.equal(evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "full", buffer_tier: "full_bad_condition" },
      backendPreparedAheadSeconds: 950,
      clientBufferedAheadSeconds: 899,
      deviceClass: "phone",
    }).ready, false);
  });

  test("caps the effective release gate to the remaining title duration", () => {
    const gate = evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "full", buffer_tier: "full_healthy" },
      backendPreparedAheadSeconds: 60,
      clientBufferedAheadSeconds: 60,
      remainingPlayableSeconds: 60,
      deviceClass: "phone",
    });

    assert.equal(gate.configuredClientBufferSeconds, 120);
    assert.equal(gate.requiredClientBufferSeconds, 60);
    assert.equal(gate.ready, true);
  });

  test("audio switch release uses the audio-specific client buffer gate", () => {
    assert.equal(evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "full", buffer_tier: "full_bad_condition" },
      backendPreparedAheadSeconds: 20,
      clientBufferedAheadSeconds: 14,
      audioSwitch: true,
      deviceClass: "phone",
    }).ready, false);
    assert.equal(evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "full", buffer_tier: "full_bad_condition" },
      backendPreparedAheadSeconds: 20,
      clientBufferedAheadSeconds: 15,
      audioSwitch: true,
      deviceClass: "phone",
    }).ready, true);
  });

  test("iPhone prewarm can start with an attached source before client buffer reaches release threshold", () => {
    const gate = evaluateClientPlaybackReleaseGate({
      session: { playback_mode: "lite", buffer_tier: "lite_fast" },
      backendPreparedAheadSeconds: 20,
      clientBufferedAheadSeconds: 0,
      deviceClass: "phone",
    });

    assert.equal(gate.ready, false);
    assert.equal(shouldStartClientBufferPrewarm({
      iosMobile: true,
      hasMobileSession: true,
      hasAttachedSource: true,
      mobilePlayerCanPlay: false,
      playbackIntentActive: true,
      releaseGateReady: gate.ready,
    }), true);
  });

  test("client prewarm does not replace the formal release gate", () => {
    assert.equal(shouldStartClientBufferPrewarm({
      iosMobile: true,
      hasMobileSession: true,
      hasAttachedSource: true,
      mobilePlayerCanPlay: false,
      playbackIntentActive: true,
      releaseGateReady: true,
    }), false);
    assert.equal(shouldStartClientBufferPrewarm({
      iosMobile: true,
      hasMobileSession: true,
      hasAttachedSource: false,
      mobilePlayerCanPlay: false,
      playbackIntentActive: true,
      releaseGateReady: false,
    }), false);
  });

  test("client prewarm mutes the media element and restores the previous audio state on release", () => {
    const video = { muted: false, volume: 0.72 };

    const saved = muteVideoForClientPrewarm(video);
    assert.equal(video.muted, true);
    assert.deepEqual(saved, { muted: false, volume: 0.72 });

    const nextState = restoreVideoAfterClientPrewarm(video, saved);
    assert.equal(nextState, null);
    assert.equal(video.muted, false);
    assert.equal(video.volume, 0.72);
  });

  test("client prewarm preserves an already-muted media element after release", () => {
    const video = { muted: true, volume: 0.4 };

    const saved = muteVideoForClientPrewarm(video);
    assert.equal(video.muted, true);

    restoreVideoAfterClientPrewarm(video, saved);
    assert.equal(video.muted, true);
    assert.equal(video.volume, 0.4);
  });

  test("first-frame release accepts loaded current data with video dimensions", () => {
    assert.equal(hasVideoFirstFrameForPlaybackRelease({
      readyState: 2,
      videoWidth: 1920,
      videoHeight: 1080,
    }, { loadedDataSeen: true }), true);

    assert.equal(hasVideoFirstFrameForPlaybackRelease({
      readyState: 2,
      videoWidth: 0,
      videoHeight: 1080,
    }, { loadedDataSeen: true }), false);

    assert.equal(hasVideoFirstFrameForPlaybackRelease({
      readyState: 1,
      videoWidth: 1920,
      videoHeight: 1080,
    }, { canPlaySeen: true }), false);
  });
});

describe("visible HLS supply recovery gate", () => {
  test("backend low-water does not visibly recover while client buffer is playable and time advances", () => {
    const decision = shouldStartVisibleHlsSupplyRecovery({
      session: { stalled_recovery_needed: true },
      livenessSample: {
        bufferedAheadSeconds: 20,
        elapsedMs: 3000,
        currentTimeDeltaSeconds: 2.8,
        timeAdvancing: true,
        readyState: 4,
        networkState: 2,
      },
      lifecycleState: "attached",
      mobilePlayerCanPlay: true,
      videoPaused: false,
    });

    assert.equal(decision.start, false);
    assert.equal(decision.reason, "client_buffer_playable");
  });

  test("backend low-water visibly recovers only when client buffer is empty and time is stopped", () => {
    const decision = shouldStartVisibleHlsSupplyRecovery({
      session: { stalled_recovery_needed: true },
      livenessSample: {
        bufferedAheadSeconds: 0,
        elapsedMs: 3200,
        currentTimeDeltaSeconds: 0,
        timeAdvancing: false,
        readyState: 2,
        networkState: 2,
      },
      lifecycleState: "attached",
      mobilePlayerCanPlay: true,
      videoPaused: false,
    });

    assert.equal(decision.start, true);
  });

  test("low-water recovery waits for a confirmed stopped-time sample before reattaching", () => {
    const decision = shouldStartVisibleHlsSupplyRecovery({
      session: { stalled_recovery_needed: true },
      livenessSample: {
        bufferedAheadSeconds: 0,
        elapsedMs: 500,
        currentTimeDeltaSeconds: 0,
        timeAdvancing: false,
      },
      lifecycleState: "attached",
      mobilePlayerCanPlay: true,
      videoPaused: false,
    });

    assert.equal(decision.start, false);
    assert.equal(decision.reason, "client_time_not_confirmed_stopped");
  });
});

describe("buildHlsConfig", () => {
  test("uses target, retry settings, and no low-latency mode", () => {
    const config = buildHlsConfig({
      session: { playback_mode: "full", buffer_tier: "full_bad_condition" },
      deviceClass: "phone",
    });
    assert.equal(config.maxBufferLength, 900);
    assert.equal(config.maxMaxBufferLength, 900);
    assert.equal(config.backBufferLength, 120);
    assert.equal(config.maxBufferSize, 250 * MB);
    assert.equal(config.lowLatencyMode, false);
    assert.equal(config.autoStartLoad, true);
    assert.equal(config.enableWorker, true);
    assert.equal(config.fragLoadingMaxRetry, 6);
    assert.equal(config.manifestLoadingMaxRetry, 4);
    assert.equal(config.levelLoadingMaxRetry, 4);
    assert.equal(config.nudgeMaxRetry, 5);
  });

  test("retunes existing hls instance when tier changes", () => {
    const hls = { config: buildHlsConfig({ session: { buffer_tier: "lite_fast" }, deviceClass: "phone" }) };
    retuneHlsInstance(hls, {
      session: { playback_mode: "full", buffer_tier: "full_bad_condition" },
      deviceClass: "desktop",
    });
    assert.equal(hls.config.maxBufferLength, 900);
    assert.equal(hls.config.maxMaxBufferLength, 900);
    assert.equal(hls.config.maxBufferSize, 3 * GB);
    assert.equal(hls.config.backBufferLength, 120);
  });
});

describe("native HLS helpers", () => {
  test("readClientBufferedAheadSeconds handles no ranges", () => {
    assert.equal(readClientBufferedAheadSeconds({ currentTime: 5, buffered: makeBufferedRange([]) }), 0);
  });

  test("readClientBufferedAheadSeconds handles one containing range", () => {
    assert.equal(readClientBufferedAheadSeconds({ currentTime: 5, buffered: makeBufferedRange([[0, 12]]) }), 7);
  });

  test("readClientBufferedAheadSeconds handles multiple ranges", () => {
    assert.equal(readClientBufferedAheadSeconds({ currentTime: 25, buffered: makeBufferedRange([[0, 10], [20, 31]]) }), 6);
  });

  test("first-frame-stall classification works", () => {
    const previous = { currentTimeSeconds: 10, sampledAtMs: 0 };
    const sample = readClientPlaybackLiveness(
      { currentTime: 10.2, readyState: 3, networkState: 2, paused: false, buffered: makeBufferedRange([[10, 11]]) },
      previous,
      4200,
    );
    const result = classifyPlaybackStall({
      session: { playback_mode: "lite", buffer_tier: "lite_fast", ahead_runway_seconds: 20 },
      livenessSample: sample,
    });
    assert.equal(result.firstFrameStall, true);
    assert.equal(result.stallReason, "first_frame_stall");
  });

  test("first-frame-stall disarms after real playback progression", () => {
    assert.equal(shouldDisarmFirstFrameStallMonitor({
      attachmentStartSeconds: 80,
      currentAbsolutePositionSeconds: 83.5,
    }), true);
    assert.equal(shouldDisarmFirstFrameStallMonitor({
      attachmentStartSeconds: 80,
      currentAbsolutePositionSeconds: 80.5,
      successfulTimeupdateCount: 3,
    }), true);
    assert.equal(shouldDisarmFirstFrameStallMonitor({
      attachmentStartSeconds: 80,
      currentAbsolutePositionSeconds: 80.5,
      advancingDurationMs: 6000,
    }), true);
  });

  test("mid-playback stalls are not classified as first-frame stalls after disarm", () => {
    const result = classifyPlaybackStall({
      session: { playback_mode: "lite", buffer_tier: "lite_fast", ahead_runway_seconds: 20 },
      livenessSample: {
        elapsedMs: 4200,
        currentTimeDeltaSeconds: 0.2,
        paused: false,
        bufferedAheadSeconds: 10,
      },
      firstFrameEligible: false,
    });
    assert.equal(result.firstFrameStall, false);
    assert.equal(result.midPlaybackStall, true);
    assert.equal(result.stallReason, "mid_playback_stall");
  });

  test("client_buffer_starved vs backend_supply_waiting classification works", () => {
    const starved = classifyPlaybackStall({
      session: { playback_mode: "lite", buffer_tier: "lite_fast", ahead_runway_seconds: 20 },
      livenessSample: { elapsedMs: 1000, currentTimeDeltaSeconds: 1, paused: false, bufferedAheadSeconds: 2 },
    });
    assert.equal(starved.stallReason, "client_buffer_starved");
    const waiting = classifyPlaybackStall({
      session: { playback_mode: "full", buffer_tier: "full_bad_condition", ahead_runway_seconds: 46 },
      livenessSample: { elapsedMs: 1000, currentTimeDeltaSeconds: 1, paused: false, bufferedAheadSeconds: 2 },
    });
    assert.equal(waiting.stallReason, "backend_supply_waiting");
  });
});

describe("recovery and manifest window helpers", () => {
  test("manifest window exhaustion is not movie completion when far from full duration", () => {
    const state = classifyManifestWindowState({
      absolutePositionSeconds: 118,
      manifestEndSeconds: 120,
      fullDurationSeconds: 3600,
      completionGraceSeconds: 15,
      refreshRunwaySeconds: 12,
    });
    assert.equal(state.realCompletion, false);
    assert.equal(state.manifestWindowExhausted, true);
    assert.equal(state.shouldRefreshManifest, true);
  });

  test("movie completion only applies near full duration", () => {
    const state = classifyManifestWindowState({
      absolutePositionSeconds: 3590,
      manifestEndSeconds: 3600,
      fullDurationSeconds: 3600,
      completionGraceSeconds: 15,
      refreshRunwaySeconds: 12,
    });
    assert.equal(state.realCompletion, true);
    assert.equal(state.manifestWindowExhausted, false);
  });

  test("mid-playback recovery preserves the strongest absolute position", () => {
    assert.equal(resolvePlaybackRecoveryTargetSeconds({
      currentAbsolutePositionSeconds: 121,
      committedPlayheadSeconds: 120,
      actualMediaElementTimeSeconds: 120,
      targetPositionSeconds: 0,
    }), 121);
  });

  test("native HLS reattach at eight seconds resumes from live playhead instead of zero", () => {
    assert.equal(resolvePlaybackRecoveryTargetSeconds({
      currentAbsolutePositionSeconds: 8.2,
      committedPlayheadSeconds: 0,
      actualMediaElementTimeSeconds: 0,
      targetPositionSeconds: 0,
    }), 8.2);
  });

  test("native HLS stale playlist recovery is allowed when backend is far ahead but client is starved", () => {
    assert.equal(shouldRecoverNativeHlsStalePlaylist({
      hlsJsAttached: false,
      backendPreparedAheadSeconds: 46,
      stallReason: "client_buffer_starved",
    }), true);
    assert.equal(shouldRecoverNativeHlsStalePlaylist({
      hlsJsAttached: false,
      backendPreparedAheadSeconds: 46,
      stallReason: "mid_playback_stall",
    }), true);
    assert.equal(shouldRecoverNativeHlsStalePlaylist({
      hlsJsAttached: true,
      backendPreparedAheadSeconds: 46,
      stallReason: "client_buffer_starved",
    }), false);
  });

  test("backBufferLength stays an hls.js config field past 120s", () => {
    const hls = { config: buildHlsConfig({ session: { buffer_tier: "full_healthy" }, deviceClass: "desktop" }) };
    retuneHlsInstance(hls, {
      session: { buffer_tier: "full_healthy", target_position_seconds: 180 },
      deviceClass: "desktop",
    });
    assert.equal(hls.config.backBufferLength, 120);
    assert.equal(resolvePlaybackRecoveryTargetSeconds({
      currentAbsolutePositionSeconds: 180,
      committedPlayheadSeconds: 180,
      targetPositionSeconds: 180,
    }), 180);
  });
});
