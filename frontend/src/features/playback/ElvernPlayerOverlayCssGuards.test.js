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
    const bottomBar = cssBlock(styles, ".elvern-overlay__bottom-bar");
    const topBar = cssBlock(styles, ".elvern-overlay__top-bar");

    expect(tapSurface).toContain("position: absolute");
    expect(tapSurface).toContain("inset: 0");
    expect(centerTransport).toContain("position: absolute");
    expect(bottomBar).toContain("position: relative");
    expect(topBar).toContain("position: relative");
    expect(numericZIndex(centerTransport)).toBeGreaterThan(numericZIndex(tapSurface));
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
