# Title Scrubbing Diagnostic Report

Generated: 2026-06-01T03:36:05Z

## Scope

This report tracks the deterministic movie-title parser hardening and the follow-up over-trim regression fix. It covers parser/runtime behavior only.

No LLM/AI title scrubbing was added. No database rows were rewritten or rescrubbed. Playback, audio, subtitles, Route2, native-HLS, adaptive behavior, cloud probing, age grouping, duplicate hiding, and frontend UI were not changed.

## Sample

Sample file: `/home/sectum/Projects/Elvern/tmp/1000 Movie Names.txt`

Latest generated reports:

- `/tmp/elvern-title-scrub-after-overtrim-fix-report.json`
- `/tmp/elvern-title-scrub-after-overtrim-fix-summary.txt`

## Over-Trim Regression After Phase 1

### Symptoms

The first Phase 1 parser change fixed the high-priority Never Ending Story suffix case, but it over-trimmed meaningful title tokens in real titles:

- `John.Wick.Chapter.2.2017.1080p.BluRay.x264.mkv` became `John Wick Chapter`
- `Big.Hero.6.2014.1080p.BluRay.x264.mkv` became `Big Hero`
- `Inside.Out.2.2024.1080p.WEB-DL.x264.mkv` became `Inside Out`
- `The.BFG.2016.1080p.BluRay.x264.mkv` became `The`
- `Blade II (2002) (1080p BluRay x265 10bit EAC3 7.1 Celdra) [QxR]` became `Blade`

Under-trimming metadata is bad, but over-trimming real title words, numbers, roman numerals, or acronyms is worse for display titles and poster matching.

### Root Cause

The backward metadata suffix scanner walked too far left after finding a release-year and technical suffix. Once it had seen metadata, it treated the title token immediately before the release year as removable suffix metadata:

- sequel numbers such as `2` and `6` were treated like audio-channel fragments;
- acronym-like title tokens such as `BFG` were treated as bare release groups;
- after metadata bracket removal, roman numerals such as `II` could be treated as trailing bare release groups.

The bare-release-group cleanup was also too broad when repeated suffix cutting ran after the real metadata suffix had already been removed.

### Fix

The production parser now stops the backward suffix scan at the token directly before a recognized release-year boundary unless that token is itself known suffix metadata. This preserves meaningful title-region tokens such as sequel numbers and acronyms while still cutting the metadata suffix starting at the release year.

Bare release groups are now removed only when tied to a real metadata context:

- dash/metadata-attached release group handling remains;
- trailing bare groups after metadata bracket removal are still stripped;
- roman numerals are preserved as title tokens;
- article-only output such as `The` from `The BFG` is rejected by tests.

The Never Ending Story fix remains narrow: compound language chains such as `ITA-ENG` are cut only as part of a metadata suffix chain after the title/year region, not by generic removal of title-region tokens.

## Required Regression Cases

| Raw filename | Fixed display title | Year |
| --- | --- | --- |
| `John.Wick.Chapter.2.2017.1080p.BluRay.x264.mkv` | `John Wick Chapter 2` | 2017 |
| `Big.Hero.6.2014.1080p.BluRay.x264.mkv` | `Big Hero 6` | 2014 |
| `Inside.Out.2.2024.1080p.WEB-DL.x264.mkv` | `Inside Out 2` | 2024 |
| `The.BFG.2016.1080p.BluRay.x264.mkv` | `The BFG` | 2016 |
| `Blade II (2002) (1080p BluRay x265 10bit EAC3 7.1 Celdra) [QxR]` | `Blade II` | 2002 |
| `The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv` | `The Never Ending Story` | 1984 |
| `Se7en.1995.1080p.BluRay.x264.mkv` | `Se7en` | 1995 |
| `3.Idiots.2009.1080p.BluRay.x264.mkv` | `3 Idiots` | 2009 |
| `Malcolm.X.1992.1080p.BluRay.x264.mkv` | `Malcolm X` | 1992 |
| `Project.X.2012.1080p.BluRay.x264.mkv` | `Project X` | 2012 |
| `Nightbitch (2024) [1080p Ita Eng Spa 5.1 HEVC10 SubS] byMe7alh [MIRCrew]` | `Nightbitch` | 2024 |
| `Before Sunset (2004 ITA/ENG) [1080p x265] [Paso77]` | `Before Sunset` | 2004 |

Dirty stored-title case:

- stored title: `The Never Ending Story ITA-ENG`
- original filename: `The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv`
- fixed display title: `The Never Ending Story`
- title source: `original_filename`

## Poster Candidates

Display title remains `The Never Ending Story`.

Poster candidate variants may include:

- `The Never Ending Story`
- `The NeverEnding Story`
- `Never Ending Story`
- `NeverEnding Story`

Poster candidates do not drive or rewrite the display title.

## 1000-Sample Diagnostic

Latest over-trim-fix run:

- Total samples: 1000
- Suspected failures: 48
- Passed by heuristic: 952
- Suspected failure rate: 4.8%

Category counts:

- suspicious parser output: 22
- year extraction failed: 18
- video/source token leaked: 18
- release group leaked: 9
- bracket metadata leaked: 8
- language token leaked: 7
- subtitle token leaked: 3
- title over-trimmed: 3
- fallback/low confidence: 2
- audio token leaked: 1
- audio channel token leaked: 1

Comparison with prior Phase 1 diagnostic:

- suspected failures improved from 57 to 48;
- the requested live over-trim examples now parse correctly;
- no new high-confidence over-trim failure was accepted for the requested regression set.

Remaining suspected failures are intentionally left for later deterministic parser work. This fix prioritizes preserving meaningful title tokens over chasing the lowest heuristic failure count.

## Do Not Regress

- Do not remove meaningful trailing numbers from title regions.
- Do not remove final acronym or roman numeral tokens from title regions.
- Do not output article-only display titles such as `The`, `A`, or `An` when the raw title contains meaningful tokens.
- Do not let stored dirty titles beat a safer filename parse.
- Do not use poster-candidate variants as display titles.
- Do not write or rescrub the database without a separate explicit remediation phase.
- Do not add LLM/AI title rewriting.
