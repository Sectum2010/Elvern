# Loading Strategy Phase 3: Lightweight Library API Audit

## Scope and evidence labels

This document audits the current source after Loading Strategy Phase 2 and Poster Index v1. It does not add an endpoint, change `/api/library` or `/api/library/search`, paginate results, alter search ranking, or change any response schema.

- **Confirmed:** directly established by current source or a repeatable local test.
- **Inference:** likely consequence of confirmed implementation, but not yet measured on representative hardware/data.
- **Benchmark needed:** a decision must wait for measurements.

## Current contracts

### Library collections

| Field | Current consumer and purpose | Lightweight contract assessment |
| --- | --- | --- |
| `items` | `LibraryPage` main/search/sorted grid; `LibrarySourcePage` source grid | Required, but individual items can be lighter. |
| `series_rails` | Local Series Rails on both library surfaces | Required when the view includes local rails. |
| `cloud_series_rails` | Cloud Series Rails | Required when the view includes cloud rails. |
| `continue_watching` | Root Library Continue Watching section | Required only for root views that render it. |
| `recently_added` | Root Library Recently Added section, subject to user settings | Required only for root views that render it. |
| `query` | Search response identity/debug consistency | Useful for search responses; unnecessary for an unsearched shell. |
| `arrange` | Response reflects normalized source/genre/quality/sort | Required to confirm canonical server interpretation. |
| `available_genres` | Arrange menu and invalid-genre URL recovery | Required for the root filter UI; could be versioned separately. |
| `scan_in_progress` | 2.5-second polling while a scan is active and completion notice | Required unless replaced by a separate revision/status channel. |
| `total_items` | Indexed count and source page count | Required. |

**Confirmed:** the source routes `/library/local` and `/library/cloud` do not call new server endpoints. They use the existing exact source-filtered request `/api/library?category=movies&source=local|cloud`, share the normal TanStack Library cache, and then compose the relevant `items` and rail collection. Their local `q` filters the already fetched source payload.

### `LibraryItemSummary` field audit

| Field | Current Library use | Classification for a future lightweight summary |
| --- | --- | --- |
| `id` | React keys, detail URL, playback indicator, progress cache patch, relocation item identity | Required. |
| `title` | search/source filtering and fallback card title | Required. |
| `parsed_title` | `getMovieCardTitle()` prefers `display_title` | Current UI requires at least `display_title`; the full parser diagnostics are not needed by cards. |
| `original_filename` | indirect `qualityRank` input and final title fallback; standard users receive `null` | Admin-sensitive and avoidable if quality is server-authoritative. Do not expose in a future standard-user summary. |
| `source_kind` | Local/Cloud badge and phone cloud progress compatibility | Required. |
| `source_label` | Not read by LibraryPage, LibrarySourcePage, MediaCard, or SeriesRail | Frontend-unused in Library summary; Detail/other admin surfaces use it. |
| `library_source_id` | Not read by current Library surfaces | Frontend-unused in Library summary; may be authorization/admin metadata. |
| `library_source_name` | Not read by current Library surfaces | Detail-only presentation. |
| `library_source_shared` | Not read by current Library surfaces | Frontend-unused in Library summary. |
| `library_category` | Server already filters by category; not read by current Library surfaces | Redundant for an exact-view response. |
| `library_category_path` | Not read; standard users receive `null` | Sensitive operational path; omit. |
| `library_category_name` | Not read by current Library surfaces | Candidate to omit. |
| `library_folder_role` | Not read by current Library surfaces | Candidate to omit. |
| `library_folder_path` | Not read; standard users receive `null` | Sensitive operational path; omit. |
| `library_folder_name` | Not read by current Library surfaces | Candidate to omit unless a future grouped UI needs it. |
| `poster_url` | MediaCard image identity; frontend appends `variant=card&display_width=...` | Required. |
| `edition_label` | Not rendered by current library cards | Detail/hidden-list use; omit from lightweight card unless product adds edition badges. |
| `quality_tier` | Server filtering/ordering metadata is already applied; current MediaCard recomputes rank | A future card should use one server-authoritative rank field, but changing that behavior needs a separate compatibility decision. |
| `quality_label` | Not read by current Library surfaces | Omit if `quality_tier` is retained. |
| `genres` | Server filters before response; item-level genres are not rendered | Omit from card item; retain top-level `available_genres`. |
| `genre_display` | Not read by current Library surfaces | Detail-only. |
| `hidden_for_user` | Hidden rows are already excluded; not read by Library surfaces | Redundant in visible-library response. |
| `hidden_globally` | Globally hidden rows are already excluded; not read by Library surfaces | Redundant in visible-library response. |
| `file_size` | indirect `qualityRank` input and server size sorting | Current card computation needs it; future server-authoritative quality could remove it. |
| `duration_seconds` | cloud continue-watching duration fallback on phones | Required for that compatibility path unless backend always supplies progress duration. |
| `width`, `height` | indirect `qualityRank` resolution detection | Current card computation needs both. |
| `video_codec`, `audio_codec`, `container` | indirect `qualityRank` detection | Current card computation needs them; otherwise Detail-only. |
| `year` | source-page local filtering and title context | Required. |
| `created_at`, `updated_at`, `last_scanned_at` | Not read by current Library surfaces after server ordering | Omit from exact-view card items. |
| `progress_seconds` | progress bar, Continue Watching filtering/normalization, cache patch | Required. |
| `progress_duration_seconds` | progress bar and cache patch | Required. |
| `completed` | Continue Watching behavior and progress cache patch | Required. |
| `download_access_allowed` | Not read by Library cards; meaningful in Detail | Detail-only. |

**Confirmed:** the standard-user redaction layer nulls `original_filename`, `library_category_path`, and `library_folder_path` in every top-level and rail item. A future lightweight response should omit these fields rather than serialize null placeholders.

**Confirmed:** the current quality badge is computed in the browser from title/filename, dimensions, codecs, container, and size. The backend also emits `quality_tier` and `quality_label`. This is duplicated responsibility. Consolidating it is a product/compatibility decision, not a safe incidental payload cut.

## Repeated item serialization and JSON duplication

**Confirmed:** one media row can appear in `items`, a local or cloud Series Rail, `continue_watching`, and `recently_added`. Every occurrence currently contains a complete `LibraryItemSummary` object.

**Confirmed after Poster Index v1:** poster URL resolution is request-memoized by media item ID, so duplicate section membership no longer repeats poster matching/stat work. The JSON object itself is still constructed and transferred repeatedly.

**Inference:** duplication grows most for active libraries with many franchises and recently watched titles. Exact bytes and parse/commit cost depend on the overlap and title/parser payload sizes.

Future wire formats to benchmark:

1. `entities: {id -> lightweight item}` plus section arrays of IDs.
2. One flat `items` array plus section membership records containing stable item IDs and `cardInstanceKey` ingredients.
3. Bounded section payloads with explicit continuation cursors.

The frontend must continue deriving a unique instance identity from section/rail key plus media item ID. Entity normalization must not collapse duplicate visual instances used by relocation.

## Current backend cost map

### `/api/library`

**Confirmed:** the route first calls `maybe_refresh_local_library()` and then `list_library()`.

**Confirmed:** `list_library()` resolves user settings and the effective shared local source, reads the effective poster root once, and fetches full accessible media rows. It separately fetches Continue Watching rows, watch-event aggregates, tracking-event aggregates, genre groups, global hidden item IDs, global hidden movie keys, user hidden item IDs, and user hidden movie keys. Helper calls can add app-setting/source-binding reads and conditional writes, so a fixed SQL count cannot be claimed from static source alone.

**Confirmed:** category, source, genre, quality, duplicate representative selection, hidden filtering, recent ordering, smart/explicit sorting, and rail grouping are substantially performed in Python after full row retrieval.

**Confirmed:** the same rows are decorated more than once for the main and Continue Watching paths. Rails and section lists serialize overlapping rows independently.

### `/api/library/search`

**Confirmed:** search reads all accessible rows for the user, builds an in-memory search index/score for each row in Python, sorts matches, then performs duplicate/hidden/filter processing. This phase explicitly forbids changing that algorithm or ordering.

**Benchmark needed:** row count, score time, allocation volume, and p50/p90 latency before deciding between SQL predicates, a denormalized search table, or FTS. No search optimization is implemented here.

### Poster work after Poster Index v1

**Confirmed:** one process-scoped immutable snapshot is acquired per effective poster root. A list/search/detail request passes the snapshot into serialization. The snapshot is built from one sorted directory traversal, is capped at four roots, and is replaced atomically under a lock.

**Confirmed:** exact, normalized yearful, singular/plural yearful, and unique yearless precedence remains covered by old-versus-index parity tests. A request-local memo prevents repeated poster URL resolution for an item appearing in multiple sections. The selected poster is still individually `stat`ed for its path/mtime/size URL token, preserving replacement identity.

**Confirmed:** add/remove/rename changes the root fingerprint and triggers one rebuild; poster reference mutation and scan start/end explicitly invalidate snapshots. A controlled warning and safe legacy fallback protect Library availability if a build fails.

**Inference:** Poster Index removes directory traversal as a per-item multiplier, but title parsing, item serialization, Python filtering, JSON duplication, and synchronous first-time card image generation remain meaningful costs.

### Repeatable Poster Index v1 measurement

**Confirmed on the local development host with synthetic temporary data:**

| Measurement | Result |
| --- | ---: |
| Media lookups | 1,000 |
| Poster files | 3,000 |
| Cold index build | 158.143 ms |
| Warm indexed lookup total | 3,984.492 ms |
| Warm indexed average | 3,984.492 us |
| Legacy resolver total | 118,685.788 ms |
| Legacy average | 118,685.788 us |
| Index directory iterations | 1 |
| Legacy directory iterations | 1,000 |
| Index entry stats during build | 3,000 |
| Warm lookup speedup | 29.79x |

Command: `.venv/bin/python scripts/benchmark-poster-index.py --items 1000 --posters 3000 --json`.

This measurement uses generated names and an OS temporary directory. It proves the algorithmic traversal reduction on this host; it is not a claim about production latency on every filesystem or machine.

### Card display cache

**Confirmed:** an uncached `variant=card` request still performs Pillow decode, EXIF transpose, LANCZOS resize, and optimized progressive JPEG encoding at quality 97/subsampling 0 (or optimized PNG). Cached display files retain existing private immutable cache headers.

**Inference:** simultaneous first-view uncached cards can compete for CPU and storage I/O. Poster Index does not alter this and must not be presented as a card-generation queue.

## Proposed future API, not implemented

### Three candidate designs

| Concern | Option 1: `/api/library/shell` | Option 2: `/api/library?view=summary-v2` | Option 3: normalized v2 (`items_by_id` + section IDs) |
| --- | --- | --- | --- |
| First Library shell | Fastest only if it intentionally omits deferred sections | Faster through field reduction, but still computes all current sections unless backend work also changes | Good when complete section membership ships once; normalization primarily saves bytes/parse allocations |
| Response bytes | Smallest initial response, followed by one or more section requests | Moderate reduction | Best complete-view reduction when items overlap sections |
| Backward compatibility | New endpoint, low collision with v1 | Same route with explicit mode; middleware/cache mistakes are a larger risk | New versioned contract, no silent v1 mutation |
| TanStack Query key | Separate `library-shell` and section keys; more coordination | Existing identity plus explicit response version | Existing identity plus version; one normalized query can remain atomic |
| Invalidation | Shell and section queries can become inconsistent without a shared revision | Can use current Library prefix | Can use current Library prefix and one revision |
| source/category/genre/quality/sort | Every shell/section request must repeat the exact view identity | Naturally reuses current parameters | Naturally reuses current parameters |
| q/search compatibility | Separate search shell introduces more states and layout changes | Could version search later without changing URL state | Normalized search can return one results section; search algorithm remains independent |
| Standard-user redaction | Must be duplicated across shell and each section endpoint | Existing redaction path can branch on schema | Central entity serializer can omit sensitive fields once, but needs contract tests |
| Multiuser isolation | Multiple query families increase clear-on-identity-change surface | Current user/role query identity remains applicable | Current user/role identity remains applicable |
| Progress patch | Must patch entity in each shell/section cache | Current collection patch can remain during migration | Simplest long-term: patch one `items_by_id[itemId]` entity |
| Scan polling | Shell can own status, but section revision changes must coordinate | Current behavior remains | Top-level status/revision remains in one payload |
| Detail return | Risk of target section not existing when restoration starts | Similar to current complete response | Complete section membership and entities allow immediate exact-instance render |
| SeriesRail | Deferred rail insertion can move the page after restore | Existing rail payload remains atomic | Rail membership IDs preserve exact order without duplicate item bodies |
| Future virtualization | Multiple independently loaded sections complicate index mapping | Similar to current arrays | Strongest stable entity/section index foundation |
| Future resource detector | Can request sections progressively, but increases layout instability | Only payload size changes | Detector can choose mount/admission without changing data truth |
| Migration difficulty | High: orchestration, revisions, skeleton dimensions, and extra failure states | Lowest short-term | Medium: frontend adapter plus normalized contract |
| Rollback | Disable shell feature flag and return to v1 | Remove `view` opt-in | Disable v2 feature flag; retain v1 endpoint and adapter fixtures |

### Recommendation

Prefer **Option 3 as a versioned endpoint**, initially returning the complete current view atomically. It provides the main repeated-item byte reduction and a future virtualization-friendly section index without inserting rails after desktop relocation. Option 2 is the lowest-effort experiment but keeps duplicated item bodies unless it also adopts normalization. Option 1 should not be the first rollout because deferred rail/Continue Watching insertion can recreate the exact upper-layout movement that the desktop return transaction now has to correct.

The first v2 experiment should still contain all section membership required for first layout: visible root grid/search results, rail order and membership, Continue Watching, Recently Added visibility/membership, `total_items`, `available_genres`, and `scan_in_progress`. Heavy technical/detail-only fields can be omitted. Do not split any data that determines section presence, rail count, item order, or card height into a later request unless stable reserved geometry and revision matching are proven.

Add one opaque per-user view `revision` or ETag that changes when membership/order/card-summary truth changes. A background response with a different revision can replace the normalized entity/section snapshot atomically under current stale-while-revalidate behavior. Do not progressively merge rails from mismatched revisions.

### Compatibility-first shape

A future endpoint should be versioned rather than silently shrinking `LibraryListResponse`, for example `/api/library/v2/summary`. A candidate response can include:

- view identity: normalized category/source/genre/quality/sort/query;
- `revision` or section revisions;
- `scan_in_progress`, `total_items`, and `available_genres`;
- lightweight entity records with card title, year, poster URL, source kind, server-authoritative quality identity, and progress fields;
- section membership records for grids and rails;
- stable cursor/bound information where needed.

Do not use filesystem paths, raw filenames, technical probe diagnostics, download authorization, or Detail playback fields in the lightweight contract.

### Migration sequence

1. Add response-byte and timing instrumentation to the existing endpoint without recording titles, queries, or paths.
2. Capture golden fixtures for admin and standard-user payload/redaction behavior.
3. Introduce a versioned endpoint behind a frontend feature flag; keep v1 untouched.
4. Add an entity adapter that reconstructs current section item objects while preserving exact `cardInstanceKey` semantics.
5. Compare old/new response membership, ordering, progress, permission filtering, poster URLs, and relocation in shadow tests.
6. Move source/category/filter work toward indexed SQL only after parity is proven.
7. Add bounded sections/pagination only after relocation and responsive rail contracts support missing DOM/data targets.
8. Retire v1 only after representative device/browser benchmarks and rollback rehearsal.

Mapped phases:

- **Phase A:** keep v1; add versioned normalized response, admin/standard-user golden contract tests, and old-versus-new membership/order shadow comparison.
- **Phase B:** opt in `LibraryPage`, then `LibrarySourcePage`, behind an independently reversible frontend flag. Preserve v1 query code and invalidate both versions during transition.
- **Phase C:** run response, browser commit, desktop return, mobile rotation, and real-device benchmarks. Switch the default only if parity and thresholds pass; rollback is the feature flag, not a database migration.

## Security and authorization contract

- Query identity must remain scoped by user ID and role/permission identity.
- Backend visibility, age access, hidden/global-hidden, duplicate, and source ownership decisions remain authoritative.
- A lightweight endpoint must not depend on frontend filtering for access control.
- Standard-user path/filename redaction must become omission, never accidental reintroduction through normalized entities.
- Revision/cache keys must not encode titles, paths, tokens, or private query text into shared logs.
- CDN/shared caching is inappropriate for private per-user library payloads without a separate proven security design.

## Benchmark plan

### Dataset matrix

Use synthetic or approved fixtures at 100, 500, 1,000, and 3,000 visible items. Include 0%, 25%, and 60% overlap between main grid, rails, Continue Watching, and Recently Added. Test 1x and 3x poster counts relative to items, with high exact-match and high normalized/yearless fallback mixes.

### Backend measurements

- SQL statement count and cumulative SQL time per endpoint.
- rows fetched versus rows returned.
- Python filtering, dedupe, rail construction, title parsing, serialization, and JSON encoding time.
- response bytes before/after compression and repeated-item byte share.
- poster snapshot cold build, warm lookup, entry stat count, rebuild count, fallback count, and lock wait time.
- process RSS/peak allocation and p50/p90/p99 TTFB.
- cold and warm card-generation latency, CPU time, and concurrent I/O.

### Frontend measurements

- transfer, JSON parse, query-cache insertion, React render/commit, and first stable card layout.
- DOM node count and JS heap where supported.
- Detail return time to correct exact-instance viewport position.
- background refetch reorder and restore-correction count.
- source-page query filtering cost for 100/500/1,000/3,000-item exact-source payloads.

### Procedure

- Run at least five repetitions per cell; report median, p90, and worst result.
- Separate cold process/index/cache runs from warm runs.
- Record browser, OS, CPU, storage, viewport, DPR, and build commit.
- Use generated identifiers only. Do not log titles, paths, poster URLs, auth data, or library queries.
- Establish acceptance thresholds before selecting pagination size, normalized wire shape, or search technology.

## Decisions and remaining risks

**Safe conclusion now:** Poster Index v1 removes the repeated poster-directory traversal bottleneck without requiring a public API change.

**Not yet justified:** a specific lightweight field cut, normalized wire format, pagination size, SQL/FTS search design, or card-generation concurrency policy. Each changes compatibility or product behavior and needs the benchmark/shadow sequence above.

**Next single step:** instrument an approved synthetic 100/500/1,000/3,000-item benchmark of the existing endpoint, including SQL count, response bytes, repeated-item share, serialization time, and browser parse/commit time. Use that evidence to choose the smallest versioned v2 experiment.
