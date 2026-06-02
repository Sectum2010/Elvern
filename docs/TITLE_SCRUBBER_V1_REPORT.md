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

The benchmark report files are stored under `/home/sectum/Projects/Elvern/tmp/title-scrubber-v1.0.0-*`.

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

## Remaining Known Limitations

- 689 TRUE failures remain in the expanded 123,892-title benchmark set.
- Collections and multi-movie packs are not fully normalized.
- Events, sports, concerts, and non-movie rows are outside the normal movie parser target.
- Ambiguous alternate-title, country-year, actor-credit, and release-group cases may still require manual review.
- Future LLM/manual review could help low-confidence cases, but v1.0.0 does not include LLM/AI and does not auto-apply suggestions.
- No DB cleanup has been applied yet.

## Future Phases

- v1.1: optional admin dry-run rescrub for existing DB titles, with review before apply.
- v1.2: poster matching candidate enhancements.
- v1.3: collection parser for multi-movie packs.
- Future: LLM suggestions only for low-confidence cases, never direct auto-apply.

## Operational Notes

Rerun the v1.0.0 diagnostic:

```bash
cd /home/sectum/Projects/Elvern
.venv/bin/python tmp/title-scrubber-v1.0.0-classifier.py
```

Input sample:

- `tmp/Movie Name DB.txt`

Outputs:

- `tmp/title-scrubber-v1.0.0-bucket-plan.md`
- `tmp/title-scrubber-v1.0.0-classifier.py`
- `tmp/title-scrubber-v1.0.0-probe.py`
- `tmp/title-scrubber-v1.0.0-probe.txt`
- `tmp/title-scrubber-v1.0.0-report.json`
- `tmp/title-scrubber-v1.0.0-summary.txt`
- `tmp/title-scrubber-v1.0.0-failed-sample.txt`
- `tmp/title-scrubber-v1.0.0-classified-report.json`
- `tmp/title-scrubber-v1.0.0-classified-summary.txt`
- `tmp/title-scrubber-v1.0.0-all-failed-results.txt`
- `tmp/title-scrubber-v1.0.0-manual-review-buckets.md`
- `tmp/title-scrubber-v1.0.0-before.txt`
- `tmp/title-scrubber-v1.0.0-after.txt`

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
