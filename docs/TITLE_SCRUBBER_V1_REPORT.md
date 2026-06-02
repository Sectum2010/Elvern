# Title Scrubber v1.0.0 Report

## Executive Summary

Title Scrubber v1.0.0 is the stabilized deterministic movie-title parser used by Elvern runtime parsing. It does not use LLM/AI title rewriting, does not write or rewrite database rows, and does not batch-rescrub existing `media_items`.

On the expanded 123,892-title benchmark set, v1.0.0 reached a 99.44% TRUE pass rate:

- TRUE failures: 689
- 99% target threshold: <= 1,238 TRUE failures
- Margin under 99% threshold: 549
- TRUE_FAIL_OVERTRIM_REAL: 27
- Legacy suspected failures: 12,247

The parser is runtime-only. Existing stored rows are unchanged unless a separate approved dry-run/rescrub phase is introduced later.

## Scope

In scope:

- Movie filename and stored-title normalization for display title, base title, and poster-match title.
- TV/anime/cartoon episode identity preservation, including `S01E01`, `S1E1`, `1x02`, `E01`, `EP01`, and `OVA 01`.
- Poster matching safe variants that are broader than display title.
- Deterministic cleanup of technical metadata, release-year suffixes, bracket spans, country-year blocks, title-number cases, dash-title continuations, and known edition/decorator suffixes.

Out of scope:

- Semantic aliasing.
- TMDb/IMDb lookup.
- LLM/AI cleanup.
- Fuzzy grouping.
- Direct DB rewrite.
- Age grouping or duplicate hiding changes.
- Frontend UI changes.
- Playback, audio, subtitles, prewarm, Route2, native-HLS, adaptive, or cloud changes.

## Final Benchmark

Sample file: `/home/sectum/Projects/Elvern/tmp/Movie Name DB.txt`

| Run | Sample size | TRUE failures | TRUE pass rate | TRUE overtrim | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| Original broad diagnostic | n/a | n/a | n/a | n/a | Earlier heuristic was not apples-to-apples with strict TRUE classification. |
| Phase 1.7 | 75,814 | 10,756 | 85.81% | broad TRUE overtrim bucket | First strict TRUE classifier. |
| Phase 1.8 | 75,814 | 5,605 | 92.61% | 365 | Bracket span and release-year grammar hardening. |
| Phase 1.9 | 75,814 | 2,109 | 97.22% | 205 | Metadata suffix, release group, and genre descriptor hardening. |
| Phase 2.0 | 75,814 | 1,719 | 97.73% | 199 | Director/year parentheticals and trailing extras hardening. |
| Phase 2.1 / v1.0.0 initial | 75,814 | 519 | 99.32% | 23 | Stabilized post-year, country-year, title-number, and classifier cleanup. |
| v1.0.0 expanded DB run | 123,892 | 689 | 99.44% | 27 | Latest run after appending additional source title lists to `tmp/Movie Name DB.txt`. |

Latest expanded v1.0.0 classification counts:

| Classification | Count |
| --- | ---: |
| PASS | 108,136 |
| FALSE_POSITIVE_CLEAN_OUTPUT | 8,537 |
| EXPECTED_COLLECTION_OR_RANGE | 4,681 |
| EXPECTED_EDITION_STRIP | 963 |
| EXPECTED_EVENT_OR_SPORTS | 886 |
| TRUE_FAIL_RELEASE_YEAR_GRAMMAR | 270 |
| TRUE_FAIL_DASH_TITLE | 201 |
| TRUE_FAIL_METADATA_SUFFIX | 187 |
| TRUE_FAIL_OVERTRIM_REAL | 27 |
| TRUE_FAIL_BRACKET_SPAN | 4 |

Major Phase 2.0 -> v1.0.0 bucket reductions:

| Bucket | Phase 2.0 | v1.0.0 | Delta |
| --- | ---: | ---: | ---: |
| TRUE_FAIL_RELEASE_YEAR_GRAMMAR | 685 | 234 | -451 |
| TRUE_FAIL_METADATA_SUFFIX | 550 | 165 | -385 |
| TRUE_FAIL_OVERTRIM_REAL | 199 | 23 | -176 |
| TRUE_FAIL_DASH_TITLE | 180 | 93 | -87 |
| TRUE_FAIL_BRACKET_SPAN | 105 | 4 | -101 |

The Phase 2.0 comparison table is retained as historical context from the original 75,814-title benchmark. The current expanded run is the 123,892-title v1.0.0 row above.

This document is the canonical report. Historical generated `tmp/title-scrubber-v1.0.0-*` outputs are diagnostic artifacts, not runtime dependencies.

## Algorithm Architecture

The parser evaluates available title candidates from trusted stored title, original filename, and provided year context. It distrusts dirty stored titles when filename evidence is cleaner and safer.

Key components:

- Candidate selection chooses the safest available display-title source.
- Dirty stored-title distrust prevents metadata-heavy stored titles from beating cleaner filename parses.
- Balanced bracket span parser removes metadata spans without slicing real title text.
- Explicit year block parser handles `(YYYY)`, `[YYYY]`, director/year parentheticals, and alternate-title year parentheticals.
- Release-year metadata suffix grammar cuts strong technical/source/language suffixes after a recognized release year.
- Country-year bracket grammar parses blocks such as `[1984 - USA]` and `[2024 - France + Taiwan]`.
- Post-year actor/decorator technical suffix grammar strips suffixes only when technical metadata anchors the post-year tail.
- Dash title continuation preservation protects real alternate titles and translation titles.
- Genre descriptor stripping removes pure genre descriptors in strict suffix context.
- Compact year + metadata suffix handling supports forms such as `[2008]BRRip` and `(1955)Mp-4`.
- Star-year handling parses `*YYYY*` release-year blocks.
- Label-based language/subtitle suffix handling strips `Language:`, `Lang:`, `Subs:`, hardcoded subs, `audio-no subs`, `srt`, and similar post-year suffixes.
- Leading decorator/prefix removal is narrow and requires a known decorator/person/franchise prefix plus a plausible title/year on the right side.
- Title-number preservation protects four-digit title tokens before a different release year, such as `Blade Runner 2049`.
- Over-trim guards reject article-only output and protect meaningful numbers, roman numerals, acronyms, slash titles, and episode identities.
- Poster candidate variants are separated from display title; broader poster variants do not rewrite the displayed movie title.

## Protected Regression Set

The v1.0.0 protected set includes:

- `The Never Ending Story (1984) ITA-ENG...`
- `John Wick Chapter 2`
- `Big Hero 6`
- `Inside Out 2`
- `The BFG`
- `Kingdom of Heaven DC`
- `Legend 1985 DC`
- `LEGO DC`
- `DC League`
- `The Final Cut`
- `A Final Cut For Orson 40 Years in The Making`
- `V/H/S`
- `V/H/S: Viral`
- `[REC]`
- `[18+]`
- `S01E01`
- `S1E1`
- `1x02`
- `E01`
- `EP01`
- `OVA 01`
- `Blade Runner 2049`
- `Wonder Woman 1984`
- `Death Race 2000`
- `Argentina 1985`
- `The Italian Job`
- `The French Connection`
- Nickarad actor/technical suffix rows
- Country-year bracket rows
- Star-year rows
- `1001 Movies` rows
- Compact `[2008]DVDRip` rows
- Label `Language` / `Subs` suffix rows

The named regression test is `test_title_scrubber_v1_protected_regression_set` in `backend/tests/test_media_title_parser.py`.

## Remaining 689 TRUE Failures

The latest expanded benchmark leaves 689 TRUE failures across five buckets. The examples below are representative rows from the latest v1.0.0 classified failure output. They are review data only; no parser changes were made from this report pass.

### TRUE_FAIL_RELEASE_YEAR_GRAMMAR

Count: 270. Status: safe v1.0.1 candidate where a visible four-digit release year is followed by strong technical/source metadata. Manual review is still needed for concerts, ranges, and collection-like rows.

| Example input | Current output | Review status |
| --- | --- | --- |
| `Black Phone 1-2 SAGA (2021-25) 720p h264 Ac3 5.1 Ita Eng-MIRCrew` | `Black Phone 1-2 SAGA (2021-25)` / no year | Manual review: range and saga wording. |
| `AC/DC - Live At Donington 1991 [Full Concert 1080p] 88` | `AC/DC - Live At Donington 1991 88` / no year | Out-of-scope or review: concert row. |
| `A.Question.of.Silence.1982.(Dutch-1001.Movies).720p.x264-Classics` | `A Question of Silence 1982 (Dutch-1001 Movies)` / no year | Safe candidate: year before known collection/decorator span. |
| `Atomic War Bride [1960 - Yugoslavia] [English Audio] war drama` | `Atomic War Bride [1960 - Yugoslavia] war drama` / no year | Safe candidate: country-year plus audio/genre suffix. |
| `Mohawk 1956[XviD-Ita Mp3][TNT Village]` | `Mohawk 1956 [TNT Village]` / no year | Safe candidate: compact year plus bracket metadata. |

Recommended test ideas for v1.0.1:

- Compact year followed by bracketed technical metadata parses the release year.
- Country-year followed by audio/genre suffix preserves title and parses year.
- Concert/event rows remain review or out-of-scope rather than being over-normalized.

### TRUE_FAIL_DASH_TITLE

Count: 201. Status: mixed. Safe v1.0.1 candidates exist where the right side of the dash is a real subtitle or alternate-language title before the year. High caution is required because many dash tails are release groups or metadata.

| Example input | Current output | Review status |
| --- | --- | --- |
| `THOR - BLU-RAY 3D(TM)FS (2011) 1080p.mkv ENG-COMENT-RU-ESP LAT-PT-FR for VR` | `Thor` / 2011 | Review: likely metadata tail, current output may be acceptable despite classifier flag. |
| `Scemo e piu scemo - inizio cosi (2003) ITA 720p` | `Scemo e piu scemo` / 2003 | Safe candidate: real dash title continuation before year. |
| `Knightriders - i cavalieri (1981) 720p h264 Ita Eng Sub Ita Eng-MIRCrew` | `Knightriders` / 1981 | Safe candidate: alternate title before year. |
| `James Bond - Form Russia With Love (1963) 720p BluRay x264 Hindi DD 2.0 ~AbhiSona~` | `Form Russia With Love` / 1963 | Manual review: franchise/person prefix behavior. |
| `CHARLIE CHAPLIN - A King in New York (1957) 720p-H264-AAC-& nickarad` | `A King in New York` / 1957 | Expected/review: known leading-person prefix removal. |

Recommended test ideas for v1.0.1:

- Preserve real subtitle continuations where the dash tail is followed by a release year.
- Keep decorator/person/franchise prefix stripping protected by explicit regression tests.
- Do not treat technical dash tails as real title continuations.

### TRUE_FAIL_METADATA_SUFFIX

Count: 187. Status: safe v1.0.1 candidate for repeated visible release-group and technical suffix chains, but collection/range rows should stay review/out-of-scope.

| Example input | Current output | Review status |
| --- | --- | --- |
| `11-59 - Sfida contro il tempo (2005) 720p h264 Ac3 5.1 Ita Eng Sub Ita Eng-MIRCrew` | `11-59 - Sfida contro il tempo` / 2005 | Review: classifier sees release-group shape in title number. |
| `REPACK-Kingpin.1996.2160p.DV.HDR10Plus.Ai-Upscaled.HEVC.DTS-HD.MA.5.1-RIFE.4.25v2-60fps-DirtyHippie` | `REPACK-Kingpin` / 1996 | Safe candidate: leading `REPACK-` decorator/release-group contamination. |
| `The Missiles of October (1974) MP4-DvD Rip [samson599]` | `The Missiles of October MP4-DvD Rip [samson599]` / 1974 | Safe candidate: post-year source plus release-group suffix. |
| `U Boot 96-Das Boot [EXTENDED] (1981) ITA-GER Ac3 5.1 BDRip 1080p H264 [ArMor]` | `U Boot 96-Das Boot` / 1981 | Review: output may already be acceptable; classifier sees release-group shape. |
| `Yellow Submarine (1968-2012) [BD-RIP] [1080p] [4;3] ...` | noisy range output / no year | Expected/review: collection or restoration range-like row. |

Recommended test ideas for v1.0.1:

- Strip leading `REPACK-` only when followed by a clear title/year/technical suffix.
- Strip post-year source plus release-group suffixes such as `[samson599]`.
- Avoid treating title numbers such as `11-59` or `U Boot 96` as release groups.

### TRUE_FAIL_OVERTRIM_REAL

Count: 27. Status: manual review first. These are the highest-risk cases because fixing them means preventing real title loss without allowing metadata leaks.

| Example input | Current output | Review status |
| --- | --- | --- |
| `Chips the War Dog [1990 - USA] WWII drama` | `Chips` / 1990 | Safe candidate with caution: protect `the War Dog` from genre descriptor stripping. |
| `The Horror at 37,000 Feet [1973 - USA] thriller` | `The` / 1973 | Safe candidate: article-only output guard should restore full title. |
| `[2x: TVrip colour + HDLight 1080 original] La cuisine au beurre (1963) - Gilles Grangier, Fernandel, Bourvil` | `[2X:` / 1963 | Manual review: leading technical bracket should not become title. |
| `In Love and War [1958 - USA] Jeffrey Hunter WWII drama` | `In` / 1958 | Safe candidate: short preposition-only output should fail back to fuller title. |
| `What Did You Do in the War, Daddy [1966 - USA] James Coburn` | `What` / 1966 | Safe candidate: protect comma title before country-year suffix. |

Recommended test ideas for v1.0.1:

- Article-only and preposition-only display output falls back to the fuller title.
- Country-year actor/genre suffix stripping must not remove the title body.
- Leading technical bracket spans are removed before title selection, not selected as display title.

### TRUE_FAIL_BRACKET_SPAN

Count: 4. Status: safe v1.0.1 candidate for obvious bracketed release groups/technical spans, but title-like bracket content must stay protected.

| Example input | Current output | Review status |
| --- | --- | --- |
| `13(tzameti).2005.DVDRip.Xvid.rHBa.EngSubs` | `13(TZAMETI)` / 2005 | Review: bracket content may be real alternate stylization. |
| `2012[movie] 2009.DVDRip.XviD-LAPP.avi` | `2012[MOVIE]` / 2009 | Safe candidate: bracketed `movie` metadata. |
| `[bonkai77] patlabor! the movie (1989) [bd-1080p] [dual-audio] [x265] ...` | `[BONKAI77] Patlabor! the Movie` / 1989 | Safe candidate: leading release group bracket. |
| `[FatCatRAW][Kamen Rider THE MOVIE 1972-1988 4K remaster box]` | title-like bracket output / no year | Review/out-of-scope: collection/range and non-Latin source title. |

Recommended test ideas for v1.0.1:

- Strip leading release-group brackets before a clear title/year.
- Strip bracketed `movie` metadata after title numbers only when a separate release year exists.
- Keep alternate title/stylization brackets protected unless strong metadata evidence is present.

## v1.0.1 Candidate Review

Top repeated subpatterns in the 689 TRUE failures:

| Pattern | Count | Recommendation |
| --- | ---: | --- |
| Clear release year not parsed | 270 | Safe candidate where year is followed by strong technical/source metadata; review concerts/ranges. |
| Lost dash title continuation | 201 | Mixed; protect real alternate titles, but avoid weakening release-group stripping. |
| Release-group metadata suffix | 150 | Safe candidate for repeated technical suffix chains and explicit release-group brackets. |
| Metadata token suffix chain | 23 | Safe candidate when anchored after parsed year or strong source token. |
| Implausibly short output | 22 | Manual review first; high over-trim risk. |
| Compound language/metadata tokens | 33 | Safe candidate if constrained to post-year technical tails. |
| Bracket metadata | 4 | Safe candidate for obvious release-group/metadata brackets only. |

Proposed v1.0.1 posture:

- Implement only repeated, low-risk grammar improvements after explicit approval.
- Prioritize release-year grammar, post-year metadata suffixes, and leading technical/release-group brackets.
- Treat overtrim fixes as protected fallback improvements rather than aggressive stripping.
- Keep collections, concerts, sports/events, and semantic/LLM/fuzzy cases out of parser v1.0.1.

## Remaining Known Limitations

- Collections and multi-movie packs are not fully normalized.
- Events, sports, concerts, and non-movie rows are outside the normal movie parser target.
- Ambiguous alternate-title, country-year, actor-credit, and release-group cases may still require manual review.
- Future LLM/manual review could help low-confidence cases, but v1.0.0 does not include LLM/AI and does not auto-apply suggestions.
- No DB cleanup has been applied yet.

## Future Phases

- v1.0.1: optional deterministic grammar patch only if the 689-failure review is approved.
- v1.1: optional admin dry-run rescrub for existing DB titles, with review before apply.
- v1.2: poster matching candidate enhancements.
- v1.3: collection parser for multi-movie packs.
- Future: LLM suggestions only for low-confidence cases, never direct auto-apply.

## Operational Notes

Rerun the v1.0.0 diagnostic:

```bash
cd /home/sectum/Projects/Elvern
.venv/bin/python scripts/diagnostics/title_scrubber_benchmark.py --input "tmp/Movie Name DB.txt"
.venv/bin/python scripts/diagnostics/title_scrubber_probe.py
```

Input sample:

- `tmp/Movie Name DB.txt`

When rerun, generated outputs are written under project `tmp/`:

- `tmp/title-scrubber-v1.0.0-report.json`
- `tmp/title-scrubber-v1.0.0-summary.txt`
- `tmp/title-scrubber-v1.0.0-failed-sample.txt`
- `tmp/title-scrubber-v1.0.0-classified-report.json`
- `tmp/title-scrubber-v1.0.0-classified-summary.txt`
- `tmp/title-scrubber-v1.0.0-all-failed-results.txt`
- `tmp/title-scrubber-v1.0.0-manual-review-buckets.md`

Primary tests:

```bash
cd /home/sectum/Projects/Elvern
.venv/bin/python -m pytest backend/tests/test_media_title_parser.py -q
```

## Safety Principles

- Under-trim is safer than over-trim.
- Display title stays the bare movie title.
- Poster candidates may be broader but do not replace display title.
- Never remove title numbers, roman numerals, acronyms, slash title identity, or episode identities.
- Never write or rescrub the database without dry-run output and explicit approval.
- Keep diagnostics under project `tmp/`, not system `/tmp`.
