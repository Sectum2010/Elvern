import assert from "node:assert/strict";
import { describe, test } from "vitest";

import {
  ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS,
  ELVERN_OVERLAY_LAYOUT_VARIANTS,
  describeReattachAutoplayPrompt,
  formatPlaybackDuration,
  formatPlaybackTime,
  isAutoplayBlockedError,
  resolveOverlayLayoutCapabilities,
  resolveOverlayLayoutVariant,
  shouldOverlayBeVisible,
} from "./elvernOverlayLayout.js";

describe("formatPlaybackTime", () => {
  test("renders 0 as 0:00 instead of 'Unknown length'", () => {
    assert.equal(formatPlaybackTime(0), "0:00");
  });

  test("renders null/undefined/NaN as 0:00", () => {
    assert.equal(formatPlaybackTime(null), "0:00");
    assert.equal(formatPlaybackTime(undefined), "0:00");
    assert.equal(formatPlaybackTime(Number.NaN), "0:00");
  });

  test("renders sub-minute values", () => {
    assert.equal(formatPlaybackTime(7), "0:07");
  });

  test("renders multi-minute values without an hour segment", () => {
    assert.equal(formatPlaybackTime(125), "2:05");
  });

  test("renders multi-hour values with the hour segment", () => {
    assert.equal(formatPlaybackTime(3600 + 25 * 60 + 9), "1:25:09");
  });

  test("clamps negative input to 0:00", () => {
    assert.equal(formatPlaybackTime(-5), "0:00");
  });
});

describe("formatPlaybackDuration", () => {
  test("renders 0 as '--:--' to mark unknown duration", () => {
    assert.equal(formatPlaybackDuration(0), "--:--");
  });

  test("renders invalid input as '--:--'", () => {
    assert.equal(formatPlaybackDuration(null), "--:--");
    assert.equal(formatPlaybackDuration(undefined), "--:--");
    assert.equal(formatPlaybackDuration(Number.NaN), "--:--");
    assert.equal(formatPlaybackDuration(-1), "--:--");
  });

  test("renders durations longer than an hour with the hour segment", () => {
    assert.equal(formatPlaybackDuration(2 * 3600 + 22 * 60 + 40), "2:22:40");
  });
});

describe("resolveOverlayLayoutVariant", () => {
  test("phone-class strings map to PHONE", () => {
    assert.equal(resolveOverlayLayoutVariant("phone"), ELVERN_OVERLAY_LAYOUT_VARIANTS.PHONE);
    assert.equal(resolveOverlayLayoutVariant("iphone"), ELVERN_OVERLAY_LAYOUT_VARIANTS.PHONE);
  });

  test("tablet-class strings map to TABLET", () => {
    assert.equal(resolveOverlayLayoutVariant("tablet"), ELVERN_OVERLAY_LAYOUT_VARIANTS.TABLET);
    assert.equal(resolveOverlayLayoutVariant("iPad"), ELVERN_OVERLAY_LAYOUT_VARIANTS.TABLET);
  });

  test("desktop and laptop map to DESKTOP", () => {
    assert.equal(resolveOverlayLayoutVariant("desktop"), ELVERN_OVERLAY_LAYOUT_VARIANTS.DESKTOP);
    assert.equal(resolveOverlayLayoutVariant("laptop"), ELVERN_OVERLAY_LAYOUT_VARIANTS.DESKTOP);
  });

  test("unknown values fall back to DESKTOP", () => {
    assert.equal(resolveOverlayLayoutVariant("unknown"), ELVERN_OVERLAY_LAYOUT_VARIANTS.DESKTOP);
    assert.equal(resolveOverlayLayoutVariant(null), ELVERN_OVERLAY_LAYOUT_VARIANTS.DESKTOP);
  });
});

describe("resolveOverlayLayoutCapabilities", () => {
  test("phone hides inline volume/speed/captions/audio/pip and uses a More menu", () => {
    const capabilities = resolveOverlayLayoutCapabilities("phone");
    assert.equal(capabilities.variant, "phone");
    assert.equal(capabilities.showInlineVolumeSlider, false);
    assert.equal(capabilities.showInlineMuteToggle, false);
    assert.equal(capabilities.showInlineSpeed, false);
    assert.equal(capabilities.showInlineCaptions, false);
    assert.equal(capabilities.showInlineAudio, false);
    assert.equal(capabilities.showInlinePip, false);
    assert.equal(capabilities.useMoreMenu, true);
    assert.equal(capabilities.compactCenterHint, true);
  });

  test("tablet shows mute + speed + captions inline but folds PiP/audio into More", () => {
    const capabilities = resolveOverlayLayoutCapabilities("tablet");
    assert.equal(capabilities.showInlineVolumeSlider, false);
    assert.equal(capabilities.showInlineMuteToggle, true);
    assert.equal(capabilities.showInlineSpeed, true);
    assert.equal(capabilities.showInlineCaptions, true);
    assert.equal(capabilities.showInlineAudio, false);
    assert.equal(capabilities.showInlinePip, false);
    assert.equal(capabilities.useMoreMenu, true);
  });

  test("desktop renders the full inline control row without a More menu", () => {
    const capabilities = resolveOverlayLayoutCapabilities("desktop");
    assert.equal(capabilities.showInlineVolumeSlider, true);
    assert.equal(capabilities.showInlineMuteToggle, true);
    assert.equal(capabilities.showInlineSpeed, true);
    assert.equal(capabilities.showInlineCaptions, true);
    assert.equal(capabilities.showInlineAudio, true);
    assert.equal(capabilities.showInlinePip, true);
    assert.equal(capabilities.useMoreMenu, false);
  });
});

describe("shouldOverlayBeVisible", () => {
  test("paused video keeps overlay visible regardless of timer", () => {
    assert.equal(shouldOverlayBeVisible({
      isPlaying: false,
      lastInteractionAtMs: 0,
      nowMs: 999_999,
    }), true);
  });

  test("playing + interaction is recent → visible", () => {
    assert.equal(shouldOverlayBeVisible({
      isPlaying: true,
      lastInteractionAtMs: 1000,
      nowMs: 1500,
    }), true);
  });

  test("playing + idle past delay → hidden", () => {
    assert.equal(shouldOverlayBeVisible({
      isPlaying: true,
      lastInteractionAtMs: 0,
      nowMs: ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS + 1,
    }), false);
  });

  test("preparing keeps overlay visible even after idle delay", () => {
    assert.equal(shouldOverlayBeVisible({
      isPlaying: true,
      preparing: true,
      lastInteractionAtMs: 0,
      nowMs: 999_999,
    }), true);
  });

  test("dragging timeline keeps overlay visible", () => {
    assert.equal(shouldOverlayBeVisible({
      isPlaying: true,
      isDraggingTimeline: true,
      lastInteractionAtMs: 0,
      nowMs: 999_999,
    }), true);
  });

  test("any open menu keeps overlay visible", () => {
    assert.equal(shouldOverlayBeVisible({
      isPlaying: true,
      anyMenuOpen: true,
      lastInteractionAtMs: 0,
      nowMs: 999_999,
    }), true);
  });

  test("focused controls keep overlay visible", () => {
    assert.equal(shouldOverlayBeVisible({
      isPlaying: true,
      controlsFocused: true,
      lastInteractionAtMs: 0,
      nowMs: 999_999,
    }), true);
  });

  test("error state keeps overlay visible", () => {
    assert.equal(shouldOverlayBeVisible({
      isPlaying: true,
      hasError: true,
      lastInteractionAtMs: 0,
      nowMs: 999_999,
    }), true);
  });

  test("pointer being inside the player is NOT a permanent reason to stay visible", () => {
    // Phase 1.1 regression guard: prior code used isPointerInside as a visibility predicate
    // and overlay never auto-hid while the cursor sat over the video. The pure helper
    // does not accept any pointer-inside flag at all.
    assert.equal(shouldOverlayBeVisible({
      isPlaying: true,
      lastInteractionAtMs: 0,
      nowMs: ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS + 1,
    }), false);
  });
});

describe("isAutoplayBlockedError", () => {
  test("recognises Safari NotAllowedError after a window-slide reattach", () => {
    assert.equal(isAutoplayBlockedError({ name: "NotAllowedError", message: "play() failed" }), true);
  });

  test("recognises Chrome 'user didn't interact' message", () => {
    assert.equal(
      isAutoplayBlockedError({ name: "Error", message: "play() failed because the user didn't interact with the document first." }),
      true,
    );
  });

  test("returns false for unrelated playback failures", () => {
    assert.equal(isAutoplayBlockedError({ name: "MediaError", message: "Network error" }), false);
    assert.equal(isAutoplayBlockedError(null), false);
    assert.equal(isAutoplayBlockedError(undefined), false);
  });
});

describe("describeReattachAutoplayPrompt", () => {
  test("returns null when nothing was blocked", () => {
    assert.equal(
      describeReattachAutoplayPrompt({ isAutoplayBlocked: false, reattachReason: "native_hls_window_slide" }),
      null,
    );
  });

  test("uses the resume-aware wording when reattach was a window slide", () => {
    assert.equal(
      describeReattachAutoplayPrompt({ isAutoplayBlocked: true, reattachReason: "native_hls_window_anchor_drift" }),
      "Tap play to resume from where you were.",
    );
  });

  test("uses the generic prompt for non-window reattaches", () => {
    assert.equal(
      describeReattachAutoplayPrompt({ isAutoplayBlocked: true, reattachReason: "" }),
      "Tap play to resume.",
    );
  });
});
