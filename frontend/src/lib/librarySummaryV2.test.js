import { describe, expect, test } from "vitest";

import {
  adaptLibrarySummaryV2ToLegacyView,
  buildLibrarySummaryV2RequestPath,
  compareLibraryV1AndV2,
  isLibrarySummaryV2CapabilityFailure,
  LibrarySummaryV2ContractError,
  resolveLibrarySummaryV2Mode,
  validateLibrarySummaryV2Payload,
} from "./librarySummaryV2";


const REVISION = "a".repeat(64);

function qualityRank() {
  return {
    key: "wood",
    label: "Wood",
    score: 0,
    description: "Basic fallback copy.",
    detected: [],
    tooltip: "Basic fallback copy.",
  };
}

function item(id) {
  return {
    id,
    title: `Synthetic ${id}`,
    year: 2024,
    poster_url: `/api/library/item/${id}/poster?v=token${id}`,
    source_kind: "local",
    quality_rank: qualityRank(),
    duration_seconds: 7200,
    progress_seconds: id === 2 ? 120 : null,
    progress_duration_seconds: id === 2 ? 7200 : null,
    completed: false,
  };
}

function v2Payload() {
  return {
    schema_version: "library-summary-v2",
    revision: REVISION,
    view: {
      category: "movies",
      source: "all",
      genres: [],
      qualities: [],
      genre: null,
      quality: "all",
      sort: "smart",
    },
    items_by_id: { "1": item(1), "2": item(2) },
    sections: {
      item_ids: [1, 2],
      series_rails: [{ key: "synthetic", title: "Synthetic", film_count: 2, item_ids: [1, 2] }],
      cloud_series_rails: [],
      continue_watching_item_ids: [2],
      recently_added_item_ids: [2, 1],
    },
    available_genres: ["Action"],
    total_items: 2,
    scan_in_progress: false,
  };
}

function v1Payload() {
  const first = {
    ...item(1),
    original_filename: null,
    width: null,
    height: null,
    video_codec: null,
    audio_codec: null,
    container: null,
    file_size: 0,
  };
  const second = {
    ...item(2),
    original_filename: null,
    width: null,
    height: null,
    video_codec: null,
    audio_codec: null,
    container: null,
    file_size: 0,
  };
  return {
    items: [first, second],
    series_rails: [{ key: "synthetic", title: "Synthetic", film_count: 2, items: [first, second] }],
    cloud_series_rails: [],
    continue_watching: [second],
    recently_added: [second, first],
    arrange: {
      source: "all",
      genres: [],
      qualities: [],
      genre: null,
      quality: "all",
      sort: "smart",
    },
    available_genres: ["Action"],
    total_items: 2,
    scan_in_progress: false,
  };
}


describe("library summary v2 contract", () => {
  test("feature mode defaults missing and empty values to on but rejects invalid values", () => {
    expect(resolveLibrarySummaryV2Mode(undefined)).toBe("on");
    expect(resolveLibrarySummaryV2Mode("")).toBe("on");
    expect(resolveLibrarySummaryV2Mode("   ")).toBe("on");
    expect(resolveLibrarySummaryV2Mode("OFF")).toBe("off");
    expect(resolveLibrarySummaryV2Mode("SHADOW")).toBe("shadow");
    expect(resolveLibrarySummaryV2Mode("on")).toBe("on");
    expect(resolveLibrarySummaryV2Mode("unexpected")).toBe("off");
  });

  test("request path carries only supported non-search view fields", () => {
    expect(buildLibrarySummaryV2RequestPath({
      category: "anime",
      source: "cloud",
      genre: "Action",
      quality: "gold",
      sort: "az",
    })).toBe("/api/library/v2/summary?category=anime&source=cloud&genre=Action&quality=gold&sort=az");
  });

  test("adapter preserves order and shares entity references across sections", () => {
    const raw = v2Payload();
    const adapted = adaptLibrarySummaryV2ToLegacyView(raw);

    expect(adapted.items.map((entry) => entry.id)).toEqual([1, 2]);
    expect(adapted.series_rails[0].items.map((entry) => entry.id)).toEqual([1, 2]);
    expect(adapted.continue_watching[0]).toBe(adapted.items[1]);
    expect(adapted.recently_added[1]).toBe(adapted.items[0]);
  });

  test("dangling IDs reject the entire normalized payload", () => {
    const raw = v2Payload();
    raw.sections.item_ids.push(999);

    expect(() => validateLibrarySummaryV2Payload(raw)).toThrow(LibrarySummaryV2ContractError);
  });

  test("sensitive fields reject the entire normalized payload", () => {
    const raw = v2Payload();
    raw.items_by_id["1"].original_filename = "must-not-cross-wire.mkv";

    expect(() => validateLibrarySummaryV2Payload(raw)).toThrow(LibrarySummaryV2ContractError);
  });

  test("unknown top-level, item, quality, section, and rail fields reject the versioned contract", () => {
    const mutations = [
      (raw) => { raw.unversioned_extension = true; },
      (raw) => { raw.items_by_id["1"].created_at = "private"; },
      (raw) => { raw.items_by_id["1"].quality_rank.raw_score_inputs = ["private"]; },
      (raw) => { raw.sections.progressive_membership = []; },
      (raw) => { raw.sections.series_rails[0].rows = []; },
    ];

    mutations.forEach((mutate) => {
      const raw = v2Payload();
      mutate(raw);
      expect(() => validateLibrarySummaryV2Payload(raw)).toThrow(LibrarySummaryV2ContractError);
    });
  });

  test("invalid quality rank values reject the entire normalized payload", () => {
    const mutations = [
      (rank) => { rank.key = "mythic"; },
      (rank) => { rank.score = Number.POSITIVE_INFINITY; },
      (rank) => { rank.detected = "REMUX"; },
      (rank) => { rank.detected = ["REMUX", 2160]; },
      (rank) => { rank.label = null; },
      (rank) => { rank.tooltip = false; },
      (rank) => { delete rank.description; },
    ];

    mutations.forEach((mutate) => {
      const raw = v2Payload();
      mutate(raw.items_by_id["1"].quality_rank);
      expect(() => validateLibrarySummaryV2Payload(raw)).toThrow(LibrarySummaryV2ContractError);
    });
  });

  test("semantic shadow comparer reports parity without private values", () => {
    const result = compareLibraryV1AndV2(v1Payload(), v2Payload(), {
      viewIdentity: { category: "movies" },
    });

    expect(result).toEqual({ matches: true, mismatchCount: 0, mismatches: [] });
  });

  test.each([
    {
      label: "one quality",
      genres: [],
      qualities: ["gold"],
      expectedGenre: null,
      expectedQuality: "gold",
    },
    {
      label: "multiple qualities",
      genres: [],
      qualities: ["diamond", "gold"],
      expectedGenre: null,
      expectedQuality: null,
    },
    {
      label: "one genre",
      genres: ["Action"],
      qualities: [],
      expectedGenre: "Action",
      expectedQuality: "all",
    },
    {
      label: "multiple genres and qualities",
      genres: ["Action", "Drama"],
      qualities: ["diamond", "gold"],
      expectedGenre: null,
      expectedQuality: null,
    },
  ])("shadow view parity handles $label", ({
    genres,
    qualities,
    expectedGenre,
    expectedQuality,
  }) => {
    const v1 = v1Payload();
    const v2 = v2Payload();
    v1.arrange = {
      ...v1.arrange,
      genres,
      qualities,
      genre: expectedGenre,
      quality: expectedQuality,
    };
    v2.view = {
      ...v2.view,
      genres,
      qualities,
      genre: expectedGenre,
      quality: expectedQuality,
    };

    expect(compareLibraryV1AndV2(v1, v2, {
      viewIdentity: { category: "movies" },
    })).toEqual({ matches: true, mismatchCount: 0, mismatches: [] });
  });

  test("shadow reports a true view mismatch", () => {
    const v2 = v2Payload();
    v2.view.quality = "gold";

    const result = compareLibraryV1AndV2(v1Payload(), v2, {
      viewIdentity: { category: "movies" },
    });

    expect(result.matches).toBe(false);
    expect(result.mismatches).toContainEqual({ category: "view" });
  });

  test("shadow uses the v1 server rank even when v1 raw metadata is redacted", () => {
    const v1 = v1Payload();
    const v2 = v2Payload();
    const diamond = {
      key: "diamond",
      label: "Diamond",
      score: 17,
      description: "Reference-grade library copy with minimal compromise.",
      detected: ["REMUX", "2160p", "Atmos", "HEVC", "80 GB"],
      tooltip: "Reference-grade library copy with minimal compromise. Detected: REMUX · 2160p · Atmos · HEVC · 80 GB.",
    };
    v1.items[0].quality_rank = diamond;
    v1.series_rails[0].items[0].quality_rank = diamond;
    v1.recently_added[1].quality_rank = diamond;
    v2.items_by_id["1"].quality_rank = diamond;

    expect(compareLibraryV1AndV2(v1, v2, {
      viewIdentity: { category: "movies" },
    })).toEqual({ matches: true, mismatchCount: 0, mismatches: [] });
  });

  test("shadow reports a missing v1 server rank instead of recalculating it", () => {
    const v1 = v1Payload();
    delete v1.items[0].quality_rank;
    delete v1.series_rails[0].items[0].quality_rank;
    delete v1.recently_added[1].quality_rank;

    const result = compareLibraryV1AndV2(v1, v2Payload(), {
      viewIdentity: { category: "movies" },
    });

    expect(result.matches).toBe(false);
    expect(result.mismatches).toContainEqual({ category: "v1_quality_rank_missing", itemId: 1 });
  });

  test("semantic mismatch diagnostics include only category and numeric identity", () => {
    const raw = v2Payload();
    raw.items_by_id["2"].poster_url = "/private/poster/value";
    const result = compareLibraryV1AndV2(v1Payload(), raw, {
      viewIdentity: { category: "movies" },
    });

    expect(result.matches).toBe(false);
    expect(result.mismatches).toContainEqual({ category: "poster_url", itemId: 2 });
    expect(JSON.stringify(result)).not.toContain("/private/poster/value");
    expect(JSON.stringify(result)).not.toContain("Synthetic");
  });

  test("capability fallback is narrow", () => {
    expect(isLibrarySummaryV2CapabilityFailure({ status: 404 })).toBe(true);
    expect(isLibrarySummaryV2CapabilityFailure({
      status: 503,
      detail: { code: "library_summary_v2_disabled" },
    })).toBe(true);
    expect(isLibrarySummaryV2CapabilityFailure(new LibrarySummaryV2ContractError("bad"))).toBe(true);
    expect(isLibrarySummaryV2CapabilityFailure({ status: 401 })).toBe(false);
    expect(isLibrarySummaryV2CapabilityFailure({ status: 403 })).toBe(false);
    expect(isLibrarySummaryV2CapabilityFailure({ status: 500 })).toBe(false);
  });
});
