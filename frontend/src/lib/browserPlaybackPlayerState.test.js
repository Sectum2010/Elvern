import { test } from "vitest";
import assert from "node:assert/strict";

import { resolveBrowserPlaybackPlayerViewState } from "./browserPlaybackPlayerState.js";

function buildRoute2Session(overrides = {}) {
  return {
    engine_mode: "route2",
    attach_ready: true,
    playback_mode: "lite",
    ...overrides,
  };
}

test("non-iPhone route2 source stays renderable even before mobile can-play flips", () => {
  const state = resolveBrowserPlaybackPlayerViewState({
    activePlaybackMode: "lite",
    iosMobile: false,
    mobileFrozenFrameUrl: "",
    mobilePlayerCanPlay: false,
    mobileSession: buildRoute2Session(),
    optimizedPlaybackPending: false,
    streamSource: { mode: "hls", url: "blob:test" },
  });

  assert.equal(state.showInlinePlayer, true);
  assert.equal(state.showMobileWarmupShell, false);
  assert.equal(state.showPlayerShell, true);
  assert.equal(state.playerClassName, "player");
  assert.equal(state.videoControlsEnabled, true);
  assert.equal(state.browserPlaybackPreparing, false);
});

test("iPhone route2 source keeps the warmup shell until mobile can-play is confirmed", () => {
  const state = resolveBrowserPlaybackPlayerViewState({
    activePlaybackMode: "lite",
    iosMobile: true,
    mobileFrozenFrameUrl: "",
    mobilePlayerCanPlay: false,
    mobileSession: buildRoute2Session(),
    optimizedPlaybackPending: false,
    streamSource: { mode: "hls", url: "blob:test" },
  });

  assert.equal(state.showInlinePlayer, false);
  assert.equal(state.showMobilePrewarmCard, true);
  assert.equal(state.showMobilePreparingPlaceholder, false);
  assert.equal(state.showMobileWarmupShell, true);
  assert.equal(state.showPlayerShell, true);
  assert.equal(state.playerClassName, "player player--warmup");
  assert.equal(state.videoControlsEnabled, false);
});

test("route2 session without a ready source uses the player prewarm card instead of the outer placeholder", () => {
  const state = resolveBrowserPlaybackPlayerViewState({
    activePlaybackMode: "lite",
    iosMobile: false,
    mobileFrozenFrameUrl: "",
    mobilePlayerCanPlay: false,
    mobileSession: buildRoute2Session({ attach_ready: true }),
    optimizedPlaybackPending: false,
    streamSource: null,
  });

  assert.equal(state.showPlayerShell, true);
  assert.equal(state.showMobilePrewarmCard, true);
  assert.equal(state.showMobilePreparingPlaceholder, false);
  assert.equal(state.browserPlaybackPreparing, true);
});

test("browser playback pending before a session uses the player prewarm card", () => {
  const state = resolveBrowserPlaybackPlayerViewState({
    activePlaybackMode: "lite",
    iosMobile: false,
    mobileFrozenFrameUrl: "",
    mobilePlayerCanPlay: false,
    mobileSession: null,
    optimizedPlaybackPending: true,
    streamSource: null,
  });

  assert.equal(state.showPlayerShell, true);
  assert.equal(state.showMobilePrewarmCard, true);
  assert.equal(state.showMobilePreparingPlaceholder, false);
  assert.equal(state.browserPlaybackPreparing, true);
});

test("iPhone frozen retarget warmup keeps the shell without the generic prewarm card", () => {
  const state = resolveBrowserPlaybackPlayerViewState({
    activePlaybackMode: "lite",
    iosMobile: true,
    mobileFrozenFrameUrl: "blob:frozen-frame",
    mobilePlayerCanPlay: false,
    mobileSession: buildRoute2Session(),
    optimizedPlaybackPending: false,
    streamSource: { mode: "hls", url: "blob:test" },
  });

  assert.equal(state.showMobileWarmupShell, true);
  assert.equal(state.showMobilePrewarmCard, false);
  assert.equal(state.showPlayerShell, true);
});
