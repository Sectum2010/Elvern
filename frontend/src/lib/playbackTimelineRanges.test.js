import assert from "node:assert/strict";
import { describe, test } from "vitest";

import {
  SEEK_TARGET_KIND,
  accumulatePlayedRange,
  appendPlayedSample,
  classifySeekTarget,
  clampRangesToDuration,
  computePlayedNotCachedRanges,
  getContiguousBufferedEndFromPosition,
  isAbsolutePositionInRanges,
  mapAbsoluteToMediaElementSeconds,
  mapMediaElementSecondsToAbsolute,
  mergeRanges,
  normalizeTimeRanges,
  rangesToAbsolute,
  readBufferedAbsoluteRanges,
  readPlayedAbsoluteRanges,
  subtractRanges,
} from "./playbackTimelineRanges.js";

function makeTimeRanges(ranges) {
  return {
    length: ranges.length,
    start(index) {
      return ranges[index][0];
    },
    end(index) {
      return ranges[index][1];
    },
  };
}

describe("normalizeTimeRanges", () => {
  test("converts a TimeRanges-like into a plain merged array", () => {
    const result = normalizeTimeRanges(makeTimeRanges([[0, 10], [12, 20]]));
    assert.deepEqual(result, [[0, 10], [12, 20]]);
  });

  test("merges contiguous ranges within epsilon", () => {
    const result = normalizeTimeRanges(makeTimeRanges([[0, 10], [10.02, 20]]));
    assert.deepEqual(result, [[0, 20]]);
  });

  test("accepts plain arrays", () => {
    const result = normalizeTimeRanges([[5, 8], [3, 6]]);
    assert.deepEqual(result, [[3, 8]]);
  });

  test("returns empty for null/undefined input", () => {
    assert.deepEqual(normalizeTimeRanges(null), []);
    assert.deepEqual(normalizeTimeRanges(undefined), []);
  });

  test("skips invalid range entries gracefully", () => {
    const result = normalizeTimeRanges([[NaN, 10], [3, 5], ["a", "b"]]);
    assert.deepEqual(result, [[3, 5]]);
  });
});

describe("mergeRanges", () => {
  test("merges overlapping ranges", () => {
    assert.deepEqual(mergeRanges([[0, 10], [5, 15]]), [[0, 15]]);
  });

  test("merges adjacent ranges within epsilon", () => {
    assert.deepEqual(mergeRanges([[0, 10], [10.04, 12]]), [[0, 12]]);
  });

  test("preserves disjoint ranges", () => {
    assert.deepEqual(mergeRanges([[0, 10], [20, 30]]), [[0, 10], [20, 30]]);
  });

  test("sorts ranges by start", () => {
    assert.deepEqual(mergeRanges([[20, 30], [0, 10]]), [[0, 10], [20, 30]]);
  });
});

describe("clampRangesToDuration", () => {
  test("clips ranges into the [0, duration] window", () => {
    assert.deepEqual(clampRangesToDuration([[-5, 10], [50, 200]], 100), [[0, 10], [50, 100]]);
  });

  test("drops entirely out-of-range entries", () => {
    assert.deepEqual(clampRangesToDuration([[200, 300]], 100), []);
  });

  test("returns empty for non-positive durations", () => {
    assert.deepEqual(clampRangesToDuration([[0, 10]], 0), []);
    assert.deepEqual(clampRangesToDuration([[0, 10]], -1), []);
  });
});

describe("subtractRanges", () => {
  test("subtracts a covered region", () => {
    assert.deepEqual(subtractRanges([[0, 100]], [[20, 40]]), [[0, 20], [40, 100]]);
  });

  test("subtracts overlapping leading edge", () => {
    assert.deepEqual(subtractRanges([[10, 30]], [[0, 15]]), [[15, 30]]);
  });

  test("subtracts overlapping trailing edge", () => {
    assert.deepEqual(subtractRanges([[10, 30]], [[25, 40]]), [[10, 25]]);
  });

  test("returns empty when fully covered", () => {
    assert.deepEqual(subtractRanges([[10, 20]], [[0, 100]]), []);
  });

  test("returns minuend untouched when no overlap", () => {
    assert.deepEqual(subtractRanges([[10, 20]], [[50, 60]]), [[10, 20]]);
  });

  test("returns empty when minuend is empty", () => {
    assert.deepEqual(subtractRanges([], [[0, 10]]), []);
  });
});

describe("accumulatePlayedRange + appendPlayedSample", () => {
  test("adds a contiguous played range", () => {
    const result = accumulatePlayedRange([[0, 10]], [10.04, 20]);
    assert.deepEqual(result, [[0, 20]]);
  });

  test("adds a disjoint played range", () => {
    const result = accumulatePlayedRange([[0, 10]], [30, 40]);
    assert.deepEqual(result, [[0, 10], [30, 40]]);
  });

  test("appendPlayedSample extends a range when the delta is small", () => {
    const result = appendPlayedSample([[0, 10]], 10, 11.4);
    assert.deepEqual(result, [[0, 11.4]]);
  });

  test("appendPlayedSample treats large jumps as a new short interval (a seek)", () => {
    const result = appendPlayedSample([[0, 10]], 10, 200);
    assert.equal(result.length, 2);
    assert.deepEqual(result[0], [0, 10]);
    assert.equal(result[1][1], 200);
    assert.ok(result[1][0] >= 199.7 && result[1][0] <= 200);
  });

  test("appendPlayedSample is a no-op when the position did not advance", () => {
    const result = appendPlayedSample([[0, 10]], 10, 10);
    assert.deepEqual(result, [[0, 10]]);
  });
});

describe("rangesToAbsolute", () => {
  test("returns ranges unchanged for non-route2 sessions", () => {
    assert.deepEqual(rangesToAbsolute(null, [[0, 10]]), [[0, 10]]);
    assert.deepEqual(rangesToAbsolute({ engine_mode: "direct" }, [[5, 15]]), [[5, 15]]);
  });

  test("shifts ranges by the route2 ready_start_seconds offset", () => {
    const result = rangesToAbsolute(
      { engine_mode: "route2", ready_start_seconds: 100, ready_end_seconds: 200 },
      [[0, 30], [40, 60]],
    );
    assert.deepEqual(result, [[100, 130], [140, 160]]);
  });
});

describe("readBufferedAbsoluteRanges + readPlayedAbsoluteRanges", () => {
  test("readBufferedAbsoluteRanges maps video.buffered into absolute movie time", () => {
    const video = { buffered: makeTimeRanges([[0, 30], [40, 60]]) };
    const session = { engine_mode: "route2", ready_start_seconds: 100 };
    assert.deepEqual(readBufferedAbsoluteRanges(video, session), [[100, 130], [140, 160]]);
  });

  test("readPlayedAbsoluteRanges maps video.played into absolute movie time", () => {
    const video = { played: makeTimeRanges([[0, 5]]) };
    const session = { engine_mode: "route2", ready_start_seconds: 50 };
    assert.deepEqual(readPlayedAbsoluteRanges(video, session), [[50, 55]]);
  });

  test("returns [] for null videoElement", () => {
    assert.deepEqual(readBufferedAbsoluteRanges(null, null), []);
    assert.deepEqual(readPlayedAbsoluteRanges(null, null), []);
  });
});

describe("getContiguousBufferedEndFromPosition", () => {
  test("returns the buffered end containing the current playhead", () => {
    assert.equal(getContiguousBufferedEndFromPosition(12, [[10, 35]]), 35);
  });

  test("returns 0 when the video exposes no client buffer", () => {
    assert.equal(getContiguousBufferedEndFromPosition(12, []), 0);
  });

  test("does not use disjoint future ranges as prepared-through", () => {
    assert.equal(getContiguousBufferedEndFromPosition(12, [[60, 90]]), 0);
  });
});

describe("computePlayedNotCachedRanges", () => {
  test("buffered overlap wins so played-not-cached excludes buffered regions", () => {
    const result = computePlayedNotCachedRanges(
      [[0, 100]],
      [[60, 100]],
      120,
    );
    assert.deepEqual(result, [[0, 60]]);
  });

  test("returns the played range when nothing is currently buffered", () => {
    const result = computePlayedNotCachedRanges([[10, 30]], [], 120);
    assert.deepEqual(result, [[10, 30]]);
  });

  test("clamps both inputs into [0, duration]", () => {
    const result = computePlayedNotCachedRanges(
      [[-5, 50], [80, 110]],
      [[20, 30]],
      100,
    );
    assert.deepEqual(result, [[0, 20], [30, 50], [80, 100]]);
  });
});

describe("isAbsolutePositionInRanges", () => {
  test("reports true when position is inside a range", () => {
    assert.equal(isAbsolutePositionInRanges(15, [[10, 20]]), true);
  });

  test("reports false when position is outside every range", () => {
    assert.equal(isAbsolutePositionInRanges(25, [[10, 20], [30, 40]]), false);
  });
});

describe("mapAbsoluteToMediaElementSeconds + mapMediaElementSecondsToAbsolute", () => {
  test("round-trips for route2 sessions", () => {
    const session = { engine_mode: "route2", ready_start_seconds: 100, ready_end_seconds: 200 };
    const local = mapAbsoluteToMediaElementSeconds(session, 150);
    assert.equal(local, 50);
    const absolute = mapMediaElementSecondsToAbsolute(session, local);
    assert.equal(absolute, 150);
  });

  test("returns the input unchanged for direct sessions", () => {
    assert.equal(mapAbsoluteToMediaElementSeconds(null, 42), 42);
    assert.equal(mapMediaElementSecondsToAbsolute(null, 42), 42);
  });
});

describe("classifySeekTarget", () => {
  test("buffered target returns BUFFERED", () => {
    const kind = classifySeekTarget({
      absoluteTargetSeconds: 50,
      bufferedAbsoluteRanges: [[40, 80]],
      sessionPayload: { engine_mode: "route2", ready_start_seconds: 0, ready_end_seconds: 200 },
    });
    assert.equal(kind, SEEK_TARGET_KIND.BUFFERED);
  });

  test("in-window-but-unbuffered target returns WINDOW", () => {
    const kind = classifySeekTarget({
      absoluteTargetSeconds: 150,
      bufferedAbsoluteRanges: [[40, 80]],
      sessionPayload: { engine_mode: "route2", ready_start_seconds: 0, ready_end_seconds: 200 },
    });
    assert.equal(kind, SEEK_TARGET_KIND.WINDOW);
  });

  test("out-of-window target returns UNCACHED", () => {
    const kind = classifySeekTarget({
      absoluteTargetSeconds: 800,
      bufferedAbsoluteRanges: [[40, 80]],
      sessionPayload: { engine_mode: "route2", ready_start_seconds: 0, ready_end_seconds: 200 },
    });
    assert.equal(kind, SEEK_TARGET_KIND.UNCACHED);
  });

  test("non-route2 sessions treat any target as in-window when no buffered range matches", () => {
    const kind = classifySeekTarget({
      absoluteTargetSeconds: 800,
      bufferedAbsoluteRanges: [],
      sessionPayload: null,
    });
    assert.equal(kind, SEEK_TARGET_KIND.WINDOW);
  });

  test("targets near the buffered tail are not treated as buffered (headroom respected)", () => {
    const kind = classifySeekTarget({
      absoluteTargetSeconds: 79.9,
      bufferedAbsoluteRanges: [[40, 80]],
      sessionPayload: null,
      bufferedHeadroomSeconds: 0.5,
    });
    assert.equal(kind, SEEK_TARGET_KIND.WINDOW);
  });

  test("active_window_* fields decide window membership when present", () => {
    // Phase 2: the sliding-window contract uses active_window_*; targets outside
    // it must classify as UNCACHED so the controller triggers a retarget.
    const sessionPayload = {
      engine_mode: "route2",
      ready_start_seconds: 0,
      ready_end_seconds: 7200,
      active_window_start_seconds: 180,
      active_window_end_seconds: 420,
    };
    assert.equal(classifySeekTarget({
      absoluteTargetSeconds: 300,
      bufferedAbsoluteRanges: [],
      sessionPayload,
    }), SEEK_TARGET_KIND.WINDOW);
    assert.equal(classifySeekTarget({
      absoluteTargetSeconds: 50,
      bufferedAbsoluteRanges: [],
      sessionPayload,
    }), SEEK_TARGET_KIND.UNCACHED);
    assert.equal(classifySeekTarget({
      absoluteTargetSeconds: 5000,
      bufferedAbsoluteRanges: [],
      sessionPayload,
    }), SEEK_TARGET_KIND.UNCACHED);
  });
});
