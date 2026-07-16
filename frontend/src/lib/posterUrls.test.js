import { test } from "vitest";
import assert from "node:assert/strict";

import { getCardPosterUrl, normalizePosterDisplayWidth } from "./posterUrls.js";


test("appends variant=card to a poster url with no query", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster"),
    "/api/library/item/123/poster?variant=card&display_width=1400",
  );
});


test("appends variant=card while preserving an existing cache token", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster?v=abc"),
    "/api/library/item/123/poster?v=abc&variant=card&display_width=1400",
  );
});


test("replaces an existing variant param", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster?v=abc&variant=original"),
    "/api/library/item/123/poster?v=abc&variant=card&display_width=1400",
  );
});


test("preserves other params and hash fragments", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster?foo=1&v=abc#frag"),
    "/api/library/item/123/poster?foo=1&v=abc&variant=card&display_width=1400#frag",
  );
});


test("handles empty values safely", () => {
  assert.equal(getCardPosterUrl(""), "");
  assert.equal(getCardPosterUrl(null), null);
});


test("uses normalized display width as immutable browser cache identity", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster?v=abc", "800"),
    "/api/library/item/123/poster?v=abc&variant=card&display_width=800",
  );
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster?v=abc", "original"),
    "/api/library/item/123/poster?v=abc&variant=card&display_width=original",
  );
  assert.equal(normalizePosterDisplayWidth(" 2200 "), "2200");
  assert.equal(normalizePosterDisplayWidth("forged"), "1400");
});
