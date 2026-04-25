import test from "node:test";
import assert from "node:assert/strict";

import { getCardPosterUrl } from "./posterUrls.js";


test("appends variant=card to a poster url with no query", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster"),
    "/api/library/item/123/poster?variant=card",
  );
});


test("appends variant=card while preserving an existing cache token", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster?v=abc"),
    "/api/library/item/123/poster?v=abc&variant=card",
  );
});


test("replaces an existing variant param", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster?v=abc&variant=original"),
    "/api/library/item/123/poster?v=abc&variant=card",
  );
});


test("preserves other params and hash fragments", () => {
  assert.equal(
    getCardPosterUrl("/api/library/item/123/poster?foo=1&v=abc#frag"),
    "/api/library/item/123/poster?foo=1&v=abc&variant=card#frag",
  );
});


test("handles empty values safely", () => {
  assert.equal(getCardPosterUrl(""), "");
  assert.equal(getCardPosterUrl(null), null);
});
