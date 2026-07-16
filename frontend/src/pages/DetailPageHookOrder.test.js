import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

const detailPagePath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "DetailPage.jsx",
);

function readDetailPage() {
  return fs.readFileSync(detailPagePath, "utf8");
}

describe("DetailPage hook-order guards", () => {
  test("does not route detail media through the card poster URL helper", () => {
    const source = readDetailPage();
    expect(source).not.toContain("getCardPosterUrl");
    expect(source).not.toContain("variant=card");
  });

  test("does not declare React hooks after early render returns", () => {
    const source = readDetailPage();
    const firstEarlyReturnIndex = source.indexOf("if (loading) {");
    expect(firstEarlyReturnIndex).toBeGreaterThan(0);

    const afterEarlyReturns = source.slice(firstEarlyReturnIndex);
    expect(afterEarlyReturns).not.toMatch(/\buse(?:Effect|Memo|Ref|State|Callback|Reducer|Context|LayoutEffect)\s*\(/);
  });

  test("fullscreen touch pan lock does not block track menu scrolling", () => {
    const source = readDetailPage();
    const handlerStart = source.indexOf("const preventViewportPan = (event) => {");
    expect(handlerStart).toBeGreaterThan(0);
    const handlerEnd = source.indexOf("shell.addEventListener(\"touchmove\", preventViewportPan", handlerStart);
    expect(handlerEnd).toBeGreaterThan(handlerStart);
    const handlerSource = source.slice(handlerStart, handlerEnd);

    expect(handlerSource).toContain(".elvern-overlay__track-menu");
    expect(handlerSource).toContain(".elvern-overlay__menu-host");
    expect(handlerSource).toContain(".elvern-overlay__track-menu-item");
    expect(handlerSource.indexOf("target?.closest?.(\".elvern-overlay__track-menu\")"))
      .toBeLessThan(handlerSource.indexOf("event.preventDefault()"));
  });

  test("normal player progress wording keeps Prepared through label", () => {
    const source = readDetailPage();
    const noteStart = source.indexOf("const optimizedProgressNote =");
    expect(noteStart).toBeGreaterThan(0);
    const noteEnd = source.indexOf("function normalizeDesktopSeekValue", noteStart);
    expect(noteEnd).toBeGreaterThan(noteStart);
    const noteSource = source.slice(noteStart, noteEnd);

    expect(noteSource).toContain("Prepared through");
    expect(noteSource).not.toContain("Device buffered");
    expect(noteSource).not.toContain("Server ready");
    expect(noteSource).not.toContain("Client buffer");
  });

  test("browser prewarm uses the video-card prepare UI instead of a duplicate external EST box", () => {
    const source = readDetailPage();

    expect(source).not.toContain("playback-pending-indicator");
    expect(source).not.toContain("className=\"playback-pending-est\"");
    expect(source).toContain("player-prewarm-card__estimate");
    expect(source).not.toContain("Elvern is preparing stable");
    expect(source).toContain("Prepared through");
  });

  test("movie info opens from a top-right player icon instead of the bottom action row", () => {
    const source = readDetailPage();
    const playerCardIndex = source.indexOf("<div className=\"player-card\">");
    const secondaryActionsIndex = source.indexOf("<div className=\"detail-secondary-actions\">", playerCardIndex);
    expect(playerCardIndex).toBeGreaterThan(0);
    expect(secondaryActionsIndex).toBeGreaterThan(playerCardIndex);

    const playerCardHeader = source.slice(playerCardIndex, secondaryActionsIndex);
    const bottomActions = source.slice(secondaryActionsIndex, source.indexOf("detail-download-action", secondaryActionsIndex));

    expect(playerCardHeader).toContain("detail-player-info-button");
    expect(playerCardHeader).toContain("aria-label=\"Movie info\"");
    expect(playerCardHeader).toContain("detail-player-info-button__glyph");
    expect(bottomActions).not.toContain(">Info<");
  });

  test("admin movie info exposes age requirement editing without changing playback controls", () => {
    const source = readDetailPage();

    expect(source).toContain("const AGE_REQUIREMENT_OPTIONS = [null, ...Array.from({ length: 18 }");
    expect(source).toContain("function formatAgeRequirement(value)");
    expect(source).toContain("/api/library/item/${item.id}/age-requirement");
    expect(source).toContain("/api/library/age-groups/${encodeURIComponent(item.age_group_key)}");
    expect(source).toContain("<h2>Age Requirement</h2>");
    expect(source).toContain("This applies to matching copies of this movie.");
    expect(source).toContain("Manage age group");
    expect(source).toContain("detail-age-group-link");
    expect(source).toContain("Open Age Groups in Settings");
    expect(source).toContain("Edit age requirement");
    expect(source).toContain("onClick={saveAgeRequirement}");
    expect(source).toContain("ageRequirementEditor.pending ? \"Saving...\" : \"Save\"");
  });

  test("movie info exposes genres only inside the Info modal", () => {
    const source = readDetailPage();
    const infoModalStart = source.indexOf("{infoModalOpen ? (");
    const infoModalEnd = source.indexOf("{ageGroupModal.open ? (", infoModalStart);
    expect(infoModalStart).toBeGreaterThan(0);
    expect(infoModalEnd).toBeGreaterThan(infoModalStart);

    const beforeInfoModal = source.slice(0, infoModalStart);
    const infoModalSource = source.slice(infoModalStart, infoModalEnd);

    expect(source).toContain("const COMMON_GENRE_OPTIONS = [");
    expect(source).toContain("const MAX_GENRE_COUNT = 3;");
    expect(source).toContain("/api/library/item/${item.id}/genres");
    expect(infoModalSource).toContain("detail-genre-chip");
    expect(infoModalSource).toContain("Unknown");
    expect(infoModalSource).toContain("Edit genres");
    expect(infoModalSource).toContain("Save genres");
    expect(infoModalSource).toContain("isAdmin && !genreEditor.editing");
    expect(infoModalSource).toContain("COMMON_GENRE_OPTIONS.map");
    expect(beforeInfoModal).not.toContain("detail-genre-chip");
    expect(beforeInfoModal).not.toContain("Edit genres");
  });
});
