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
  if (vi.isFakeTimers()) {
    vi.setSystemTime(vi.getRealSystemTime());
  }
  vi.useRealTimers();
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
    client_attach_revision: 1,
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
    readyState: 4,
    networkState: 2,
    buffered: {
      length: 1,
      start: () => 0,
      end: () => 30,
    },
    play: vi.fn(),
    pause: vi.fn(),
    removeAttribute: vi.fn(),
    load: vi.fn(),
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
    refs,
    getApi: () => latestApi,
    video,
  };
}

function setVideoBuffered(video, ranges) {
  video.buffered = {
    length: ranges.length,
    start: (index) => ranges[index][0],
    end: (index) => ranges[index][1],
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

test("audio switch promotion force-attaches and waits for loaded source before ack", async () => {
  const initialPayload = makeRoute2Payload({
    attach_ready: true,
    active_epoch_id: "epoch-a",
    active_manifest_url: "/api/browser-playback/epochs/epoch-a/index.m3u8",
    attach_revision: 1,
    client_attach_revision: 1,
    selected_audio_stream_index: 1,
    active_audio_stream_index: 1,
  });
  const pendingPayload = makeRoute2Payload({
    attach_ready: true,
    active_epoch_id: "epoch-a",
    active_manifest_url: "/api/browser-playback/epochs/epoch-a/index.m3u8",
    attach_revision: 1,
    client_attach_revision: 1,
    selected_audio_stream_index: 5,
    active_audio_stream_index: 1,
    pending_audio_stream_index: 5,
    audio_switch_state: "preparing",
  });
  const promotedPayload = makeRoute2Payload({
    attach_ready: true,
    active_epoch_id: "epoch-b",
    active_manifest_url: "/api/browser-playback/epochs/epoch-b/index.m3u8",
    attach_revision: 2,
    client_attach_revision: 1,
    selected_audio_stream_index: 5,
    active_audio_stream_index: 5,
    pending_audio_stream_index: null,
    audio_switch_state: "active",
  });
  const acknowledgedPayload = makeRoute2Payload({
    ...promotedPayload,
    client_attach_revision: 2,
  });
  postOptimizedPlaybackHeartbeat.mockResolvedValue(acknowledgedPayload);

  const { callbacks, getApi, refs, video } = renderOptimizedPlaybackHarness();

  await act(async () => {
    getApi().syncMobilePlaybackState(initialPayload);
    getApi().maybeAttachRoute2Authority(initialPayload, { autoplay: true });
  });

  expect(refs.attachedOptimizedManifestUrlRef.current).toContain("epoch-a");
  expect(callbacks.setStreamSource).toHaveBeenLastCalledWith(expect.any(Function));

  await act(async () => {
    getApi().syncMobilePlaybackState(pendingPayload);
    getApi().syncMobilePlaybackState(promotedPayload);
    getApi().maybeAttachRoute2Authority(promotedPayload, { autoplay: true });
  });

  expect(callbacks.clearPlayerBinding).toHaveBeenCalled();
  expect(video.pause).toHaveBeenCalled();
  expect(refs.attachedOptimizedManifestUrlRef.current).toBe(
    "/api/browser-playback/epochs/epoch-b/index.m3u8?attach_revision=2",
  );

  await act(async () => {
    getApi().maybeAcknowledgeRoute2Attachment({ playing: true, force: true });
    await Promise.resolve();
  });

  expect(postOptimizedPlaybackHeartbeat).not.toHaveBeenCalled();

  await act(async () => {
    getApi().maybeAcknowledgeRoute2Attachment({
      playing: true,
      force: true,
      loadedEventName: "loadedmetadata",
    });
    await Promise.resolve();
  });

  expect(postOptimizedPlaybackHeartbeat).toHaveBeenCalledWith(expect.objectContaining({
    data: expect.objectContaining({
      client_attach_revision: 2,
    }),
  }));
});

test("normal Route2 window slide does not trigger audio switch force reattach", async () => {
  const initialPayload = makeRoute2Payload({
    attach_ready: true,
    active_epoch_id: "epoch-a",
    active_manifest_url: "/api/browser-playback/epochs/epoch-a/index.m3u8",
    attach_revision: 1,
    client_attach_revision: 1,
    active_window_end_seconds: 15,
  });
  const windowSlidePayload = makeRoute2Payload({
    attach_ready: true,
    active_epoch_id: "epoch-a",
    active_manifest_url: "/api/browser-playback/epochs/epoch-a/index.m3u8",
    attach_revision: 1,
    client_attach_revision: 1,
    active_window_end_seconds: 24,
  });

  const { callbacks, getApi } = renderOptimizedPlaybackHarness();

  await act(async () => {
    getApi().syncMobilePlaybackState(initialPayload);
    getApi().maybeAttachRoute2Authority(initialPayload, { autoplay: true });
  });
  callbacks.clearPlayerBinding.mockClear();

  await act(async () => {
    getApi().syncMobilePlaybackState(windowSlidePayload);
    getApi().maybeAttachRoute2Authority(windowSlidePayload, { autoplay: true });
  });

  expect(callbacks.clearPlayerBinding).not.toHaveBeenCalled();
});

test("backend low-water does not start visible recovery while client buffer is playable", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  try {
    const initialPayload = makeRoute2Payload({
      attach_ready: true,
      state: "ready",
      stalled_recovery_needed: false,
      ahead_runway_seconds: 0,
    });
    const lowWaterPayload = makeRoute2Payload({
      attach_ready: true,
      state: "ready",
      stalled_recovery_needed: true,
      ahead_runway_seconds: 0,
    });
    postOptimizedPlaybackHeartbeat
      .mockResolvedValueOnce(initialPayload)
      .mockResolvedValueOnce(lowWaterPayload);

    const { callbacks, getApi, video } = renderOptimizedPlaybackHarness();

    await act(async () => {
      getApi().syncMobilePlaybackState(initialPayload);
      getApi().mobilePlayerCanPlayRef.current = true;
    });

    video.currentTime = 10;
    setVideoBuffered(video, [[0, 32]]);

    await act(async () => {
      await getApi().postMobileRuntimeHeartbeat({ playing: true, force: true });
    });

    vi.setSystemTime(3500);
    video.currentTime = 13;
    setVideoBuffered(video, [[0, 36]]);

    await act(async () => {
      await getApi().postMobileRuntimeHeartbeat({ playing: true, force: true });
    });

    expect(postOptimizedPlaybackHeartbeat).toHaveBeenCalledTimes(2);
    expect(callbacks.setOptimizedPlaybackPending).not.toHaveBeenCalledWith(true);
    expect(callbacks.setSeekNotice).not.toHaveBeenCalledWith(expect.stringContaining("Rebuffering"));
    expect(getApi().mobileLifecycleStateRef.current).toBe("attached");
  } finally {
    vi.setSystemTime(vi.getRealSystemTime());
    vi.useRealTimers();
  }
});
