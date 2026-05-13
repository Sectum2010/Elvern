import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ElvernPlayerOverlay from "./ElvernPlayerOverlay.jsx";
import { ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS } from "../../lib/elvernOverlayLayout.js";

function renderOverlay({
  cinemaModeActive = false,
  deviceClass = "desktop",
  onToggleFullscreen = null,
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

  const renderProps = (overrides = {}) => ({
    cinemaModeActive,
    durationSeconds: 600,
    deviceClass,
    onSeekCommit: () => {},
    onToggleFullscreen,
    shellRef: { current: shell },
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

  test("phone layout uses phone class, More menu, and no inline volume controls", () => {
    const { getMoreButton, queryByLabelText, root } = renderOverlay({ deviceClass: "phone" });

    expect(root).toHaveClass("elvern-overlay--variant-phone");
    expect(root).toHaveClass("elvern-overlay--phone");
    expect(getMoreButton()).not.toBeNull();
    expect(queryByLabelText("Volume")).toBeNull();
    expect(queryByLabelText("Mute")).toBeNull();
  });

  test("opening More then tapping player surface closes More without toggling playback", () => {
    const { getMoreButton, pauseMock, queryByRole, setPlaying, tapSurface } = renderOverlay({ deviceClass: "phone" });

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

  test("opening More then tapping outside the player closes More", () => {
    const { getMoreButton, queryByRole } = renderOverlay({ deviceClass: "phone" });

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
    const { getMoreButton, queryByRole, root } = renderOverlay({ deviceClass: "phone" });

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
    const { getFullscreenButton, getMoreButton, queryByRole } = renderOverlay({ deviceClass: "phone", onToggleFullscreen });

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
    const { getMoreButton, queryByRole, rerenderOverlay } = renderOverlay({ deviceClass: "phone" });

    act(() => {
      fireEvent.click(getMoreButton());
    });
    expect(queryByRole("menu")).not.toBeNull();

    act(() => {
      rerenderOverlay({ cinemaModeActive: true });
    });

    expect(queryByRole("menu")).toBeNull();
  });

  test("timeline drag closes More menu", () => {
    const { getMoreButton, queryByRole } = renderOverlay({ deviceClass: "phone" });

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

  test("phone touch reveal stays visible for five seconds before hiding", () => {
    const { root, setPlaying, tapSurface } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(5001);
    });
    expect(root).toHaveClass("elvern-overlay--idle");

    act(() => {
      fireTouchPointerUp(tapSurface);
    });
    expect(root).toHaveClass("elvern-overlay--visible");

    act(() => {
      vi.advanceTimersByTime(4999);
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
      vi.advanceTimersByTime(5001);
      fireTouchPointerUp(tapSurface);
      firePointerOut(root, "touch");
    });

    expect(root).toHaveClass("elvern-overlay--visible");
  });

  test("second phone background tap hides controls before five seconds", () => {
    const { root, setPlaying, tapSurface } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(5001);
      fireTouchPointerUp(tapSurface);
    });
    expect(root).toHaveClass("elvern-overlay--visible");

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
      vi.advanceTimersByTime(5001);
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

  test("desktop mouse leave still hides controls", () => {
    const { root, setPlaying } = renderOverlay();

    setPlaying();
    act(() => {
      firePointerOut(root, "mouse");
    });

    expect(root).toHaveClass("elvern-overlay--idle");
  });
});
