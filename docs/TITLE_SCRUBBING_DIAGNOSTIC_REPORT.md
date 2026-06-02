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
- `/tmp/elvern-title-scrub-75k-phase15-report.json`
- `/tmp/elvern-title-scrub-75k-phase15-summary.txt`
- `/tmp/elvern-title-scrub-75k-phase15-failed-sample.txt`
- `/tmp/elvern-title-scrub-75k-phase16-report.json`
- `/tmp/elvern-title-scrub-75k-phase16-summary.txt`
- `/tmp/elvern-title-scrub-75k-phase16-failed-sample.txt`
- `/tmp/elvern-title-scrub-75k-phase16-legacy-fast-summary.json`
- `/tmp/elvern-title-scrub-75k-phase16-legacy-fast-summary.txt`

## Phase 1.6 DC Context and Title-Preservation Patch

### Scope

Phase 1.6 fixes narrowly-scoped deterministic parser regressions found after the Phase 1.5 scrubber run. It does not attempt to chase a 100% heuristic pass rate.

No LLM/AI title scrubbing was added. No database rows were rewritten or batch-rescrubbed. Frontend UI, playback, audio switching, subtitles, burn-in, Route2, native-HLS, adaptive behavior, cloud probing, age grouping, and duplicate hiding were not changed.

### Root Causes

- `DC` needed context-aware handling. It should be stripped as `director's cut` when it follows a real title, year, edition marker, or technical metadata, but it must remain in DC franchise titles.
- Metadata brackets such as `(DC 1080p BluRay...)` were removed as technical metadata, but their `director's cut` edition marker was not being carried into `edition_identity`.
- A literal escaped apostrophe, as in `Monster\\'s Ball`, was treated as a Windows path separator before normalization. That turned the title into a broken basename and could hang the large diagnostic path.
- Edition suffix stripping needed an article-only guard so titles like `The Final Cut` and `Director's Cut` are not collapsed to `The` or an empty title.

### Fixed Examples

- `Kingdom.of.Heaven.DC.Roadshow.Version.2005...` parses as:
  - display title: `Kingdom of Heaven`
  - year: `2005`
  - edition: `roadshow|director's cut`
- `Legend 1985 DC.mkv`, `Troy DC 2004...`, and `Movie.Name.2000.DC...` now strip `DC` only as a director's-cut edition marker.
- `Monster\\'s Ball [Unrated DC].2001...` now parses as:
  - display title: `Monster's Ball`
  - year: `2001`
  - edition: `unrated|director's cut`
- `(DC 1080p BluRay...)` technical brackets now carry the `director's cut` marker before the bracket is removed.

### Preserved Guards

- DC franchise titles remain title text, not edition metadata:
  - `LEGO DC - Shazam! Magic and Monsters`
  - `LEGO DC Batman - Family Matters`
  - `LEGO DC Comics Super Heroes - Justice League - Cosmic Clash`
  - `DC League of Super-Pets`
  - `DC Showcase Catwoman`
- Slash titles and language slash chains remain protected:
  - `V/H/S`
  - `V/H/S: Viral`
  - `The Hunt/Jagten`
- Dash subtitles remain protected when technical metadata follows:
  - `Avatar - The Way of Water`
  - `Dune - Part Two`
  - `Venom - The Last Dance`
  - `F1 - The Movie`
- TV/anime episode identity tokens remain protected:
  - `S01E01`
  - `S1E1`
  - `1x02`
  - `E01`
  - `EP01`
  - `OVA 01`

### 75k Diagnostic Comparison

Sample file: `/home/sectum/Projects/Elvern/tmp/Movie Name DB.txt`

Comparable legacy heuristic summary after Phase 1.6:

- Total movie strings: 75,814
- Suspected failures: 16,646
- Suspected failure rate: 21.96%
- Title over-trimmed primary pattern: 88

Delta from Phase 1.5:

- Suspected failures: -81
- Suspected failure rate: -0.10 percentage points
- Title over-trimmed primary pattern: -4

The stricter Phase 1.6 supplemental diagnostic also completed without hanging:

- Total movie strings: 75,814
- Suspected failures: 20,417
- DC director-context primary pattern: 2

The remaining DC-context flags are ambiguous collection/date-range strings such as `EX RM DC 1984-2015`; they are intentionally left for later deterministic parser work rather than broadening `DC` inference.

### Do Not Regress

- Do not strip `DC` from DC franchise titles.
- Do not leave `DC` in display titles when it is clearly a director's-cut suffix or technical-bracket marker.
- Do not treat escaped punctuation inside titles as path separators.
- Do not collapse article-plus-edition titles such as `The Final Cut`.
- Do not chase the remaining 75k heuristic failures by increasing over-trim risk.

## Phase 1.5 75k Deterministic Hardening

### Scope

Phase 1.5 hardens high-frequency, low-false-positive parser patterns found in the 75k scrubber report. It remains deterministic parser work only.

No LLM/AI title scrubbing was added. No database rows were rewritten or batch-rescrubbed. Frontend UI, playback, audio switching, subtitles, burn-in, Route2, native-HLS, adaptive behavior, cloud probing, age grouping, and duplicate hiding were not changed.

### Fixed Patterns

- Dash subtitle titles are preserved when metadata follows the subtitle segment:
  - `Avatar - The Way of Water`
  - `Dune - Part Two`
  - `Venom - The Last Dance`
  - `F1 - The Movie`
- LEGO/DC titles are no longer confused with edition metadata:
  - `LEGO DC - Shazam! Magic and Monsters`
  - `LEGO DC Batman - Family Matters`
  - `LEGO DC Comics Super Heroes - Justice League - Cosmic Clash`
- Trailing title-cased release-group brackets are stripped only after dense technical metadata:
  - `[Prof]`, `[Kris]`, and similar uploader tags after codec/source brackets are removed.
  - Leading short acronym titles such as `[REC]` are preserved.
- Year/language/source suffixes are scrubbed for common `JPN SUB ENG`, `ITA SUB`, `PT-BR MULTISUB`, and `iTA-KOR` chains.
- Additional video/source/audio/container tokens are recognized, including `DVDRip`, `HDTV`, `BDMux`, `BDrmx`, `H254`, `OPUS`, `Mkv`, `SD`, `HD`, `60FPS`, `MULTISUB`, and `7RIP`.
- Slash titles and language slashes are preserved correctly:
  - `V/H/S`
  - `V/H/S: Viral`
  - `EN/FR/ES` and longer language chains no longer trigger path-basename truncation.
- TV/anime/cartoon episode identity tokens are preserved:
  - `S01E01`
  - `S1E1`
  - `1x02`
  - `E01`
  - `EP01`
  - `OVA 01`

### Probe Results

The required 35-case probe was run before and after Phase 1.5:

- Before: 3/35 passed, 32/35 failed.
- After: 35/35 passed, 0/35 failed.

The after-probe output is saved at `/tmp/elvern-title-parser-phase15-after.txt`.

### 75k Diagnostic Comparison

Sample file: `/home/sectum/Projects/Elvern/tmp/Movie Name DB.txt`

Before baseline:

- Total movie strings: 75,814
- Suspected failures: 18,507
- Suspected failure rate: 24.41%
- Title over-trimmed primary pattern: 141

After Phase 1.5:

- Total movie strings: 75,814
- Suspected failures: 16,727
- Suspected failure rate: 22.06%
- Title over-trimmed primary pattern: 92

Delta:

- Suspected failures: -1,780
- Suspected failure rate: -2.35 percentage points
- Title over-trimmed primary pattern: -49

Primary failure pattern counts after Phase 1.5:

- year extraction failed: 8,830
- release group leaked: 4,008
- language token leaked: 1,127
- bracket metadata leaked: 850
- video/source token leaked: 746
- audio channel token leaked: 651
- roman/number issue: 210
- audio token leaked: 108
- title over-trimmed: 92
- suspicious parser output: 77
- compound language token leaked: 14
- fallback/low confidence: 7
- subtitle token leaked: 6
- poster candidate missing alternate spelling: 1

### Remaining Weaknesses

The remaining 75k suspected failures are intentionally not chased in this phase:

- Year extraction remains difficult when title text contains multiple human names, date ranges, TV collections, or music-like descriptors.
- Release-group heuristics still conservatively flag bracketed title-like text and some dash suffixes.
- Long TV, anime, documentary, and collection strings can still contain source/video tokens.
- Some language/subtitle token chains remain in mixed-format strings.
- Broad heuristic perfection is out of scope; Phase 1.5 prioritizes deterministic parser safety and over-trim prevention.

### Do Not Regress

- Do not collapse dash-subtitle titles to the first word or franchise root.
- Do not treat `DC` in LEGO/DC titles as director's cut metadata.
- Do not strip `[REC]` or short leading bracket acronym titles.
- Do not strip TV/anime episode identities such as `S01E01`, `1x02`, `EP01`, or `OVA 01`.
- Do not use path basename logic on slash titles such as `V/H/S` or language chains such as `EN/FR/ES`.
- Do not chase 100% heuristic pass rate by increasing over-trim risk.

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
