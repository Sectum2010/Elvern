import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

import { getQualityRank } from "./qualityRank";


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
});
