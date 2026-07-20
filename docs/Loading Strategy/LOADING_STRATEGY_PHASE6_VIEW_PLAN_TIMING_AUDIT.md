# Loading Strategy Phase 6: Library View Plan Timing Audit

## Method

The benchmark uses an isolated temporary SQLite database and synthetic rows only. It does not read the live database, live poster directories, media titles, filenames, or paths. Each 100/500/1000/3000-item and 0/25/60-percent section-overlap cell runs at least five times. Timings are observational and are not CI pass/fail thresholds.

The committed runner is `scripts/benchmark-library-summary-v2.py`. Local outputs are:

- `tmp/library-view-plan-benchmark.json` (before safe optimizations)
- `tmp/library-view-plan-benchmark-after2.json` (after safe optimizations)

## Confirmed Bottlenecks

### Empty Poster Index resolution

Before optimization, an empty but valid Poster Index still ran title/name normalization for every item. In the 3000-item, 60-percent-overlap cell, `poster_url_resolution` p50 was 11054.41 ms and v2 serialization p50 was 12727.71 ms.

The safe change returns `None` immediately when the request's immutable Poster Index snapshot has zero entries. No poster can match an empty index, so membership, output, revision, and poster fallback behavior are unchanged.

After optimization, the same cell's `poster_url_resolution` p50 was 2.24 ms and v2 serialization p50 was 1653.26 ms. The remaining v2 serialization time is primarily per-item normalized payload construction, not filesystem traversal.

### Repeated shared local root lookup

Rows without persisted series-folder metadata used to resolve the same effective shared local Library root once per row while building a local Series Rail. SQL tracing showed 1224 SELECT/WITH statements for the 3000-item, 60-percent-overlap cell and 3024 for the 3000-item, zero-overlap cell.

The safe change lazily resolves that request-invariant root once and reuses it for fallback folder classification. After optimization every benchmark cell reports 25 SELECT/WITH statements. Existing folder metadata still bypasses the fallback, and cloud rail behavior is unchanged.

## Before And After

The following table uses the 60-percent section-overlap profile, which exercises duplicate visibility, section overlap, and Series Rail work.

| Items | View Plan before p50 | View Plan after p50 | v2 serialize before p50 | v2 serialize after p50 | SQL before | SQL after |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 279.81 ms | 232.25 ms | 422.28 ms | 55.45 ms | 64 | 25 |
| 500 | 1289.97 ms | 1046.57 ms | 2115.14 ms | 275.20 ms | 224 | 25 |
| 1000 | 2544.25 ms | 2083.35 ms | 4212.80 ms | 555.62 ms | 424 | 25 |
| 3000 | 7552.69 ms | 6143.22 ms | 12727.71 ms | 1653.26 ms | 1224 | 25 |

Payload bytes and normalized section membership were unchanged in paired runs. For the 3000-item, 60-percent-overlap cell, v1 and v2 uncompressed sizes remained 8,305,677 and 1,526,296 bytes respectively.

## Largest Remaining Stages

For 3000 items and 60-percent overlap after optimization:

- `hidden_filtering`: 3340.52 ms p50.
- `duplicate_representative`: 3340.52 ms p50.
- `row_decoration`: 1746.71 ms p50.
- `local_series_rail_build`: 1020.66 ms p50.
- `accessible_media_sql`: 9.72 ms p50.
- `revision_hash`: 8.28 ms p50.

`hidden_filtering` and `duplicate_representative` deliberately record the same combined operation and must not be added together. That combined operation is the largest confirmed View Plan stage.

## Safe Improvements Implemented

1. Empty Poster Index early return.
2. Request-local reuse of the effective shared local Library root during fallback Series Rail classification.

Both are internal memoization/short-circuit changes. They do not change v1/v2 membership, section order, rail key/title/order, Continue Watching, Recently Added, permission filtering, revision truth, search, relocation, or `cardInstanceKey`.

Parity is protected by existing v1/v2 contract and category/rail tests plus focused empty-index and root-resolution-count tests. Timing-enabled and timing-disabled v2 payloads are asserted equal.

## Deferred Improvements

- Hidden filtering and duplicate representative selection are business-critical and remain untouched. Any rewrite needs dedicated equivalence fixtures for globally hidden, per-user hidden, duplicate quality preference, permissions, and Continue Watching representative transfer.
- Row decoration includes title/genre/quality authority and remains untouched until profiling can separate those pure computations without changing rank or genre semantics.
- Local Series Rail title normalization remains untouched. It affects headings, coalescing, order, and relocation keys.
- SQL predicate movement, aggregate query merging, and long-lived View Plan caching are deferred. They require broader privacy and parity evidence.
- Search is explicitly outside this phase.

## Timing Privacy And Operation

Production timing is off by default with `ELVERN_LIBRARY_PLAN_TIMING_ENABLED=false`. Enabled logs contain only a 12-character random correlation ID, fixed stage durations, row/visible/rail/unique counts, and no private item or user data. The response contract is unchanged.

## Conclusion

The measurements justified two narrow internal changes and did not justify a broad View Plan rewrite. The largest remaining cost is visibility/duplicate representative work, where semantic risk is high. The next safe step is a dedicated parity corpus and sub-stage profiler for that operation, not an immediate algorithm replacement.
