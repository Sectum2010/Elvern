import test from "node:test";
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
  assert.equal(state.showMobileWarmupShell, true);
  assert.equal(state.showPlayerShell, true);
  assert.equal(state.playerClassName, "player player--warmup");
  assert.equal(state.videoControlsEnabled, true);
});

test("route2 session without a ready source keeps the preparing placeholder visible", () => {
  const state = resolveBrowserPlaybackPlayerViewState({
    activePlaybackMode: "lite",
    iosMobile: false,
    mobileFrozenFrameUrl: "",
    mobilePlayerCanPlay: false,
    mobileSession: buildRoute2Session({ attach_ready: true }),
    optimizedPlaybackPending: false,
    streamSource: null,
  });

  assert.equal(state.showPlayerShell, false);
  assert.equal(state.showMobilePreparingPlaceholder, true);
  assert.equal(state.browserPlaybackPreparing, true);
});
