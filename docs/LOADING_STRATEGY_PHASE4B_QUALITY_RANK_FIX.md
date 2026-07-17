# Loading Strategy Phase 4B: Server-Authoritative Quality Rank

## Scope

Phase 4B fixes one Phase 4 rollout blocker: the same media item could receive different visible quality ranks by role or API version. After that fix and final contract hardening, v2 is now the frontend default for non-search Root, Local, and Cloud views. This activation does not change search, pagination, virtualization, poster quality, playback, relocation, orientation restore, or scheduler behavior.

## Confirmed root cause

The v2 route passed a role-derived `include_original_filename_for_quality` flag. Standard-user serialization copied the internal row, set `original_filename` to `None`, and then calculated `quality_rank`. Filename-only signals such as REMUX, 2160p, Atmos, and HEVC disappeared before scoring.

Library filtering had already calculated `quality_tier` from the complete internal row. A standard user could therefore select Diamond, receive the item in the Diamond result, and see a lower badge on that same card. v1 did not carry a full `quality_rank`, so the frontend and shadow comparer recalculated from the already-redacted v1 item and hid the authority mismatch.

The old role test encoded this regression as expected behavior by comparing standard-user v2 against a calculation made from standard-user-visible fields. Phase 4B replaces that assumption with role-invariant server authority.

## Canonical rank contract

`backend/app/services/library_quality_rank_service.py::build_library_quality_rank(row)` is the sole authority for card rank and filter tier identity. It receives a complete internal media row and returns only derived fields:

- `key`
- `label`
- `score`
- `description`
- `detected`
- `tooltip`

`quality_tier_for_row()` now delegates to that helper and returns `quality_rank["key"]`. The separate `_quality_sort_key` remains because duplicate preference and product sorting are distinct semantics.

The shared Python/Vitest golden fixture preserves the pre-Phase-4 frontend algorithm. Phase 4B does not alter thresholds, points, labels, or token rules. It covers REMUX, BluRay, BDRip, BRRip, WEB-DL, WEBRip, 2160p/4K/UHD, 1080p, 720p, 480p/576p, Atmos, TrueHD, DTS-HD, DTS, DDP/EAC3/AC3, AAC, HEVC/x265/H265, AV1, H264/x264/AVC, size boundaries, filename-only metadata, technical-only metadata, empty metadata, and precedence.

One compatibility difference was confirmed: the pre-Phase-4 frontend source detector contains `bdrip` twice and does not award BluRay source points for `brrip`, while the former independent backend tier detector did recognize BRRip. The fixed contract follows the required pre-Phase-4 visible frontend behavior. This known token quirk is documented rather than changed in a rank-authority remediation.

## v1 and v2 data flow

v1 now adds an optional schema field, `quality_rank`, without removing or renaming existing fields. `_serialize_media_item()` computes it before route-level standard-user redaction. The same request uses a media-ID keyed rank memo across main items, rails, Continue Watching, and Recently Added, so repeated visual instances do not repeat rank calculation.

`/api/library`, source-filtered root requests, and `/api/library/search` therefore receive the same authoritative rank. Existing v1 fields and ordering remain unchanged.

v2 no longer accepts or passes any role-dependent rank flag. `_serialize_media_item_v2()` always calculates from the complete internal row and serializes only the derived rank into the normalized entity. The v2 lightweight field allowlist is unchanged, and rank remains part of the opaque revision input.

## Role parity and privacy

Admin and standard users now receive identical rank key, label, score, description, detected labels, and tooltip for the same visible item. Derived labels such as REMUX, 2160p, and Atmos are product output, not raw filenames or paths.

Privacy boundaries remain:

- standard-user v1 still nulls `original_filename`, `library_category_path`, and `library_folder_path`;
- standard-user detail redaction retains its broader existing path and diagnostics policy;
- v2 entities still reject raw filename, file path, library paths, dimensions, codecs, container, file size, and unknown fields;
- shadow diagnostics still contain only category, numeric item ID, section, and hashed rail key.

## Frontend resolver and shadow truth

`resolveLibraryQualityRank(item)` is the single `MediaCard` resolver:

1. A complete `item.quality_rank` is returned unchanged.
2. If it is absent but a valid `quality_tier` exists, badge key, label, description, and tooltip tier text follow the server tier. The legacy calculation supplies only score and detected details.
3. If neither server field exists, the legacy client algorithm is the final rolling-deployment fallback.

The shadow comparer now compares the complete v1 server `quality_rank` with v2. Missing or incomplete v1 rank records `v1_quality_rank_missing`; it is never silently reconstructed from redacted metadata. Production shadow mismatches keep rendering v1. Tests still fail on mismatch.

## Immediate-risk audit

The Phase 4 focused audit confirmed:

- v2 contract errors, endpoint 404, and the explicit backend-disabled error are the only capability fallbacks;
- 401, 403, and ordinary 500 errors are not disguised as v2 capability failures;
- the central `library` query prefix still invalidates v1, v2, and shadow caches;
- progress saves patch every v1 visual instance and only `v2.items_by_id` for normalized data;
- progress patches mark caches stale and do not manufacture a new v2 revision;
- completion refetch targets active render queries, excluding shadow-only queries;
- root formal search remains v1;
- Local/Cloud `q` remains URL-backed, debounced, client-side source filtering;
- the v2 adapter preserves section arrays and duplicate visual instances;
- desktop return restore and mobile orientation implementations are untouched;
- standard/admin redaction and the backend kill switch remain intact.

No immediate related defect required a broader architecture change.

## Deployment mode audit

Frontend source defaults `VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE` to `on` when it is absent or empty. Explicit `off`, `shadow`, and `on` remain valid; an invalid non-empty value fails closed to `off`. No tracked env example, Docker file, systemd template, CI workflow, Vite script, frontend preview command, or deploy file overrides that mode. The current operator-managed deploy env also leaves the frontend mode unset, so a rebuilt bundle uses the source default.

Backend `ELVERN_LIBRARY_SUMMARY_V2_ENABLED` defaults to `true`, and the current deploy env does not override it to false. The emergency backend capability stop remains available.

Live mode must be checked in browser Network tools against the deployed bundle:

- non-search views request only v1: `off`;
- non-search views request v1 plus background v2: `shadow`;
- non-search views request only v2, while formal search still requests v1: `on`.

Changing a Vite mode requires a frontend rebuild.

Before final activation, `validateLibrarySummaryV2Payload()` was hardened to call `isCompleteLibraryQualityRank()` after exact field validation. Unknown rank keys, non-finite scores, malformed `detected` values, and missing or incorrectly typed rank fields now raise `LibrarySummaryV2ContractError`. That error retains the existing narrow v1 capability fallback.

Activation and rollback values are:

```text
# Default non-search Root/Local/Cloud mode; explicit form
VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE=on

# One-step frontend rollback; rebuild the frontend after changing it
VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE=off

# Emergency backend capability stop
ELVERN_LIBRARY_SUMMARY_V2_ENABLED=false
```

## Rollout readiness

The final activation requires complete local validation plus Chromium Root, Local, and Cloud Detail-return coverage. Synthetic and mocked-browser parity do not prove a representative private library or real devices.

Deployment verification must still inspect authenticated Network traffic and manually check desktop, iPhone, iPad, and Android for Root, Local, Cloud, formal search, Detail return, progress, Continue Watching, Recently Added, badges, and tooltips. Firefox/WebKit and real-device performance remain residual risks, so the frontend rollback and backend emergency stop must stay rehearsable.

## Benchmark

The backend benchmark uses only an isolated synthetic database at 100, 500, 1000, and 3000 items with 0%, 25%, and 60% section overlap. It measures view-plan build, v1/v2 serialization, JSON encoding, payload bytes, gzip bytes, and poster resolver counts. The frontend benchmark measures JSON parsing, v2 adaptation, and TanStack cache insertion with synthetic payloads.

Phase 4B reran five repetitions per synthetic cell before and after adding v1 `quality_rank`. The byte results were:

| Items | Overlap | v1 raw before | v1 raw after | v1 gzip before | v1 gzip after |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 0% | 159,953 | 193,329 | 3,847 | 4,446 |
| 100 | 25% | 204,184 | 246,798 | 4,773 | 5,450 |
| 100 | 60% | 253,984 | 307,028 | 5,762 | 6,486 |
| 500 | 0% | 730,360 | 882,936 | 13,545 | 15,815 |
| 500 | 25% | 916,915 | 1,108,529 | 17,047 | 19,657 |
| 500 | 60% | 1,166,109 | 1,409,873 | 21,358 | 24,157 |
| 1,000 | 0% | 1,443,362 | 1,744,938 | 25,469 | 29,825 |
| 1,000 | 25% | 1,807,911 | 2,185,775 | 32,005 | 36,717 |
| 1,000 | 60% | 2,306,311 | 2,788,475 | 39,952 | 45,433 |
| 3,000 | 0% | 4,297,362 | 5,194,938 | 72,991 | 85,554 |
| 3,000 | 25% | 5,373,911 | 6,496,775 | 91,167 | 104,884 |
| 3,000 | 60% | 6,869,913 | 8,305,677 | 114,934 | 131,007 |

The v1 raw payload increased by about 20.87% to 20.91%; gzip increased by 12.57% to 17.21%. This is the expected rolling-deployment cost of repeating the authoritative rank in each v1 visual item instance. The v2 raw and gzip byte deltas were exactly zero in all 12 cells. After the change, v2 remained 70.77% to 83.40% smaller raw and 55.08% to 68.88% smaller with gzip than v1. Timing values are diagnostic and must not be treated as a cross-machine performance guarantee.

The synthetic frontend benchmark also completed for all 12 cells. At 3,000 items, median v2 JSON parse was 3.40-4.07 ms and median normalized adaptation was 11.69-13.39 ms across the tested overlap values. It does not measure browser render/commit, stable layout, DOM count, or Detail return error, so those remain real-browser and real-device rollout gates.

## Explicitly not implemented

- v2 search or search optimization
- pagination or virtualization
- resource detection
- cross-platform smart poster scheduler expansion
- poster dimensions, JPEG quality, or cache algorithm changes
- playback, audio, or subtitle changes
- relocation, restore tolerance, or orientation changes
- database migration or private-library telemetry
