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
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase17-before-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase17-before-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase17-before-failed-sample.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase17-classified-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase17-classified-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase17-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase17-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase17-failed-sample.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase18-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase18-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase18-failed-sample.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase18-classified-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase18-classified-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase18-all-failed-results.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase18-manual-review-buckets.md`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-phase19-bucket-plan.md`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-parser-phase19-before.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-parser-phase19-after.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase19-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase19-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase19-failed-sample.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase19-classified-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase19-classified-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase19-all-failed-results.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase19-manual-review-buckets.md`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-phase20-bucket-plan.md`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-parser-phase20-before.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-parser-phase20-after.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase20-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase20-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase20-failed-sample.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase20-classified-report.json`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase20-classified-summary.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase20-all-failed-results.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase20-manual-review-buckets.md`

## Phase 1.8 Bracket Spans and Release-Year Grammar

### Scope

Phase 1.8 hardens deterministic parsing only. It adds no LLM/AI title scrubbing, performs no database writes, and does not batch-rescrub `media_items`. Frontend UI, playback, audio switching, subtitles, burn-in, Route2, native-HLS, adaptive behavior, cloud probing, age grouping, duplicate hiding, and library presentation behavior were not changed.

### Parser Fixes

- Added a balanced bracket-span parser for `()`, `[]`, and `{}` so nested or adjacent metadata spans can be removed without slicing across title text.
- Preserved slash-heavy bracket metadata while preventing `/` inside brackets from being treated as a filesystem path separator.
- Removed bracketed audio-channel spans such as `[5.1]` and compact release-group spans such as `[YTS.MX]`.
- Added release-year grammar for `title year technical-suffix` cases, including glued year forms such as `Sposa!2026`, without stripping title-number years such as `Blade Runner 2049`.
- Recognized more technical suffix tokens in strict contexts, including `TVRip`, `XviD`, `DVD5`, `DVD9`, and `VHSRip`.
- Prevented one-word titles such as `Hybrid` from being erased just because the word can also appear in technical metadata.
- Narrowed the collection/range guard so `Criterion Collection 1080p...` after a real release year can be treated as metadata, while collection/range examples remain review cases.

### 75k Diagnostic Comparison

Sample file: `/home/sectum/Projects/Elvern/tmp/Movie Name DB.txt`

Phase 1.7 classifier run:

- Total movie strings: 75,814
- Legacy-style suspected failures: 14,896
- TRUE failures: 10,756
- Top TRUE patterns: bracket metadata `4,495`, release-year grammar `4,116`, source/video/codec token `1,369`, TRUE over-trim `623`

Phase 1.8 classifier run:

- Total movie strings: 75,814
- Legacy-style suspected failures: 9,981
- TRUE failures: 5,605
- TRUE failure delta vs Phase 1.7: `-5,151`

Phase 1.8 classification counts:

- `PASS`: 64,806
- `FALSE_POSITIVE_CLEAN_OUTPUT`: 3,617
- `TRUE_FAIL_BRACKET_SPAN`: 3,147
- `TRUE_FAIL_METADATA_SUFFIX`: 933
- `EXPECTED_COLLECTION_OR_RANGE`: 920
- `EXPECTED_EVENT_OR_SPORTS`: 866
- `TRUE_FAIL_RELEASE_YEAR_GRAMMAR`: 787
- `TRUE_FAIL_DASH_TITLE`: 373
- `TRUE_FAIL_OVERTRIM_REAL`: 365

Top TRUE failure patterns remaining:

- bracket metadata: 3,147
- clear release year was not parsed: 787
- source/video/codec token: 657
- compound metadata token: 548
- lost dash title continuation: 373
- lost episode identity: 235
- release group: 214
- compound language token: 180
- metadata token suffix chain: 173
- subtitle token suffix chain: 79

### Fixed Examples

- `Hybrid (2007) 720p WEB-DL x264 Eng Subs [Dual Audio] [Hindi DDP 2.0 - English DDP 5.1] Exclusive By -=!Dr.STAR!=-` parses as `Hybrid`, year `2007`.
- `Aurore (2005) DVDRip x264 [French-AC3-5.1/Stereo] [English/French Subs] [Frankvjecy]` parses as `Aurore`, year `2005`.
- `The.Brothers.Karamazov.1958.(Yul Brynner-Maria Schell).720p.x264-Classics` parses as `The Brothers Karamazov`, year `1958`.
- `Il Padrone Sono Me 1955 ITA TVRip XviD` parses as `Il Padrone Sono Me`, year `1955`.
- `I 600 Giorni Di Salò 1991 ITA SUB ITA DVD9` parses as `I 600 Giorni Di Salò`, year `1991`.
- `Luciferina (2018) [1080p] [BluRay] [5.1] [YTS.MX]` parses as `Luciferina`, year `2018`.
- `Dr. Dolittle 3 2006-ENG-SD-WEBRip-334MiB-AAC-x264 [PortalGoods]` parses as `Dr Dolittle 3`, year `2006`.
- `Catch.Me.If.You.Can[2002]1080p.BRrip-aЯRo` parses as `Catch Me If You Can`, year `2002`.
- `Chinese Zodiac 2012 Upscaled BluRay 2160p HDR10 HEVC DTS-HD MA 5.1 x265-E` parses as `Chinese Zodiac`, year `2012`.
- `No.Country.for.Old.Men.2007.Criterion.Collection.1080p.Bluray.DDP5.1.HEVC.x265-BluBirD.mkv` parses as `No Country for Old Men`, year `2007`.

### Review Buckets

Manual review buckets were written to `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase18-manual-review-buckets.md`.

Examples intentionally left for review:

- collection/range packs such as `Essential Fellini - Criterion Collection (1950-1997) ...`;
- director/cast parentheticals such as `Marshall (Hudlin, 2017)`;
- sports/event/date strings where the title grammar is not a normal movie release-year pattern;
- remaining bracket spans where a broader rule may over-trim alternate titles, anime batch identities, or collection names.

### Do Not Regress

- Do not use system `/tmp` for project scrub reports; write project scratch reports under `/home/sectum/Projects/Elvern/tmp`.
- Do not treat collection/range/event buckets as automatic parser failures.
- Do not strip one-word real titles because the word can also be technical metadata.
- Do not strip `Blade Runner 2049`, `1917`, roman numerals, TV/anime episode identities, or slash title identity.
- Do not chase 100% by widening bracket/span removal into cast, alternate-title, or collection text.

## Phase 1.9 Remaining TRUE Failure Reduction

### Scope

Phase 1.9 reduces remaining TRUE failures from the Phase 1.8 diagnostic without broad over-trim rules. It is deterministic parser and ignored project-`tmp` diagnostic work only.

No LLM/AI title scrubbing was added. No database rows were rewritten or batch-rescrubbed. Frontend UI, playback, audio switching, subtitles, burn-in, Route2, native-HLS, adaptive behavior, cloud probing, age grouping, duplicate hiding, and library presentation behavior were not changed.

### Bucket Plan

The Phase 1.8 failures were bucketed before parser changes. The plan is saved at `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-phase19-bucket-plan.md`.

Safe-now buckets:

- metadata-heavy bracket spans and uploader brackets;
- trusted release-group suffixes only when tied to metadata context;
- classics-style `Title.Year.(cast - genre).technical` suffixes;
- genre descriptor dash suffixes such as `Sci-Fi Rom-Com` and `Fantasy`;
- edition/cut phrases that intentionally collapse to the base movie title.

Deferred/high-risk buckets:

- country/year descriptive brackets such as `[2006 - USA]`;
- alternate-title brackets;
- collection and multi-year packs;
- sports/events/date strings;
- real dash-title continuations;
- TV/anime episode identity over-trim candidates.

### Parser Fixes

- Added known lowercase release/uploader groups seen in the 75k data, including `armor`, `bifra`, `cosmo`, `cyber`, `d3lt4crew`, `dr4gon`, `idncrew`, `lullozzo`, `nonymovies`, `psychic`, `tombdoc`, and `ytsmx`.
- Expanded safe genre descriptor handling for dash suffixes, including `adventure`, `biography`, `fantasy`, `history`, `mystery`, `noir`, `rom-com`, `softcore`, `war`, and `western`.
- Removed trusted edition bracket spans such as `[Unrated Version]`, `[Festival Cut]`, `[Korean Edition]`, `[Special Edition Miramax]`, and `[Resolve Color Grade]`.
- Improved post-year descriptor handling for classics-style names such as `The.Beast.of.the.City.1932.(Walter Huston - Film Noir).1080p...`.
- Narrowed collection/range detection so singular `Film` in `Film Noir` is not mistaken for a collection marker.
- Added a release-year suffix token pass that recognizes dot-chained technical suffixes after descriptor spans.
- Added a guard so release-year suffix cleanup cannot remove TV/anime episode identities such as `S01E03` after a title/year prefix.
- Kept the existing title preservation guards for slash titles, roman numerals, sequel numbers, `[REC]`, `[18+]`, and episode tokens.

### Classifier Changes

- Phase 1.9 reports are written under project `tmp/`, not system `/tmp`.
- Bracket classification now reuses parser-aligned metadata/release-group checks instead of treating every short bracket as TRUE metadata.
- Descriptive brackets such as `[2006 - USA]` and alternate-title brackets are no longer counted as confirmed bracket metadata failures.
- `EXPECTED_EDITION_STRIP` was added for successful base-title collapse of edition/cut markers such as `Director's Cut`, `Theatrical Version`, `Unrated`, and `Extended Collector's Edition`.
- Collection/range detection now catches more multi-year collection rows while avoiding singular `film` false positives.
- Manual review buckets are saved at `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase19-manual-review-buckets.md`.

### Probe Results

The required 50-case Phase 1.9 probe was run before and after parser changes:

- Before: 49/50 passed.
- After: 50/50 passed.

Probe text files:

- `/home/sectum/Projects/Elvern/tmp/elvern-title-parser-phase19-before.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-parser-phase19-after.txt`

The before failure was:

- `Space Oddity - Sci-Fi Rom-Com 2022 Eng Rus Multi Subs 720p [HEVC-mp4]`
  - before output: `Space Oddity - Sci-Fi Rom-Com`, year `2022`
  - after output: `Space Oddity`, year `2022`

Additional guarded fixes include:

- `The.Beast.of.the.City.1932.(Walter Huston - Film Noir).1080p.BRRip.x264-Classics` -> `The Beast of the City`, year `1932`
- `El.Condor.1970.(Lee Van Cleef - Jim Brown - Western).720p.x264-Classics` -> `El Condor`, year `1970`
- `Dogma - Fantasy 1999 Eng Rus Multi Subs 720p [H264-mp4]` -> `Dogma`, year `1999`
- `His and Hers 2026 S01E03 XviD-AFG` remains `His and Hers 2026 S01E03`, year `None`

### 75k Diagnostic Comparison

Sample file: `/home/sectum/Projects/Elvern/tmp/Movie Name DB.txt`

Phase 1.8 classifier run:

- Total movie strings: 75,814
- Legacy-style suspected failures: 9,981
- TRUE failures: 5,605

Phase 1.9 classifier run:

- Total movie strings: 75,814
- Legacy-style suspected failures: 7,932
- TRUE failures: 2,109
- TRUE failure delta vs Phase 1.8: `-3,496`

Phase 1.9 classification counts:

- `PASS`: 66,540
- `FALSE_POSITIVE_CLEAN_OUTPUT`: 4,710
- `EXPECTED_COLLECTION_OR_RANGE`: 1,519
- `TRUE_FAIL_METADATA_SUFFIX`: 934
- `EXPECTED_EVENT_OR_SPORTS`: 867
- `TRUE_FAIL_RELEASE_YEAR_GRAMMAR`: 722
- `TRUE_FAIL_OVERTRIM_REAL`: 205
- `TRUE_FAIL_DASH_TITLE`: 169
- `TRUE_FAIL_BRACKET_SPAN`: 79
- `EXPECTED_EDITION_STRIP`: 69

Top TRUE failure deltas from Phase 1.8 to Phase 1.9:

- `TRUE_FAIL_BRACKET_SPAN`: `3,147` -> `79`
- `TRUE_FAIL_RELEASE_YEAR_GRAMMAR`: `787` -> `722`
- `TRUE_FAIL_METADATA_SUFFIX`: `933` -> `934`
- `TRUE_FAIL_DASH_TITLE`: `373` -> `169`
- `TRUE_FAIL_OVERTRIM_REAL`: `365` -> `205`
- `EXPECTED_COLLECTION_OR_RANGE`: `920` -> `1,519`
- `FALSE_POSITIVE_CLEAN_OUTPUT`: `3,617` -> `4,710`
- `EXPECTED_EDITION_STRIP`: `0` -> `69`

### 99% Target Distance

For 75,814 rows, a 99% pass target allows about 758 TRUE failures. Phase 1.9 has 2,109 TRUE failures, leaving a gap of 1,351 TRUE failures to reach that target.

The largest remaining blockers are:

- `TRUE_FAIL_METADATA_SUFFIX`: 934
- `TRUE_FAIL_RELEASE_YEAR_GRAMMAR`: 722
- `TRUE_FAIL_OVERTRIM_REAL`: 205
- `TRUE_FAIL_DASH_TITLE`: 169
- `TRUE_FAIL_BRACKET_SPAN`: 79

### Deferred Examples

These remain intentionally deferred because broad rules would risk over-trimming:

- `Marshall (Hudlin, 2017) [BDMux1080p Ita-Eng]`
- `Black Phone 1-2 SAGA (2021-25) 720p h264 Ac3 5.1 Ita Eng-MIRCrew`
- `Mr. Ove - En Man Som Heter Ove (2015) 1080p H265 ITA SWE AC3 5.1 MULTISUB - Tarek`
- `Dirty Dancing 2 - Havana Nights (2004) WEBDL 1080p H264 MultiLang Ac3 5.1 MultiSub [ArMor] iDN_CreW`
- `Blade Runner 2049 (2017) 2160p 4K UHD HDR10 DV Blu-ray REMUX Dual Audio [Hindi + English] ESub ~ RemuxDoc`
- `Persepolis (2007) [HDRip-AC3][Spanish]`

### Do Not Regress

- Do not use system `/tmp` for project scrub reports; write project scratch reports under `/home/sectum/Projects/Elvern/tmp`.
- Do not count edition/cut stripping as over-trim when the output is the intended base movie title.
- Do not use singular `film` as a collection/range marker.
- Do not strip country/year or alternate-title brackets without stronger metadata evidence.
- Do not remove `S01E03`, `1x02`, `EP01`, `OVA 01`, or similar TV/anime/cartoon identity tokens.
- Do not chase the 99% target by widening metadata cuts into cast/director parentheticals, alternate titles, or title subtitles.

## Phase 2.0 Safe TRUE Failure Reduction

### Scope

Phase 2.0 targets only the remaining safe deterministic parser buckets from the Phase 1.9 report. It adds no LLM/AI title scrubbing, performs no DB writes or batch rescrubs, and does not touch frontend UI, playback, audio switching, subtitles, burn-in, Route2, native-HLS, adaptive behavior, cloud probing, age grouping, duplicate hiding, or library presentation behavior.

### Bucket Plan

The pre-change plan is saved at `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-phase20-bucket-plan.md`.

Safe-now parser work:

- director/year parentheticals with person-like content, such as `(Spielberg, 2002)`;
- `+ Extras` suffixes after a parsed movie year and technical extras metadata;
- hyphen-separated release years followed by strong technical metadata, such as `Title-2007-BdRip`.

Deferred/high-risk work:

- country/year descriptive brackets such as `[1978 - Japan]`;
- alternate-title brackets that also contain a year, such as `(Into the Night, 1985)`;
- collection/range titles and season packs;
- real dash-title continuations and translated-title aliases;
- true over-trim rows involving title numbers, roman numerals, or episode identity.

### Parser Fixes

- Removed director/year parenthetical spans only when the non-year side looks like a compact person name. This fixes `Minority Report (Spielberg, 2002)`, `American Psycho (Harron, 2000)`, `Danny the Dog (Leterrier, 2005)`, and `Herbie il Super Maggiolino (2005, Robinson)`.
- Removed trailing `+ Extras`, `+ EXTRAS`, and similar bonus-feature suffixes only after a parsed year is already known. This fixes `The Big Lebowski (1998) + EXTRAS ...` and `Inside Man (2006) + Extras ...`.
- Allowed hyphen-separated release-year metadata cuts for `Title-2007-BdRip` style rows while preserving title/date ranges whose left side already ends in a year. This fixes `Ocean's Thirteen-2007-BdRip-(1080p)-Italian AC3-English AAC-x264` without treating `Russia 1985-1999 ...` as a release-year boundary.

### Classifier Changes

Phase 2.0 classifier changes are diagnostic-only under project `tmp/`.

- Phase 2.0 reports compare directly against Phase 1.9.
- Clean single-word titles with strong `full-movie`/download/source metadata suffixes are no longer counted as over-trim just because the old metadata-heavy raw name had many tokens.
- Episode/date-range and bare season-pack rows are review buckets instead of confirmed movie-title parser failures.
- Manual review buckets are saved at `/home/sectum/Projects/Elvern/tmp/elvern-title-scrub-75k-phase20-manual-review-buckets.md`.

### Probe Results

The Phase 2.0 probe was run before and after parser changes:

- Before: 52/58 passed.
- After: 58/58 passed.

Probe text files:

- `/home/sectum/Projects/Elvern/tmp/elvern-title-parser-phase20-before.txt`
- `/home/sectum/Projects/Elvern/tmp/elvern-title-parser-phase20-after.txt`

Examples fixed:

- `Minority Report (Spielberg, 2002).mkv` -> `Minority Report`, year `2002`
- `American Psycho (Harron, 2000).mkv` -> `American Psycho`, year `2000`
- `Danny the Dog (Leterrier, 2005)` -> `Danny the Dog`, year `2005`
- `The Big Lebowski (1998) + EXTRAS ...` -> `The Big Lebowski`, year `1998`
- `Inside Man (2006) + Extras ...` -> `Inside Man`, year `2006`
- `Ocean's Thirteen-2007-BdRip-(1080p)-Italian AC3-English AAC-x264` -> `Ocean's Thirteen`, year `2007`

Guarded examples:

- `Beast (Bestia) 2021 ...` remains `Beast (Bestia)`, year `2021`
- `Bad Hair (Pelo Malo) 2013 ...` remains `Bad Hair (Pelo Malo)`, year `2013`
- `For Love And Gold (L'Armata Brancaleone) 1966 ...` remains `For Love And Gold (L'Armata Brancaleone)`, year `1966`
- `Russia.1985-1999.TraumaZone.S01E07.WEBRip.x264-XEN0N` remains a date-range/episode identity with no parsed movie release year.

### 75k Diagnostic Comparison

Sample file: `/home/sectum/Projects/Elvern/tmp/Movie Name DB.txt`

Phase 1.9 classifier run:

- Total movie strings: 75,814
- Legacy-style suspected failures: 7,932
- TRUE failures: 2,109
- TRUE over-trim: 205

Phase 2.0 classifier run:

- Total movie strings: 75,814
- Legacy-style suspected failures: 7,443
- TRUE failures: 1,719
- TRUE failure delta vs Phase 1.9: `-390`
- TRUE over-trim: 199

Phase 2.0 classification counts:

- `PASS`: 66,041
- `FALSE_POSITIVE_CLEAN_OUTPUT`: 4,488
- `EXPECTED_COLLECTION_OR_RANGE`: 2,636
- `EXPECTED_EVENT_OR_SPORTS`: 863
- `TRUE_FAIL_RELEASE_YEAR_GRAMMAR`: 685
- `TRUE_FAIL_METADATA_SUFFIX`: 550
- `TRUE_FAIL_OVERTRIM_REAL`: 199
- `TRUE_FAIL_DASH_TITLE`: 180
- `TRUE_FAIL_BRACKET_SPAN`: 105
- `EXPECTED_EDITION_STRIP`: 67

### 99% Target Distance

For 75,814 rows, a 99% pass target allows about 758 TRUE failures. Phase 2.0 has 1,719 TRUE failures, leaving a gap of 961 TRUE failures to reach that target.

Remaining largest blockers:

- `TRUE_FAIL_RELEASE_YEAR_GRAMMAR`: 685
- `TRUE_FAIL_METADATA_SUFFIX`: 550
- `TRUE_FAIL_OVERTRIM_REAL`: 199
- `TRUE_FAIL_DASH_TITLE`: 180
- `TRUE_FAIL_BRACKET_SPAN`: 105

### Do Not Regress

- Do not strip alternate-title parentheticals as director/year spans unless the non-year side is compact person-like content.
- Do not remove `+ Extras` unless a release year is already parsed.
- Do not treat `YYYY-YYYY` date-range forms as ordinary movie release-year metadata; bare season-pack handling remains diagnostic/review-only in this phase.
- Do not widen one-word title cleanup into production parser logic without separate review.
- Do not chase the 99% target by stripping country/year brackets, translated title continuations, or title-number identity.

## Phase 1.7 TRUE Failure Classification and Targeted Parser Patch

### Scope

Phase 1.7 separates the old broad scrubber heuristic from a stricter TRUE pass/fail classifier, then fixes a small set of high-confidence deterministic parser patterns.

No LLM/AI title scrubbing was added. No database rows were rewritten or batch-rescrubbed. Frontend UI, playback, audio switching, subtitles, burn-in, Route2, native-HLS, adaptive behavior, cloud probing, age grouping, and duplicate hiding were not changed.

### Classifier Changes

The previous 75k suspected-failure heuristic was useful for finding candidates, but it counted several clean outputs as failures. Phase 1.7 splits rows into:

- `TRUE_FAIL_METADATA_LEAK`: visible source/video/codec/audio/language/subtitle/release-group metadata remains in the display title.
- `TRUE_FAIL_YEAR_EXTRACTION`: a clear title-year-metadata suffix exists, but no release year was parsed.
- `TRUE_FAIL_OVERTRIM`: the parser lost real title identity such as dash subtitles, episode tokens, sequel numbers, or collapsed to an article/franchise-only title.
- `EXPECTED_OR_REVIEW`: collection ranges, date/event strings, alternate-title strings, or other cases that should not be counted as definite parser failure.
- `FALSE_POSITIVE_CLEAN_OUTPUT`: the old heuristic still flags a row, but the display title is clean enough.

Language-like words are no longer counted as visible metadata just because they appear in a title. For example, `The French Italian`, `The Japanese Wife`, `No Time To Die`, and `Open Range` are not TRUE metadata leaks unless a visible suffix chain still looks like release metadata.

### 75k Diagnostic Comparison

Sample file: `/home/sectum/Projects/Elvern/tmp/Movie Name DB.txt`

Phase 1.6 legacy heuristic:

- Total movie strings: 75,814
- Suspected failures: 16,646
- Suspected failure rate: 21.96%
- Title over-trimmed primary pattern: 88

Phase 1.7 classifier run:

- Total movie strings: 75,814
- Legacy-style suspected failures under the refined diagnostic: 14,896
- TRUE failures: 10,756
- FALSE_POSITIVE_CLEAN_OUTPUT: 3,783
- EXPECTED_OR_REVIEW: 720
- TRUE_FAIL_OVERTRIM: 623

The Phase 1.7 `TRUE_FAIL_OVERTRIM` count is intentionally broader than the old single "title over-trimmed primary pattern"; it includes dash-title loss, episode-token loss, article-only output, implausibly short output, and franchise-only collapse. It is not a direct apples-to-apples replacement for the Phase 1.6 primary-pattern count.

Phase 1.7 classification counts:

- `PASS`: 60,555
- `TRUE_FAIL_METADATA_LEAK`: 6,017
- `TRUE_FAIL_YEAR_EXTRACTION`: 4,116
- `FALSE_POSITIVE_CLEAN_OUTPUT`: 3,783
- `EXPECTED_OR_REVIEW`: 720
- `TRUE_FAIL_OVERTRIM`: 623

Top TRUE failure patterns remaining:

- bracket metadata: 4,495
- clear release year was not parsed: 4,116
- source/video/codec token: 1,369
- compound metadata token: 608
- language token suffix chain: 406
- lost dash title continuation: 391
- release group: 361
- metadata token suffix chain: 315
- compound language token: 246
- subtitle token suffix chain: 121

### Parser Fixes

- Metadata bracket removal now runs through bounded repeated passes so parenthetical metadata containing nested language brackets can be fully removed.
- Year-pair parentheticals such as `(2001/2003)` are removed as metadata while preserving the first year.
- Clear dash genre descriptors followed by year/source/language metadata, such as `- Sci-Fi Comedy 2021 ...` and `- Horror 1999 ...`, are stripped.
- Meaningful dash subtitles remain protected, including `Part One`, `Part 2`, `Ghost Protocol`, and similar title continuations.
- `No Language`, `No Sub`, `No Subs`, `No Subtitles`, `TVRip`, `Matte`, and `AIEnhanced` are recognized as metadata only in suffix/technical contexts.
- Spaced slash and fullwidth slash alternate-title strings are not mistaken for filesystem paths.
- The backward suffix pass no longer strips language-like title words immediately before a release year, preserving `The French Italian`.
- Clear `title year compound-metadata` suffixes, such as `1972 remux-framestor`, are still cut.

### Fixed Examples

- `Transporter 2 (2005) (WEBDL-1080p x265 AC3 5.1 [EN] [EN+SV]) MrPanda` parses as `Transporter 2`, year `2005`.
- `Beast (Bestia) 2021 No Language 1080p WEB-DL x264` parses as `Beast (Bestia)`, year `2021`.
- `The French Italian 2025 1080p AMZN WEBRip DDP2.0 H265` parses as `The French Italian`, year `2025`.
- `Dont Look Up - Sci-Fi Comedy 2021 Eng Fra Ita Rus Ukr Multi Subs 2160p [HEVC-mp4]` parses as `Dont Look Up`, year `2021`.
- `Solaris - Sci-Fi 1972 Eng Rus Comm Multi Subs 1080p [HEVC-mp4]` parses as `Solaris`, year `1972`.
- `Mission: Impossible - Ghost Protocol (2011) 1080p BluRay x264.mkv` preserves `Mission: Impossible - Ghost Protocol`, year `2011`.
- `The Hunger Games: Mockingjay - Part 2 (2015) 1080p BluRay x264.mkv` preserves `The Hunger Games: Mockingjay - Part 2`, year `2015`.
- `Epoch / Epoch: Evolution (2001/2003) SD` preserves the alternate-title slash string and parses year `2001`.
- `Help! I'm a Fish／Hjælp! Jeg er en fisk／A Fish Tale (2000) DVDRip.mkv` preserves the slash alternate-title string and parses year `2000`.

### Remaining Hard Patterns

The remaining TRUE failure rows are intentionally left for later phases. Common hard cases include dense cast/director parentheticals, complex collection/date ranges, sports or event strings, mixed alternate-title metadata, and malformed source/release suffixes where a wider rule would risk over-trimming real titles.

### Do Not Regress

- Do not treat the old suspected-failure percentage as the true parser failure rate.
- Do not count clean outputs as failures only because warnings were emitted.
- Do not strip language-like words from real titles such as `The French Italian`, `The Japanese Wife`, `No Time To Die`, or `Open Range`.
- Do not strip `[REC]` or `[18+]` prefix title identity.
- Do not strip TV/anime/cartoon episode identity tokens such as `S01E01`, `S1E1`, `1x02`, `E01`, `EP01`, or `OVA 01`.
- Do not chase 100% by increasing over-trim risk.

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
