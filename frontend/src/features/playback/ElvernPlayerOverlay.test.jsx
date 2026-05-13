import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ElvernPlayerOverlay from "./ElvernPlayerOverlay.jsx";
import { ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS } from "../../lib/elvernOverlayLayout.js";

function renderOverlay({ deviceClass = "desktop", onToggleFullscreen = null } = {}) {
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

  const view = render(
    <ElvernPlayerOverlay
      durationSeconds={600}
      deviceClass={deviceClass}
      onSeekCommit={() => {}}
      onToggleFullscreen={onToggleFullscreen}
      shellRef={{ current: shell }}
      videoRef={{ current: video }}
    />,
  );

  return {
    ...view,
    muteButton: view.queryByRole("button", { name: "Mute" }),
    fullscreenButton: view.queryByRole("button", { name: "Fullscreen" }),
    pauseMock,
    playMock,
    root: view.container.querySelector(".elvern-overlay"),
    surface: view.container.querySelector(".elvern-overlay__surface"),
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

  test("center surface focus does not pin the controls visible while playing", () => {
    const { root, setPlaying, surface } = renderOverlay();

    setPlaying();
    act(() => {
      surface.focus();
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
    const { playMock, surface } = renderOverlay();

    act(() => {
      fireEvent.keyDown(surface, { key: " ", code: "Space" });
      fireEvent.keyDown(surface, { key: " ", code: "Space", repeat: true });
    });

    expect(playMock).toHaveBeenCalledTimes(1);
  });

  test("space pauses while playing", () => {
    const { pauseMock, setPlaying, surface } = renderOverlay();

    setPlaying();
    act(() => {
      fireEvent.keyDown(surface, { key: " ", code: "Space" });
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

  test("phone touch reveal stays visible for five seconds before hiding", () => {
    const { root, setPlaying, surface } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(5001);
    });
    expect(root).toHaveClass("elvern-overlay--idle");

    act(() => {
      fireTouchPointerUp(surface);
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
    const { root, setPlaying, surface } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(5001);
      fireTouchPointerUp(surface);
      firePointerOut(root, "touch");
    });

    expect(root).toHaveClass("elvern-overlay--visible");
  });

  test("second phone background tap hides controls before five seconds", () => {
    const { root, setPlaying, surface } = renderOverlay({ deviceClass: "phone" });

    setPlaying();
    act(() => {
      vi.advanceTimersByTime(5001);
      fireTouchPointerUp(surface);
    });
    expect(root).toHaveClass("elvern-overlay--visible");

    act(() => {
      fireTouchPointerUp(surface);
    });
    expect(root).toHaveClass("elvern-overlay--idle");
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
