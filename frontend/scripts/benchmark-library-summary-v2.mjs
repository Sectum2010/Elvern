#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { QueryClient } from "@tanstack/react-query";

import { adaptLibrarySummaryV2ToLegacyView } from "../src/lib/librarySummaryV2.js";


const ITEM_COUNTS = [100, 500, 1000, 3000];
const OVERLAP_RATIOS = [0, 0.25, 0.6];
const REPETITIONS = 5;
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(SCRIPT_DIR, "../..");


function percentile(values, ratio) {
  const ordered = [...values].sort((left, right) => left - right);
  const index = Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * ratio)));
  return ordered[index] || 0;
}


function timingSummary(values) {
  return {
    p50_ms: Number(percentile(values, 0.5).toFixed(4)),
    p90_ms: Number(percentile(values, 0.9).toFixed(4)),
    worst_ms: Number(Math.max(...values).toFixed(4)),
  };
}


function syntheticV1Item(id) {
  return {
    id,
    title: `Synthetic Title ${String(id).padStart(5, "0")}`,
    original_filename: `Synthetic.Title.${String(id).padStart(5, "0")}.2160p.WEB-DL.mkv`,
    poster_url: `/api/library/item/${id}/poster?v=synthetic${id}`,
    source_kind: id % 5 === 0 ? "cloud" : "local",
    year: 2020 + (id % 6),
    duration_seconds: 7200,
    progress_seconds: id % 300,
    progress_duration_seconds: 7200,
    completed: false,
    width: 3840,
    height: 2160,
    video_codec: "hevc",
    audio_codec: "eac3",
    container: "mkv",
    file_size: 20 * (1024 ** 3),
    quality_rank: syntheticQualityRank(),
  };
}


function syntheticQualityRank() {
  return {
    key: "gold",
    label: "Gold",
    score: 11,
    description: "Excellent quality, just below reference tier.",
    detected: ["WEB-DL", "2160p", "Dolby Digital", "HEVC", "20 GB"],
    tooltip: "Excellent quality, just below reference tier. Detected: WEB-DL · 2160p · Dolby Digital · HEVC · 20 GB.",
  };
}


function syntheticV2Item(item) {
  return {
    id: item.id,
    title: item.title,
    year: item.year,
    poster_url: item.poster_url,
    source_kind: item.source_kind,
    quality_rank: syntheticQualityRank(),
    duration_seconds: item.duration_seconds,
    progress_seconds: item.progress_seconds,
    progress_duration_seconds: item.progress_duration_seconds,
    completed: item.completed,
  };
}


function buildPayloads(itemCount, overlapRatio) {
  const items = Array.from({ length: itemCount }, (_, index) => syntheticV1Item(index + 1));
  const overlapCount = Math.floor(itemCount * overlapRatio);
  const railItems = items.slice(0, overlapCount);
  const continueItems = items.slice(0, Math.min(overlapCount, 6));
  const recentItems = items.slice(Math.max(0, itemCount - Math.min(overlapCount, 12)));
  const rails = railItems.length > 0 ? [{
    key: "synthetic-series",
    title: "Synthetic Series",
    film_count: railItems.length,
    items: railItems,
  }] : [];
  const v1 = {
    items,
    series_rails: rails,
    cloud_series_rails: [],
    continue_watching: continueItems,
    recently_added: recentItems,
    arrange: { source: "all", genre: null, quality: "all", sort: "smart" },
    available_genres: ["Action"],
    total_items: itemCount,
    scan_in_progress: false,
  };
  const v2 = {
    schema_version: "library-summary-v2",
    revision: "a".repeat(64),
    view: { category: "movies", source: "all", genre: null, quality: "all", sort: "smart" },
    items_by_id: Object.fromEntries(items.map((item) => [String(item.id), syntheticV2Item(item)])),
    sections: {
      item_ids: items.map((item) => item.id),
      series_rails: rails.map((rail) => ({
        key: rail.key,
        title: rail.title,
        film_count: rail.film_count,
        item_ids: rail.items.map((item) => item.id),
      })),
      cloud_series_rails: [],
      continue_watching_item_ids: continueItems.map((item) => item.id),
      recently_added_item_ids: recentItems.map((item) => item.id),
    },
    available_genres: ["Action"],
    total_items: itemCount,
    scan_in_progress: false,
  };
  return { v1, v2 };
}


function measure(operation) {
  const started = performance.now();
  operation();
  return performance.now() - started;
}


function gitCommit() {
  try {
    return execFileSync("git", ["rev-parse", "--short", "HEAD"], {
      cwd: PROJECT_ROOT,
      encoding: "utf8",
    }).trim();
  } catch {
    return "unknown";
  }
}


function run() {
  const cells = [];
  for (const itemCount of ITEM_COUNTS) {
    for (const overlapRatio of OVERLAP_RATIOS) {
      const { v1, v2 } = buildPayloads(itemCount, overlapRatio);
      const v1Json = JSON.stringify(v1);
      const v2Json = JSON.stringify(v2);
      const timings = {
        v1_parse: [],
        v2_parse: [],
        v2_adapter: [],
        v1_cache_insert: [],
        v2_cache_insert: [],
      };
      for (let repetition = 0; repetition < REPETITIONS; repetition += 1) {
        let parsedV2;
        timings.v1_parse.push(measure(() => JSON.parse(v1Json)));
        timings.v2_parse.push(measure(() => { parsedV2 = JSON.parse(v2Json); }));
        timings.v2_adapter.push(measure(() => adaptLibrarySummaryV2ToLegacyView(parsedV2)));

        const client = new QueryClient();
        timings.v1_cache_insert.push(measure(() => {
          client.setQueryData(["library", "v1", { itemCount, overlapRatio }], v1);
        }));
        timings.v2_cache_insert.push(measure(() => {
          client.setQueryData(["library", "v2", { itemCount, overlapRatio }], v2);
        }));
        client.clear();
      }
      cells.push({
        item_count: itemCount,
        section_overlap_percent: Math.round(overlapRatio * 100),
        v1_json_parse: timingSummary(timings.v1_parse),
        v2_json_parse: timingSummary(timings.v2_parse),
        v2_adapter: timingSummary(timings.v2_adapter),
        v1_tanstack_cache_insert: timingSummary(timings.v1_cache_insert),
        v2_tanstack_cache_insert: timingSummary(timings.v2_cache_insert),
      });
    }
  }
  return {
    kind: "synthetic_frontend_library_summary_v2",
    repetitions_per_cell: REPETITIONS,
    platform: `${process.platform} ${process.arch}`,
    node: process.version,
    cpu: os.cpus()[0]?.model || "unknown",
    browser: null,
    commit: gitCommit(),
    private_data_used: false,
    measured: ["JSON.parse", "v2 adapter", "TanStack cache insertion"],
    not_measured_here: ["React render/commit", "first stable layout", "DOM node count", "Detail return error"],
    cells,
  };
}


const outputIndex = process.argv.indexOf("--json-output");
const outputPath = outputIndex >= 0 ? process.argv[outputIndex + 1] : "";
if (outputIndex >= 0 && !outputPath) {
  throw new Error("--json-output requires a path");
}
const report = run();
const encoded = `${JSON.stringify(report, null, 2)}\n`;
if (outputPath) {
  writeFileSync(path.resolve(PROJECT_ROOT, outputPath), encoded, "utf8");
}
process.stdout.write(encoded);
