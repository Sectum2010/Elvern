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
  test("app shell text cannot force card content outside its container", () => {
    const styles = readStyles();
    const appShellLayoutGuard = cssBlock(styles, ":where(.app-shell, .app-shell *)");
    const appShellTextGuard = cssBlock(
      styles,
      ":where(.app-shell) :where(p, h1, h2, h3, h4, h5, h6, span, strong, small)",
    );

    expect(appShellLayoutGuard).toContain("min-inline-size: 0");
    expect(appShellTextGuard).toContain("max-inline-size: 100%");
    expect(appShellTextGuard).toContain("overflow-wrap: break-word");
  });

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

  test("iPhone prewarm card covers the hidden media element before release", () => {
    const styles = readStyles();
    const card = cssBlock(styles, ".player-prewarm-card");
    const warmupVideo = cssBlock(styles, ".player--warmup");

    expect(warmupVideo).toContain("opacity: 0");
    expect(card).toContain("position: absolute");
    expect(card).toContain("inset: 0");
    expect(card).toContain("z-index: 8");
    expect(card).toContain("display: grid");
    expect(card).toContain("grid-template-columns: auto minmax(0, max-content)");
    expect(card).toContain("align-content: center");
    expect(card).toContain("align-items: start");
    expect(card).toContain("justify-content: center");
    expect(card).not.toContain("align-items: flex-start");
    expect(card).toContain("clamp(0.35rem, 1.8vw, 0.65rem)");
    expect(card).toContain("clamp(1rem, 3vh, 1.35rem)");
    expect(card).toContain("background:");
    expect(card).toContain("pointer-events: auto");
    const copy = cssBlock(styles, ".player-prewarm-card__copy");
    expect(copy).toContain("gap: 0.34rem");
    expect(copy).toContain("inline-size: max-content");
    expect(copy).toContain("max-inline-size: min(26rem, 100%)");
    const title = cssBlock(styles, ".player-prewarm-card__title");
    expect(title).toContain("font-size: clamp(0.82rem");
    expect(title).toContain("white-space: nowrap");
    const estimate = cssBlock(styles, ".player-prewarm-card__estimate");
    expect(estimate).not.toContain("position: relative");
    expect(estimate).not.toContain("left:");
    expect(estimate).not.toContain("top:");
    expect(estimate).toContain("font-size: clamp(1.32rem");
    expect(estimate).toContain("font-variant-numeric: tabular-nums");
    expect(estimate).toContain("white-space: nowrap");
  });

  test("prewarm card viewport toggle stays accessible above the loading card", () => {
    const styles = readStyles();
    const card = cssBlock(styles, ".player-prewarm-card");
    const toggle = cssBlock(styles, ".player-prewarm-viewport-toggle");
    const transparentToggle = cssBlock(styles, ".player-prewarm-viewport-toggle.elvern-overlay__inline-maximize");

    expect(toggle).toContain("position: absolute");
    expect(toggle).toContain("top: max(0.55rem");
    expect(toggle).toContain("right: max(0.55rem");
    expect(numericZIndex(toggle)).toBeGreaterThan(numericZIndex(card));
    expect(transparentToggle).toContain("border: 0");
    expect(transparentToggle).toContain("background: transparent");
    expect(transparentToggle).toContain("box-shadow: none");
    expect(transparentToggle).not.toContain("border-radius");
    expect(styles).not.toContain("player-prewarm-viewport-toggle__glyph");
  });

  test("prepared-through runtime note stays inside the player card width", () => {
    const styles = readStyles();
    const notes = cssBlock(styles, ".player-runtime-notes");
    const preparedThrough = cssBlock(styles, ".player-runtime-notes__prepared-through");

    expect(notes).toContain("inline-size: 100%");
    expect(notes).toContain("max-inline-size: 100%");
    expect(notes).toContain("min-block-size:");
    expect(notes).toContain("overflow: hidden");
    expect(preparedThrough).toContain("display: block");
    expect(preparedThrough).toContain("margin: 0");
    expect(preparedThrough).toContain("max-inline-size: 100%");
    expect(preparedThrough).toContain("overflow-wrap: break-word");
    expect(preparedThrough).not.toContain("position: relative");
    expect(preparedThrough).not.toContain("left:");
    expect(preparedThrough).not.toContain("top:");
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

  test("standard-fit contains the full frame while zoom-fill covers the viewport", () => {
    const styles = readStyles();
    const standardFitBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-custom.player-shell--video-fit-standard-fit .player",
    );
    const zoomFillBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-custom.player-shell--video-fit-zoom-fill .player",
    );
    const phoneFillBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom.player-shell--video-fit-zoom-fill.player-shell--cinema-takeover .player",
    );

    expect(standardFitBlock).toContain("width: 100%");
    expect(standardFitBlock).toContain("height: 100%");
    expect(standardFitBlock).toContain("object-fit: contain");
    expect(standardFitBlock).toContain("transform: none");
    expect(zoomFillBlock).toContain("height: 100%");
    expect(zoomFillBlock).toContain("object-fit: cover");
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

  test("phone fullscreen controls are bottom-right aligned without a spacer push", () => {
    const styles = readStyles();
    const bottomBar = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom.player-shell--cinema-takeover .elvern-overlay__bottom-bar",
    );
    const controlsRow = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom.player-shell--cinema-takeover .elvern-overlay__controls-row",
    );
    const spacer = cssBlockForSelectorListItem(
      styles,
      ".player-shell--elvern-phone.player-shell--elvern-custom.player-shell--cinema-takeover .elvern-overlay__spacer",
    );

    expect(bottomBar).toContain("bottom: max(0.2rem");
    expect(controlsRow).toContain("justify-content: flex-end");
    expect(spacer).toContain("flex: 0 0 auto");
  });

  test("phone track menus use readable row layout", () => {
    const styles = readStyles();
    const menuBlock = cssBlock(styles, ".elvern-overlay--phone .elvern-overlay__track-menu");
    const shellOpenBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--track-menu-open.player-shell--elvern-phone.player-shell--elvern-custom.player-shell--cinema-takeover",
    );
    const shellMenuBlock = cssBlockForSelectorListItem(
      styles,
      ".player-shell--track-menu-open.player-shell--elvern-phone.player-shell--elvern-custom.player-shell--cinema-takeover .elvern-overlay__track-menu",
    );
    const rowBlock = cssBlock(styles, ".elvern-overlay--phone .elvern-overlay__track-menu-item");
    const shortcutBlock = cssBlock(styles, ".elvern-overlay--phone .elvern-overlay__track-shortcut");
    const pendingBlock = cssBlock(styles, ".elvern-overlay__track-menu-item--pending");
    const pendingDisabledBlock = cssBlock(styles, ".elvern-overlay__track-menu-item--pending:disabled");
    const pendingSpinnerBlock = cssBlock(styles, ".elvern-overlay__track-menu-item--pending .elvern-overlay__track-menu-spinner");
    const errorBlock = cssBlock(styles, ".elvern-overlay__track-menu-item--error");
    const errorButtonBlock = cssBlock(styles, ".elvern-overlay__icon-button--error");
    const lockedBlock = cssBlock(styles, ".elvern-overlay__track-menu-item--locked");

    expect(styles).not.toContain(".elvern-overlay__track-sheet");
    expect(shellOpenBlock).toContain("touch-action: pan-y");
    expect(shellMenuBlock).toContain("touch-action: pan-y");
    expect(menuBlock).toContain("width: min(18rem");
    expect(menuBlock).toContain("max-height: min(72dvh");
    expect(menuBlock).toContain("overflow-y: auto");
    expect(menuBlock).toContain("-webkit-overflow-scrolling: touch");
    expect(menuBlock).toContain("touch-action: pan-y");
    expect(shortcutBlock).toContain("touch-action: pan-y");
    expect(rowBlock).toContain("min-height: 2.55rem");
    expect(rowBlock).toContain("align-items: center");
    expect(rowBlock).toContain("gap: 0.55rem");
    expect(rowBlock).toContain("overflow-wrap: anywhere");
    expect(rowBlock).toContain("white-space: normal");
    expect(rowBlock).toContain("text-align: left");
    expect(pendingBlock).toContain("background: rgba(225, 29, 72");
    expect(pendingBlock).toContain("color: #ffffff");
    expect(pendingDisabledBlock).toContain("cursor: progress");
    expect(pendingDisabledBlock).toContain("opacity: 1");
    expect(pendingSpinnerBlock).toContain("opacity: 1");
    expect(errorBlock).toContain("background: rgba(220, 38, 38");
    expect(errorBlock).toContain("color: #ffffff");
    expect(errorButtonBlock).toContain("background: rgba(220, 38, 38");
    expect(lockedBlock).toContain("opacity:");
  });

  test("timeline preparing marker uses red recovery target styling", () => {
    const styles = readStyles();
    const markerBlock = cssBlock(styles, ".elvern-timeline__preparing-marker");

    expect(markerBlock).toContain("background: #e11d48");
    expect(markerBlock).toContain("rgba(225, 29, 72");
    expect(markerBlock).not.toContain("#7dd3fc");
    expect(markerBlock).not.toContain("rgba(125, 211, 252");
  });

  test("timeline primary pre-cache layer is the server prepared layer", () => {
    const styles = readStyles();
    const bufferedBlock = cssBlock(styles, ".elvern-timeline__layer--buffered");
    const serverPreparedBlock = cssBlock(styles, ".elvern-timeline__layer--server-prepared");

    expect(bufferedBlock).toContain("rgba(255, 255, 255, 0.22");
    expect(serverPreparedBlock).toContain("rgba(255, 255, 255, 0.72");
  });

  test("desktop track menus keep long audio and subtitle labels readable", () => {
    const styles = readStyles();
    const menuBlock = cssBlock(styles, ".elvern-overlay__track-menu");
    const rowBlock = cssBlock(styles, ".elvern-overlay__track-menu-item");
    const labelBlock = cssBlockForSelectorListItem(styles, ".elvern-overlay__track-menu-label");
    const warningBlock = cssBlock(styles, ".elvern-overlay__track-menu-warning");
    const errorBlock = cssBlock(styles, ".elvern-overlay__track-menu-error-mark");
    const spinnerBlock = cssBlock(styles, ".elvern-overlay__track-menu-spinner");

    expect(menuBlock).toContain("min-width: min(18rem");
    expect(menuBlock).toContain("max-width: min(24rem");
    expect(menuBlock).toContain("max-inline-size: min(24rem");
    expect(menuBlock).toContain("max-height: min(58vh");
    expect(menuBlock).toContain("overflow-y: auto");
    expect(menuBlock).toContain("line-height: 1.25");
    expect(rowBlock).toContain("display: flex");
    expect(rowBlock).toContain("align-items: center");
    expect(rowBlock).toContain("min-height: 2.45rem");
    expect(rowBlock).toContain("line-height: 1.25");
    expect(rowBlock).toContain("white-space: normal");
    expect(rowBlock).toContain("overflow-wrap: anywhere");
    expect(labelBlock).toContain("flex: 1 1 auto");
    expect(labelBlock).toContain("min-width: 0");
    expect(labelBlock).toContain("line-height: 1.25");
    expect(labelBlock).not.toContain("position: absolute");
    expect(warningBlock).toContain("flex: 0 0 1rem");
    expect(errorBlock).toContain("flex: 0 0 1rem");
    expect(spinnerBlock).toContain("width: 0.78rem");
    expect(rowBlock).not.toContain("line-height: 0");
  });

  test("timeline has no standalone playhead knob styling", () => {
    const styles = readStyles();

    expect(styles).not.toContain(".elvern-timeline__playhead");
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
      ".player-shell--elvern-custom.player-shell--video-fit-zoom-fill .player",
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
