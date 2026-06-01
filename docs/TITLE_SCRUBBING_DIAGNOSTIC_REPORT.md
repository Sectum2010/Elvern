# Title Scrubbing Diagnostic Report

Generated: 2026-06-01T03:07:05.846964+00:00

## Scope

This report started as a diagnostic-only snapshot for movie title scrubbing and poster matching parser behavior, then was updated after the Phase 1 deterministic parser improvement. Phase 1 changed runtime parser/title-normalization behavior only; it did not write the database, rescan media, rewrite existing `media_items.title` values, change frontend UI, or touch playback/age/duplicate logic.

LLM/AI title scrubbing is intentionally out of scope for this phase. It is listed only as a future low-confidence review aid.

## Sample File

Used sample file: `/home/sectum/Projects/Elvern/tmp/1000 Movie Names.txt`

Candidates inspected:

- `/home/sectum/Projects/Elvern/tmp/1000 Movie Names.txt`: 1000 non-empty lines, 72915 bytes
- `/home/sectum/Projects/Elvern/tmp/movie_name_pairs.json`: 52 non-empty lines, 5468 bytes
- `/home/sectum/Projects/Elvern/tmp/playback-diagnostics-linux.json`: 340 non-empty lines, 9627 bytes

The selected file was used because it is the closest raw filename list to 1000 non-empty entries.

## Current Parser Flow Summary

- `parse_media_title` builds candidates from trusted title, original filename, and stored title, then chooses a best candidate and applies a safety fallback if display-title risk warnings are found.
- `_parse_title_candidate` prepares text by stripping path/extension, normalizing Unicode, replacing dot/underscore separators, removing classified bracket groups, removing right-side dash metadata segments, cutting filename suffixes, stripping trailing metadata/year/edition markers, and finally smart-casing display output.
- `_cut_non_title_suffix` first checks dash-separated suffixes, then applies a backward suffix scanner before falling back to the older left-to-right metadata boundary scanner.
- `_metadata_suffix_boundary` still scans left-to-right for standalone release years, metadata boundary tokens, language boundary tokens, and edition suffix starts when the backward scanner does not find a cut.
- `_classification_tokens` now splits punctuation, hyphenated, slash, and plus-joined metadata compounds for segment classification.
- `_token_is_metadata` and `_token_is_strong_metadata` cover common source, codec, resolution, and audio tokens; Phase 1 adds separate localization/subtitle metadata taxonomy for suffix classification.
- `resolve_title_metadata`, `resolve_poster_match_identity`, and `build_poster_candidate_family` reuse `parse_media_title`; poster families add apostrophe, ampersand, and roman/arithmetic variants for parser-derived titles.
- `library_presentation_service` uses `build_poster_candidate_family` for poster filenames and normalized key families; display title and poster candidate identity are intentionally separate concepts.

## Summary

- Total samples parsed: 1000
- Suspected failures: 57
- Passes by heuristic: 943
- Suspected failure rate: 5.7%

## Category Counts

- suspicious parser output: 29
- video/source token leaked: 25
- year extraction failed: 20
- release group leaked: 16
- bracket metadata leaked: 8
- audio token leaked: 8
- language token leaked: 7
- subtitle token leaked: 3
- title over-trimmed: 3
- fallback/low confidence: 2
- audio channel token leaked: 1

## Top 20 Highest-Confidence Suspected Failures

1. `Hybrid (2007) 720p WEB-DL x264 Eng Subs [Dual Audio] [Hindi DDP 2.0 - English DDP 5.1] Exclusive By -=!Dr.STAR!=-`
   - output: `(2007) 720p WEB-DL x264 Eng Subs [Dual Audio] [Hindi DDP 2 0 - English DDP 5 1] Exclusive By - =!Dr STAR!=`; year: `2007`; confidence: `low`; categories: language token leaked, audio token leaked, audio channel token leaked, video/source token leaked, subtitle token leaked, bracket metadata leaked, fallback/low confidence, suspicious parser output
2. `The History Of Sound - Sulle note di un amore (2025) .mkv HD 720p E-AC3 iTA DTS ENG x264 - FHC_CREW.mkv`
   - output: `The History Of Sound - Sulle note di un amore mkv HD 720p E-AC3`; year: `2025`; confidence: `high`; categories: audio token leaked, video/source token leaked, release group leaked, suspicious parser output
3. `Americana (2025) .mkv HD 720p E-AC3 iTA AC3 ENG AVC - FHC_CREW.mkv`
   - output: `Americana mkv HD 720p E-AC3`; year: `2025`; confidence: `high`; categories: audio token leaked, video/source token leaked, release group leaked, suspicious parser output
4. `Whistle - Il richiamo della morte (2025) .mkv HD 720p E-AC3 iTA DTS ENG x264 - FHC_CREW.mkv`
   - output: `Whistle - Il richiamo della morte mkv HD 720p E-AC3`; year: `2025`; confidence: `high`; categories: audio token leaked, video/source token leaked, release group leaked, suspicious parser output
5. `Se solo potessi ti prenderei a calci (2025) .mkv HD 720p E-AC3 iTA AC33 ENG x264 - FHC_CREW.mkv`
   - output: `Se solo potessi ti prenderei a calci mkv HD 720p E-AC3`; year: `2025`; confidence: `high`; categories: audio token leaked, video/source token leaked, release group leaked, suspicious parser output
6. `Influencers (2025) .mkv HD 720p E-AC3 iTA DTS ENG x264 - FHC_CREW.mkv`
   - output: `Influencers mkv HD 720p E-AC3`; year: `2025`; confidence: `high`; categories: audio token leaked, video/source token leaked, release group leaked, suspicious parser output
7. `Greenland 2 Migration (2026) .mkv HD 720p E-AC3 iTA AC3 ENG x264 - FHC_CREW.mkv`
   - output: `Greenland 2 Migration mkv HD 720p E-AC3`; year: `2026`; confidence: `high`; categories: audio token leaked, video/source token leaked, release group leaked, suspicious parser output
8. `Cuckoo (2024) .mkv HD 720p E-AC3 iTA DTS ENG x264 - FHC_CREW.mkv`
   - output: `Cuckoo mkv HD 720p E-AC3`; year: `2024`; confidence: `high`; categories: audio token leaked, video/source token leaked, release group leaked, suspicious parser output
9. `Death By Hanging 1968 JPN SUB ENG, ITA 1080p BluRay x264`
   - output: `Death By Hanging 1968 JPN SUB ENG,`; year: `None`; confidence: `high`; categories: language token leaked, subtitle token leaked, year extraction failed, suspicious parser output
10. `The Man With The Suitcase 1984 FRE SUB ENG, ITA 1080p BluRay x264`
   - output: `The Man With The Suitcase 1984 FRE SUB ENG,`; year: `None`; confidence: `high`; categories: language token leaked, subtitle token leaked, year extraction failed, suspicious parser output
11. `DC.League.of.Super-Pets.2022.1080p.BluRay.x264-iFT_EniaHD`
   - output: `DC League of Super-Pets 2022 1080p BluRay x264-iFT EniaHD`; year: `2022`; confidence: `low`; categories: video/source token leaked, fallback/low confidence, suspicious parser output
12. `Jujutsu Kaisen 0 (2021) (1080p BluRay x265 10-bit Jap 5.1 + Eng 5.1 AAC) [WeSLeY]`
   - output: `Jujutsu Kaisen 0 (1080p`; year: `2021`; confidence: `high`; categories: video/source token leaked, suspicious parser output
13. `Apocalypse In The Tropics 2024 PT-BR MULTISUB 1080p WEB-DL x264`
   - output: `Apocalypse In The Tropics 2024 PT-BR`; year: `None`; confidence: `high`; categories: release group leaked, year extraction failed
14. `The Red Turtle (2016) (1080p BluRay x265 10-bit Fre 5.1 AAC) [WeSLeY]`
   - output: `The Red Turtle (1080p`; year: `2016`; confidence: `high`; categories: video/source token leaked, suspicious parser output
15. `Ponyo (2008) (1080p BluRay x265 10-bit Eng 5.1 AAC + Jap 5.1 AAC) [WeSLeY]`
   - output: `Ponyo (1080p`; year: `2008`; confidence: `high`; categories: video/source token leaked, suspicious parser output
16. `Howl's Moving Castle (2004) (1080p BluRay x265 10-bit Eng 5.1 AAC + Jap 5.1 AAC) [WeSLeY]`
   - output: `Howl's Moving Castle (1080p`; year: `2004`; confidence: `high`; categories: video/source token leaked, suspicious parser output
17. `Spirited Away (2001) (1080p BluRay x265 10-bit Eng 5.1 AAC + Jap 5.1 AAC) [WeSLeY]`
   - output: `Spirited Away (1080p`; year: `2001`; confidence: `high`; categories: video/source token leaked, suspicious parser output
18. `Princess Mononoke (1997) (1080p BluRay x265 10-bit Eng 5.1 AAC + Jap 5.1 AAC) [WeSLeY]`
   - output: `Princess Mononoke (1080p`; year: `1997`; confidence: `high`; categories: video/source token leaked, suspicious parser output
19. `Whisper of the Heart (1995) (1080p BluRay x265 10-bit Eng 5.1 DTS + Jap 5.1 AAC) [WeSLeY]`
   - output: `Whisper of the Heart (1080p`; year: `1995`; confidence: `high`; categories: video/source token leaked, suspicious parser output
20. `Pom Poko (1994) (1080p BluRay x265 10-bit Eng 2.0 FLAC + Jap 2.0 FLAC) [WeSLeY]`
   - output: `Pom Poko (1080p`; year: `1994`; confidence: `high`; categories: video/source token leaked, suspicious parser output

## The Never Ending Story Case

Raw filename:

`The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv`

After Phase 1 parsed output:

- display_title: `The Never Ending Story`
- base_title: `The Never Ending Story`
- parsed_year: `1984`
- poster_match_title: `The Never Ending Story`
- poster candidates: ['The Never Ending Story', 'The NeverEnding Story', 'Never Ending Story', 'NeverEnding Story']
- warnings: ['year_block_removed', 'bracket_release_group_removed', 'metadata_bracket_suffix_removed', 'technical_suffix_density_cut', 'compound_language_suffix_removed', 'subtitle_suffix_removed', 'metadata_suffix_removed']

Expected display title:

`The Never Ending Story`

Expected primary poster match title:

`The Never Ending Story`

Safe poster candidate variants:

- The Never Ending Story
- The NeverEnding Story
- Never Ending Story
- NeverEnding Story

Baseline diagnosis:

Before Phase 1, the parenthesized year was removed as a year block before suffix scanning. The remaining suffix began with ITA-ENG. _metadata_suffix_boundary called _canonical_metadata_token on the raw token, yielding 'ita-eng', while its language boundary set expected 'ita', 'eng', or 'itaeng'. The scanner then cut later at AC3 and left ITA-ENG attached to the title.

Phase 1 fix:

The deterministic suffix path now recognizes compound language-chain tokens and cuts the full language/audio/source/subtitle suffix. Poster-only safe spacing variants were added without changing the display title.

## Pattern Analysis

### Language Chains

Language tokens and language chains were a major baseline suspected failure source. Phase 1 substantially reduced this class by recognizing hyphenated, slash-joined, and plus-joined chains such as `ITA-ENG`, `ITA/ENG`, `ITA-THA-ENG`, and `Hindi+English` during suffix classification.

### Compound Language Tokens

The baseline segment classifier could split hyphenated compounds via `_classification_tokens`, but the suffix-boundary scanner used `_canonical_metadata_token` on a single raw token. Phase 1 added compound localization recognition so a token like `ITA-ENG` can be treated as suffix metadata without changing display-title formatting.

### Audio/Channel Chains

Audio codec and channel tokens are usually strong metadata once reached, but they may be reached too late if a preceding language chain is not classified as the suffix boundary. This leaves the language chain in the display title.

### Source/Quality Suffix

Source and quality tokens such as `WEBRip`, `WEB-DL`, `BluRay`, `2160p`, `1080p`, `x265`, and `HEVC` are mostly covered by strong metadata rules. Failures tend to happen when a non-covered token appears before these strong tokens.

### Subtitle Suffix

Subtitle suffixes are inconsistent. `subs`, `subbed`, and `dubbed` are metadata tokens, while short `sub` is handled by scanner boundary sets and can be missed in some compound chains.

### Release Group Suffix

Dash release groups are handled when the segment is clearly dash-separated or attached to known metadata. Bare bracket release groups can be removed as metadata/id only when their segment classification is strong enough.

### Bracket Handling

Bracket groups are removed early when classified as year, edition, metadata, or id. Removing the year early is correct, but it can expose a following language chain as the first suffix token. If that language chain is not recognized, leakage follows.

### Year Extraction

Year extraction is mostly reliable for standalone year blocks and trailing years. The main year-related issue observed here is the interaction between early year removal and suffix-boundary detection.

### Over-Trimming

Over-trimming is less common than under-trimming in this heuristic run, but titles with meaningful numbers/roman numerals should continue to be protected by number-hint logic.

## Proposed Deterministic Parser Architecture

### A. Tokenization Layer

- Normalize Unicode and whitespace.
- Split separators consistently: dot, underscore, dash, bracket, plus.
- Preserve original tokens and normalized tokens.
- Split compound language chains such as `ITA-ENG` into `ITA` and `ENG`, while also recording the original compound.

### B. Backward Suffix Scanner

- Scan from filename end backwards.
- Classify suffix chunks into release group, subtitles, languages, audio codec, audio channels, video codec, source, resolution, HDR/DV, edition, and year.
- Stop only when reaching a probable title region.
- Treat compound language/audio/source chains as suffix metadata when followed by strong metadata or when they occur after an extracted year.

### C. Candidate Model

Produce structured fields:

- clean_title
- year
- edition
- source
- resolution
- video_codec
- audio_codec
- audio_channels
- languages
- subtitle_languages
- release_group
- warnings
- confidence

### D. Stored Title Distrust

- If a stored title ends with technical/language/source metadata, do not trust it over filename-derived parsing.
- Re-derive title from original filename/path when stored metadata appears contaminated.

### E. Poster Matching

- Display title remains clean and human-readable.
- Poster candidates may include safe variants: spacing variants, apostrophe variants, article-dropping variants, and known safe title normalization variants.
- Never use a poster candidate variant as display title unless a future high-confidence rule explicitly says it is the canonical display title.

### F. Existing DB Remediation

- Add a dry-run report first.
- Show old title vs. proposed new title.
- Require admin approval before apply.
- Take a backup/checkpoint before writes.
- Update poster matching cache if necessary.

## Future AI/LLM Phase Note

LLM integration should be a later-only review aid for low-confidence samples. It should produce suggested scrubbed titles and reasons, never automatic database rewrites.

## Existing Library Remediation Plan

A future production fix must address existing contaminated records, not just future scans. Proposed later admin action:

1. `Rescrub library titles` dry run.
2. Review old title, proposed title, year, edition, poster identity, confidence, and warnings.
3. Create backup/checkpoint.
4. Apply selected changes.
5. Refresh poster matching cache if needed.

## Next-Phase Implementation Plan

1. Add deterministic tokenizer and compound language-chain splitting.
2. Replace suffix cutting with a true backward scanner.
3. Expand language/audio/subtitle/source taxonomy.
4. Extract release groups as metadata, not display title.
5. Harden stored-title distrust rules.
6. Add poster candidate variants for safe alternate spellings like `NeverEnding`.
7. Add regression fixtures from this report's top failures.
8. Add dry-run library remediation tooling.

## After Phase 1 Deterministic Parser Improvement

Phase 1 implemented targeted production parser/runtime improvements. No LLM/AI scrubbing, database rewrite, batch rescan, frontend redesign, playback, age grouping, duplicate hiding, or cloud probing change was made.

### What Changed

- Added deterministic localization/subtitle metadata taxonomy for suffix classification.
- Added compound language-chain recognition for forms such as `ITA-ENG`, `ITA/ENG`, `ITA-THA-ENG`, and `Hindi+English`.
- Strengthened metadata-heavy bracket classification, including trailing bracket release groups such as `[ArMor]`, `[MIRCrew]`, and `[Paso77]`.
- Added a backward metadata suffix scanner so language/audio/source chains are cut as one suffix instead of waiting for a later codec token.
- Hardened runtime stored-title distrust for contaminated stored titles such as `The Never Ending Story ITA-ENG`.
- Added poster-only safe spacing variants for `Never Ending` / `NeverEnding`, while keeping the display title unchanged.

### Before / After

| Metric | Baseline | After Phase 1 |
| --- | ---: | ---: |
| Total samples | 1000 | 1000 |
| Suspected failures | 131 | 57 |
| Passed by heuristic | 869 | 943 |
| Suspected failure rate | 13.1% | 5.7% |
| Release group leaked | 98 | 16 |
| Bracket metadata leaked | 79 | 8 |
| Language token leaked | 48 | 7 |
| Compound language token leaked | 17 | 0 |
| Year extraction failed | 29 | 20 |
| Video/source token leaked | 25 | 25 |

### Never Ending Story After Fix

Raw filename:

`The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv`

After Phase 1:

- display_title: `The Never Ending Story`
- base_title: `The Never Ending Story`
- parsed_year: `1984`
- poster_match_title: `The Never Ending Story`
- poster candidates: `The Never Ending Story`, `The NeverEnding Story`, `Never Ending Story`, `NeverEnding Story`
- suspicious_output: `false`

The Phase 1 fix cuts at the compound language suffix instead of leaving `ITA-ENG` attached to the title.

### Remaining Failure Categories

The after-run still flags 57 suspected failures. The largest remaining categories are:

- suspicious parser output: 29
- video/source token leaked: 25
- year extraction failed: 20
- release group leaked: 16
- bracket metadata leaked: 8
- audio token leaked: 8
- language token leaked: 7

Known remaining limitations:

- Some source/quality phrases still need broader taxonomy or safer density rules.
- Some year extraction failures involve unusual punctuation, future titles, or mixed metadata syntax.
- Some suspicious outputs are intentionally conservative and should be reviewed before tightening rules.
- Existing contaminated database rows are not rewritten in this phase; runtime parsing improves presentation where the presentation path uses parser output.

### Report Files

- Baseline JSON: `/tmp/elvern-title-scrub-before-report.json`
- After JSON: `/tmp/elvern-title-scrub-after-report.json`
- After CSV: `/tmp/elvern-title-scrub-after-report.csv`
- After summary: `/tmp/elvern-title-scrub-after-summary.txt`

## Phase 1 Production Change Confirmation

This phase changed deterministic parser/runtime parsing and poster candidate generation only. It did not write the database, rescrub stored `media_items.title` values, add LLM/AI logic, or change frontend/playback/age/duplicate behavior.
