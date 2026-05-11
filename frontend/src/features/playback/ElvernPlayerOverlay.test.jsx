import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ElvernPlayerOverlay from "./ElvernPlayerOverlay.jsx";
import { ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS } from "../../lib/elvernOverlayLayout.js";

function renderOverlay() {
  const video = document.createElement("video");
  const shell = document.createElement("div");
  let paused = true;
  let ended = false;

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

  const view = render(
    <ElvernPlayerOverlay
      durationSeconds={600}
      onSeekCommit={() => {}}
      shellRef={{ current: shell }}
      videoRef={{ current: video }}
    />,
  );

  return {
    ...view,
    muteButton: view.getByRole("button", { name: "Mute" }),
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
});
