import { cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, expect, test, vi } from "vitest";

import { useOptimizedPlaybackSession } from "./useOptimizedPlaybackSession";
import { useBrowserPlaybackController } from "./useBrowserPlaybackController";

vi.mock("./useOptimizedPlaybackSession", () => ({
  useOptimizedPlaybackSession: vi.fn(),
}));

const controllerSourcePath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "useBrowserPlaybackController.js",
);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function ref(value = null) {
  return { current: value };
}

function makeOptimizedPlaybackSession(overrides = {}) {
  return {
    mobileSessionRef: ref(),
    mobilePendingTargetRef: ref(),
    requestedTargetSecondsRef: ref(),
    mobileAutoplayPendingRef: ref(false),
    mobileResumeAfterReadyRef: ref(false),
    mobileSeekPendingRef: ref(false),
    pendingSeekPhaseRef: ref("idle"),
    mobileAttachedEpochRef: ref(),
    mobileCanPlaySeenRef: ref(false),
    mobileLoadedDataSeenRef: ref(false),
    mobileAwaitingTargetSeekRef: ref(false),
    mobileFrameReadyRef: ref(false),
    mobileFrameProbePendingRef: ref(false),
    mobileReadinessGenerationRef: ref(0),
    mobilePlayerCanPlayRef: ref(false),
    mobileWarmupProbeActiveRef: ref(false),
    mobileWarmupPlaybackObservedRef: ref(false),
    mobileWarmupStartPositionRef: ref(0),
    mobileRetargetTransitionRef: ref(false),
    mobileLastStablePositionRef: ref(0),
    mobileLifecycleStateRef: ref("idle"),
    mobileRecoveryInFlightRef: ref(false),
    mobileLastHeartbeatAtRef: ref(0),
    mobileHeartbeatInFlightRef: ref(false),
    mobileWasBackgroundedRef: ref(false),
    mobileBackgroundHiddenAtRef: ref(0),
    mobileWasPlayingBeforeSuspendRef: ref(false),
    mobileStallTimerRef: ref(),
    mobileStallStartedAtRef: ref(0),
    audioSwitchAttachRef: ref(),
    committedPlayheadSecondsRef: ref(0),
    actualMediaElementTimeRef: ref(0),
    mobileSession: null,
    activePlaybackMode: "",
    browserPlaybackLabel: "browser playback",
    browserPlaybackLabelTitle: "Browser Playback",
    browserStreamLabelTitle: "Browser stream",
    browserReadyLabelTitle: "Ready",
    mobilePlayerCanPlay: false,
    mobileFrozenFrameUrl: "",
    prepareEstimateObservedAtMs: 0,
    prepareEstimateNowMs: 0,
    videoElementKey: "video",
    setRequestedTargetSeconds: vi.fn(),
    setCommittedPlayheadSeconds: vi.fn(),
    setActualMediaElementTime: vi.fn(),
    setPendingSeekPhase: vi.fn(),
    setMobilePlayerCanPlay: vi.fn(),
    setMobileFrozenFrameUrl: vi.fn(),
    setMobileLifecycleStateValue: vi.fn(),
    applyMobileLifecycleStatus: vi.fn(),
    resetMobilePlaybackState: vi.fn(),
    isHlsSessionPayload: vi.fn(() => false),
    resolveSessionAttachmentIdentity: vi.fn(() => ""),
    resolveMobileCommittedPosition: vi.fn(() => 0),
    syncMobilePlaybackState: vi.fn(),
    postMobileRuntimeHeartbeat: vi.fn(() => Promise.resolve(null)),
    maybeAcknowledgeHlsAttachment: vi.fn(),
    recoverMobilePlaybackAfterResume: vi.fn(),
    softResumeMobilePlaybackAfterBackground: vi.fn(),
    startMobileOptimizedPlayback: vi.fn(),
    retargetMobileOptimizedPlayback: vi.fn(),
    selectBrowserPlaybackAudioTrack: vi.fn(),
    prepareBrowserPlaybackSubtitleTrack: vi.fn(),
    restoreActiveBrowserPlaybackSession: vi.fn(),
    finalizeRetargetVisibility: vi.fn(),
    ...overrides,
  };
}

function renderController({ onReady }) {
  function Harness() {
    const controller = useBrowserPlaybackController({
      itemId: 42,
      item: null,
      progress: null,
      iosMobile: false,
      onProgressChange: vi.fn(),
      onProviderAuthRequired: vi.fn(),
    });
    onReady(controller);
    return null;
  }

  render(createElement(Harness));
}

test("browser playback controller exposes optimized audio selector callback", () => {
  const selectBrowserPlaybackAudioTrack = vi.fn();
  let latestController = null;
  useOptimizedPlaybackSession.mockReturnValue(makeOptimizedPlaybackSession({
    selectBrowserPlaybackAudioTrack,
  }));

  renderController({
    onReady: (controller) => {
      latestController = controller;
    },
  });

  expect(latestController.selectBrowserPlaybackAudioTrack).toBe(selectBrowserPlaybackAudioTrack);
});

test("stall recovery passes stale native playlist flag without undefined shorthand", () => {
  const source = fs.readFileSync(controllerSourcePath, "utf8");
  const stalledStart = source.indexOf("function handlePlaybackStalled()");
  const stalledEnd = source.indexOf("function handleSeeked()", stalledStart);
  expect(stalledStart).toBeGreaterThan(0);
  expect(stalledEnd).toBeGreaterThan(stalledStart);
  const stalledSource = source.slice(stalledStart, stalledEnd);

  expect(stalledSource).toContain("stalePlaylistStall: staleNativePlaylistStall");
  expect(stalledSource.match(/stalePlaylistStall: staleNativePlaylistStall/g)).toHaveLength(2);
  expect(stalledSource).not.toMatch(/(?<!:)\bstalePlaylistStall,\s*$/m);
});

test("sendBeacon progress reports a narrow dirty payload without changing save cadence", () => {
  const source = fs.readFileSync(controllerSourcePath, "utf8");
  const beaconStart = source.indexOf("function beaconProgress(completed = false)");
  const beaconEnd = source.indexOf("function resolvePlaybackTrackingMode()", beaconStart);
  const beaconSource = source.slice(beaconStart, beaconEnd);

  expect(beaconStart).toBeGreaterThan(0);
  expect(beaconEnd).toBeGreaterThan(beaconStart);
  expect(beaconSource).toContain("onProgressDirty?.({");
  expect(beaconSource).toContain("media_item_id: item.id");
  expect(beaconSource).toContain("navigator.sendBeacon(");
  expect(source).toContain("}, 5000);");
});
