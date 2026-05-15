import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ElvernPlayerOverlay from "./ElvernPlayerOverlay.jsx";
import { ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS } from "../../lib/elvernOverlayLayout.js";

function renderOverlay({
  backendAudioTracks = [],
  backendSubtitleTracks = [],
  cinemaModeActive = false,
  deviceClass = "desktop",
  hlsRef = null,
  onBackendAudioTrackSelect = null,
  onBackendSubtitleTrackSelect = null,
  onClientPreparedThruChange = null,
  onToggleFullscreen = null,
  onVideoFitModeChange = null,
  preparing = false,
  preparingMessage = "",
  sessionPayload = null,
  setupVideo = null,
  trackRefreshKey = "",
  videoFitMode = "standard-fit",
  videoElementKey = 0,
} = {}) {
  const video = document.createElement("video");
  const shell = document.createElement("div");
  let paused = true;
  let ended = false;
  const playMock = vi.fn(() => {
    paused = false;
    ended = false;
    video.dispatchEvent(new Event("play"));
    return undefined;
  });
  const pauseMock = vi.fn(() => {
    paused = true;
    video.dispatchEvent(new Event("pause"));
  });

  Object.defineProperty(video, "paused", {
    configurable: true,
    get: () => paused,
  });
  Object.defineProperty(video, "ended", {
    configurable: true,
    get: () => ended,
  });
  Object.defineProperty(video, "currentTime", {
    configurable: true,
    get: () => 0,
  });
  Object.defineProperty(video, "volume", {
    configurable: true,
    get: () => 1,
    set: () => {},
  });
  Object.defineProperty(video, "muted", {
    configurable: true,
    get: () => false,
    set: () => {},
  });
  Object.defineProperty(video, "playbackRate", {
    configurable: true,
    get: () => 1,
    set: () => {},
  });
  Object.defineProperty(video, "play", {
    configurable: true,
    value: playMock,
  });
  Object.defineProperty(video, "pause", {
    configurable: true,
    value: pauseMock,
  });
  if (typeof setupVideo === "function") {
    setupVideo(video);
  }

  const renderProps = (overrides = {}) => ({
    cinemaModeActive,
    backendAudioTracks,
    backendSubtitleTracks,
    durationSeconds: 600,
    deviceClass,
    hlsRef,
    onBackendAudioTrackSelect,
    onBackendSubtitleTrackSelect,
    onSeekCommit: () => {},
    onClientPreparedThruChange,
    onToggleFullscreen,
    onVideoFitModeChange,
    preparing,
    preparingMessage,
    sessionPayload,
    shellRef: { current: shell },
    trackRefreshKey,
    videoFitMode,
    videoElementKey,
    videoRef: { current: video },
    ...overrides,
  });

  const view = render(
    <ElvernPlayerOverlay
      {...renderProps()}
    />,
  );

  const rerenderOverlay = (overrides = {}) => {
    view.rerender(
      <ElvernPlayerOverlay
        {...renderProps(overrides)}
      />,
    );
  };

  return {
    ...view,
    fullscreenButton: view.queryByRole("button", { name: cinemaModeActive ? "Exit fullscreen" : "Fullscreen" }),
    getFullscreenButton() {
      return view.queryByRole("button", { name: /fullscreen/i });
    },
    getMoreButton() {
      return view.queryByRole("button", { name: "More options" });
    },
    muteButton: view.queryByRole("button", { name: "Mute" }),
    pauseMock,
    playMock,
    rerenderOverlay,
    root: view.container.querySelector(".elvern-overlay"),
    centerTransport: view.container.querySelector(".elvern-overlay__center-transport"),
    tapSurface: view.container.querySelector(".elvern-overlay__tap-surface"),
    video,
    setPlaying() {
      paused = false;
      ended = false;
      act(() => {
        video.dispatchEvent(new Event("play"));
      });
    },
  };
}

/*
  Keep the shape above explicit. The player overlay is easy to regress because
  jsdom does not perform real mobile hit testing; these tests exercise state
  transitions while CSS guards cover stacking and phone anchoring.
*/

function firePointerEvent(element, type, pointerType) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "pointerType", {
    configurable: true,
    value: pointerType,
  });
  fireEvent(element, event);
}

function fireTouchPointerUp(element) {
  firePointerEvent(element, "pointerup", "touch");
}

function firePointerOut(element, pointerType) {
  firePointerEvent(element, "pointerout", pointerType);
}

function makeTrackList(tracks) {
  const listeners = new Map();
  const list = {
    length: tracks.length,
    addEventListener: vi.fn((eventName, listener) => {
      listeners.set(eventName, listener);
    }),
    removeEventListener: vi.fn(),
    dispatch(eventName) {
      listeners.get(eventName)?.();
    },
  };
  tracks.forEach((track, index) => {
    list[index] = track;
  });
  return list;
}

function makeTimeRanges(ranges) {
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

describe("ElvernPlayerOverlay controls visibility", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  test("playing overlay auto-hides after the idle delay", () => {
    const { root, setPlaying } = renderOverlay();

    expect(root).toHaveClass("elvern-overlay--visible");

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS + 1);
    });

    expect(root).toHaveClass("elvern-overlay--idle");
  });

  test("background tap surface focus does not pin the controls visible while playing", () => {
    const { root, setPlaying, tapSurface } = renderOverlay();

    setPlaying();
    act(() => {
      tapSurface.focus();
      vi.advanceTimersByTime(ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS + 1);
    });

    expect(root).toHaveClass("elvern-overlay--idle");
  });

  test("pointer-created control focus does not pin controls visible", () => {
    const { muteButton, root, setPlaying } = renderOverlay();

    setPlaying();
    act(() => {
      fireEvent.pointerDown(muteButton, { pointerType: "mouse" });
      muteButton.focus();
      vi.advanceTimersByTime(ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS + 1);
    });

    expect(root).toHaveClass("elvern-overlay--idle");
  });

  test("native video controls are disabled for the custom overlay", () => {
    const { video } = renderOverlay();

    video.controls = true;
    act(() => {
      video.dispatchEvent(new Event("playing"));
    });

    expect(video.controls).toBe(false);
    expect(video.hasAttribute("controls")).toBe(false);
  });

  test("space toggles playback instead of activating the focused fullscreen button", () => {
    const onToggleFullscreen = vi.fn();
    const { fullscreenButton, playMock } = renderOverlay({ onToggleFullscreen });

    act(() => {
      fullscreenButton.focus();
      fireEvent.keyDown(fullscreenButton, { key: " ", code: "Space" });
      fireEvent.keyUp(fullscreenButton, { key: " ", code: "Space" });
    });

    expect(playMock).toHaveBeenCalledTimes(1);
    expect(onToggleFullscreen).not.toHaveBeenCalled();
  });

  test("repeated space keydown does not toggle playback repeatedly", () => {
    const { centerTransport, playMock } = renderOverlay();

    act(() => {
      fireEvent.keyDown(centerTransport, { key: " ", code: "Space" });
      fireEvent.keyDown(centerTransport, { key: " ", code: "Space", repeat: true });
    });

    expect(playMock).toHaveBeenCalledTimes(1);
  });

  test("space pauses while playing", () => {
    const { centerTransport, pauseMock, setPlaying } = renderOverlay();

    setPlaying();
    act(() => {
      fireEvent.keyDown(centerTransport, { key: " ", code: "Space" });
    });

    expect(pauseMock).toHaveBeenCalledTimes(1);
  });

  test("fullscreen button still works through click", () => {
    const onToggleFullscreen = vi.fn();
    const { fullscreenButton } = renderOverlay({ onToggleFullscreen });

    act(() => {
      fireEvent.click(fullscreenButton);
    });

    expect(onToggleFullscreen).toHaveBeenCalledTimes(1);
  });

  test("phone inline minimal renders only center transport and inline maximize", () => {
    const onToggleFullscreen = vi.fn();
    const { centerTransport, getFullscreenButton, getMoreButton, queryByLabelText, root } = renderOverlay({ deviceClass: "phone", onToggleFullscreen });

    expect(root).toHaveClass("elvern-overlay--variant-phone");
    expect(root).toHaveClass("elvern-overlay--phone");
    expect(root).toHaveClass("elvern-overlay--phone-inline-minimal");
    expect(centerTransport).not.toBeNull();
    expect(getFullscreenButton()).not.toBeNull();
    expect(getFullscreenButton()).toHaveClass("elvern-overlay__inline-maximize");
    expect(document.querySelector(".elvern-timeline__track")).toBeNull();
    expect(document.querySelector(".elvern-overlay__bottom-bar")).toBeNull();
    expect(document.querySelector(".elvern-overlay__time-row")).toBeNull();
    expect(document.querySelector(".elvern-overlay__controls-row")).toBeNull();
    expect(getMoreButton()).toBeNull();
    expect(queryByLabelText("Volume")).toBeNull();
    expect(queryByLabelText("Mute")).toBeNull();
  });

  test("phone inline maximize uses the chrome-free dedicated button class", () => {
    const onToggleFullscreen = vi.fn();
    const { getFullscreenButton } = renderOverlay({ deviceClass: "phone", onToggleFullscreen });

    expect(getFullscreenButton()).toHaveClass("elvern-overlay__inline-maximize");
    expect(getFullscreenButton()).not.toHaveClass("elvern-overlay__icon-button");
    expect(getFullscreenButton().querySelector(".elvern-overlay__inline-maximize-icon")).not.toBeNull();
  });

  test("phone inline maximize calls fullscreen without toggling playback", () => {
    const onToggleFullscreen = vi.fn();
    const { getFullscreenButton, pauseMock, playMock, setPlaying } = renderOverlay({ deviceClass: "phone", onToggleFullscreen });

    setPlaying();
    act(() => {
      fireTouchPointerUp(getFullscreenButton());
      fireEvent.click(getFullscreenButton());
    });

    expect(onToggleFullscreen).toHaveBeenCalledTimes(1);
    expect(playMock).not.toHaveBeenCalled();
    expect(pauseMock).not.toHaveBeenCalled();
  });

  test("phone cinema renders full controls again", () => {
    const { getMoreButton, queryByLabelText, root } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    expect(root).toHaveClass("elvern-overlay--phone");
    expect(root).not.toHaveClass("elvern-overlay--phone-inline-minimal");
    expect(document.querySelector(".elvern-timeline__track")).not.toBeNull();
    expect(document.querySelector(".elvern-overlay__bottom-bar")).not.toBeNull();
    expect(document.querySelector(".elvern-overlay__time-row")).not.toBeNull();
    expect(document.querySelector(".elvern-overlay__controls-row")).not.toBeNull();
    expect(getMoreButton()).not.toBeNull();
    expect(queryByLabelText("Volume")).toBeNull();
    expect(queryByLabelText("Mute")).toBeNull();
  });

  test("phone cinema controls auto-hide after three seconds while playing", () => {
    const { root, setPlaying } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(2999);
    });
    expect(root).toHaveClass("elvern-overlay--visible");

    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(root).toHaveClass("elvern-overlay--idle");
  });

  test("preparing state does not render blocking player-shell prep text", () => {
    const { queryByText, container } = renderOverlay({
      cinemaModeActive: true,
      deviceClass: "phone",
      preparing: true,
      preparingMessage: "Elvern is still preparing enough playback",
    });

    expect(container.querySelector(".elvern-overlay__preparing")).toBeNull();
    expect(queryByText(/Elvern is preparing playback/i)).toBeNull();
    expect(queryByText(/still preparing enough playback/i)).toBeNull();
  });

  test("prepared-through callback reports contiguous client buffer instead of server cache ranges", () => {
    const onClientPreparedThruChange = vi.fn();
    const { video } = renderOverlay({
      onClientPreparedThruChange,
      sessionPayload: {
        cache_ranges: [[0, 300]],
        engine_mode: "route2",
        ready_start_seconds: 0,
        ready_end_seconds: 300,
      },
      setupVideo(videoElement) {
        Object.defineProperty(videoElement, "currentTime", {
          configurable: true,
          get: () => 12,
        });
        Object.defineProperty(videoElement, "buffered", {
          configurable: true,
          get: () => makeTimeRanges([[10, 35]]),
        });
      },
    });

    act(() => {
      video.dispatchEvent(new Event("progress"));
    });

    expect(onClientPreparedThruChange).toHaveBeenLastCalledWith(35);
    expect(onClientPreparedThruChange).not.toHaveBeenCalledWith(300);
  });

  test("fullscreen More menu exposes fit/fill toggle", () => {
    const onVideoFitModeChange = vi.fn();
    const { getMoreButton, queryByRole } = renderOverlay({
      cinemaModeActive: true,
      deviceClass: "phone",
      onVideoFitModeChange,
    });

    act(() => {
      fireEvent.click(getMoreButton());
    });

    const fillButton = queryByRole("menuitem", { name: /Fill screen/i });
    expect(fillButton).not.toBeNull();

    act(() => {
      fireEvent.click(fillButton);
    });

    expect(onVideoFitModeChange).toHaveBeenCalledWith("zoom-fill");
  });

  test("zoom-fill mode survives overlay rerender and offers fit action", () => {
    const onVideoFitModeChange = vi.fn();
    const { getMoreButton, queryByRole, rerenderOverlay } = renderOverlay({
      cinemaModeActive: true,
      deviceClass: "phone",
      onVideoFitModeChange,
      videoFitMode: "zoom-fill",
    });

    rerenderOverlay({
      cinemaModeActive: true,
      onVideoFitModeChange,
      videoElementKey: 1,
      videoFitMode: "zoom-fill",
    });
    act(() => {
      fireEvent.click(getMoreButton());
    });

    expect(queryByRole("menuitem", { name: /Fit screen/i })).not.toBeNull();
  });

  test("opening More then tapping player surface closes More without toggling playback", () => {
    const { getMoreButton, pauseMock, queryByRole, setPlaying, tapSurface } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    setPlaying();
    act(() => {
      fireEvent.click(getMoreButton());
    });
    expect(queryByRole("menu")).not.toBeNull();

    act(() => {
      fireTouchPointerUp(tapSurface);
      fireEvent.click(tapSurface);
    });

    expect(queryByRole("menu")).toBeNull();
    expect(pauseMock).not.toHaveBeenCalled();
  });

  test("phone cinema visible background tap hides controls without pausing", () => {
    const { pauseMock, root, setPlaying, tapSurface } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    setPlaying();
    expect(root).toHaveClass("elvern-overlay--visible");

    act(() => {
      fireTouchPointerUp(tapSurface);
    });

    expect(root).toHaveClass("elvern-overlay--idle");
    expect(pauseMock).not.toHaveBeenCalled();
  });

  test("phone cinema hidden background tap reveals controls without pausing", () => {
    const { pauseMock, root, setPlaying, tapSurface } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(3001);
    });
    expect(root).toHaveClass("elvern-overlay--idle");

    act(() => {
      fireTouchPointerUp(tapSurface);
    });

    expect(root).toHaveClass("elvern-overlay--visible");
    expect(pauseMock).not.toHaveBeenCalled();
  });

  test("phone cinema synthetic click after touch hide does not flash controls back on", () => {
    const { root, setPlaying, tapSurface } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    setPlaying();
    act(() => {
      fireTouchPointerUp(tapSurface);
      fireEvent.click(tapSurface);
    });

    expect(root).toHaveClass("elvern-overlay--idle");
  });

  test("phone cinema paused background tap can manually hide controls", () => {
    const { playMock, root, tapSurface } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    expect(root).toHaveClass("elvern-overlay--visible");

    act(() => {
      fireTouchPointerUp(tapSurface);
      fireEvent.click(tapSurface);
    });

    expect(root).toHaveClass("elvern-overlay--idle");
    expect(playMock).not.toHaveBeenCalled();
  });

  test("phone cinema center transport clears manual hide and toggles playback", () => {
    const { centerTransport, playMock, root, tapSurface } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    act(() => {
      fireTouchPointerUp(tapSurface);
    });
    expect(root).toHaveClass("elvern-overlay--idle");

    act(() => {
      fireTouchPointerUp(centerTransport);
      fireEvent.click(centerTransport);
    });

    expect(playMock).toHaveBeenCalledTimes(1);
    expect(root).toHaveClass("elvern-overlay--visible");
  });

  test("opening More then tapping outside the player closes More", () => {
    const { getMoreButton, queryByRole } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    act(() => {
      fireEvent.click(getMoreButton());
    });
    expect(queryByRole("menu")).not.toBeNull();

    act(() => {
      fireEvent.pointerDown(document.body);
    });

    expect(queryByRole("menu")).toBeNull();
  });

  test("Escape closes More menu", () => {
    const { getMoreButton, queryByRole, root } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    act(() => {
      fireEvent.click(getMoreButton());
    });
    expect(queryByRole("menu")).not.toBeNull();

    act(() => {
      fireEvent.keyDown(root, { key: "Escape" });
    });

    expect(queryByRole("menu")).toBeNull();
  });

  test("fullscreen remains accessible and closes More before toggling", () => {
    const onToggleFullscreen = vi.fn();
    const { getFullscreenButton, getMoreButton, queryByRole } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone", onToggleFullscreen });

    act(() => {
      fireEvent.click(getMoreButton());
    });
    expect(queryByRole("menu")).not.toBeNull();
    expect(getFullscreenButton()).not.toBeNull();

    act(() => {
      fireEvent.click(getFullscreenButton());
    });

    expect(onToggleFullscreen).toHaveBeenCalledTimes(1);
    expect(queryByRole("menu")).toBeNull();
  });

  test("cinema state changes close More menu", () => {
    const { getMoreButton, queryByRole, rerenderOverlay } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    act(() => {
      fireEvent.click(getMoreButton());
    });
    expect(queryByRole("menu")).not.toBeNull();

    act(() => {
      rerenderOverlay({ cinemaModeActive: false });
    });

    expect(queryByRole("menu")).toBeNull();
  });

  test("timeline drag closes More menu", () => {
    const { getMoreButton, queryByRole } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    act(() => {
      fireEvent.click(getMoreButton());
    });
    expect(queryByRole("menu")).not.toBeNull();

    act(() => {
      fireEvent.pointerDown(document.querySelector(".elvern-timeline__track"), {
        button: 0,
        clientX: 10,
        pointerId: 1,
      });
    });

    expect(queryByRole("menu")).toBeNull();
  });

  test("phone inline center transport auto-hides after three seconds while playing", () => {
    const onToggleFullscreen = vi.fn();
    const { getFullscreenButton, root, setPlaying, tapSurface } = renderOverlay({ deviceClass: "phone", onToggleFullscreen });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(3001);
    });
    expect(root).toHaveClass("elvern-overlay--idle");
    expect(getFullscreenButton()).toHaveClass("elvern-overlay__inline-maximize");

    act(() => {
      fireTouchPointerUp(tapSurface);
    });
    expect(root).toHaveClass("elvern-overlay--visible");

    act(() => {
      vi.advanceTimersByTime(2999);
    });
    expect(root).toHaveClass("elvern-overlay--visible");

    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(root).toHaveClass("elvern-overlay--idle");
  });

  test("touch pointer leave does not immediately hide phone controls", () => {
    const { root, setPlaying, tapSurface } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(3001);
      fireTouchPointerUp(tapSurface);
      firePointerOut(root, "touch");
    });

    expect(root).toHaveClass("elvern-overlay--visible");
  });

  test("second phone background tap hides controls before three seconds", () => {
    const onToggleFullscreen = vi.fn();
    const { getFullscreenButton, root, setPlaying, tapSurface } = renderOverlay({ deviceClass: "phone", onToggleFullscreen });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(3001);
      fireTouchPointerUp(tapSurface);
    });
    expect(root).toHaveClass("elvern-overlay--visible");
    expect(getFullscreenButton()).not.toBeNull();

    act(() => {
      fireTouchPointerUp(tapSurface);
    });
    expect(root).toHaveClass("elvern-overlay--idle");
  });

  test("phone center transport pauses while controls are visible", () => {
    const { centerTransport, pauseMock, setPlaying } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      fireTouchPointerUp(centerTransport);
      fireEvent.click(centerTransport);
    });

    expect(pauseMock).toHaveBeenCalledTimes(1);
  });

  test("phone center transport plays when paused", () => {
    const { centerTransport, playMock } = renderOverlay({ deviceClass: "phone" });

    act(() => {
      fireTouchPointerUp(centerTransport);
      fireEvent.click(centerTransport);
    });

    expect(playMock).toHaveBeenCalledTimes(1);
  });

  test("phone visible background tap hides controls without pausing", () => {
    const { pauseMock, root, setPlaying, tapSurface } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      fireTouchPointerUp(tapSurface);
    });

    expect(root).toHaveClass("elvern-overlay--idle");
    expect(pauseMock).not.toHaveBeenCalled();
  });

  test("phone hidden background tap reveals controls without pausing", () => {
    const { pauseMock, root, setPlaying, tapSurface } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(3001);
    });
    expect(root).toHaveClass("elvern-overlay--idle");

    act(() => {
      fireTouchPointerUp(tapSurface);
    });

    expect(root).toHaveClass("elvern-overlay--visible");
    expect(pauseMock).not.toHaveBeenCalled();
  });

  test("center transport click does not run the background tap handler", () => {
    const { centerTransport, pauseMock, root, setPlaying } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      fireTouchPointerUp(centerTransport);
      fireEvent.click(centerTransport);
    });

    expect(pauseMock).toHaveBeenCalledTimes(1);
    expect(root).toHaveClass("elvern-overlay--visible");
  });

  test("cinema phone center transport still toggles playback", () => {
    const { centerTransport, pauseMock, setPlaying } = renderOverlay({ cinemaModeActive: true, deviceClass: "phone" });

    setPlaying();
    act(() => {
      fireTouchPointerUp(centerTransport);
      fireEvent.click(centerTransport);
    });

    expect(pauseMock).toHaveBeenCalledTimes(1);
  });

  test("native subtitle tracks can be selected and turned off", async () => {
    const english = { kind: "subtitles", label: "English", language: "en", mode: "disabled" };
    const spanish = { kind: "captions", label: "Spanish", language: "es", mode: "disabled" };
    const { getByRole } = renderOverlay({
      cinemaModeActive: true,
      setupVideo(video) {
        Object.defineProperty(video, "textTracks", {
          configurable: true,
          value: makeTrackList([english, spanish]),
        });
      },
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });
    await act(async () => {
      fireEvent.click(getByRole("menuitemradio", { name: "Spanish" }));
      await Promise.resolve();
    });
    expect(english.mode).toBe("disabled");
    expect(spanish.mode).toBe("showing");

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });
    act(() => {
      fireEvent.click(getByRole("menuitemradio", { name: "Off" }));
    });
    expect(spanish.mode).toBe("disabled");
  });

  test("native audio tracks can be selected", async () => {
    const english = { label: "English", language: "en", enabled: true };
    const french = { label: "French", language: "fr", enabled: false };
    const { getByRole, getByText } = renderOverlay({
      cinemaModeActive: true,
      setupVideo(video) {
        Object.defineProperty(video, "audioTracks", {
          configurable: true,
          value: makeTrackList([english, french]),
        });
      },
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Audio track" }));
    });
    await act(async () => {
      fireEvent.click(getByRole("menuitemradio", { name: "French" }));
      await Promise.resolve();
    });

    expect(english.enabled).toBe(false);
    expect(french.enabled).toBe(true);
  });

  test("hls.js subtitle and audio track APIs are used when available", async () => {
    const hls = {
      audioTrack: 0,
      audioTracks: [{ name: "English" }, { name: "Director" }],
      off: vi.fn(),
      on: vi.fn(),
      subtitleDisplay: false,
      subtitleTrack: -1,
      subtitleTracks: [{ name: "English CC" }, { name: "French" }],
    };
    const { getByRole } = renderOverlay({
      cinemaModeActive: true,
      deviceClass: "phone",
      hlsRef: { current: hls },
      trackRefreshKey: "hls.js",
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });
    await act(async () => {
      fireEvent.click(getByRole("menuitemradio", { name: "French" }));
      await Promise.resolve();
    });
    expect(hls.subtitleTrack).toBe(1);
    expect(hls.subtitleDisplay).toBe(true);

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });
    act(() => {
      fireEvent.click(getByRole("menuitemradio", { name: "Off" }));
    });
    expect(hls.subtitleTrack).toBe(-1);
    expect(hls.subtitleDisplay).toBe(false);

    act(() => {
      fireEvent.click(getByRole("button", { name: "Audio track" }));
    });
    await act(async () => {
      fireEvent.click(getByRole("menuitemradio", { name: "Director" }));
      await Promise.resolve();
    });
    expect(hls.audioTrack).toBe(1);
  });

  test("backend-discovered tracks override browser tracks and hide commentary rows", async () => {
    const onBackendAudioTrackSelect = vi.fn();
    const { getByRole, getByText, queryByText } = renderOverlay({
      backendAudioTracks: [
        { index: 1, title: "English", codec: "aac", disposition_default: true },
        { index: 2, title: "French", codec: "ac3" },
        { index: 2, label: "Commentary (aac)", codec: "aac" },
      ],
      backendSubtitleTracks: [
        { index: 3, label: "English subtitles (subrip)", codec: "subrip", language: "en" },
        { index: 5, label: "Director commentary", codec: "subrip", language: "en" },
      ],
      cinemaModeActive: true,
      deviceClass: "phone",
      onBackendAudioTrackSelect,
      onToggleFullscreen: vi.fn(),
      setupVideo: (video) => {
        Object.defineProperty(video, "audioTracks", {
          configurable: true,
          value: [{ label: "Audio 1", language: "", enabled: true }],
        });
      },
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });
    expect(getByText("English subtitles")).toBeTruthy();
    expect(queryByText("Director commentary")).toBeNull();

    act(() => {
      fireEvent.click(getByRole("button", { name: "Audio track" }));
    });
    expect(getByText("French")).toBeTruthy();
    expect(queryByText("Commentary")).toBeNull();
    expect(queryByText("Audio 1")).toBeNull();

    await act(async () => {
      fireEvent.click(getByRole("menuitemradio", { name: "French" }));
      await Promise.resolve();
    });
    expect(onBackendAudioTrackSelect).toHaveBeenCalledWith(expect.objectContaining({
      index: 2,
      source: "backend",
      switchRequiresPreparation: true,
    }));
  });

  test("backend track labels preserve useful duplicate distinctions", () => {
    const { getByRole, getByText, queryByText } = renderOverlay({
      backendAudioTracks: [
        { index: 1, codec: "ac3", channels: 6, language: "eng", disposition_default: true },
        { index: 2, codec: "aac", channels: 2, language: "eng" },
        { index: 3, codec: "aac", channels: 2, language: "eng", title: "Audio Commentary" },
      ],
      backendSubtitleTracks: [
        { index: 4, codec: "subrip", language: "chi", label: "Chinese (Simplified / subrip)", text_based: true },
        { index: 5, codec: "ass", language: "chi", label: "Chinese", text_based: true },
        { index: 6, codec: "hdmv_pgs_subtitle", language: "chi", label: "Chinese", image_based: true },
        { index: 7, codec: "subrip", language: "eng", title: "Director Commentary", text_based: true },
      ],
      cinemaModeActive: true,
      deviceClass: "phone",
      onToggleFullscreen: vi.fn(),
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });
    expect(getByText("Chinese Simplified")).toBeTruthy();
    expect(getByText("Chinese · ASS")).toBeTruthy();
    expect(getByText("Chinese · PGS")).toBeTruthy();
    expect(queryByText("Director Commentary")).toBeNull();

    act(() => {
      fireEvent.click(getByRole("button", { name: "Audio track" }));
    });
    expect(getByText("English · AC3 · 5.1")).toBeTruthy();
    expect(getByText("English · AAC · 2ch")).toBeTruthy();
    expect(queryByText("Audio Commentary")).toBeNull();
  });

  test("backend audio snapshot state keeps exactly one selected row", () => {
    const { container, getByRole, getByText } = renderOverlay({
      backendAudioTracks: [
        { index: 1, title: "English", codec: "aac", disposition_default: true },
        { index: 2, title: "French", codec: "ac3", disposition_default: true },
        { index: 3, title: "Japanese", codec: "aac" },
      ],
      cinemaModeActive: true,
      deviceClass: "phone",
      onToggleFullscreen: vi.fn(),
      sessionPayload: {
        active_audio_stream_index: 2,
        audio_switch_state: "active",
        selected_audio_stream_index: 2,
      },
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Audio track" }));
    });

    const checkedRows = Array.from(container.querySelectorAll('[role="menuitemradio"][aria-checked="true"]'));
    expect(checkedRows).toHaveLength(1);
    expect(checkedRows[0].textContent).toContain("French");
  });

  test("clicking backend audio shows row pending until backend active state changes", async () => {
    let resolveSwitch;
    const onBackendAudioTrackSelect = vi.fn(() => new Promise((resolve) => {
      resolveSwitch = () => resolve({
        active_audio_stream_index: 1,
        pending_audio_stream_index: 2,
        audio_switch_state: "preparing",
      });
    }));
    const { getByRole, rerenderOverlay } = renderOverlay({
      backendAudioTracks: [
        { index: 1, title: "English", codec: "aac", disposition_default: true },
        { index: 2, title: "French", codec: "ac3" },
      ],
      cinemaModeActive: true,
      deviceClass: "phone",
      onBackendAudioTrackSelect,
      onToggleFullscreen: vi.fn(),
      sessionPayload: {
        active_audio_stream_index: 1,
        audio_switch_state: "active",
        selected_audio_stream_index: 1,
      },
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Audio track" }));
    });
    act(() => {
      fireEvent.click(getByRole("menuitemradio", { name: "French" }));
    });

    expect(onBackendAudioTrackSelect).toHaveBeenCalledWith(expect.objectContaining({ index: 2 }));
    expect(getByRole("menuitemradio", { name: "French" })).toHaveAttribute("aria-busy", "true");
    expect(getByRole("menuitemradio", { name: "English" })).toHaveAttribute("aria-checked", "true");
    expect(getByRole("menuitemradio", { name: "French" })).toHaveAttribute("aria-checked", "false");

    await act(async () => {
      resolveSwitch();
      await Promise.resolve();
    });
    rerenderOverlay({
      sessionPayload: {
        active_audio_stream_index: 1,
        pending_audio_stream_index: 2,
        audio_switch_state: "preparing",
        selected_audio_stream_index: 2,
      },
    });
    expect(getByRole("menuitemradio", { name: "French" })).toHaveAttribute("aria-busy", "true");
    expect(getByRole("menuitemradio", { name: "English" })).toHaveAttribute("aria-checked", "true");

    rerenderOverlay({
      sessionPayload: {
        active_audio_stream_index: 2,
        pending_audio_stream_index: null,
        audio_switch_state: "active",
        selected_audio_stream_index: 2,
      },
    });
    expect(getByRole("menuitemradio", { name: "French" })).toHaveAttribute("aria-checked", "true");
  });

  test("text backend subtitles are clickable while image subtitles are marked unsupported", async () => {
    let resolvePrepare;
    const delayedSubtitlePrepare = vi.fn(() => new Promise((resolve) => {
      resolvePrepare = () => resolve({
        label: "English SRT",
        vtt_url: "/api/browser-playback/sessions/demo/subtitles/3.vtt",
      });
    }));
    const { getByRole, getByText, queryByText } = renderOverlay({
      backendSubtitleTracks: [
        { index: 3, label: "English SRT", codec: "subrip", language: "en", text_based: true },
        {
          index: 4,
          label: "PGS English",
          codec: "hdmv_pgs_subtitle",
          image_based: true,
          track_source: "raw_probe_summary_json",
        },
      ],
      cinemaModeActive: true,
      deviceClass: "phone",
      onBackendSubtitleTrackSelect: delayedSubtitlePrepare,
      onToggleFullscreen: vi.fn(),
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });

    expect(getByText("PGS English")).toBeTruthy();
    expect(getByText("!")).toBeTruthy();
    expect(queryByText(/Image subtitles need future burn-in support/i)).toBeNull();

    act(() => {
      fireEvent.click(getByRole("menuitemradio", { name: "English SRT" }));
    });
    expect(delayedSubtitlePrepare).toHaveBeenCalledWith(expect.objectContaining({
      index: 3,
      source: "backend",
      textBased: true,
    }));
    expect(getByRole("menuitemradio", { name: "English SRT" })).toHaveAttribute("aria-busy", "true");

    await act(async () => {
      resolvePrepare();
      await Promise.resolve();
    });
  });

  test("fallback subtitle rows are not shown as confirmed burn-in warnings", () => {
    const { getByRole, getByText, queryByText } = renderOverlay({
      backendSubtitleTracks: [
        {
          index: 0,
          label: "Indexed SRT",
          codec: "subrip",
          text_based: true,
          browser_supported: false,
          track_source: "subtitle_table_fallback",
        },
      ],
      cinemaModeActive: true,
      deviceClass: "phone",
      onBackendSubtitleTrackSelect: vi.fn(),
      onToggleFullscreen: vi.fn(),
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });

    expect(getByText("Indexed SRT")).toBeTruthy();
    expect(queryByText("!")).toBeNull();
    expect(queryByText("Indexed SRT").closest("button")).toBeNull();
  });

  test("phone subtitle menu renders many backend tracks in a scrollable list", () => {
    const manySubtitles = Array.from({ length: 30 }, (_, index) => ({
      index: index + 3,
      label: `Subtitle ${index + 1}`,
      codec: index < 24 ? "subrip" : "hdmv_pgs_subtitle",
      text_based: index < 24,
      image_based: index >= 24,
    }));
    const { container, getByRole, getByText } = renderOverlay({
      backendSubtitleTracks: manySubtitles,
      cinemaModeActive: true,
      deviceClass: "phone",
      onBackendSubtitleTrackSelect: vi.fn(),
      onToggleFullscreen: vi.fn(),
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });

    expect(container.querySelector(".elvern-overlay__track-sheet")).toBeNull();
    expect(container.querySelector(".elvern-overlay__menu.elvern-overlay__track-menu")).toBeTruthy();
    expect(container.querySelectorAll(".elvern-overlay__track-menu-item").length).toBeGreaterThanOrEqual(31);
    expect(getByText("Subtitle 1")).toBeTruthy();
    expect(getByText("Subtitle 30 · PGS")).toBeTruthy();
  });

  test("phone rectangular track menu keeps touch scrolling away from the tap surface", () => {
    const { container, getByRole, root } = renderOverlay({
      backendSubtitleTracks: Array.from({ length: 30 }, (_, index) => ({
        index: index + 3,
        label: `Subtitle ${index + 1}`,
        codec: "subrip",
        text_based: true,
      })),
      cinemaModeActive: true,
      deviceClass: "phone",
      onBackendSubtitleTrackSelect: vi.fn(),
      onToggleFullscreen: vi.fn(),
    });

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });
    const menu = container.querySelector(".elvern-overlay__menu.elvern-overlay__track-menu");
    expect(menu).toBeTruthy();

    act(() => {
      fireEvent.touchMove(menu);
    });

    expect(root).toHaveClass("elvern-overlay--visible");

    const event = new Event("touchmove", { bubbles: true, cancelable: true });
    act(() => {
      menu.dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(false);
  });

  test("phone cinema places subtitle and audio icons before More when tracks exist", () => {
    const hls = {
      audioTrack: 0,
      audioTracks: [{ name: "English" }, { name: "Director" }],
      off: vi.fn(),
      on: vi.fn(),
      subtitleDisplay: false,
      subtitleTrack: -1,
      subtitleTracks: [{ name: "English CC" }],
    };
    const { container } = renderOverlay({
      cinemaModeActive: true,
      deviceClass: "phone",
      hlsRef: { current: hls },
      onToggleFullscreen: vi.fn(),
      onVideoFitModeChange: vi.fn(),
      trackRefreshKey: "hls.js",
    });

    const labels = Array.from(container.querySelectorAll(".elvern-overlay__controls-row button"))
      .map((button) => button.getAttribute("aria-label"))
      .filter(Boolean);

    expect(labels.indexOf("Subtitles")).toBeLessThan(labels.indexOf("Audio track"));
    expect(labels.indexOf("Audio track")).toBeLessThan(labels.indexOf("More options"));
    expect(labels.indexOf("More options")).toBeLessThan(labels.indexOf("Exit fullscreen"));
  });

  test("phone cinema keeps subtitle and audio icons visible with unavailable messages", () => {
    const { getByRole, queryByRole } = renderOverlay({
      cinemaModeActive: true,
      deviceClass: "phone",
      onToggleFullscreen: vi.fn(),
      onVideoFitModeChange: vi.fn(),
    });

    expect(queryByRole("button", { name: "Subtitles" })).not.toBeNull();
    expect(queryByRole("button", { name: "Audio track" })).not.toBeNull();

    act(() => {
      fireEvent.click(getByRole("button", { name: "Subtitles" }));
    });
    expect(getByRole("menuitem", { name: "No subtitle tracks found" })).toBeTruthy();

    act(() => {
      fireEvent.click(getByRole("button", { name: "Audio track" }));
    });
    expect(getByRole("menuitem", { name: "No alternate audio tracks found" })).toBeTruthy();
  });

  test("desktop mouse leave still hides controls", () => {
    const { root, setPlaying } = renderOverlay();

    setPlaying();
    act(() => {
      firePointerOut(root, "mouse");
    });

    expect(root).toHaveClass("elvern-overlay--idle");
  });
});
