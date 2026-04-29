import assert from "node:assert/strict";
import test from "node:test";

import {
  packIpadPortraitSeriesRailRows,
  packSeriesRailRows,
} from "./seriesRails.js";

function rail(key, filmCount) {
  return {
    key,
    film_count: filmCount,
    items: Array.from({ length: filmCount }, (_, index) => ({ id: `${key}-${index}` })),
  };
}

function railWithVisibleItems(key, filmCount, visibleItemCount) {
  return {
    key,
    film_count: filmCount,
    items: Array.from({ length: visibleItemCount }, (_, index) => ({ id: `${key}-${index}` })),
  };
}

function rowsToKeys(rows) {
  return rows.map((row) => row.blocks.map((block) => block.rail.key));
}

test("iPad portrait packing leaves rows unchanged when there are no two-film sections", () => {
  const rows = packIpadPortraitSeriesRailRows([
    rail("series-a", 3),
    rail("series-b", 4),
    rail("series-c", 5),
  ]);

  assert.equal(rows.length, 3);
  assert.deepEqual(rowsToKeys(rows), [["series-a"], ["series-b"], ["series-c"]]);
  assert.equal(rows[0].blocks[0].slots, 3);
  assert.equal(rows[1].blocks[0].slots, 6);
  assert.equal(rows[2].blocks[0].slots, 6);
});

test("iPad portrait packing leaves one two-film section unpaired", () => {
  const rows = packIpadPortraitSeriesRailRows([
    rail("series-a", 2),
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].layout, undefined);
  assert.deepEqual(rowsToKeys(rows), [["series-a"]]);
  assert.equal(rows[0].blocks[0].slots, 2);
});

test("iPad portrait packing pairs two two-film sections into one visual row", () => {
  const rows = packIpadPortraitSeriesRailRows([
    rail("series-a", 2),
    rail("series-b", 2),
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].layout, "ipad-two-pair");
  assert.deepEqual(rowsToKeys(rows), [["series-a", "series-b"]]);
  assert.deepEqual(rows[0].blocks.map((block) => block.slots), [2, 2]);
});

test("iPad portrait packing pairs first two of three two-film sections and leaves the third unpaired", () => {
  const rows = packIpadPortraitSeriesRailRows([
    rail("series-a", 2),
    rail("series-b", 2),
    rail("series-c", 2),
  ]);

  assert.equal(rows.length, 2);
  assert.equal(rows[0].layout, "ipad-two-pair");
  assert.deepEqual(rowsToKeys(rows), [["series-a", "series-b"], ["series-c"]]);
  assert.equal(rows[1].layout, undefined);
});

test("iPad portrait packing creates multiple generic two-plus-two rows", () => {
  const rows = packIpadPortraitSeriesRailRows([
    rail("series-a", 2),
    rail("series-b", 2),
    rail("series-c", 2),
    rail("series-d", 2),
  ]);

  assert.equal(rows.length, 2);
  assert.equal(rows[0].layout, "ipad-two-pair");
  assert.equal(rows[1].layout, "ipad-two-pair");
  assert.deepEqual(rowsToKeys(rows), [
    ["series-a", "series-b"],
    ["series-c", "series-d"],
  ]);
});

test("iPad portrait packing pairs all eligible two-film sections across mixed rails", () => {
  const rows = packIpadPortraitSeriesRailRows([
    rail("series-a", 2),
    rail("series-b", 3),
    rail("series-c", 2),
    rail("series-d", 4),
    rail("series-e", 2),
    rail("series-f", 2),
  ]);

  assert.equal(rows.length, 4);
  assert.deepEqual(rowsToKeys(rows), [
    ["series-a", "series-c"],
    ["series-b"],
    ["series-d"],
    ["series-e", "series-f"],
  ]);
  assert.equal(rows[0].layout, "ipad-two-pair");
  assert.equal(rows[1].layout, undefined);
  assert.equal(rows[1].blocks[0].slots, 3);
  assert.equal(rows[2].layout, undefined);
  assert.equal(rows[2].blocks[0].slots, 6);
  assert.equal(rows[3].layout, "ipad-two-pair");
});

test("iPad portrait packing uses visible item count for pairing", () => {
  const rows = packIpadPortraitSeriesRailRows([
    railWithVisibleItems("filtered-a", 5, 2),
    railWithVisibleItems("filtered-b", 4, 2),
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].layout, "ipad-two-pair");
  assert.deepEqual(rowsToKeys(rows), [["filtered-a", "filtered-b"]]);
});

test("default series rail packing remains available for non-iPad layouts", () => {
  const rows = packSeriesRailRows([
    rail("series-a", 2),
    rail("series-b", 2),
    rail("series-c", 2),
  ]);

  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].blocks.map((block) => block.rail.key), [
    "series-a",
    "series-b",
    "series-c",
  ]);
});
