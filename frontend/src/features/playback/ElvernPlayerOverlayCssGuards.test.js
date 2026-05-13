import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

const stylesPath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../styles.css",
);

function readStyles() {
  return fs.readFileSync(stylesPath, "utf8");
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function cssBlock(styles, selector) {
  const match = new RegExp(`(^|\\n)${escapeRegExp(selector)}\\s*\\{`).exec(styles);
  expect(match, `missing exact CSS selector: ${selector}`).not.toBeNull();
  const start = styles.indexOf("{", match.index);
  const end = styles.indexOf("}", start);
  expect(start, `missing CSS block start: ${selector}`).toBeGreaterThanOrEqual(0);
  expect(end, `missing CSS block end: ${selector}`).toBeGreaterThan(start);
  return styles.slice(start + 1, end);
}

function cssBlockForSelectorListItem(styles, selector) {
  const index = styles.indexOf(selector);
  expect(index, `missing CSS selector list item: ${selector}`).toBeGreaterThanOrEqual(0);
  const start = styles.indexOf("{", index);
  const end = styles.indexOf("}", start);
  expect(start, `missing CSS block start after: ${selector}`).toBeGreaterThanOrEqual(0);
  expect(end, `missing CSS block end after: ${selector}`).toBeGreaterThan(start);
  return styles.slice(start + 1, end);
}

function numericZIndex(block) {
  const match = block.match(/z-index:\s*(\d+)/);
  expect(match, `missing numeric z-index in block: ${block}`).not.toBeNull();
  return Number.parseInt(match[1], 10);
}

describe("Elvern player mobile CSS guards", () => {
  test("phone inline player surface fills the aspect-ratio shell without percentage height", () => {
    const styles = readStyles();
    const block = cssBlock(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom:not(.player-shell--cinema-takeover) .player-fullscreen-surface",
    );

    expect(block).toContain("position: absolute");
    expect(block).toContain("inset: 0");
    expect(block).toContain("width: auto");
    expect(block).toContain("height: auto");
    expect(block).not.toContain("height: 100%");
  });

  test("real controls stack above the full-surface tap target", () => {
    const styles = readStyles();
    const tapSurface = cssBlock(styles, ".elvern-overlay__tap-surface");
    const centerTransport = cssBlock(styles, ".elvern-overlay__center-transport");
    const inlineMaximize = cssBlock(styles, ".elvern-overlay__inline-maximize");
    const bottomBar = cssBlock(styles, ".elvern-overlay__bottom-bar");
    const topBar = cssBlock(styles, ".elvern-overlay__top-bar");

    expect(tapSurface).toContain("position: absolute");
    expect(tapSurface).toContain("inset: 0");
    expect(centerTransport).toContain("position: absolute");
    expect(inlineMaximize).toContain("position: absolute");
    expect(bottomBar).toContain("position: relative");
    expect(topBar).toContain("position: relative");
    expect(numericZIndex(centerTransport)).toBeGreaterThan(numericZIndex(tapSurface));
    expect(numericZIndex(inlineMaximize)).toBeGreaterThan(numericZIndex(tapSurface));
    expect(numericZIndex(bottomBar)).toBeGreaterThan(numericZIndex(tapSurface));
    expect(numericZIndex(topBar)).toBeGreaterThan(numericZIndex(tapSurface));
  });

  test("phone bottom controls are explicitly anchored to the player bottom", () => {
    const styles = readStyles();
    const block = cssBlock(styles, ".elvern-overlay--phone .elvern-overlay__bottom-bar");

    expect(block).toContain("position: absolute");
    expect(block).toContain("left:");
    expect(block).toContain("right:");
    expect(block).toContain("bottom:");
  });

  test("phone inline minimal mode has no bottom-bar positioning dependency", () => {
    const styles = readStyles();
    const block = cssBlock(styles, ".elvern-overlay--phone-inline-minimal");

    expect(block).toContain("display: block");
    expect(styles).not.toContain(".elvern-overlay--phone-inline-minimal .elvern-overlay__bottom-bar");
    expect(styles).not.toContain(".elvern-overlay--phone-inline-minimal .elvern-timeline");
  });

  test("phone inline maximize is card-corner anchored above the tap surface", () => {
    const styles = readStyles();
    const block = cssBlock(styles, ".elvern-overlay__inline-maximize");

    expect(block).toContain("top: 0.3rem");
    expect(block).toContain("right: 0.3rem");
    expect(block).not.toContain("env(safe-area-inset-top");
    expect(block).not.toContain("env(safe-area-inset-right");
    expect(block).toContain("width: 44px");
    expect(block).toContain("height: 44px");
    expect(numericZIndex(block)).toBeGreaterThan(1);
  });

  test("phone inline maximize has invisible visual chrome", () => {
    const styles = readStyles();
    const block = cssBlock(styles, ".elvern-overlay__inline-maximize");

    expect(block).toContain("border: 0");
    expect(block).toContain("background: transparent");
    expect(block).not.toContain("backdrop-filter");
    expect(block).not.toContain("border-radius");
  });

  test("fit and fill modes control video object-fit", () => {
    const styles = readStyles();
    const fitBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-custom.player-shell--video-fit-fit .player",
    );
    const fillBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-custom.player-shell--video-fit-fill .player",
    );
    const phoneFillBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom.player-shell--video-fit-fill.player-shell--cinema-takeover .player",
    );

    expect(fitBlock).toContain("object-fit: contain");
    expect(fillBlock).toContain("object-fit: cover");
    expect(phoneFillBlock).toContain("object-fit: cover");
  });

  test("phone fullscreen bottom bar does not paint a panel over the movie", () => {
    const styles = readStyles();
    const block = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom.player-shell--cinema-takeover .elvern-overlay__bottom-bar",
    );

    expect(block).toContain("background: transparent");
    expect(block).toContain("backdrop-filter: none");
    expect(block).not.toContain("linear-gradient");
  });

  test("phone fullscreen shell locks touch panning", () => {
    const styles = readStyles();
    const shellBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom.player-shell--cinema-takeover",
    );
    const surfaceBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom.player-shell--cinema-takeover .player-fullscreen-surface",
    );
    const fillBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-custom.player-shell--video-fit-fill .player",
    );

    expect(shellBlock).toContain("position: fixed");
    expect(shellBlock).toContain("overflow: hidden");
    expect(shellBlock).toContain("overscroll-behavior: none");
    expect(shellBlock).toContain("touch-action: none");
    expect(surfaceBlock).toContain("touch-action: none");
    expect(fillBlock).toContain("object-fit: cover");
    expect(fillBlock).toContain("transform: none");
  });

  test("phone More menu is a popover, not a fullscreen-blocking fixed sheet", () => {
    const styles = readStyles();
    const block = cssBlock(styles, ".elvern-overlay--phone .elvern-overlay__menu--sheet");

    expect(block).toContain("position: absolute");
    expect(block).toContain("bottom: calc(100% + 0.45rem)");
    expect(block).not.toContain("position: fixed");
    expect(block).not.toContain("width: 100%");
  });

  test("overlay root and split tap/transport layers fill the player shell", () => {
    const styles = readStyles();
    const root = cssBlock(styles, ".elvern-overlay");
    const tapSurface = cssBlock(styles, ".elvern-overlay__tap-surface");
    const centerTransport = cssBlock(styles, ".elvern-overlay__center-transport");

    expect(root).toContain("position: absolute");
    expect(root).toContain("inset: 0");
    expect(root).toContain("width: 100%");
    expect(root).toContain("height: 100%");
    expect(tapSurface).toContain("position: absolute");
    expect(tapSurface).toContain("inset: 0");
    expect(tapSurface).toContain("width: 100%");
    expect(tapSurface).toContain("height: 100%");
    expect(centerTransport).toContain("position: absolute");
    expect(centerTransport).toContain("left: 50%");
    expect(centerTransport).toContain("top: 50%");
    expect(centerTransport).toContain("transform: translate(-50%, -50%)");
  });

  test("phone transport no longer depends on the old surface-hint hit target", () => {
    const styles = readStyles();

    expect(styles).not.toContain(".elvern-overlay__surface-hint");
    expect(styles).not.toContain(".elvern-overlay__surface ");
  });

  test("phone fullscreen selectors override the phone inline shell specificity", () => {
    const styles = readStyles();
    const selector = ".player-shell--elvern-phone.player-shell--elvern-custom:fullscreen";
    const block = cssBlockForSelectorListItem(styles, selector);

    expect(block).toContain("height: 100dvh");
    expect(block).toContain("aspect-ratio: auto");
  });
});
