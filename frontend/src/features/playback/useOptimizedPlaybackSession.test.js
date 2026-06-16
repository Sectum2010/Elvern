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
import {
  AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE,
  AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS,
  buildAudioSwitchAttachDiagnostic,
  softResumeRequiresHardReattach,
  useOptimizedPlaybackSession,
} from "./useOptimizedPlaybackSession.js";

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

async function attachPromotedAudioSwitch(getApi, { initialPayload, pendingPayload, promotedPayload }) {
  await act(async () => {
    getApi().syncMobilePlaybackState(initialPayload);
    getApi().maybeAttachRoute2Authority(initialPayload, { autoplay: true });
  });

  await act(async () => {
    getApi().syncMobilePlaybackState(pendingPayload);
    getApi().syncMobilePlaybackState(promotedPayload);
    getApi().maybeAttachRoute2Authority(promotedPayload, { autoplay: true });
  });
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
  video.paused = true;
  video.readyState = 0;

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

test("audio switch attach timeout retries the same source once without another audio request", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
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

  const { callbacks, getApi, video } = renderOptimizedPlaybackHarness();
  await attachPromotedAudioSwitch(getApi, { initialPayload, pendingPayload, promotedPayload });
  video.paused = true;
  video.readyState = 0;
  callbacks.clearPlayerBinding.mockClear();
  callbacks.setPlaybackError.mockClear();
  selectOptimizedPlaybackAudioTrack.mockClear();

  await act(async () => {
    vi.advanceTimersByTime(AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS);
    await Promise.resolve();
  });

  expect(selectOptimizedPlaybackAudioTrack).not.toHaveBeenCalled();
  expect(callbacks.clearPlayerBinding).toHaveBeenCalledTimes(1);
  expect(getApi().audioSwitchAttachRef.current).toMatchObject({
    expectedAttachRevision: 2,
    expectedActiveEpochId: "epoch-b",
    phase: "source_set",
    retryCount: 1,
  });
  expect(callbacks.setPlaybackError).not.toHaveBeenCalledWith(AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE);
});

test("audio switch attach second timeout clears pending UI and ignores late loaded ack", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
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

  const { callbacks, getApi, video } = renderOptimizedPlaybackHarness();
  await attachPromotedAudioSwitch(getApi, { initialPayload, pendingPayload, promotedPayload });
  video.paused = true;
  video.readyState = 0;

  await act(async () => {
    vi.advanceTimersByTime(AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS);
    await Promise.resolve();
  });
  postOptimizedPlaybackHeartbeat.mockClear();

  await act(async () => {
    vi.advanceTimersByTime(AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS);
    await Promise.resolve();
  });

  expect(getApi().audioSwitchAttachRef.current).toMatchObject({
    expectedAttachRevision: 2,
    phase: "failed",
    retryCount: 1,
  });
  expect(getApi().mobilePlayerCanPlayRef.current).toBe(true);
  expect(callbacks.clearOptimizedPlaybackPending).toHaveBeenCalled();
  expect(callbacks.setPlaybackError).toHaveBeenCalledWith(AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE);

  await act(async () => {
    getApi().maybeAcknowledgeRoute2Attachment({
      playing: true,
      force: true,
      loadedEventName: "loadedmetadata",
    });
    await Promise.resolve();
  });

  expect(postOptimizedPlaybackHeartbeat).not.toHaveBeenCalled();
  expect(getApi().audioSwitchAttachRef.current.phase).toBe("failed");
});

test("audio switch attach infers success from playback evidence without loaded events", async () => {
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

  const { callbacks, getApi, video } = renderOptimizedPlaybackHarness();
  video.paused = false;
  video.readyState = 4;
  await attachPromotedAudioSwitch(getApi, { initialPayload, pendingPayload, promotedPayload });
  callbacks.setPlaybackError.mockClear();

  await act(async () => {
    getApi().maybeAcknowledgeRoute2Attachment({ playing: true, force: true });
    await Promise.resolve();
  });

  expect(postOptimizedPlaybackHeartbeat).toHaveBeenCalledWith(expect.objectContaining({
    data: expect.objectContaining({
      client_attach_revision: 2,
    }),
  }));
  expect(getApi().audioSwitchAttachRef.current).toMatchObject({
    expectedAttachRevision: 2,
    phase: "acked",
    successReason: "media_playback_observed",
  });
  expect(callbacks.clearOptimizedPlaybackPending).toHaveBeenCalled();
  expect(callbacks.setPlaybackError).toHaveBeenCalledWith("");
  expect(callbacks.setPlaybackError).not.toHaveBeenCalledWith(AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE);
});

test("audio switch timeout infers success from session playback progress before false failure", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
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
    client_ready_state: 4,
    client_time_advancing: true,
  });

  const { callbacks, getApi, video } = renderOptimizedPlaybackHarness();
  video.paused = true;
  video.readyState = 0;
  await attachPromotedAudioSwitch(getApi, { initialPayload, pendingPayload, promotedPayload });
  callbacks.clearPlayerBinding.mockClear();
  callbacks.setPlaybackError.mockClear();

  await act(async () => {
    vi.advanceTimersByTime(AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS);
    await Promise.resolve();
  });

  expect(callbacks.clearPlayerBinding).not.toHaveBeenCalled();
  expect(getApi().audioSwitchAttachRef.current).toMatchObject({
    expectedAttachRevision: 2,
    phase: "acked",
    successReason: "session_playback_progress",
  });
  expect(callbacks.setPlaybackError).not.toHaveBeenCalledWith(AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE);
});

test("stale audio switch attach failure clears when a later payload proves success", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
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

  const { callbacks, getApi, video } = renderOptimizedPlaybackHarness();
  video.paused = true;
  video.readyState = 0;
  await attachPromotedAudioSwitch(getApi, { initialPayload, pendingPayload, promotedPayload });

  await act(async () => {
    vi.advanceTimersByTime(AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS);
    await Promise.resolve();
  });
  await act(async () => {
    vi.advanceTimersByTime(AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS);
    await Promise.resolve();
  });
  expect(callbacks.setPlaybackError).toHaveBeenCalledWith(AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE);

  callbacks.setPlaybackError.mockClear();
  await act(async () => {
    getApi().syncMobilePlaybackState(acknowledgedPayload);
  });

  expect(getApi().audioSwitchAttachRef.current).toMatchObject({
    expectedAttachRevision: 2,
    phase: "acked",
    successReason: "client_attach_revision",
  });
  expect(callbacks.setPlaybackError).toHaveBeenCalledWith("");
  expect(callbacks.setPlaybackError).not.toHaveBeenCalledWith(AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE);
});

test("audio switch attach loaded before timeout keeps the ack path unchanged", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
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

  const { callbacks, getApi } = renderOptimizedPlaybackHarness();
  await attachPromotedAudioSwitch(getApi, { initialPayload, pendingPayload, promotedPayload });

  await act(async () => {
    getApi().maybeAcknowledgeRoute2Attachment({
      playing: true,
      force: true,
      loadedEventName: "loadeddata",
    });
    await Promise.resolve();
  });
  await act(async () => {
    vi.advanceTimersByTime(AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS + 1);
    await Promise.resolve();
  });

  expect(postOptimizedPlaybackHeartbeat).toHaveBeenCalledWith(expect.objectContaining({
    data: expect.objectContaining({
      client_attach_revision: 2,
    }),
  }));
  expect(callbacks.setPlaybackError).not.toHaveBeenCalledWith(AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE);
  expect(getApi().audioSwitchAttachRef.current.phase).not.toBe("failed");
});

test("audio switch attach diagnostics omit manifest URLs tokens and paths", () => {
  const diagnostic = buildAudioSwitchAttachDiagnostic("audio_switch_attach_timeout", {
    expectedAttachRevision: 2,
    expectedActiveEpochId: "epoch-b",
    expectedManifestUrl: "/api/browser-playback/epochs/epoch-b/index.m3u8?token=secret",
    manifestUrl: "/media/private/movie.m3u8",
    targetAudioStreamIndex: 5,
    phase: "source_set",
    retryCount: 1,
    sourceSetAtMs: 123,
    loadedAtMs: 0,
    token: "secret",
    path: "/media/private/movie.mkv",
  });

  expect(diagnostic).toEqual({
    event: "audio_switch_attach_timeout",
    expectedAttachRevision: 2,
    expectedActiveEpochId: "epoch-b",
    targetAudioStreamIndex: 5,
    phase: "source_set",
    retryCount: 1,
    sourceSetAtMs: true,
    loadedAtMs: false,
  });
  expect(JSON.stringify(diagnostic)).not.toMatch(/m3u8|token|secret|\/media/);
});

test("audio switch success inferred diagnostics keep only safe fields", () => {
  const diagnostic = buildAudioSwitchAttachDiagnostic("audio_switch_attach_success_inferred", {
    expectedAttachRevision: 2,
    expectedActiveEpochId: "epoch-b",
    targetAudioStreamIndex: 5,
    phase: "acked",
    retryCount: 1,
    clientAttachRevision: 1,
    currentAttachRevision: 2,
    successReason: "media_playback_observed",
    currentSrc: "/private/tokenized/stream.m3u8",
    streamUrl: "/api/browser-playback/secret-token/route2.m3u8",
    token: "secret",
    path: "/media/private/movie.mkv",
  });

  expect(diagnostic).toEqual({
    event: "audio_switch_attach_success_inferred",
    expectedAttachRevision: 2,
    expectedActiveEpochId: "epoch-b",
    targetAudioStreamIndex: 5,
    phase: "acked",
    retryCount: 1,
    clientAttachRevision: 1,
    currentAttachRevision: 2,
    successReason: "media_playback_observed",
  });
  expect(JSON.stringify(diagnostic)).not.toMatch(/m3u8|token|secret|\/media|currentSrc/);
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

test("automatic stalled recovery creates replacement from rolled-back stable target", async () => {
  const activePayload = makeRoute2Payload({
    session_id: "session-recovery",
    attach_ready: true,
    state: "ready",
    target_position_seconds: 160,
    committed_playhead_seconds: 160,
    actual_media_element_time_seconds: 160,
    ready_start_seconds: 0,
    ready_end_seconds: 220,
    active_epoch_id: "epoch-active",
    active_manifest_url: "/api/browser-playback/epochs/epoch-active/index.m3u8",
  });
  const recoveryPayload = makeRoute2Payload({
    session_id: "session-recovery-new",
    attach_ready: false,
    state: "preparing",
    target_position_seconds: 157.5,
    committed_playhead_seconds: 157.5,
    actual_media_element_time_seconds: 157.5,
    ready_start_seconds: 0,
    ready_end_seconds: 170,
    active_epoch_id: "epoch-recovery",
    active_manifest_url: "/api/browser-playback/epochs/epoch-recovery/index.m3u8",
  });
  createOptimizedPlaybackSession
    .mockResolvedValueOnce(activePayload)
    .mockResolvedValueOnce(recoveryPayload);
  fetchOptimizedPlaybackSessionStatus.mockRejectedValueOnce(new Error("network"));

  const { getApi, video } = renderOptimizedPlaybackHarness();

  await act(async () => {
    await getApi().startMobileOptimizedPlayback({ autoplay: false, playbackMode: "lite" });
  });

  getApi().mobileLastStablePositionRef.current = 160;
  getApi().committedPlayheadSecondsRef.current = 160;
  getApi().actualMediaElementTimeRef.current = 160;
  getApi().mobilePlayerCanPlayRef.current = false;
  video.currentTime = 163;

  await act(async () => {
    await getApi().recoverMobilePlaybackAfterResume("stalled");
  });

  expect(createOptimizedPlaybackSession).toHaveBeenNthCalledWith(2, expect.objectContaining({
    clientDeviceClass: "phone",
    engineMode: "route2",
    playbackMode: "lite",
    startPositionSeconds: 157.5,
  }));
  expect(getApi().committedPlayheadSecondsRef.current).toBe(157.5);
  expect(getApi().actualMediaElementTimeRef.current).toBe(157.5);
  expect(getApi().mobileLastStablePositionRef.current).toBe(157.5);
});

test("user initiated seek keeps explicit target without automatic recovery rollback", async () => {
  const activePayload = makeRoute2Payload({
    session_id: "session-seek",
    attach_ready: true,
    state: "ready",
    target_position_seconds: 160,
    committed_playhead_seconds: 160,
    actual_media_element_time_seconds: 160,
    ready_end_seconds: 220,
  });
  const seekingPayload = makeRoute2Payload({
    ...activePayload,
    state: "seeking",
    pending_target_seconds: 200,
    target_position_seconds: 200,
  });
  createOptimizedPlaybackSession.mockResolvedValueOnce(activePayload);
  seekOptimizedPlaybackSession.mockResolvedValueOnce(seekingPayload);

  const { getApi, video } = renderOptimizedPlaybackHarness();

  await act(async () => {
    await getApi().startMobileOptimizedPlayback({ autoplay: false, playbackMode: "lite" });
  });

  getApi().mobileLastStablePositionRef.current = 160;
  getApi().mobilePlayerCanPlayRef.current = false;
  video.currentTime = 163;

  await act(async () => {
    await getApi().retargetMobileOptimizedPlayback(200, { resumeAfterReady: true });
  });

  expect(seekOptimizedPlaybackSession).toHaveBeenCalledWith(expect.objectContaining({
    targetPositionSeconds: 200,
  }));
  expect(getApi().requestedTargetSecondsRef.current).toBe(200);
});
