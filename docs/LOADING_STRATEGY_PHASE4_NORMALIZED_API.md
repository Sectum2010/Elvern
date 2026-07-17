# Loading Strategy Phase 4: Versioned Normalized Library Summary API

## Status and scope

Phase 4 adds an opt-in, versioned, normalized Library response while retaining every v1 route. The frontend default remains `off`. Formal root search remains on v1. No pagination, virtualization, search algorithm change, smart-poster expansion, poster-quality change, or playback change is part of this phase.

## Endpoint

`GET /api/library/v2/summary`

Supported view parameters:

- `category`: `movies`, `tv`, `anime`, or `cartoon`
- `source`: `all`, `local`, or `cloud`
- `genre`
- `quality`: the existing Library quality tiers
- `sort`: the existing Library sort modes

An empty `q` is accepted as no search. A non-empty `q` returns HTTP 400 with guidance to use `/api/library/search`. The route uses the existing authenticated user, source ownership/sharing, hidden/global-hidden, duplicate, category, genre, quality, sorting, scan freshness, and progress rules.

Response headers are:

```text
Cache-Control: private, no-store
Vary: Cookie
```

This prevents browser/shared HTTP persistence. TanStack Query still provides the existing user-isolated in-memory stale-while-revalidate cache.

## Response contract

```json
{
  "schema_version": "library-summary-v2",
  "revision": "opaque-64-character-sha256",
  "view": {
    "category": "movies",
    "source": "all",
    "genre": null,
    "quality": "all",
    "sort": "smart"
  },
  "items_by_id": {
    "42": {
      "id": 42,
      "title": "Example Movie",
      "year": 2024,
      "poster_url": "/api/library/item/42/poster?v=opaque-token",
      "source_kind": "local",
      "quality_rank": {
        "key": "gold",
        "label": "Gold",
        "score": 12,
        "description": "Excellent quality, just below reference tier.",
        "detected": ["WEB-DL", "2160p"],
        "tooltip": "Excellent quality, just below reference tier. Detected: WEB-DL · 2160p."
      },
      "duration_seconds": 7200,
      "progress_seconds": 120,
      "progress_duration_seconds": 7200,
      "completed": false
    }
  },
  "sections": {
    "item_ids": [42],
    "series_rails": [],
    "cloud_series_rails": [],
    "continue_watching_item_ids": [42],
    "recently_added_item_ids": [42]
  },
  "available_genres": ["Action"],
  "total_items": 1,
  "scan_in_progress": false
}
```

Every section ID must exist in `items_by_id`. Each entity is transferred once, while the same ID can still produce distinct visual cards in multiple sections through the existing section/rail `cardInstanceKey` rules. The frontend contract validator rejects dangling IDs, missing fields, and unversioned extra fields instead of rendering a partial snapshot.

## Lightweight entity and privacy

The v2 card entity contains only:

- `id`, `title`, `year`, `poster_url`, and `source_kind`
- server-authoritative `quality_rank`
- `duration_seconds`, `progress_seconds`, `progress_duration_seconds`, and `completed`

It does not transfer raw filename, media or library paths, source internals, parser diagnostics, technical probe payloads, subtitles, audio tracks, streams, download authorization, timestamps, hidden flags, item-level genres, dimensions, codecs, container, or file size. This omission applies to administrators and standard users. Detail/admin endpoints remain the place for authorized full metadata.

## Backend View Plan

`build_library_view_plan()` owns the shared business truth:

1. Load the accessible rows and per-user progress.
2. Apply category, source ownership/sharing, hidden/global-hidden, duplicate, genre, and quality rules.
3. Sort the main collection.
4. Build local/cloud rail membership, Continue Watching, and Recently Added membership.
5. Acquire one Poster Index snapshot for the request.

`serialize_library_view_v1()` preserves the existing repeated-object v1 contract. `serialize_library_view_v2()` serializes each unique row once and writes ID-only section membership. v2 is not produced by stripping an already built v1 response. Search deliberately keeps its existing independent v1 code path.

## Quality rank authority

The backend helper is a compatibility port of the existing frontend `getQualityRank()` behavior, including rank key/label/score, description, detected labels, and tooltip. Python and Vitest read the same golden fixture covering source, resolution, audio, codec, size boundaries, filename-only metadata, technical-fields-only metadata, empty metadata, and token precedence.

Phase 4B makes `backend/app/services/library_quality_rank_service.py` the single card-rank and quality-tier authority. Both v1 and v2 compute `quality_rank` from the complete internal row before any role redaction, so admin and standard users receive the same derived rank. v1 adds the rank object to its existing item shape; v2 keeps its existing lightweight entity shape. Raw filename, path, dimensions, codecs, container, and file size still do not cross the v2 wire, and standard-user v1 redaction remains in place.

`MediaCard` resolves rank in rolling-deployment order: complete server `quality_rank`, then server `quality_tier` identity plus legacy detected details, then the legacy client calculation only when neither server field exists. Shadow comparison treats v1 `quality_rank` as truth and records `v1_quality_rank_missing` rather than recalculating from redacted v1 fields.

## Opaque revision

The server SHA-256 hashes a canonical, key-sorted JSON representation of the complete normalized snapshot without the revision field. The 64-character digest is stable for the same user/view/card-summary truth and changes with entity membership/order, rail membership/order, Continue Watching or Recently Added order, title/year/source, poster token, quality rank, progress/completion, available genres, or scan state.

The digest does not embed a username, query, path, filename, title, or poster URL as readable text. It is snapshot identity for diagnostics and shadow comparison only. There is no ETag or HTTP 304 behavior in Phase 4.

## Frontend feature modes

One resolver reads `VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE`:

| Mode | Root non-search | Root formal search | Local/Cloud source | Render source |
| --- | --- | --- | --- | --- |
| `off` (default) | v1 | v1 search | v1 | v1 |
| `shadow` | v1 plus background v2 | v1 search | v1 plus background v2 | v1 |
| `on` | v2 | v1 search | v2 | v2 |

Shadow v2 does not participate in loading, rendering, relocation, orientation restore, or visible errors. The semantic comparer checks view identity, section membership/order, card fields, progress, poster identity, and quality parity. Diagnostics contain only mismatch category, numeric item ID, section type, and a hashed rail key. Console output is disabled unless local debug key `elvern_library_summary_v2_debug` is explicitly enabled. Vitest treats a shadow mismatch as a test failure; production keeps the v1 UI.

## Query cache, progress, and invalidation

Keys are separate:

```text
["library", "v1", identity]
["library", "v2", identity]
["library", "shadow-v2", identity]
```

Identity retains user ID, role, category, source, genre, quality, sort, and query. v2 summary query is normalized to `""`. All versions use the existing `staleTime = 5 minutes` and `gcTime = 4 hours` and remain memory-only.

Central invalidation uses the `library` prefix, so current scan, hide/restore, genre/age, poster, cloud, and user-settings invalidation paths cover v1, v2, and shadow caches. Logout, 401, null identity, user/role/age identity changes, and maintenance-auth session termination remove all protected Library queries. A business 403 requests auth revalidation but does not clear the same identity's cache.

Routine progress saves patch existing v1 item instances and `v2.items_by_id[String(itemId)]`, mark all Library queries stale with `refetchType=none`, and do not invent a new authoritative revision. A completion transition can silently refetch active render queries; shadow queries do not become render queries.

## Fallback and rollback

The backend capability switch is `ELVERN_LIBRARY_SUMMARY_V2_ENABLED` and defaults to enabled so the opt-in frontend can probe it. Disabled returns an explicit `library_summary_v2_disabled` capability error.

In frontend `on` mode, only these conditions use v1 fallback:

- endpoint 404;
- explicit backend disabled capability error;
- v2 contract validation failure.

Authentication 401/403 and ordinary 500 errors are not disguised as capability fallback.

Rollout commands/configuration:

```text
# Default and immediate frontend rollback
VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE=off

# Background semantic comparison while v1 renders
VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE=shadow

# Opt-in v2 rendering for non-search views
VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE=on

# Emergency server capability stop
ELVERN_LIBRARY_SUMMARY_V2_ENABLED=false
```

Changing a Vite mode requires rebuilding the frontend artifact. No database migration or v1 removal is involved.

## Relocation and rendering compatibility

Phase 4 does not modify `libraryNavigation`, `desktopLibraryReturnRestore`, `viewportAnchor`, mobile orientation restore, correction limits, tolerance, settle timing, or user-intent cancellation. The adapter reconstructs the same section arrays and reuses one entity object across arrays without collapsing distinct DOM instances. Root formal search remains entirely v1.

Chromium desktop Playwright covers v2-on root, Local, and Cloud deep-card Detail returns with the existing <=8px tolerance. The root fixture also proves one entity can render in Continue Watching and Other Movies with two distinct instance keys. Existing unit tests continue to cover stale correction, user-scroll cancellation, source return, and iPhone/iPad orientation behavior.

## Benchmark

Repeatable commands:

```bash
.venv/bin/python scripts/benchmark-library-summary-v2.py --repetitions 5 --json-output tmp/library-summary-v2-final.json
node frontend/scripts/benchmark-library-summary-v2.mjs --json-output tmp/library-summary-v2-frontend.json
```

The Python benchmark creates isolated synthetic app-data and media roots, initializes a temporary Elvern database, and invokes the production View Plan and serializers. It never reads the live database or private titles. The Node benchmark measures real v2 contract validation/adapter code and TanStack insertion. Each 100/500/1000/3000-item, 0/25/60%-overlap cell runs five times and records p50, p90, and worst.

The generated JSON reports are the authoritative full tables. Phase 4 does not reuse the Poster Index 29.79x result as a claim about complete Library speed. Browser React commit, first stable layout, heap, and real-device measurements remain separate from Node parse/adapter measurements.

The 2026-07-16 local run used commit `fc53ebc`, Linux/aarch64, 20 CPU cores (Cortex-X925/Cortex-A725), Python 3.12.3, Node 22.12.0, and an ext4 filesystem on NVMe. Chromium desktop validation used Playwright 1.60.0. All benchmark rows used generated titles and isolated temporary databases; no live database, media names, paths, or user queries were read.

Selected backend p50 results are below. `plan` is the shared production View Plan cost; serializer columns exclude that shared cost.

| Items | Section overlap | Plan ms | v1 serialize ms | v2 serialize ms | Raw bytes reduction | Gzip reduction | v1/v2 poster resolves |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 0% | 321.47 | 496.55 | 422.73 | 68.32% | 48.10% | 112 / 100 |
| 100 | 60% | 288.89 | 569.09 | 423.77 | 79.94% | 63.14% | 178 / 100 |
| 1000 | 0% | 3038.39 | 4948.95 | 4258.17 | 65.06% | 48.97% | 1012 / 1000 |
| 1000 | 60% | 2608.70 | 5652.24 | 4281.47 | 78.03% | 64.61% | 1618 / 1000 |
| 3000 | 0% | 8913.19 | 14746.13 | 12625.53 | 64.67% | 48.88% | 3012 / 3000 |
| 3000 | 60% | 7722.81 | 16806.06 | 12756.08 | 77.78% | 64.49% | 4818 / 3000 |

The v2 serializer is faster in every synthetic cell because it computes and transfers one entity per unique item instead of repeating full card objects. The shared View Plan remains expensive and scales roughly linearly, so these results are not evidence of equivalent end-to-end response or render improvement on a real library. Poster Index directory iterations were zero because the synthetic benchmark intentionally contains no private or generated poster files.

Selected frontend p50 results:

| Items | Section overlap | v1 parse ms | v2 parse ms | v2 adapter ms | v1/v2 Query cache insert ms |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 0% | 0.078 | 0.098 | 0.441 | 0.106 / 0.018 |
| 1000 | 60% | 1.310 | 1.335 | 5.426 | 0.018 / 0.005 |
| 3000 | 0% | 2.829 | 4.249 | 12.924 | 0.138 / 0.010 |
| 3000 | 60% | 3.795 | 3.703 | 12.205 | 0.118 / 0.008 |

The adapter cost is measurable but remains under 13 ms p50 at 3000 synthetic items on this machine. Node does not measure React render/commit, first stable layout, DOM count, browser memory, or Detail-return error; those remain browser/real-device work. Contract and synthetic shadow parity tests produced zero unexplained mismatches. No representative private-library shadow run was performed, so Phase 4 does not recommend changing the default from `off` to `on` yet.

## Known limitations and future work

Phase 4 does not implement:

- search database optimization or SQLite FTS;
- v2 formal search;
- pagination or cursors;
- virtual loading/grid/rail virtualization;
- resource detector/adaptive loading profiles;
- cross-platform smart poster scheduling;
- card-generation concurrency/prewarming;
- persistent Poster Index;
- pinch-zoom restriction;
- JPEG quality or poster-width changes.

The default remains `off`. A future default-on decision requires an explained zero-mismatch shadow run against representative approved libraries plus real desktop/mobile/tablet render, memory, rotation, and return benchmarks.
