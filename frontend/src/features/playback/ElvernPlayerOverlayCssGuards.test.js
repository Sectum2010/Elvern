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
    const surface = cssBlock(styles, ".elvern-overlay__surface");
    const bottomBar = cssBlock(styles, ".elvern-overlay__bottom-bar");
    const topBar = cssBlock(styles, ".elvern-overlay__top-bar");

    expect(surface).toContain("position: relative");
    expect(bottomBar).toContain("position: relative");
    expect(topBar).toContain("position: relative");
    expect(numericZIndex(bottomBar)).toBeGreaterThan(numericZIndex(surface));
    expect(numericZIndex(topBar)).toBeGreaterThan(numericZIndex(surface));
  });

  test("phone fullscreen selectors override the phone inline shell specificity", () => {
    const styles = readStyles();
    const selector = ".player-shell--elvern-phone.player-shell--elvern-custom:fullscreen";
    const block = cssBlockForSelectorListItem(styles, selector);

    expect(block).toContain("height: 100dvh");
    expect(block).toContain("aspect-ratio: auto");
  });
});
