import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

import { getQualityRank, resolveLibraryQualityRank } from "./qualityRank";


const CURRENT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.resolve(
  CURRENT_DIR,
  "../../../backend/tests/fixtures/library_quality_rank_cases.json",
);
const CASES = JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8"));


describe("library quality rank golden contract", () => {
  test.each(CASES)("matches $name", ({ item, expected }) => {
    expect(getQualityRank(item)).toEqual(expected);
  });

  test("prefers a complete server-authoritative quality rank", () => {
    const qualityRank = CASES[0].expected;

    expect(resolveLibraryQualityRank({
      ...CASES.at(-1).item,
      quality_rank: qualityRank,
      quality_tier: "wood",
    })).toBe(qualityRank);
  });

  test("uses server quality_tier identity with legacy detected details", () => {
    const resolved = resolveLibraryQualityRank({
      title: "Plain",
      original_filename: "Plain.mkv",
      width: null,
      height: null,
      audio_codec: null,
      video_codec: null,
      container: "mkv",
      file_size: 0,
      quality_tier: "diamond",
    });

    expect(resolved).toEqual({
      key: "diamond",
      label: "Diamond",
      score: 0,
      description: "Reference-grade library copy with minimal compromise.",
      detected: [],
      tooltip: "Reference-grade library copy with minimal compromise.",
    });
  });

  test("falls back to the legacy rank only when server fields are absent", () => {
    expect(resolveLibraryQualityRank(CASES[0].item)).toEqual(CASES[0].expected);
  });
});
