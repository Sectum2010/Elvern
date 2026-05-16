import { act, cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, expect, test, vi } from "vitest";

import {
  createOptimizedPlaybackSession,
  fetchActiveOptimizedPlaybackSession,
  fetchOptimizedPlaybackSessionStatus,
  postOptimizedPlaybackHeartbeat,
  prepareOptimizedPlaybackSubtitleTrack,
  selectOptimizedPlaybackAudioTrack,
  seekOptimizedPlaybackSession,
} from "./browserSessionClient";
import { softResumeRequiresHardReattach, useOptimizedPlaybackSession } from "./useOptimizedPlaybackSession.js";

vi.mock("./browserSessionClient", () => ({
  createOptimizedPlaybackSession: vi.fn(),
  fetchActiveOptimizedPlaybackSession: vi.fn(),
  fetchOptimizedPlaybackSessionStatus: vi.fn(),
  postOptimizedPlaybackHeartbeat: vi.fn(),
  prepareOptimizedPlaybackSubtitleTrack: vi.fn(),
  selectOptimizedPlaybackAudioTrack: vi.fn(),
  seekOptimizedPlaybackSession: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeRoute2Payload(overrides = {}) {
  return {
    session_id: "session-audio",
    media_item_id: 77,
    engine_mode: "route2",
    playback_mode: "lite",
    profile: "mobile_1080p",
    state: "preparing",
    status_poll_seconds: 60,
    target_position_seconds: 12,
    committed_playhead_seconds: 12,
    actual_media_element_time_seconds: 12,
    ready_start_seconds: 0,
    ready_end_seconds: 20,
    active_epoch_id: "epoch-a",
    attach_revision: 1,
    active_manifest_url: "/api/browser-playback/sessions/session-audio/route2.m3u8",
    selected_audio_stream_index: 1,
    active_audio_stream_index: 1,
    pending_audio_stream_index: null,
    audio_switch_state: "active",
    ...overrides,
  };
}

function renderOptimizedPlaybackHarness({ onReady } = {}) {
  const video = {
    currentTime: 4,
    paused: false,
    play: vi.fn(),
    pause: vi.fn(),
  };
  const refs = {
    videoRef: { current: video },
    playbackFlowRef: { current: 1 },
    currentItemIdRef: { current: 77 },
    attachedOptimizedManifestUrlRef: { current: "" },
    browserStartPositionRef: { current: 12 },
    playbackModeIntentRef: { current: "lite" },
  };
  const callbacks = {
    clearPlayerBinding: vi.fn(),
    clearOptimizedPlaybackPending: vi.fn(),
    setPlaybackModeIntent: vi.fn((mode) => {
      refs.playbackModeIntentRef.current = mode;
    }),
    setStreamSource: vi.fn(),
    setPlaybackError: vi.fn(),
    setSeekNotice: vi.fn(),
    setPlaybackStatus: vi.fn(),
    setPlaybackPosition: vi.fn(),
    setOptimizedPlaybackPending: vi.fn(),
  };
  let latestApi = null;

  function Harness() {
    latestApi = useOptimizedPlaybackSession({
      itemId: 77,
      iosMobile: true,
      streamSource: "",
      optimizedPlaybackPending: false,
      browserPlaybackSessionRoot: "/api/browser-playback",
      browserPlaybackProfile: "mobile_1080p",
      browserPlaybackDeviceClass: "phone",
      ...refs,
      ...callbacks,
      hlsRef: { current: null },
      hlsEngineDiagnostics: null,
    });
    if (typeof onReady === "function") {
      onReady(latestApi);
    }
    return null;
  }

  const view = render(createElement(Harness));
  return {
    ...view,
    callbacks,
    getApi: () => latestApi,
    video,
  };
}

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

test("audio track selection immediately syncs returned backend pending snapshot", async () => {
  const initialPayload = makeRoute2Payload();
  const pendingPayload = makeRoute2Payload({
    selected_audio_stream_index: 2,
    active_audio_stream_index: 1,
    pending_audio_stream_index: 2,
    audio_switch_state: "preparing",
  });
  createOptimizedPlaybackSession.mockResolvedValue(initialPayload);
  selectOptimizedPlaybackAudioTrack.mockResolvedValue(pendingPayload);

  const { callbacks, getApi } = renderOptimizedPlaybackHarness();

  await act(async () => {
    await getApi().startMobileOptimizedPlayback({ autoplay: false, playbackMode: "lite" });
  });

  expect(getApi().mobileSession?.active_audio_stream_index).toBe(1);

  await act(async () => {
    await getApi().selectBrowserPlaybackAudioTrack({ index: 2, label: "French" });
  });

  expect(selectOptimizedPlaybackAudioTrack).toHaveBeenCalledWith(expect.objectContaining({
    selectedAudioStreamIndex: 2,
    playingBeforeSwitch: true,
  }));
  expect(getApi().mobileSession?.active_audio_stream_index).toBe(1);
  expect(getApi().mobileSession?.pending_audio_stream_index).toBe(2);
  expect(getApi().mobileSession?.audio_switch_state).toBe("preparing");
  expect(callbacks.clearPlayerBinding).not.toHaveBeenCalled();
  expect(callbacks.setStreamSource).not.toHaveBeenCalled();
});
