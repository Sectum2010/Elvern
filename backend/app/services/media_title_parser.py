from __future__ import annotations

from datetime import datetime
import re
import unicodedata


YEAR_PATTERN = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
EMPTY_BRACKET_PATTERN = re.compile(r"\(\s*\)|\[\s*\]|\{\s*\}")
BRACKET_GROUP_PATTERN = re.compile(r"(\(|\[|\{)([^()\[\]{}]*)(\)|\]|\})")
RIGHT_SIDE_SPLIT_PATTERN = re.compile(r"\s+-\s*")
ROMAN_NUMERAL_PATTERN = re.compile(r"^(?:ii|iii|iv|v|vi|vii|viii|ix|x)$", re.IGNORECASE)
TITLE_PARSER_VERSION = "movie-title-pipeline-2026-04-23-pattern-hardening"
BRACKET_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
BRACKET_CLOSE_TO_OPEN = {close: open_ for open_, close in BRACKET_OPEN_TO_CLOSE.items()}
SMART_CASE_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
SMART_CASE_CONTRACTION_SUFFIXES = {"d", "ll", "m", "re", "s", "t", "ve"}
SMART_CASE_ACRONYMS = {"dc", "f1", "jfk", "lego", "rec", "vhs"}
DC_TITLE_CONTEXT_FOLLOWERS = {
    "animated",
    "batman",
    "catwoman",
    "comic",
    "comics",
    "girls",
    "hero",
    "heroes",
    "justice",
    "league",
    "shazam",
    "showcase",
    "super",
    "superman",
    "universe",
}
DC_TITLE_CONTEXT_PRECEDERS = {"ac", "lego"}
DC_EDITION_CONTEXT_NEIGHBORS = {
    "cut",
    "extended",
    "roadshow",
    "theatrical",
    "unrated",
    "version",
}
KNOWN_LOWERCASE_LEADING_RELEASE_GROUPS = {
    "armor",
    "bifra",
    "cheerfultomato",
    "cosmo",
    "cosmocrew",
    "cyber",
    "d3lt4crew",
    "dr4gon",
    "frankvjecy",
    "gege",
    "idncrew",
    "kingdom",
    "kris",
    "lullozzo",
    "mirc",
    "mircrew",
    "moon",
    "mrpanda",
    "nonymovies",
    "paso77",
    "psychic",
    "portalgoods",
    "prof",
    "qx r",
    "qxr",
    "sbin k",
    "sbink",
    "sev",
    "tgx",
    "tombdoc",
    "ukbandit",
    "wesley",
    "yts",
    "ytsmx",
}
GENRE_DESCRIPTOR_TOKENS = {
    "action",
    "adventure",
    "animation",
    "biography",
    "com",
    "comedy",
    "crime",
    "documentary",
    "drama",
    "fantasy",
    "fi",
    "film",
    "history",
    "horror",
    "mystery",
    "noir",
    "rom",
    "romance",
    "sci",
    "scifi",
    "science",
    "softcore",
    "fiction",
    "thriller",
    "war",
    "western",
}
POST_COUNTRY_YEAR_DESCRIPTOR_TOKENS = {
    *GENRE_DESCRIPTOR_TOKENS,
    "budget",
    "classic",
    "cult",
    "dual",
    "eng",
    "english",
    "erotic",
    "explicit",
    "hardcoded",
    "korean",
    "multi",
    "no",
    "sci",
    "sub",
    "subs",
    "version",
}
COUNTRY_YEAR_WORDS = {
    "argentina",
    "australia",
    "belgium",
    "brazil",
    "canada",
    "china",
    "denmark",
    "france",
    "germany",
    "greece",
    "hong",
    "india",
    "italy",
    "japan",
    "kong",
    "mexico",
    "netherlands",
    "philippines",
    "poland",
    "spain",
    "sweden",
    "taiwan",
    "uk",
    "ukraine",
    "usa",
    "us",
}

METADATA_TOKENS = {
    "2160p",
    "1080p",
    "720p",
    "540p",
    "480p",
    "4k",
    "8k",
    "60fps",
    "48fps",
    "avi",
    "bd",
    "bdmux",
    "bdrmx",
    "uhd",
    "hd",
    "sd",
    "hdr",
    "hdr10",
    "hdr10plus",
    "hdr10+",
    "dv",
    "dovi",
    "sdr",
    "bluray",
    "blu-ray",
    "bdrip",
    "brrip",
    "dvdr",
    "dvdrip",
    "dvd-rip",
    "dvd5",
    "dvd9",
    "webrip",
    "webdl",
    "web-dl",
    "web",
    "hdtv",
    "vhsrip",
    "hdlight",
    "remux",
    "bdmux",
    "bdremux",
    "brremux",
    "bdrmx",
    "tvrip",
    "x264",
    "x265",
    "h264",
    "h265",
    "h254",
    "hevc",
    "avc",
    "av1",
    "xvid",
    "truehd",
    "atmos",
    "dts",
    "dtsx",
    "dts-hd",
    "dtshd",
    "aac",
    "ac3",
    "ac-3",
    "eac3",
    "e-ac3",
    "eac-3",
    "ddp",
    "dd+",
    "ma",
    "flac",
    "lpcm",
    "mkv",
    "mp4",
    "mp3",
    "mpeg2",
    "mpeg-2",
    "matte",
    "ntsc",
    "opus",
    "pal",
    "pcm",
    "proper",
    "repack",
    "imax",
    "criterion",
    "hybrid",
    "multi",
    "dual",
    "subbed",
    "subs",
    "dubbed",
    "remastered",
    "10bit",
    "8bit",
    "yify",
    "rarbg",
    "framestor",
    "internal",
    "limited",
    "readnfo",
    "audio",
    "ai-enhanced",
    "commentaries",
    "commentary",
    "comm",
    "dd",
    "ddpa",
    "ds4k",
    "esub",
    "esubs",
    "forced",
    "hevc10",
    "msubs",
    "multisub",
    "multisubs",
    "org",
    "open",
    "restored",
    "sub",
    "untouched",
    "upscaled",
    "aienhanced",
    "aiupscale",
    "originalaudio",
}
STRONG_METADATA_TOKENS = {
    "2160p",
    "1080p",
    "720p",
    "4k",
    "uhd",
    "bluray",
    "blu-ray",
    "bdrip",
    "brrip",
    "dvdrip",
    "dvd-rip",
    "dvd5",
    "dvd9",
    "webrip",
    "webdl",
    "web-dl",
    "hdtv",
    "tvrip",
    "vhsrip",
    "remux",
    "bdmux",
    "bdremux",
    "brremux",
    "bdrmx",
    "x264",
    "x265",
    "xvid",
    "h264",
    "h265",
    "h254",
    "hevc",
    "av1",
    "opus",
    "truehd",
    "atmos",
    "dts",
    "dtsx",
    "dts-hd",
    "dtshd",
    "aac",
    "ac3",
    "eac3",
    "ddp",
    "mpeg2",
    "hdr",
    "hdr10",
    "hdr10plus",
    "hdr10+",
    "dv",
    "dovi",
}
LOCALIZATION_METADATA_TOKENS = {
    "dub",
    "dubbed",
    "dual",
    "dualaudio",
    "dut",
    "dutch",
    "br",
    "chi",
    "chinese",
    "cs",
    "czech",
    "de",
    "deu",
    "en",
    "eng",
    "english",
    "es",
    "est",
    "estonian",
    "eur",
    "filipino",
    "fr",
    "fre",
    "fra",
    "french",
    "ger",
    "german",
    "hin",
    "hindi",
    "ita",
    "italian",
    "ja",
    "jap",
    "japanese",
    "jp",
    "jpn",
    "kannada",
    "kor",
    "korean",
    "latino",
    "malayalam",
    "multi",
    "nor",
    "norwegian",
    "por",
    "portuguese",
    "pt",
    "ptbr",
    "rus",
    "russian",
    "spa",
    "spanish",
    "swe",
    "swedish",
    "tagalog",
    "tamil",
    "telugu",
    "tha",
    "truefrench",
    "ukr",
    "ukrainian",
    "vost",
    "vostfr",
    "zh",
}
SUBTITLE_METADATA_TOKENS = {
    "esub",
    "esubs",
    "forced",
    "language",
    "multisub",
    "multisubs",
    "msubs",
    "no",
    "nosub",
    "nosubs",
    "nosubtitles",
    "srt",
    "sub",
    "subbed",
    "subs",
}
LEADING_DECORATOR_PREFIXES = {
    "charlie chaplin",
    "clint eastwood",
    "elvis presley",
    "humphrey bogart",
    "james bond",
    "john wayne",
    "kirk douglas",
    "walt disney",
}
AUDIO_CHANNEL_TOKEN_PATTERN = re.compile(r"(?:[257]\.1|[257]ch|\d+ch|dd[257]\.1|ddp[257]\.1|\d\.\d)")
EDITION_PATTERNS = (
    ("roadshow", re.compile(r"\broadshow(?:\s+version)?\b", re.IGNORECASE)),
    ("director's cut", re.compile(r"\b(?:director'?s|directors)\s+cut\b", re.IGNORECASE)),
    ("theatrical", re.compile(r"\btheatrical(?:\s+cut|\s+version)?\b", re.IGNORECASE)),
    ("extended", re.compile(r"\bextended(?:\s+cut|\s+edition)?\b", re.IGNORECASE)),
    ("final cut", re.compile(r"\bfinal\s+cut\b", re.IGNORECASE)),
    ("ultimate cut", re.compile(r"\bultimate\s+cut\b", re.IGNORECASE)),
    ("ultimate edition", re.compile(r"\bultimate\s+edition\b", re.IGNORECASE)),
    ("special edition", re.compile(r"\bspecial\s+edition\b", re.IGNORECASE)),
    ("collector's edition", re.compile(r"\bcollector'?s\s+edition\b", re.IGNORECASE)),
    ("anniversary edition", re.compile(r"\banniversary\s+edition\b", re.IGNORECASE)),
    ("assembly cut", re.compile(r"\bassembly\s+cut\b", re.IGNORECASE)),
    ("unrated", re.compile(r"\bunrated\b", re.IGNORECASE)),
)


def parse_media_title(
    *,
    title: object,
    original_filename: object,
    year: object,
) -> dict[str, object]:
    # Locked product rule:
    # display_title is the bare movie name only. Year, edition, IDs, and release
    # metadata may still be detected separately, but they must never leak into
    # the UI-facing display title.
    year_hint = _coerce_year(year)
    trusted_title_candidate = _empty_candidate("title", year_hint=year_hint)
    stored_title_candidate = _empty_candidate("stored_title", year_hint=year_hint)
    if _is_trusted_title_input(title):
        trusted_title_candidate = _parse_title_candidate(
            title,
            year_hint=year_hint,
            source="title",
            filename_like=False,
        )
    elif title not in {None, ""}:
        stored_title_candidate = _parse_title_candidate(
            title,
            year_hint=year_hint,
            source="stored_title",
            filename_like=False,
        )
    filename_candidate = _parse_title_candidate(
        original_filename,
        year_hint=year_hint,
        source="original_filename",
        filename_like=True,
    )
    chosen = _select_best_candidate(
        trusted_title_candidate,
        filename_candidate,
        stored_title_candidate,
    )
    chosen, safety_warnings, suspicious_output = _select_safe_output_candidate(
        chosen,
        candidates=(
            trusted_title_candidate,
            filename_candidate,
            stored_title_candidate,
        ),
        parsed_year_hint=year_hint,
    )

    parsed_year = (
        chosen["parsed_year"]
        or filename_candidate["parsed_year"]
        or trusted_title_candidate["parsed_year"]
        or stored_title_candidate["parsed_year"]
        or year_hint
    )
    edition_identity = _merge_edition_identities(
        chosen["edition_identity"],
        filename_candidate["edition_identity"],
        trusted_title_candidate["edition_identity"],
        stored_title_candidate["edition_identity"],
    )
    bare_movie_title = str(chosen["base_title"] or "").strip()
    raw_derived_title = bare_movie_title or str(chosen["fallback_display_title"] or "").strip()
    display_title = _smart_case_display_title(raw_derived_title) or "Untitled"
    poster_match_title = raw_derived_title or None
    warnings = _dedupe_strings(
        [
            *chosen["warnings"],
            *filename_candidate["warnings"],
            *trusted_title_candidate["warnings"],
            *stored_title_candidate["warnings"],
            *safety_warnings,
        ]
    )
    title_source = str(chosen["source"] or "fallback")
    if not bare_movie_title:
        title_source = "fallback"
    poster_match_identity = {
        "title": poster_match_title,
        "year": parsed_year,
        "source": title_source if poster_match_title else None,
    }

    return {
        "display_title": display_title,
        "base_title": raw_derived_title or display_title,
        "edition_identity": edition_identity,
        "parsed_year": parsed_year,
        "poster_match_title": poster_match_title,
        "poster_match_year": parsed_year,
        "poster_match_source": title_source if poster_match_title else None,
        "poster_match_identity": poster_match_identity,
        "title_source": title_source,
        "parse_confidence": chosen["parse_confidence"] if bare_movie_title else "low",
        "warnings": warnings,
        "parser_version": TITLE_PARSER_VERSION,
        "suspicious_output": suspicious_output,
    }


def extract_edition_identity_anywhere(*values: object) -> str:
    markers: list[str] = []
    for value in values:
        prepared = _prepare_candidate_text(value)
        if not prepared:
            continue
        for edition_key, pattern in EDITION_PATTERNS:
            if pattern.search(prepared) and edition_key not in markers:
                markers.append(edition_key)
    return "|".join(markers) if markers else "standard"


def _empty_candidate(source: str, *, year_hint: int | None) -> dict[str, object]:
    return {
        "source": source,
        "base_title": "",
        "parsed_year": year_hint,
        "edition_identity": "standard",
        "parse_confidence": "low",
        "warnings": [],
        "score": 0,
        "fallback_display_title": "",
        "prepared_input": "",
        "title_number_hints": [],
    }


def _parse_title_candidate(
    value: object,
    *,
    year_hint: int | None,
    source: str,
    filename_like: bool,
) -> dict[str, object]:
    prepared = _prepare_candidate_text(value)
    if not prepared:
        return _empty_candidate(source, year_hint=year_hint)

    warnings: list[str] = []
    edition_markers: list[str] = []
    working = prepared
    signal_score = 0
    parsed_year = year_hint
    removed_metadata_bracket_suffix = False

    if EMPTY_BRACKET_PATTERN.search(working):
        working = EMPTY_BRACKET_PATTERN.sub(" ", working)
        warnings.append("empty_bracket_group_removed")
        signal_score += 1

    working, star_year_normalized = _normalize_star_year_blocks(working)
    if star_year_normalized:
        warnings.append("star_year_block_normalized")
        signal_score += 1

    explicit_year_cut = _explicit_year_block_suffix_cut(working)
    if explicit_year_cut is not None:
        working = str(explicit_year_cut["title"])
        parsed_year = _coerce_year(explicit_year_cut["parsed_year"]) or parsed_year
        edition_markers.extend([str(marker) for marker in explicit_year_cut.get("edition_markers") or []])
        warnings.extend([str(marker) for marker in explicit_year_cut["warnings"]])
        signal_score += int(explicit_year_cut["signal_score"])

    if filename_like:
        early_release_year_cut = _release_year_metadata_suffix_cut(working)
        if early_release_year_cut is not None:
            title_prefix, suffix = early_release_year_cut
            suffix_hints = _suffix_parse_hints(
                suffix,
                "standalone_release_year_cut",
                "technical_suffix_density_cut",
            )
            edition_markers.extend(suffix_hints["edition_markers"])
            if parsed_year is None and suffix_hints["parsed_year"] is not None:
                parsed_year = suffix_hints["parsed_year"]
            warnings.extend([str(marker) for marker in suffix_hints.get("rule_markers") or []])
            warnings.append("metadata_suffix_removed")
            signal_score += 2
            working = title_prefix

    working, bracket_hints = _remove_metadata_bracket_spans(working, parsed_year=parsed_year)
    if bracket_hints["parsed_year"] is not None and parsed_year is None:
        parsed_year = bracket_hints["parsed_year"]
    edition_markers.extend([str(marker) for marker in bracket_hints["edition_markers"]])
    warnings.extend([str(marker) for marker in bracket_hints["warnings"]])
    if bracket_hints["removed_metadata_bracket_suffix"]:
        removed_metadata_bracket_suffix = True
    signal_score += int(bracket_hints["signal_score"])

    if "country_year_block_removed" in warnings:
        working, stripped_country_suffix = _strip_post_country_year_descriptor_suffix(working)
        if stripped_country_suffix:
            warnings.append("post_country_year_descriptor_suffix_removed")
            signal_score += 1

    working, stripped_leading_release_group_bracket = _strip_leading_release_group_bracket_prefix(working)
    if stripped_leading_release_group_bracket:
        warnings.append("bracket_release_group_removed")
        warnings.append("metadata_bracket_suffix_removed")
        signal_score += 1

    def replace_bracket_group(match: re.Match[str]) -> str:
        nonlocal parsed_year
        nonlocal signal_score
        nonlocal removed_metadata_bracket_suffix
        content = collapse_spaces(match.group(2))
        if not content:
            warnings.append("empty_bracket_group_removed")
            signal_score += 1
            return " "
        year_pair_match = re.fullmatch(r"(19\d{2}|20\d{2})/(?:19\d{2}|20\d{2})", content)
        if year_pair_match:
            if parsed_year is None:
                parsed_year = _coerce_year(year_pair_match.group(1))
            warnings.append("year_block_removed")
            signal_score += 1
            return " "

        classification = _classify_segment(content)
        if classification["kind"] == "year":
            if parsed_year is None:
                parsed_year = _coerce_year(content)
            warnings.append("year_block_removed")
            signal_score += 1
            return " "
        if classification["kind"] == "edition":
            edition_markers.extend(classification["edition_markers"])
            warnings.append("edition_block_extracted")
            signal_score += 1
            return " "
        country_year = _country_year_bracket_year(content)
        if country_year is not None and working[: match.start()].strip(" -"):
            if parsed_year is None:
                parsed_year = country_year
            warnings.append("country_year_block_removed")
            signal_score += 1
            return " "
        leading_bracket_group = not working[: match.start()].strip(" -")
        if leading_bracket_group and _looks_like_leading_release_group_prefix(
            content,
            working[match.end() :],
        ):
            warnings.append("bracket_release_group_removed")
            warnings.append("metadata_bracket_suffix_removed")
            signal_score += 1
            return " "
        preserve_leading_bracket_title = leading_bracket_group and (
            _looks_like_leading_bracket_title_acronym(content, working[match.end() :])
            or content.upper() == "REC"
        )
        trailing_bracket_group = not working[match.end() :].strip(" -")
        prefix_before_bracket = working[: match.start()].strip(" -")
        if (
            trailing_bracket_group
            and prefix_before_bracket
            and (
                _looks_like_bare_release_group_token(content)
                or _looks_like_trailing_release_group_after_metadata(
                    content,
                    prefix_before_bracket,
                )
            )
            and not _looks_like_episode_identity_token(content)
        ):
            removed_metadata_bracket_suffix = True
            warnings.append("bracket_release_group_removed")
            warnings.append("metadata_bracket_suffix_removed")
            signal_score += 1
            return " "
        if classification["kind"] in {"metadata", "id"} and not preserve_leading_bracket_title:
            removed_metadata_bracket_suffix = True
            if classification["edition_markers"]:
                edition_markers.extend(classification["edition_markers"])
                warnings.append("edition_block_extracted")
            if parsed_year is None and classification.get("parsed_year") is not None:
                parsed_year = _coerce_year(classification.get("parsed_year"))
            warnings.append(
                "metadata_id_block_removed"
                if classification["kind"] == "id"
                else "metadata_block_removed"
            )
            if classification["kind"] == "metadata":
                warnings.append("bracket_metadata_removed")
            warnings.append("metadata_bracket_suffix_removed")
            signal_score += 2 if classification["kind"] == "id" else 1
            return " "
        return f"{match.group(1)}{content}{match.group(3)}"

    for _iteration in range(4):
        updated_working = BRACKET_GROUP_PATTERN.sub(replace_bracket_group, working)
        updated_working = collapse_spaces(updated_working)
        if updated_working == working:
            break
        working = updated_working

    working, stripped_trailing_credit = _strip_trailing_post_year_credit_suffix(
        working,
        parsed_year=parsed_year,
        allow_dash_person_suffix=removed_metadata_bracket_suffix,
    )
    if stripped_trailing_credit:
        warnings.append("post_year_credit_suffix_removed")
        signal_score += 1

    working, stripped_dash_genre = _strip_trailing_dash_genre_descriptor(working)
    if stripped_dash_genre:
        warnings.append("dash_genre_descriptor_removed")
        signal_score += 1

    working, stripped_leading_credit = _strip_leading_credit_prefix(working, parsed_year=parsed_year)
    if stripped_leading_credit:
        warnings.append("leading_credit_prefix_removed")
        signal_score += 1

    if filename_like:
        release_year_cut = _release_year_metadata_suffix_cut(working)
        if release_year_cut is not None and _release_year_cut_starts_with_descriptor_span(release_year_cut[1]):
            title_prefix, suffix = release_year_cut
            suffix_hints = _suffix_parse_hints(
                suffix,
                "standalone_release_year_cut",
                "technical_suffix_density_cut",
            )
            edition_markers.extend(suffix_hints["edition_markers"])
            if parsed_year is None and suffix_hints["parsed_year"] is not None:
                parsed_year = suffix_hints["parsed_year"]
            warnings.extend([str(marker) for marker in suffix_hints.get("rule_markers") or []])
            warnings.append("metadata_suffix_removed")
            signal_score += 2
            working = title_prefix

    kept_segments: list[str] = []
    for index, segment in enumerate(RIGHT_SIDE_SPLIT_PATTERN.split(working)):
        cleaned_segment = collapse_spaces(segment).strip(" -")
        if not cleaned_segment:
            continue
        if index > 0:
            genre_suffix_hints = _dash_genre_descriptor_suffix_hints(cleaned_segment)
            if genre_suffix_hints is not None:
                if parsed_year is None and genre_suffix_hints["parsed_year"] is not None:
                    parsed_year = _coerce_year(genre_suffix_hints["parsed_year"])
                warnings.extend([str(marker) for marker in genre_suffix_hints.get("rule_markers") or []])
                warnings.append("dash_genre_descriptor_removed")
                signal_score += 1
                continue
        classification = _classify_segment(cleaned_segment)
        if index > 0 and classification["kind"] in {"metadata", "id", "edition", "year"}:
            continuation = _split_dash_title_continuation(cleaned_segment)
            if continuation is not None:
                title_prefix, metadata_suffix, continuation_hints = continuation
                kept_segments.append(title_prefix)
                removed_suffix = metadata_suffix or cleaned_segment[len(title_prefix) :].strip(" -")
                if removed_suffix:
                    warnings.extend([str(marker) for marker in continuation_hints.get("rule_markers") or []])
                    if parsed_year is None and continuation_hints.get("parsed_year") is not None:
                        parsed_year = _coerce_year(continuation_hints.get("parsed_year"))
                    warnings.append("metadata_segment_removed")
                    warnings.append("technical_suffix_density_cut")
                    signal_score += 1
                continue
            edition_markers.extend(classification["edition_markers"])
            if classification["kind"] in {"metadata", "id"}:
                warnings.append("metadata_segment_removed")
                warnings.append("technical_suffix_density_cut")
                if _looks_like_bracket_plus_release_group(cleaned_segment) or (
                    removed_metadata_bracket_suffix and _looks_like_bare_release_group_token(cleaned_segment)
                ):
                    warnings.append("dash_release_group_suffix_removed")
            elif classification["kind"] == "year":
                warnings.append("standalone_release_year_cut")
            else:
                warnings.append("edition_segment_extracted")
            signal_score += 2 if classification["kind"] == "id" else 1
            if classification["kind"] == "year" and parsed_year is None:
                parsed_year = _coerce_year(cleaned_segment)
            continue
        if (
            index > 0
            and removed_metadata_bracket_suffix
            and _looks_like_dash_suffix_junk_segment(cleaned_segment)
        ):
            warnings.append("dash_release_group_suffix_removed")
            signal_score += 1
            continue
        kept_segments.append(cleaned_segment)
    working = " - ".join(kept_segments)
    if removed_metadata_bracket_suffix:
        working, removed_trailing_group = _strip_trailing_bare_release_group_after_metadata(working)
        if removed_trailing_group:
            warnings.append("dash_release_group_suffix_removed")
            signal_score += 1

    if filename_like:
        working, cut_suffix, suffix_hints = _cut_non_title_suffix(working, parsed_year=parsed_year)
        if cut_suffix:
            edition_markers.extend(suffix_hints["edition_markers"])
            if parsed_year is None and suffix_hints["parsed_year"] is not None:
                parsed_year = suffix_hints["parsed_year"]
            warnings.extend([str(marker) for marker in suffix_hints.get("rule_markers") or []])
            warnings.append("metadata_suffix_removed")
            signal_score += 2

    working, removed_metadata = _strip_trailing_metadata_tokens(working)
    if removed_metadata:
        warnings.append("trailing_metadata_removed")
        signal_score += 1

    working, removed_extras = _strip_trailing_extras_suffix(working, parsed_year=parsed_year)
    if removed_extras:
        warnings.append("extras_suffix_removed")
        signal_score += 1

    working, stripped_year, removed_year = _strip_trailing_year(working, parsed_year=parsed_year)
    if stripped_year is not None:
        parsed_year = stripped_year
    if removed_year:
        warnings.append("trailing_year_removed")
        signal_score += 1

    working, stripped_editions = _strip_edition_suffixes(
        working,
        parsed_year=parsed_year,
        suffix_context=bool(edition_markers),
    )
    if stripped_editions:
        edition_markers.extend(stripped_editions)
        warnings.append("edition_suffix_extracted")
        signal_score += 1

    working, stripped_year, removed_year = _strip_trailing_year(working, parsed_year=parsed_year)
    if stripped_year is not None:
        parsed_year = stripped_year
    if removed_year:
        warnings.append("trailing_year_removed")
        signal_score += 1

    working = _cleanup_title_text(working)
    if not working:
        fallback_title = _cleanup_title_text(_strip_metadata_tokens_from_edges(prepared))
        if not fallback_title:
            fallback_title = _cleanup_title_text(prepared)
        return {
            "source": source,
            "base_title": "",
            "parsed_year": parsed_year,
            "edition_identity": _merge_edition_identities(*edition_markers),
            "parse_confidence": "low",
            "warnings": _dedupe_strings([*warnings, "title_fell_back_to_raw_text"]),
            "score": 1,
            "fallback_display_title": fallback_title,
            "prepared_input": prepared,
            "title_number_hints": _extract_meaningful_title_number_hints(
                prepared,
                parsed_year=parsed_year,
            ),
        }

    score = signal_score + (3 if len(working.split()) >= 2 else 2)
    if source == "title":
        score += 1
    parse_confidence = "high" if score >= 5 else "medium" if score >= 3 else "low"
    return {
        "source": source,
        "base_title": working,
        "parsed_year": parsed_year,
        "edition_identity": _merge_edition_identities(*edition_markers),
        "parse_confidence": parse_confidence,
        "warnings": _dedupe_strings(warnings),
        "score": score,
        "fallback_display_title": working,
        "prepared_input": prepared,
        "title_number_hints": _extract_meaningful_title_number_hints(
            prepared,
            parsed_year=parsed_year,
        ),
    }


def _select_best_candidate(
    trusted_title_candidate: dict[str, object],
    filename_candidate: dict[str, object],
    stored_title_candidate: dict[str, object],
) -> dict[str, object]:
    title_base = str(trusted_title_candidate["base_title"] or "").strip()
    filename_base = str(filename_candidate["base_title"] or "").strip()
    stored_title_base = str(stored_title_candidate["base_title"] or "").strip()
    if title_base:
        if filename_base and _comparison_key(title_base) == _comparison_key(filename_base):
            chosen = dict(trusted_title_candidate)
            if not chosen["parsed_year"] and filename_candidate["parsed_year"]:
                chosen["parsed_year"] = filename_candidate["parsed_year"]
            chosen["edition_identity"] = _merge_edition_identities(
                trusted_title_candidate["edition_identity"],
                filename_candidate["edition_identity"],
            )
            chosen["warnings"] = _dedupe_strings(
                [*trusted_title_candidate["warnings"], *filename_candidate["warnings"]]
            )
            chosen["score"] = max(int(trusted_title_candidate["score"]), int(filename_candidate["score"])) + 1
            return chosen
        if filename_base and _filename_candidate_extends_trusted_title(
            trusted_title_candidate=trusted_title_candidate,
            filename_candidate=filename_candidate,
        ):
            chosen = dict(filename_candidate)
            if not chosen["parsed_year"] and trusted_title_candidate["parsed_year"]:
                chosen["parsed_year"] = trusted_title_candidate["parsed_year"]
            chosen["edition_identity"] = _merge_edition_identities(
                filename_candidate["edition_identity"],
                trusted_title_candidate["edition_identity"],
            )
            chosen["warnings"] = _dedupe_strings(
                [*filename_candidate["warnings"], *trusted_title_candidate["warnings"]]
            )
            chosen["score"] = max(int(trusted_title_candidate["score"]), int(filename_candidate["score"])) + 1
            return chosen
        return trusted_title_candidate
    if filename_base:
        return filename_candidate
    if stored_title_base:
        return stored_title_candidate
    if filename_candidate["fallback_display_title"]:
        return filename_candidate
    if trusted_title_candidate["fallback_display_title"]:
        return trusted_title_candidate
    return stored_title_candidate


def _select_safe_output_candidate(
    chosen_candidate: dict[str, object],
    *,
    candidates: tuple[dict[str, object], ...],
    parsed_year_hint: int | None,
) -> tuple[dict[str, object], list[str], bool]:
    chosen_risks = _display_title_risk_warnings(
        _candidate_display_value(chosen_candidate),
        candidate=chosen_candidate,
        parsed_year=_coerce_year(chosen_candidate["parsed_year"]) or parsed_year_hint,
    )
    if not chosen_risks:
        return chosen_candidate, [], False

    best_candidate = chosen_candidate
    best_risks = chosen_risks
    for candidate in candidates:
        candidate_display = _candidate_display_value(candidate)
        if not candidate_display:
            continue
        candidate_risks = _display_title_risk_warnings(
            candidate_display,
            candidate=candidate,
            parsed_year=_coerce_year(candidate["parsed_year"]) or parsed_year_hint,
        )
        if len(candidate_risks) < len(best_risks):
            best_candidate = candidate
            best_risks = candidate_risks

    warnings = chosen_risks[:]
    if best_candidate is not chosen_candidate:
        warnings.append(
            f"suspicious_display_title_fallback:{chosen_candidate['source']}->{best_candidate['source']}"
        )
        warnings.extend(best_risks)
    return best_candidate, _dedupe_strings(warnings), True


def _candidate_display_value(candidate: dict[str, object]) -> str:
    return str(candidate.get("base_title") or candidate.get("fallback_display_title") or "").strip()


def _display_title_risk_warnings(
    value: str,
    *,
    candidate: dict[str, object],
    parsed_year: int | None,
) -> list[str]:
    warnings: list[str] = []
    cleaned = collapse_spaces(str(value or "")).strip()
    if not cleaned:
        return ["display_title_empty"]
    if cleaned.lower() in {"the", "a", "an"}:
        warnings.append("display_title_implausibly_short")
    if EMPTY_BRACKET_PATTERN.search(cleaned):
        warnings.append("display_title_contains_empty_brackets")
    if _contains_metadata_id(cleaned):
        warnings.append("display_title_contains_metadata_id")

    if any(_looks_like_release_group_token(token) for token in cleaned.split()):
        warnings.append("display_title_contains_release_group_suffix")

    tokens = _classification_tokens(cleaned)
    if any(_token_is_strong_metadata(token) for token in tokens):
        warnings.append("display_title_contains_metadata_token")
    if _contains_compound_localization_token(cleaned):
        warnings.append("display_title_contains_compound_language_suffix")
    if _looks_like_metadata_contaminated_title(cleaned):
        warnings.append("display_title_contains_metadata_token")

    source_hints = [str(hint) for hint in candidate.get("title_number_hints") or []]
    display_hints = _extract_meaningful_title_number_hints(cleaned, parsed_year=parsed_year)
    if any(hint not in display_hints for hint in source_hints):
        warnings.append("display_title_lost_meaningful_number_token")

    return _dedupe_strings(warnings)


def _prepare_candidate_text(value: object) -> str:
    if value in {None, ""}:
        return ""
    basename = _candidate_basename(str(value))
    without_extension = re.sub(r"\.[a-z0-9]{2,5}$", "", basename, flags=re.IGNORECASE)
    normalized = unicodedata.normalize("NFKC", without_extension)
    normalized = normalized.replace("\\'", "'").replace('\\"', '"')
    normalized = normalized.replace("\u00a0", " ")
    normalized = normalized.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"[._]+", " ", normalized)
    normalized = re.sub(r"\s+-\s*", " - ", normalized)
    normalized = collapse_spaces(normalized)
    return normalized.strip(" -")


def _candidate_basename(value: str) -> str:
    raw = str(value or "")
    if _looks_like_windows_path(raw):
        return raw.split("\\")[-1]
    if "/" not in raw:
        return raw
    if (
        _contains_language_slash(raw)
        or _looks_like_slash_title(raw)
        or _looks_like_spaced_slash_title(raw)
        or _slash_is_inside_bracket_span(raw)
    ):
        return raw
    parts = raw.split("/")
    if raw.startswith("/") or len(parts) > 2 or re.search(r"\.[a-z0-9]{2,5}$", parts[-1], re.IGNORECASE):
        return parts[-1]
    return raw


def _looks_like_windows_path(value: str) -> bool:
    if not value or "\\" not in value:
        return False
    if re.match(r"^[a-zA-Z]:\\", value):
        return True
    if value.startswith("\\\\"):
        return True
    parts = [part for part in value.split("\\") if part]
    if len(parts) < 3:
        return False
    final_part = parts[-1]
    return re.search(r"\.[a-z0-9]{2,5}$", final_part, re.IGNORECASE) is not None


def _contains_language_slash(value: str) -> bool:
    return re.search(
        r"(?i)\b(?:ita|eng|en|ger|de|deu|jpn|jap|ja|jp|fra|fre|fr|spa|es|hin|hindi|tha|por|pt|br|rus|kor|korean|zh|chi|chinese)"
        r"(?:/(?:ita|eng|en|ger|de|deu|jpn|jap|ja|jp|fra|fre|fr|spa|es|hin|hindi|tha|por|pt|br|rus|kor|korean|zh|chi|chinese)){1,}\b",
        value,
    ) is not None


def _looks_like_slash_title(value: str) -> bool:
    return re.search(r"(?i)\b(?:v/h/s|[a-z]/[a-z]/[a-z])\b", value) is not None


def _looks_like_spaced_slash_title(value: str) -> bool:
    raw = str(value or "")
    if " / " in raw:
        return True
    if "／" in raw:
        return True
    return re.search(r"(?<!^)\((?:19\d{2}|20\d{2})/(?:19\d{2}|20\d{2})\)", raw) is not None


def _slash_is_inside_bracket_span(value: str) -> bool:
    raw = str(value or "")
    for span in _find_bracket_spans(raw):
        if "/" in str(span["content"]):
            return True
    return False


def _find_bracket_spans(value: str) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    stack: list[tuple[str, int]] = []
    for index, char in enumerate(str(value or "")):
        if char in BRACKET_OPEN_TO_CLOSE:
            stack.append((char, index))
            continue
        if char not in BRACKET_CLOSE_TO_OPEN:
            continue
        expected_open = BRACKET_CLOSE_TO_OPEN[char]
        matching_index = next(
            (position for position in range(len(stack) - 1, -1, -1) if stack[position][0] == expected_open),
            None,
        )
        if matching_index is None:
            continue
        open_char, start = stack[matching_index]
        del stack[matching_index:]
        spans.append(
            {
                "start": start,
                "end": index + 1,
                "content_start": start + 1,
                "content_end": index,
                "content": value[start + 1 : index],
                "open": open_char,
                "close": char,
            }
        )
    return sorted(spans, key=lambda span: (int(span["start"]), int(span["end"])))


def _normalize_star_year_blocks(value: str) -> tuple[str, bool]:
    updated = re.sub(r"\*(19\d{2}|20\d{2})\*", r"(\1)", value)
    return updated, updated != value


def _explicit_year_block_suffix_cut(value: str) -> dict[str, object] | None:
    working = collapse_spaces(value).strip(" -")
    if not working:
        return None
    for span in _find_bracket_spans(working):
        start = int(span["start"])
        end = int(span["end"])
        prefix = working[:start].strip(" -")
        suffix = working[end:].strip(" -")
        if not prefix:
            continue
        content = collapse_spaces(str(span["content"]))
        parsed_year: int | None = None
        title_prefix = prefix
        warnings: list[str] = []
        if _is_standalone_year(content):
            parsed_year = _coerce_year(content)
            if not suffix or not _post_year_suffix_is_stripworthy(suffix):
                continue
            warnings.extend(["year_block_removed", "metadata_suffix_removed"])
        else:
            country_year = _country_year_bracket_year(content)
            if country_year is not None:
                parsed_year = country_year
                if suffix and not _post_country_year_suffix_is_stripworthy(suffix):
                    continue
                warnings.append("country_year_block_removed")
                if suffix:
                    warnings.append("post_country_year_descriptor_suffix_removed")
            else:
                director_year = _director_year_parenthetical(content)
                if director_year is not None and (not suffix or _post_year_suffix_is_stripworthy(suffix)):
                    parsed_year = director_year
                    warnings.append("director_year_block_removed")
                    if suffix:
                        warnings.append("metadata_suffix_removed")
                else:
                    alternate = _alternate_title_year_parenthetical(content)
                    if alternate is None:
                        continue
                    alternate_title, alternate_year = alternate
                    if suffix and not _post_year_suffix_is_stripworthy(suffix):
                        continue
                    parsed_year = alternate_year
                    title_prefix = f"{prefix} ({alternate_title})"
                    warnings.append("alternate_title_year_block_extracted")
                    if suffix:
                        warnings.append("metadata_suffix_removed")
        if parsed_year is None:
            continue
        suffix_hints = _suffix_parse_hints(suffix, "technical_suffix_density_cut") if suffix else {
            "edition_markers": [],
            "rule_markers": [],
        }
        return {
            "title": collapse_spaces(title_prefix).strip(" -"),
            "parsed_year": parsed_year,
            "warnings": _dedupe_strings([*warnings, "technical_suffix_density_cut"] if suffix else warnings),
            "edition_markers": suffix_hints["edition_markers"],
            "signal_score": 2 if suffix else 1,
        }
    return None


def _country_year_bracket_year(content: str) -> int | None:
    cleaned = collapse_spaces(content).strip(" -")
    match = re.fullmatch(
        r"(?i)(?P<year>19\d{2}|20\d{2})\s*(?:[-+/]|and)\s*(?P<countries>[A-Za-z][A-Za-z .,'&+-]{1,80})",
        cleaned,
    )
    if match is None:
        match = re.fullmatch(
            r"(?i)(?P<countries>[A-Za-z][A-Za-z .,'&+-]{1,80})\s*(?:[-+/]|and)\s*(?P<year>19\d{2}|20\d{2})",
            cleaned,
        )
    if match is None:
        return None
    countries = collapse_spaces(match.group("countries")).strip(" -+")
    if not countries or YEAR_PATTERN.search(countries):
        return None
    tokens = [
        token
        for token in re.findall(r"[A-Za-z]+", countries)
        if token.lower() not in {"and"}
    ]
    if not tokens or len(tokens) > 8:
        return None
    if not all(token.lower() in COUNTRY_YEAR_WORDS for token in tokens):
        return None
    return _coerce_year(match.group("year"))


def _alternate_title_year_parenthetical(content: str) -> tuple[str, int] | None:
    cleaned = collapse_spaces(content).strip(" -")
    match = re.fullmatch(r"(.+?)\s*[-,]\s*(19\d{2}|20\d{2})", cleaned)
    if match is None:
        return None
    alternate = collapse_spaces(match.group(1)).strip(" -")
    if not alternate or _looks_like_genre_descriptor(alternate) or _looks_like_metadata_bracket_span(alternate):
        return None
    if len(_meaningful_title_tokens_for_suffix(alternate)) < 1:
        return None
    return alternate, int(match.group(2))


def _post_year_suffix_is_stripworthy(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -_.,)")
    if not cleaned:
        return False
    if _label_suffix_is_metadata(cleaned):
        return True
    if _post_year_person_credit_suffix(cleaned):
        return True
    if _suffix_after_release_year_is_metadata(cleaned):
        return True
    if _metadata_suffix_core_is_strong(cleaned):
        return True
    if _post_year_plain_descriptor_prefix(cleaned) != cleaned and _metadata_suffix_core_is_strong(
        _post_year_plain_descriptor_prefix(cleaned)
    ):
        return True
    return False


def _label_suffix_is_metadata(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -_.,)")
    if not cleaned:
        return False
    return re.search(
        r"(?i)\b(?:language|lang|subs?|with\s+subs?|hardcoded\s+[a-z]{2,3}\s+subs?|audio[-\s]+no\s+subs?|english\s+subs?|nl\s+subs?|srt|ptbr)\b",
        cleaned,
    ) is not None


def _post_year_person_credit_suffix(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return False
    if not (
        _metadata_suffix_core_is_strong(cleaned)
        or _contains_compound_metadata_token(cleaned)
        or any(_token_is_strong_metadata(token) for token in _classification_tokens(cleaned))
    ):
        return False
    first_piece = re.split(r"\b(?:1080p|720p|2160p|480p|h264|h265|x264|x265|ac\s*3|ac3|aac|dts|dolby|dvd|blu|web|brrip|bdrip)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    first_piece = collapse_spaces(first_piece).strip(" -&")
    if not first_piece:
        return True
    lowered = first_piece.lower()
    if lowered in LEADING_DECORATOR_PREFIXES or lowered in {
        "cartoon",
        "cartoon movie",
        "demo",
        "james bond",
        "moviesbyrizzo",
        "mrpanda",
        "nickarad",
        "remastered",
        "walt disney",
    }:
        return True
    return _looks_like_person_credit_phrase(first_piece)


def _post_year_plain_descriptor_prefix(value: str) -> str:
    working = collapse_spaces(value).strip(" -")
    updated = re.sub(r"(?i)^(?:\d+(?:st|nd|rd|th)\s+anniv(?:ersary)?|anniversary|remastered|uncut|nc-17|english\s+version|eng(?:lish)?|dual\s+[a-z ]+|cartoon(?:\s+movie)?|walt\s+disney|james\s+bond)\b[\s:-]*", "", working).strip(" -")
    return collapse_spaces(updated)


def _post_country_year_suffix_is_stripworthy(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -_.,)")
    if not cleaned:
        return False
    if _post_year_suffix_is_stripworthy(cleaned):
        return True
    without_brackets = BRACKET_GROUP_PATTERN.sub(" ", cleaned)
    tokens = [
        _canonical_metadata_token(token)
        for token in re.findall(r"[A-Za-z0-9]+", without_brackets)
        if _canonical_metadata_token(token)
    ]
    if not tokens or YEAR_PATTERN.search(cleaned):
        return False
    if len(tokens) > 12:
        return False
    if any(token in GENRE_DESCRIPTOR_TOKENS for token in tokens):
        return True
    return all(token in POST_COUNTRY_YEAR_DESCRIPTOR_TOKENS for token in tokens)


def _strip_post_country_year_descriptor_suffix(value: str) -> tuple[str, bool]:
    working = collapse_spaces(value).strip(" -")
    if not working:
        return working, False
    tokens = working.split()
    for index in range(1, len(tokens)):
        suffix = " ".join(tokens[index:])
        if _post_country_year_suffix_is_stripworthy(suffix):
            prefix = " ".join(tokens[:index]).strip(" -")
            if prefix:
                return prefix, True
    return working, False


def _strip_leading_credit_prefix(value: str, *, parsed_year: int | None) -> tuple[str, bool]:
    if parsed_year is None:
        return value, False
    working = collapse_spaces(value).strip(" -")
    if not working:
        return working, False
    match = re.match(r"^(.{2,80}?)\s*-\s*(.+)$", working)
    if match is None:
        return working, False
    left = collapse_spaces(match.group(1)).strip(" -")
    right = collapse_spaces(match.group(2)).strip(" -")
    if not left or not right:
        return working, False
    if not _looks_like_credit_prefix(left):
        return working, False
    if _looks_like_genre_descriptor(right):
        return working, False
    right_tokens = _meaningful_title_tokens_for_suffix(right)
    if len(right_tokens) < 2 and not right_tokens[:1] == ["20000"]:
        return working, False
    return right, True


def _looks_like_credit_prefix(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in LEADING_DECORATOR_PREFIXES:
        return True
    return False


def _strip_leading_release_group_bracket_prefix(value: str) -> tuple[str, bool]:
    working = collapse_spaces(value).strip(" -")
    match = re.match(r"^\[([^\[\]]{2,40})\]\s+(.+)$", working)
    if match is None:
        return working, False
    content = collapse_spaces(match.group(1))
    if _looks_like_leading_bracket_title_acronym(content, match.group(2)) or content == "18+":
        return working, False
    if not (
        _looks_like_release_group_span_content(content)
        or _looks_like_metadata_bracket_span(content)
        or content.lower() in KNOWN_LOWERCASE_LEADING_RELEASE_GROUPS
    ):
        return working, False
    remainder = collapse_spaces(match.group(2)).strip(" -")
    if not remainder:
        return working, False
    return remainder, True


def _strip_trailing_dash_genre_descriptor(value: str) -> tuple[str, bool]:
    working = collapse_spaces(value).strip(" -")
    if " - " not in working:
        return working, False
    left, right = working.rsplit(" - ", 1)
    if not left or not right:
        return working, False
    if not _looks_like_genre_descriptor(right):
        return working, False
    return left.strip(" -"), True


def _strip_trailing_post_year_credit_suffix(
    value: str,
    *,
    parsed_year: int | None,
    allow_dash_person_suffix: bool,
) -> tuple[str, bool]:
    if parsed_year is None:
        return value, False
    working = collapse_spaces(value).strip(" -")
    if not working:
        return working, False
    updated = re.sub(r"\s*[\[(](?:cast|credits?|actors?)[\])]\s*$", "", working, flags=re.IGNORECASE).strip(" -")
    if updated and updated != working:
        return updated, True
    spans = _find_bracket_spans(working)
    if spans:
        last = spans[-1]
        if int(last["end"]) == len(working):
            content = collapse_spaces(str(last["content"]))
            prefix = working[: int(last["start"])].strip(" -")
            if allow_dash_person_suffix and prefix and _looks_like_person_credit_phrase(content):
                return prefix, True
    if allow_dash_person_suffix and " - " in working:
        left, right = working.rsplit(" - ", 1)
        if left and _looks_like_person_credit_phrase(right):
            return left.strip(" -"), True
    return working, False


def _looks_like_person_credit_phrase(value: str) -> bool:
    pieces = [
        collapse_spaces(piece).strip(" -")
        for piece in re.split(r"\s*(?:&|,|\band\b)\s*", value)
        if collapse_spaces(piece).strip(" -")
    ]
    if not pieces or len(pieces) > 4:
        return False
    return all(_looks_like_compact_person_name(piece) for piece in pieces)


def _looks_like_compact_person_name(value: str) -> bool:
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'’.-]+", value)
    if not tokens or len(tokens) > 3:
        return False
    lowered = [_canonical_metadata_token(token) for token in tokens]
    if any(token in SMART_CASE_STOPWORDS or _token_is_metadata(token) for token in lowered):
        return False
    return all(token[:1].isupper() or token.isupper() for token in tokens)


def _meaningful_title_tokens_for_suffix(value: str) -> list[str]:
    result: list[str] = []
    for token in _classification_tokens(value):
        if not token or _is_standalone_year(token) or _token_is_suffix_metadata(token):
            continue
        result.append(token)
    return result


def _remove_metadata_bracket_spans(value: str, *, parsed_year: int | None) -> tuple[str, dict[str, object]]:
    spans = _find_bracket_spans(value)
    if not spans:
        return value, {
            "warnings": [],
            "edition_markers": [],
            "parsed_year": None,
            "signal_score": 0,
            "removed_metadata_bracket_suffix": False,
        }

    remove_ranges: list[tuple[int, int]] = []
    warnings: list[str] = []
    edition_markers: list[str] = []
    detected_year = parsed_year
    signal_score = 0
    removed_metadata_bracket_suffix = False

    for span in sorted(spans, key=lambda item: (-(int(item["end"]) - int(item["start"])), int(item["start"]))):
        start = int(span["start"])
        end = int(span["end"])
        if any(existing_start <= start and end <= existing_end for existing_start, existing_end in remove_ranges):
            continue
        content = collapse_spaces(str(span["content"]))
        if not content:
            remove_ranges.append((start, end))
            warnings.append("empty_bracket_group_removed")
            signal_score += 1
            continue
        if _looks_like_leading_bracket_title_acronym(content, value[end:]) or content.upper() == "REC" or content == "18+":
            continue

        year_pair_match = re.fullmatch(r"(19\d{2}|20\d{2})/(?:19\d{2}|20\d{2})", content)
        if year_pair_match:
            if detected_year is None:
                detected_year = _coerce_year(year_pair_match.group(1))
            remove_ranges.append((start, end))
            warnings.append("year_block_removed")
            signal_score += 1
            continue

        classification = _classify_segment(content)
        leading_bracket_group = not value[:start].strip(" -")
        trailing_bracket_group = not value[end:].strip(" -")
        prefix_before_bracket = value[:start].strip(" -")
        if classification["kind"] == "year":
            if detected_year is None:
                detected_year = _coerce_year(content)
            remove_ranges.append((start, end))
            warnings.append("year_block_removed")
            signal_score += 1
            continue
        if classification["kind"] == "edition":
            edition_markers.extend([str(marker) for marker in classification["edition_markers"]])
            remove_ranges.append((start, end))
            warnings.append("edition_block_extracted")
            signal_score += 1
            continue
        country_year = _country_year_bracket_year(content)
        if country_year is not None and prefix_before_bracket:
            if detected_year is None:
                detected_year = country_year
            remove_ranges.append((start, end))
            warnings.append("country_year_block_removed")
            signal_score += 1
            continue
        director_year = _director_year_parenthetical(content)
        if director_year is not None:
            if detected_year is None:
                detected_year = director_year
            remove_ranges.append((start, end))
            warnings.append("director_year_block_removed")
            signal_score += 1
            continue
        if _looks_like_edition_bracket_span(content, classification):
            edition_markers.extend([str(marker) for marker in classification["edition_markers"]])
            remove_ranges.append((start, end))
            warnings.append("edition_block_extracted")
            warnings.append("metadata_bracket_suffix_removed")
            signal_score += 1
            continue
        if leading_bracket_group and _looks_like_leading_release_group_prefix(content, value[end:]):
            remove_ranges.append((start, end))
            warnings.append("bracket_release_group_removed")
            warnings.append("metadata_bracket_suffix_removed")
            signal_score += 1
            continue
        if (
            trailing_bracket_group
            and prefix_before_bracket
            and (
                _looks_like_release_group_span_content(content)
                or _looks_like_trailing_release_group_after_metadata(content, prefix_before_bracket)
            )
        ):
            remove_ranges.append((start, end))
            warnings.append("bracket_release_group_removed")
            warnings.append("metadata_bracket_suffix_removed")
            removed_metadata_bracket_suffix = True
            signal_score += 1
            continue
        if classification["kind"] in {"metadata", "id"} or _looks_like_metadata_bracket_span(content):
            if classification["edition_markers"]:
                edition_markers.extend([str(marker) for marker in classification["edition_markers"]])
                warnings.append("edition_block_extracted")
            if detected_year is None and classification.get("parsed_year") is not None:
                detected_year = _coerce_year(classification.get("parsed_year"))
            remove_ranges.append((start, end))
            warnings.append(
                "metadata_id_block_removed"
                if classification["kind"] == "id"
                else "metadata_block_removed"
            )
            warnings.append("bracket_metadata_removed")
            warnings.append("metadata_bracket_suffix_removed")
            removed_metadata_bracket_suffix = True
            signal_score += 2 if classification["kind"] == "id" else 1

    if not remove_ranges:
        return value, {
            "warnings": [],
            "edition_markers": [],
            "parsed_year": None if parsed_year == detected_year else detected_year,
            "signal_score": 0,
            "removed_metadata_bracket_suffix": False,
        }

    pieces: list[str] = []
    cursor = 0
    for start, end in sorted(remove_ranges):
        if start < cursor:
            continue
        pieces.append(value[cursor:start])
        pieces.append(" ")
        cursor = end
    pieces.append(value[cursor:])
    return collapse_spaces("".join(pieces)), {
        "warnings": _dedupe_strings(warnings),
        "edition_markers": _dedupe_strings(edition_markers),
        "parsed_year": None if parsed_year == detected_year else detected_year,
        "signal_score": signal_score,
        "removed_metadata_bracket_suffix": removed_metadata_bracket_suffix,
    }


def _looks_like_release_group_span_content(content: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", str(content or "").lower())
    if not compact:
        return False
    if compact in {"ytsmx", "ytstvmx", "yify", "rarbg"} or (compact.startswith("yts") and compact.endswith("mx")):
        return True
    known = {re.sub(r"[^a-z0-9]", "", value.lower()) for value in KNOWN_LOWERCASE_LEADING_RELEASE_GROUPS}
    return compact in known or _looks_like_bare_release_group_token(compact)


def _looks_like_metadata_bracket_span(content: str) -> bool:
    cleaned = collapse_spaces(content)
    if not cleaned:
        return False
    tokens = _classification_tokens(cleaned)
    profile = _segment_metadata_profile(tokens)
    if tokens and len(tokens) <= 4 and any(_token_is_strong_metadata(token) for token in tokens):
        return True
    if tokens and len(tokens) <= 10:
        if _contains_compound_localization_token(cleaned):
            return True
        if all(
            _token_is_localization_metadata(_canonical_metadata_token(token))
            or _token_is_subtitle_metadata(_canonical_metadata_token(token))
            for token in tokens
        ):
            return True
    if tokens and len(tokens) <= 3:
        numeric_parts = {
            _canonical_metadata_token(token)
            for token in tokens
            if _canonical_metadata_token(token)
        }
        if numeric_parts and numeric_parts.issubset({"0", "1", "2", "5", "6", "7", "8"}):
            return True
    if profile["strong_hits"] >= 1 and profile["suffix_hits"] + profile["numeric_channel_hits"] >= max(1, len(tokens) - 1):
        return True
    if profile["strong_hits"] >= 1 and _contains_compound_localization_token(cleaned):
        return True
    if _contains_metadata_id(cleaned):
        return True
    compact = re.sub(r"[^a-z0-9]", "", cleaned.lower())
    if any(token in compact for token in ("bdmux", "bdremux", "webrip", "webdl", "bluray", "brrip")) and any(
        _token_is_localization_metadata(token) for token in tokens
    ):
        return True
    if re.search(r"(?i)\b(?:gb|gib|mb|mib)\b", cleaned) and any(char.isdigit() for char in cleaned):
        return True
    return False


def _looks_like_edition_bracket_span(content: str, classification: dict[str, object]) -> bool:
    cleaned = collapse_spaces(content).strip(" -")
    if not cleaned:
        return False
    if classification.get("edition_markers"):
        remainder = cleaned
        for edition_key, pattern in EDITION_PATTERNS:
            if edition_key in classification["edition_markers"]:
                remainder = pattern.sub(" ", remainder)
        remainder = collapse_spaces(remainder).strip(" -")
        if not remainder:
            return True
        if re.fullmatch(r"(?i)(?:miramax|korean|festival|version|cut|edition|complete)", remainder):
            return True
    return re.fullmatch(
        r"(?i)(?:festival\s+cut|korean\s+edition|unrated\s+version|unrated\s+cut|resolve\s+color\s+grade)",
        cleaned,
    ) is not None


def _director_year_parenthetical(content: str) -> int | None:
    cleaned = collapse_spaces(content).strip(" -")
    if not cleaned or "," not in cleaned:
        return None
    match = re.fullmatch(
        r"(?P<name>[A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,2}),\s*(?P<year>19\d{2}|20\d{2})",
        cleaned,
    )
    if match is None:
        match = re.fullmatch(
            r"(?P<year>19\d{2}|20\d{2}),\s*(?P<name>[A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,2})",
            cleaned,
        )
    if match is None:
        return None
    name = collapse_spaces(match.group("name"))
    name_tokens = [_canonical_metadata_token(token) for token in name.split()]
    if not name_tokens or len(name_tokens) > 3:
        return None
    if any(token in SMART_CASE_STOPWORDS or _token_is_metadata(token) for token in name_tokens):
        return None
    return _coerce_year(match.group("year"))


def _extract_meaningful_title_number_hints(
    value: object,
    *,
    parsed_year: int | None,
) -> list[str]:
    prepared = _prepare_candidate_text(value)
    if not prepared:
        return []

    title_region, _cut_suffix, suffix_hints = _cut_non_title_suffix(prepared, parsed_year=parsed_year)
    effective_year = parsed_year or _coerce_year(suffix_hints["parsed_year"])
    title_region, stripped_year, _removed_year = _strip_trailing_year(
        title_region,
        parsed_year=effective_year,
    )
    effective_year = stripped_year or effective_year

    tokens = collapse_spaces(title_region).split()
    hints: list[str] = []
    for index, token in enumerate(tokens):
        canonical = _canonical_metadata_token(token)
        if not canonical or _token_is_metadata(canonical):
            continue
        if canonical == "part" and index + 1 < len(tokens):
            next_token = _canonical_metadata_token(tokens[index + 1])
            if re.fullmatch(r"\d+", next_token):
                try:
                    number_value = int(next_token)
                except (TypeError, ValueError):
                    number_value = None
                if number_value is not None and number_value != effective_year:
                    hint = f"part {number_value}"
                    if hint not in hints:
                        hints.append(hint)
                continue
        if ROMAN_NUMERAL_PATTERN.fullmatch(canonical) and canonical.lower() != "i":
            hint = canonical.upper()
            if hint not in hints:
                hints.append(hint)
            continue
        if re.fullmatch(r"\d+", canonical):
            try:
                number_value = int(canonical)
            except (TypeError, ValueError):
                continue
            if effective_year is not None and number_value == effective_year:
                continue
            if len(canonical) >= 2:
                hint = str(number_value)
                if hint not in hints:
                    hints.append(hint)
    return hints


def _filename_candidate_extends_trusted_title(
    *,
    trusted_title_candidate: dict[str, object],
    filename_candidate: dict[str, object],
) -> bool:
    trusted_title_tokens = _comparison_key(
        str(trusted_title_candidate["base_title"] or "").strip()
    ).split()
    filename_title_tokens = _comparison_key(
        str(filename_candidate["base_title"] or "").strip()
    ).split()
    if not trusted_title_tokens or len(filename_title_tokens) <= len(trusted_title_tokens):
        return False
    if filename_title_tokens[: len(trusted_title_tokens)] != trusted_title_tokens:
        return False

    parsed_year = _coerce_year(
        filename_candidate["parsed_year"] or trusted_title_candidate["parsed_year"]
    )
    extra_tokens = filename_title_tokens[len(trusted_title_tokens) :]
    return any(
        _is_meaningful_title_extension_token(token, parsed_year=parsed_year)
        for token in extra_tokens
    )


def _is_meaningful_title_extension_token(token: str, *, parsed_year: int | None) -> bool:
    normalized = _canonical_metadata_token(token)
    if not normalized:
        return False
    if normalized in {"ita", "eng", "jpn", "ger", "fra", "spa", "itaeng", "multi", "dub", "sub"}:
        return False
    if _token_is_metadata(normalized) or _token_is_strong_metadata(normalized):
        return False
    if _is_standalone_year(normalized):
        try:
            token_year = int(normalized)
        except (TypeError, ValueError):
            return False
        if parsed_year is not None and token_year == parsed_year:
            return False
        return True
    return True


def _split_dash_title_continuation(value: str) -> tuple[str, str, dict[str, object]] | None:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return None
    prefix, cut_suffix, suffix_hints = _cut_non_title_suffix(cleaned, parsed_year=None)
    if not cut_suffix:
        return None
    prefix = collapse_spaces(prefix).strip(" -")
    if not _looks_like_meaningful_dash_title_prefix(prefix):
        return None
    metadata_suffix = cleaned[len(prefix) :].strip(" -")
    return prefix, metadata_suffix, suffix_hints


def _dash_genre_descriptor_suffix_hints(value: str) -> dict[str, object] | None:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return None
    prefix, cut_suffix, suffix_hints = _cut_non_title_suffix(cleaned, parsed_year=None)
    if not cut_suffix:
        return None
    if not _looks_like_genre_descriptor(prefix):
        return None
    return suffix_hints


def _looks_like_genre_descriptor(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -").lower()
    if not cleaned:
        return False
    tokens = re.findall(r"[a-z0-9]+", cleaned)
    if not tokens:
        return False
    if tokens == ["sci", "fi"]:
        return True
    if tokens == ["science", "fiction"]:
        return True
    return all(token in GENRE_DESCRIPTOR_TOKENS for token in tokens)


def _looks_like_meaningful_dash_title_prefix(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return False
    tokens = [_canonical_metadata_token(token) for token in cleaned.split()]
    non_metadata_tokens = [
        token
        for token in tokens
        if token and not _token_is_suffix_metadata(token) and not _is_standalone_year(token)
    ]
    if not non_metadata_tokens:
        return False
    if _looks_like_genre_descriptor(cleaned):
        return False
    first = non_metadata_tokens[0]
    meaningful_starters = {
        "a",
        "an",
        "beginning",
        "chapter",
        "episode",
        "family",
        "last",
        "movie",
        "part",
        "rage",
        "saga",
        "the",
        "vol",
        "volume",
        "way",
    }
    if first in meaningful_starters:
        return True
    if len(non_metadata_tokens) >= 2:
        return True
    return any(_looks_like_episode_identity_token(token) for token in cleaned.split())


def _is_trusted_title_input(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    if "/" in raw or "\\" in raw:
        return False
    if re.search(r"\.[a-z0-9]{2,5}$", raw, re.IGNORECASE):
        return False
    prepared = _prepare_candidate_text(raw)
    if not prepared:
        return False
    if EMPTY_BRACKET_PATTERN.search(prepared):
        return False
    if BRACKET_GROUP_PATTERN.search(prepared):
        for match in BRACKET_GROUP_PATTERN.finditer(prepared):
            classification = _classify_segment(collapse_spaces(match.group(2)))
            if classification["kind"] in {"metadata", "id", "year"}:
                return False
    if " - " in prepared:
        _kept, cut_suffix, _suffix_hints = _cut_non_title_suffix(prepared, parsed_year=None)
        if cut_suffix:
            return False
    if _looks_like_metadata_contaminated_title(prepared):
        return False
    tokens = _classification_tokens(prepared)
    if not tokens:
        return False
    metadata_hits = sum(1 for token in tokens if _token_is_metadata(token))
    strong_hits = sum(1 for token in tokens if _token_is_strong_metadata(token))
    if strong_hits:
        return False
    if metadata_hits >= 2:
        return False
    return True


def _classify_segment(value: str) -> dict[str, object]:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return {"kind": "empty", "edition_markers": [], "parsed_year": None}
    if _contains_metadata_id(cleaned):
        return {"kind": "id", "edition_markers": [], "parsed_year": None}
    if _is_standalone_year(cleaned):
        return {"kind": "year", "edition_markers": [], "parsed_year": _coerce_year(cleaned)}
    if _looks_like_bare_release_group_token(cleaned):
        return {"kind": "metadata", "edition_markers": [], "parsed_year": None}

    edition_markers = _segment_edition_markers(cleaned)
    cleaned_without_editions = cleaned
    for edition_key, pattern in EDITION_PATTERNS:
        if edition_key in edition_markers:
            cleaned_without_editions = pattern.sub(" ", cleaned_without_editions)
    if "director's cut" in edition_markers:
        cleaned_without_editions = re.sub(r"(?i)\bdc\b", " ", cleaned_without_editions)
    cleaned_without_editions = collapse_spaces(cleaned_without_editions).strip(" -")
    if edition_markers and not cleaned_without_editions:
        return {"kind": "edition", "edition_markers": edition_markers, "parsed_year": None}

    tokens = _classification_tokens(cleaned_without_editions or cleaned)
    if not tokens:
        return {
            "kind": "edition" if edition_markers else "empty",
            "edition_markers": edition_markers,
            "parsed_year": None,
        }

    profile = _segment_metadata_profile(tokens)
    metadata_hits = profile["metadata_hits"]
    strong_hits = profile["strong_hits"]
    suffix_hits = profile["suffix_hits"]
    numeric_channel_hits = profile["numeric_channel_hits"]
    year_hits = profile["year_hits"]
    parsed_year = profile["parsed_year"]
    non_year_tokens = max(0, len(tokens) - year_hits)

    if metadata_hits and strong_hits and metadata_hits >= max(1, len(tokens) - 1):
        return {"kind": "metadata", "edition_markers": edition_markers, "parsed_year": parsed_year}
    if metadata_hits >= 2 and strong_hits >= 1 and metadata_hits >= max(2, len(tokens) // 2):
        return {"kind": "metadata", "edition_markers": edition_markers, "parsed_year": parsed_year}
    if metadata_hits == len(tokens) and len(tokens) <= 3:
        return {"kind": "metadata", "edition_markers": edition_markers, "parsed_year": parsed_year}
    if strong_hits >= 1 and metadata_hits >= 1 and metadata_hits + numeric_channel_hits == len(tokens):
        return {"kind": "metadata", "edition_markers": edition_markers, "parsed_year": parsed_year}
    if year_hits >= 1 and suffix_hits >= 1 and suffix_hits + year_hits + numeric_channel_hits >= len(tokens):
        return {"kind": "metadata", "edition_markers": edition_markers, "parsed_year": parsed_year}
    if suffix_hits >= 2 and suffix_hits + numeric_channel_hits >= max(2, non_year_tokens):
        return {"kind": "metadata", "edition_markers": edition_markers, "parsed_year": parsed_year}
    if suffix_hits >= 1 and strong_hits >= 1 and suffix_hits + numeric_channel_hits >= max(2, non_year_tokens - 1):
        return {"kind": "metadata", "edition_markers": edition_markers, "parsed_year": parsed_year}

    return {"kind": "title", "edition_markers": edition_markers, "parsed_year": None}


def _cut_non_title_suffix(value: str, *, parsed_year: int | None = None) -> tuple[str, bool, dict[str, object]]:
    working = collapse_spaces(value).strip(" -")
    if not working:
        return working, False, {"edition_markers": [], "parsed_year": None, "rule_markers": []}

    removed_suffix_fragments: list[str] = []
    rule_markers: list[str] = []
    cut_any = False

    release_year_cut = _release_year_metadata_suffix_cut(working)
    if release_year_cut is not None:
        title_prefix, suffix = release_year_cut
        removed_suffix_fragments.append(suffix)
        rule_markers.append("standalone_release_year_cut")
        rule_markers.append("technical_suffix_density_cut")
        working = title_prefix
        cut_any = True

    if " - " in working:
        left, right = working.split(" - ", 1)
        right_classification = _classify_segment(right)
        if right_classification["kind"] in {"metadata", "id", "edition", "year"}:
            removed_suffix_fragments.append(right)
            cut_any = True
            if right_classification["kind"] == "year":
                rule_markers.append("standalone_release_year_cut")
            elif right_classification["kind"] == "edition":
                rule_markers.append("edition_segment_extracted")
            else:
                rule_markers.append("technical_suffix_density_cut")
            working = left.strip(" -")
        elif _looks_like_dash_suffix_junk_segment(right):
            removed_suffix_fragments.append(right)
            rule_markers.append("dash_release_group_suffix_removed")
            cut_any = True
            working = left.strip(" -")

    while True:
        tokens = working.split()
        boundary_info = _backward_metadata_suffix_boundary(tokens, parsed_year=parsed_year) or _metadata_suffix_boundary(
            tokens,
            parsed_year=parsed_year,
        )
        if boundary_info is None:
            break
        boundary, boundary_markers = boundary_info
        if boundary <= 0:
            break
        removed_suffix_fragments.append(" ".join(tokens[boundary:]))
        rule_markers.extend(boundary_markers)
        working = " ".join(tokens[:boundary]).strip(" -")
        cut_any = True

    if not cut_any:
        return working, False, {"edition_markers": [], "parsed_year": None, "rule_markers": []}

    suffix = " ".join(fragment for fragment in removed_suffix_fragments if fragment)
    return working, True, _suffix_parse_hints(suffix, *rule_markers)


def _release_year_metadata_suffix_cut(value: str) -> tuple[str, str] | None:
    working = collapse_spaces(value).strip(" -")
    if not working:
        return None
    for match in YEAR_PATTERN.finditer(working):
        year_text = match.group(1)
        separator_release_year = _looks_like_hyphen_separated_release_year_metadata(working, match)
        if _year_match_is_collection_or_date_range(working, match) and not separator_release_year:
            continue
        prefix = working[: match.start()].strip(" -")
        suffix = working[match.end() :].strip(" -")
        if not prefix or not suffix:
            continue
        if _suffix_contains_later_release_year_metadata(suffix):
            continue
        prefix_tokens = [
            token
            for token in _classification_tokens(prefix)
            if token and not _token_is_suffix_metadata(token) and not _is_standalone_year(token)
        ]
        if not prefix_tokens:
            continue
        if _suffix_after_release_year_is_metadata(suffix):
            prefix = prefix.rstrip("!.,_:([")
            return prefix, f"{year_text} {suffix}".strip()
    return None


def _looks_like_hyphen_separated_release_year_metadata(value: str, match: re.Match[str]) -> bool:
    if match.start() <= 0 or value[match.start() - 1] not in "-–":
        return False
    if re.match(r"\s*[-–]\s*(?:19\d{2}|20\d{2})", value[match.end() :]):
        return False
    prefix = value[: match.start()].strip(" -")
    suffix = value[match.end() :].strip(" -")
    if not prefix or not suffix:
        return False
    if re.search(r"(?:19\d{2}|20\d{2})\s*$", prefix):
        return False
    if re.search(
        r"\b(?:collection|saga|trilogy|quadrilogy|duology|anthology|franchise|movies|films|movie\s+pack|film\s+pack|pack)\b",
        prefix,
        re.IGNORECASE,
    ):
        return False
    return _suffix_after_release_year_is_metadata(suffix)


def _release_year_cut_starts_with_descriptor_span(suffix: str) -> bool:
    cleaned = collapse_spaces(suffix).strip(" -._")
    year_match = YEAR_PATTERN.match(cleaned)
    if year_match is not None:
        cleaned = collapse_spaces(cleaned[year_match.end() :]).strip(" -._")
    spans = _find_bracket_spans(cleaned)
    if not spans:
        return False
    first = spans[0]
    if int(first["start"]) != 0:
        return False
    return _looks_like_post_year_descriptor_span(str(first["content"]))


def _suffix_contains_later_release_year_metadata(value: str) -> bool:
    suffix = collapse_spaces(value).strip(" -")
    if not suffix:
        return False
    for later_match in YEAR_PATTERN.finditer(suffix):
        if _year_match_is_collection_or_date_range(suffix, later_match):
            continue
        later_suffix = suffix[later_match.end() :].strip(" -")
        if later_suffix and _suffix_after_release_year_is_metadata(later_suffix):
            return True
    return False


def _year_match_is_collection_or_date_range(value: str, match: re.Match[str]) -> bool:
    start = match.start()
    end = match.end()
    before = value[max(0, start - 2) : start]
    after = value[end : end + 8]
    if "-" in before or re.match(r"\s*[-–]\s*(?:19\d{2}|20\d{2})", after):
        return True
    lowered = value.lower()
    if re.search(r"\b(?:collection|saga|trilogy|quadrilogy|duology|anthology|franchise|movies|films|movie\s+pack|film\s+pack|pack)\b", lowered):
        suffix = value[end:].lower()
        if re.match(r"\s*\(?\s*1001[ ._-]+movies?", suffix):
            return False
        if re.match(r"\s*(?:the\s+)?criterion\s+collection\b", suffix):
            return False
        return True
    return re.search(r"\b\d{1,2}[._ -]\d{1,2}[._ -](?:19\d{2}|20\d{2})\b", value) is not None


def _suffix_after_release_year_is_metadata(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return False
    if _label_suffix_is_metadata(cleaned):
        return True
    ordered_tokens = _ordered_context_tokens(cleaned)
    if ordered_tokens and _looks_like_episode_identity_token(ordered_tokens[0]):
        return False
    if len(ordered_tokens) >= 1 and all(
        _token_is_localization_metadata(token) or _token_is_subtitle_metadata(token)
        for token in ordered_tokens
    ):
        return True
    without_plain_descriptors = _post_year_plain_descriptor_prefix(cleaned)
    if without_plain_descriptors != cleaned and _metadata_suffix_core_is_strong(without_plain_descriptors):
        return True
    without_descriptor_spans = _strip_leading_post_year_descriptor_spans(cleaned)
    if without_descriptor_spans != cleaned and _metadata_suffix_core_is_strong(without_descriptor_spans):
        return True
    without_spans, hints = _remove_metadata_bracket_spans(cleaned, parsed_year=None)
    if hints["signal_score"] > 0:
        cleaned = without_spans
        if not cleaned:
            return True
    if _metadata_suffix_core_is_strong(cleaned):
        return True
    return False


def _metadata_suffix_core_is_strong(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return False
    token_sets = [cleaned.split()]
    classified_tokens = _classification_tokens(cleaned)
    if classified_tokens != token_sets[0]:
        token_sets.append(classified_tokens)
    ordered_tokens = _ordered_context_tokens(cleaned)
    if ordered_tokens and ordered_tokens not in token_sets:
        token_sets.append(ordered_tokens)
    if not any(token_sets):
        return False
    for tokens in token_sets:
        if not tokens:
            continue
        metrics = _suffix_metadata_metrics(tokens)
        if metrics["strong_hits"] >= 1 and metrics["metadata_hits"] >= 1:
            return True
        if any(_contains_compound_metadata_token(token) for token in tokens):
            return True
        if any(_contains_compound_localization_token(token) for token in tokens) and metrics["metadata_hits"] >= 1:
            return True
        if any(_token_is_subtitle_metadata(_canonical_metadata_token(token)) for token in tokens) and any(
            _token_is_localization_metadata(_canonical_metadata_token(token)) for token in tokens
        ):
            return True
    lowered = cleaned.lower()
    if re.search(r"\b(?:no\s+language|no\s+sub(?:s|titles)?|open\s+matte|criterion\s+collection|original\s+audio|org\s+auds)\b", lowered):
        return True
    if re.search(r"\b(?:restored|remastered|uncut|explicit|alternate\s+version|open[- ]matte|upscaled|ai[- ]?enhanced)\b", lowered):
        return any(
            _suffix_metadata_metrics(tokens)["metadata_hits"] >= 1
            or any(_token_is_strong_metadata(_canonical_metadata_token(token)) for token in tokens)
            for tokens in token_sets
        )
    return False


def _strip_leading_post_year_descriptor_spans(value: str) -> str:
    working = collapse_spaces(value).strip(" -._")
    changed = True
    while changed and working:
        changed = False
        spans = _find_bracket_spans(working)
        if not spans:
            continue
        first = spans[0]
        if int(first["start"]) != 0:
            continue
        content = collapse_spaces(str(first["content"]))
        if not _looks_like_post_year_descriptor_span(content):
            continue
        working = collapse_spaces(working[int(first["end"]) :]).strip(" -._")
        changed = True
    return working


def _looks_like_post_year_descriptor_span(content: str) -> bool:
    cleaned = collapse_spaces(content).strip(" -")
    if not cleaned:
        return False
    if re.search(r"\b(?:19\d{2}|20\d{2})\s*[-–/]\s*(?:19\d{2}|20\d{2})\b", cleaned):
        return False
    if _looks_like_metadata_bracket_span(cleaned):
        return True
    if re.search(r"(?i)\b1001\s+movies?(?:\s+you\s+must\s+see(?:\s+before\s+you\s+die)?)?\b", cleaned):
        return True
    if _looks_like_genre_descriptor(cleaned):
        return True
    lowered = cleaned.lower()
    if re.search(r"\b(?:film\s+noir|spy\s+film|softcore|western|mystery|history|war|crime|thriller|horror|drama|comedy|action|biography|adventure)\b", lowered):
        return True
    if " - " in cleaned and len(_classification_tokens(cleaned)) <= 8:
        return True
    return False


def _metadata_suffix_boundary(tokens: list[str], *, parsed_year: int | None = None) -> tuple[int, list[str]] | None:
    for index in range(len(tokens)):
        suffix_tokens = tokens[index:]
        current = _canonical_metadata_token(tokens[index])
        if not current:
            continue
        suffix_metrics = _suffix_metadata_metrics(suffix_tokens)
        if _starts_edition_suffix(tokens, index):
            return index, ["edition_segment_extracted"]
        if (
            _is_standalone_year(current)
            and (parsed_year is None or _coerce_year(current) == parsed_year)
            and suffix_metrics["metadata_hits"] >= 1
            and (
                suffix_metrics["strong_hits"] >= 1
                or suffix_metrics["release_group_hits"] >= 1
                or any(_contains_compound_metadata_token(token) for token in suffix_tokens)
            )
            and _looks_like_release_year_boundary(tokens, index)
        ):
            return index, ["standalone_release_year_cut", "technical_suffix_density_cut"]
        if _is_metadata_boundary_token(tokens[index], current):
            if index > 0 and suffix_metrics["strong_hits"] >= 1 and suffix_metrics["metadata_hits"] >= 1:
                markers = ["technical_suffix_density_cut"]
                if _contains_compound_localization_token(tokens[index]):
                    markers.append("compound_language_suffix_removed")
                return index, markers
            if index > 0 and suffix_metrics["strong_hits"] >= 1 and suffix_metrics["metadata_hits"] >= 2:
                markers = ["technical_suffix_density_cut"]
                if _contains_compound_localization_token(tokens[index]):
                    markers.append("compound_language_suffix_removed")
                return index, markers
        if _token_is_localization_metadata(current) or _contains_compound_localization_token(tokens[index]):
            if suffix_metrics["strong_hits"] >= 1:
                markers = ["technical_suffix_density_cut"]
                if _contains_compound_localization_token(tokens[index]):
                    markers.append("compound_language_suffix_removed")
                return index, markers
        if current in {"proper", "repack", "internal", "limited"}:
            return index, ["technical_suffix_density_cut"]
    return None


def _backward_metadata_suffix_boundary(tokens: list[str], *, parsed_year: int | None = None) -> tuple[int, list[str]] | None:
    if not tokens:
        return None
    suffix_start: int | None = None
    markers: list[str] = []
    metadata_seen = False
    for index in range(len(tokens) - 1, -1, -1):
        raw_token = tokens[index]
        canonical = _canonical_metadata_token(raw_token)
        if not canonical:
            continue
        if (
            metadata_seen
            and index + 1 < len(tokens)
            and _is_standalone_year(_canonical_metadata_token(tokens[index + 1]))
            and _looks_like_release_year_boundary(tokens, index + 1)
            and not _token_is_suffix_metadata(raw_token)
            and not _looks_like_release_group_token(raw_token)
        ):
            break
        previous = _canonical_metadata_token(tokens[index - 1]) if index > 0 else ""
        next_token = _canonical_metadata_token(tokens[index + 1]) if index + 1 < len(tokens) else ""
        if (
            metadata_seen
            and next_token
            and _is_standalone_year(next_token)
            and _token_is_localization_metadata(canonical)
        ):
            break
        if ROMAN_NUMERAL_PATTERN.fullmatch(canonical):
            break
        if canonical in {"0", "1", "2", "5", "6", "7", "8"} and previous == "part":
            break
        if _starts_edition_suffix(tokens, index):
            suffix_start = index
            markers.append("edition_segment_extracted")
            metadata_seen = True
            continue
        token_kind = _suffix_token_kind(raw_token, metadata_seen=metadata_seen)
        if token_kind is None:
            if metadata_seen and canonical in {"264", "265"} and previous in {"h", "x"}:
                suffix_start = index
                markers.append("technical_suffix_density_cut")
                continue
            if metadata_seen and canonical in {"h", "x"} and index + 1 < len(tokens):
                next_token = _canonical_metadata_token(tokens[index + 1])
                if next_token in {"264", "265"}:
                    suffix_start = index
                    markers.append("technical_suffix_density_cut")
                    continue
            if (
                _is_standalone_year(canonical)
                and metadata_seen
                and (parsed_year is None or _coerce_year(canonical) == parsed_year)
                and _looks_like_release_year_boundary(tokens, index)
            ):
                suffix_start = index
                markers.append("standalone_release_year_cut")
                markers.append("technical_suffix_density_cut")
                continue
            break
        suffix_start = index
        metadata_seen = True
        if token_kind == "compound_language":
            markers.append("compound_language_suffix_removed")
        elif token_kind == "subtitle":
            markers.append("subtitle_suffix_removed")
        elif token_kind == "release_group":
            markers.append("dash_release_group_suffix_removed")
        else:
            markers.append("technical_suffix_density_cut")

    if suffix_start is None or suffix_start <= 0:
        return None
    suffix_metrics = _suffix_metadata_metrics(tokens[suffix_start:])
    if suffix_metrics["metadata_hits"] < 1:
        return None
    if suffix_metrics["strong_hits"] < 1 and suffix_metrics["release_group_hits"] < 1:
        return None
    return suffix_start, _dedupe_strings(markers or ["technical_suffix_density_cut"])


def _looks_like_release_year_boundary(tokens: list[str], index: int) -> bool:
    if index <= 0:
        return False
    current = _canonical_metadata_token(tokens[index])
    if not _is_standalone_year(current):
        return False
    try:
        year_value = int(current)
    except (TypeError, ValueError):
        return False
    if year_value > datetime.now().year + 1:
        return False
    preceding_title_tokens = [
        token
        for token in tokens[:index]
        if not _token_is_metadata(_canonical_metadata_token(token))
    ]
    return bool(preceding_title_tokens)


def _suffix_metadata_metrics(tokens: list[str]) -> dict[str, int]:
    metadata_hits = 0
    strong_hits = 0
    release_group_hits = 0
    for token in tokens:
        if _looks_like_episode_identity_token(token):
            continue
        canonical = _canonical_metadata_token(token)
        token_is_suffix_metadata = _token_is_suffix_metadata(token)
        if token_is_suffix_metadata:
            metadata_hits += 1
        if _token_is_strong_metadata(canonical):
            strong_hits += 1
        if not token_is_suffix_metadata and (
            _looks_like_release_group_token(token) or _looks_like_bare_release_group_token(token)
        ):
            metadata_hits += 1
            release_group_hits += 1
    return {
        "metadata_hits": metadata_hits,
        "strong_hits": strong_hits,
        "release_group_hits": release_group_hits,
    }


def _segment_metadata_profile(tokens: list[str]) -> dict[str, int | None]:
    metadata_hits = 0
    strong_hits = 0
    suffix_hits = 0
    numeric_channel_hits = 0
    year_hits = 0
    parsed_year: int | None = None
    for token in tokens:
        if _looks_like_episode_identity_token(token):
            continue
        canonical = _canonical_metadata_token(token)
        if _is_standalone_year(canonical):
            year_hits += 1
            if parsed_year is None:
                parsed_year = _coerce_year(canonical)
            continue
        if _token_is_metadata(canonical) or _contains_compound_metadata_token(token):
            metadata_hits += 1
            suffix_hits += 1
        elif _token_is_localization_metadata(canonical) or _token_is_subtitle_metadata(canonical):
            suffix_hits += 1
        elif _contains_compound_localization_token(token):
            suffix_hits += 1
        if _token_is_strong_metadata(canonical):
            strong_hits += 1
        if _looks_like_audio_channel_token(canonical) or canonical in {"0", "1", "2", "5", "6", "7", "8"}:
            numeric_channel_hits += 1
    return {
        "metadata_hits": metadata_hits,
        "strong_hits": strong_hits,
        "suffix_hits": suffix_hits,
        "numeric_channel_hits": numeric_channel_hits,
        "year_hits": year_hits,
        "parsed_year": parsed_year,
    }


def _suffix_token_kind(raw_token: str, *, metadata_seen: bool) -> str | None:
    canonical = _canonical_metadata_token(raw_token)
    if not canonical:
        return None
    if _looks_like_episode_identity_token(raw_token):
        return None
    if _looks_like_release_group_token(raw_token):
        return "release_group"
    if _contains_compound_metadata_token(raw_token):
        return "metadata"
    if _contains_compound_localization_token(raw_token):
        return "compound_language"
    if _token_is_subtitle_metadata(canonical):
        return "subtitle"
    if _token_is_metadata(canonical):
        return "metadata"
    if _token_is_localization_metadata(canonical):
        return "language"
    if metadata_seen and canonical == "3d":
        return "metadata"
    if metadata_seen and (_looks_like_audio_channel_token(canonical) or canonical in {"0", "1", "2", "5", "6", "7", "8"}):
        return "metadata"
    if metadata_seen and _looks_like_bare_release_group_token(raw_token):
        return "release_group"
    return None


def _token_is_suffix_metadata(raw_token: str) -> bool:
    if _looks_like_episode_identity_token(raw_token):
        return False
    canonical = _canonical_metadata_token(raw_token)
    return (
        _token_is_metadata(canonical)
        or _contains_compound_metadata_token(raw_token)
        or _token_is_localization_metadata(canonical)
        or _token_is_subtitle_metadata(canonical)
        or _contains_compound_localization_token(raw_token)
        or _looks_like_audio_channel_token(canonical)
    )


def _token_is_localization_metadata(token: str) -> bool:
    return token in LOCALIZATION_METADATA_TOKENS


def _token_is_subtitle_metadata(token: str) -> bool:
    return token in SUBTITLE_METADATA_TOKENS


def _looks_like_audio_channel_token(token: str) -> bool:
    return AUDIO_CHANNEL_TOKEN_PATTERN.fullmatch(token) is not None


def _contains_compound_localization_token(value: str) -> bool:
    raw_tokens = re.findall(r"[A-Za-z0-9]+(?:[-+/][A-Za-z0-9]+)+", str(value or "").lower())
    for raw_token in raw_tokens:
        parts = [_canonical_metadata_token(part) for part in re.split(r"[-+/]+", raw_token) if part]
        if len(parts) >= 2 and sum(1 for part in parts if _token_is_localization_metadata(part)) >= 2:
            return True
        if len(parts) >= 2 and any(_token_is_localization_metadata(part) for part in parts) and any(
            _token_is_metadata(part) or _token_is_subtitle_metadata(part)
            for part in parts
        ):
            return True
    compact = _canonical_metadata_token(value)
    return compact in {"itaeng", "engita", "dualaudio", "truefrench", "vostfr", "ptbr"}


def _contains_compound_metadata_token(value: str) -> bool:
    raw_tokens = re.findall(r"[A-Za-z0-9.]+(?:[-+/][A-Za-z0-9.]+)+", str(value or "").lower())
    for raw_token in raw_tokens:
        parts = [_canonical_metadata_token(part) for part in re.split(r"[-+/]+", raw_token) if part]
        if len(parts) < 2:
            continue
        metadata_like = [
            part
            for part in parts
            if (
                _token_is_metadata(part)
                or _token_is_localization_metadata(part)
                or _token_is_subtitle_metadata(part)
                or _looks_like_audio_channel_token(part)
            )
        ]
        if len(metadata_like) >= 2 and any(_token_is_metadata(part) for part in metadata_like):
            return True
    return False


def _looks_like_episode_identity_token(value: object) -> bool:
    canonical = _canonical_metadata_token(str(value or ""))
    if not canonical:
        return False
    return (
        re.fullmatch(r"s\d{1,2}e\d{1,3}", canonical) is not None
        or re.fullmatch(r"\d{1,2}x\d{1,3}", canonical) is not None
        or re.fullmatch(r"ep\d{1,3}", canonical) is not None
        or re.fullmatch(r"e\d{1,3}", canonical) is not None
    )


def _looks_like_metadata_contaminated_title(value: str) -> bool:
    cleaned = collapse_spaces(value)
    if not cleaned:
        return False
    if _contains_compound_localization_token(cleaned):
        return True
    tokens = _classification_tokens(cleaned)
    if any(_token_is_strong_metadata(token) for token in tokens):
        return True
    if any(_looks_like_release_group_token(token) for token in cleaned.split()):
        return True
    if any(_token_is_subtitle_metadata(token) for token in tokens) and any(
        _token_is_localization_metadata(token) for token in tokens
    ):
        return True
    return False


def _suffix_parse_hints(value: str, *rule_markers: str) -> dict[str, object]:
    working = collapse_spaces(value)
    edition_markers = _segment_edition_markers(working)
    year_matches = list(YEAR_PATTERN.finditer(working))
    parsed_year = int(year_matches[0].group(1)) if year_matches else None
    for marker in _dc_abbreviation_edition_markers(
        working,
        parsed_year=parsed_year,
        metadata_seen=True,
        suffix_context=True,
    ):
        if marker not in edition_markers:
            edition_markers.append(marker)
    marker_priority = {
        "standalone_release_year_cut": 0,
        "technical_suffix_density_cut": 1,
        "compound_language_suffix_removed": 2,
        "subtitle_suffix_removed": 3,
        "dash_release_group_suffix_removed": 4,
        "edition_segment_extracted": 5,
    }
    ordered_rule_markers = sorted(
        _dedupe_strings([str(marker) for marker in rule_markers if marker]),
        key=lambda marker: marker_priority.get(marker, 99),
    )
    return {
        "edition_markers": edition_markers,
        "parsed_year": parsed_year,
        "rule_markers": ordered_rule_markers,
    }


def _is_metadata_boundary_token(raw_token: str, canonical: str) -> bool:
    if _token_is_suffix_metadata(raw_token):
        return True
    if _looks_like_release_group_token(raw_token):
        return True
    return _token_is_localization_metadata(canonical) or _token_is_subtitle_metadata(canonical)


def _starts_edition_suffix(tokens: list[str], index: int) -> bool:
    suffix = collapse_spaces(" ".join(tokens[index:]))
    if not suffix:
        return False
    prefix = collapse_spaces(" ".join(tokens[:index])).lower()
    if not prefix or prefix in {"the", "a", "an"}:
        return False
    for _edition_key, pattern in EDITION_PATTERNS:
        match = pattern.match(suffix)
        if match:
            return True
    return False


def _looks_like_release_group_token(token: str) -> bool:
    raw = str(token or "").strip()
    if not raw:
        return False
    if "-" in raw:
        prefix, group = raw.rsplit("-", 1)
        prefix_canonical = _canonical_metadata_token(prefix)
        if (
            len(group) >= 2
            and re.fullmatch(r"[A-Za-z0-9]+", group)
            and (
                _token_is_metadata(prefix_canonical)
                or re.fullmatch(r"\d{2,4}", prefix_canonical) is not None
                or prefix_canonical in {"x264", "x265", "h264", "h265", "hevc", "av1", "ddp", "dts"}
            )
        ):
            return True
    return False


def _looks_like_bare_release_group_token(token: str) -> bool:
    raw = str(token or "").strip(" -")
    if not raw or not re.fullmatch(r"[A-Za-z0-9]+", raw):
        return False
    if raw.isdigit() or not (2 <= len(raw) <= 16):
        return False
    upper_count = sum(1 for char in raw if char.isupper())
    lower_count = sum(1 for char in raw if char.islower())
    digit_count = sum(1 for char in raw if char.isdigit())
    if upper_count >= 2:
        return True
    if digit_count >= 1 and upper_count >= 1 and lower_count >= 1:
        return True
    if upper_count >= 1 and lower_count >= 1 and not (raw[0].isupper() and raw[1:].islower()):
        return True
    return False


def _looks_like_dash_suffix_junk_segment(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return False
    words = cleaned.split()
    if len(words) >= 3:
        return False
    if len(words) == 1:
        word = words[0]
        if _looks_like_bare_release_group_token(word):
            return True
        if word.isalpha() and word.islower():
            return True
    return all(word.isalpha() and word.islower() for word in words)


def _looks_like_leading_release_group_prefix(content: str, remainder: str) -> bool:
    cleaned = collapse_spaces(content).strip(" -")
    if not cleaned:
        return False
    release_group_like = _looks_like_bare_release_group_token(cleaned) or cleaned.lower() in KNOWN_LOWERCASE_LEADING_RELEASE_GROUPS
    if not release_group_like:
        if cleaned.islower() and re.fullmatch(r"[a-z0-9]{3,16}", cleaned):
            release_group_like = True
    if not release_group_like:
        return False
    if cleaned.isupper() and len(cleaned) <= 4:
        return False
    remaining = collapse_spaces(remainder).strip(" -")
    if not remaining:
        return False
    tokens = remaining.split()
    if len(tokens) < 2:
        return False
    if any(_is_standalone_year(_canonical_metadata_token(token)) for token in tokens):
        return True
    suffix_metrics = _suffix_metadata_metrics(tokens)
    return suffix_metrics["strong_hits"] >= 1 and suffix_metrics["metadata_hits"] >= 1


def _looks_like_leading_bracket_title_acronym(content: str, remainder: str) -> bool:
    cleaned = collapse_spaces(content).strip(" -")
    if not cleaned or not cleaned.isupper() or not re.fullmatch(r"[A-Z0-9]{2,4}", cleaned):
        return False
    remaining_tokens = collapse_spaces(remainder).strip(" -").split()
    if not remaining_tokens:
        return False
    return _is_standalone_year(_canonical_metadata_token(remaining_tokens[0]))


def _looks_like_trailing_release_group_after_metadata(content: str, prefix: str) -> bool:
    raw = str(content or "").strip(" -")
    if not raw or not re.fullmatch(r"[A-Za-z0-9]+", raw):
        return False
    if raw.isdigit() or not (4 <= len(raw) <= 16):
        return False
    if raw.isupper() and len(raw) <= 4:
        return False
    prefix_metrics = _suffix_metadata_metrics(_classification_tokens(prefix))
    return prefix_metrics["strong_hits"] >= 2 and prefix_metrics["metadata_hits"] >= 3


def _strip_trailing_bare_release_group_after_metadata(value: str) -> tuple[str, bool]:
    tokens = collapse_spaces(value).split()
    if len(tokens) < 2:
        return value, False
    trailing = _canonical_metadata_token(tokens[-1])
    trailing_known_group = trailing in {re.sub(r"[^a-z0-9]", "", value) for value in KNOWN_LOWERCASE_LEADING_RELEASE_GROUPS}
    if not (_looks_like_bare_release_group_token(tokens[-1]) or trailing_known_group):
        return value, False
    if ROMAN_NUMERAL_PATTERN.fullmatch(_canonical_metadata_token(tokens[-1])):
        return value, False
    prefix = " ".join(tokens[:-1]).strip(" -")
    if not prefix or prefix.lower() in {"the", "a", "an"}:
        return value, False
    return prefix, True


def _looks_like_bracket_plus_release_group(value: str) -> bool:
    return re.search(r"\]\s*-[A-Za-z0-9]{2,}\s*$", str(value or "")) is not None


def _classification_tokens(value: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9]+(?:[.'’+\-/][A-Za-z0-9]+)*", value.lower())
    tokens: list[str] = []
    for token in raw_tokens:
        for part in [token, *re.split(r"[-+/]", token)]:
            canonical = part.strip(" -").replace("’", "'")
            canonical = canonical.replace(".", "")
            canonical = canonical.replace("'", "")
            if canonical and canonical not in tokens:
                tokens.append(canonical)
    return tokens


def _ordered_context_tokens(value: str) -> list[str]:
    return [
        _canonical_metadata_token(token)
        for token in re.findall(r"[A-Za-z0-9]+", str(value or ""))
        if _canonical_metadata_token(token)
    ]


def _has_meaningful_title_prefix(tokens: list[str], index: int) -> bool:
    for token in tokens[:index]:
        if token in {"a", "an", "the"}:
            continue
        if _is_standalone_year(token) or _token_is_suffix_metadata(token):
            continue
        return True
    return False


def _is_dc_title_context(tokens: list[str], index: int) -> bool:
    if index < 0 or index >= len(tokens) or tokens[index] != "dc":
        return False
    previous_token = tokens[index - 1] if index > 0 else ""
    next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
    following_token = tokens[index + 2] if index + 2 < len(tokens) else ""
    if previous_token in DC_TITLE_CONTEXT_PRECEDERS:
        return True
    if next_token in DC_TITLE_CONTEXT_FOLLOWERS:
        return True
    if next_token == "super" and following_token in {"girl", "girls", "hero", "heroes"}:
        return True
    return False


def _is_directors_cut_abbreviation_context(
    tokens: list[str],
    index: int,
    *,
    parsed_year: int | None,
    metadata_seen: bool,
    suffix_context: bool,
) -> bool:
    if index < 0 or index >= len(tokens) or tokens[index] != "dc":
        return False
    if _is_dc_title_context(tokens, index):
        return False

    previous_token = tokens[index - 1] if index > 0 else ""
    next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
    if _is_standalone_year(previous_token):
        return True
    if previous_token in DC_EDITION_CONTEXT_NEIGHBORS:
        return True
    if next_token in DC_EDITION_CONTEXT_NEIGHBORS:
        return True
    if next_token and _token_is_strong_metadata(next_token):
        return True
    if index == len(tokens) - 1 and _has_meaningful_title_prefix(tokens, index):
        return bool(parsed_year is not None or metadata_seen or suffix_context)
    return False


def _dc_abbreviation_edition_markers(
    value: str,
    *,
    parsed_year: int | None,
    metadata_seen: bool = False,
    suffix_context: bool = False,
) -> list[str]:
    tokens = _ordered_context_tokens(value)
    for index, token in enumerate(tokens):
        if token == "dc" and _is_directors_cut_abbreviation_context(
            tokens,
            index,
            parsed_year=parsed_year,
            metadata_seen=metadata_seen,
            suffix_context=suffix_context,
        ):
            return ["director's cut"]
    return []


def _token_is_metadata(token: str) -> bool:
    if token in METADATA_TOKENS:
        return True
    if token.startswith(("ddp", "aac", "ac3", "dts", "truehd", "eac3")) and any(char.isdigit() for char in token):
        return True
    if token.startswith(("ddpa", "hevc")) and any(char.isdigit() for char in token):
        return True
    if re.fullmatch(r"(?:ddp|ddpa|aac|ac3|eac3|dts|opus)\d+(?:\d|kbps|ch)?", token):
        return True
    if re.fullmatch(r"\d+(?:fps|gb|gib|mb|mib|kbps)", token):
        return True
    if re.fullmatch(r"\d{3,4}p", token):
        return True
    if re.fullmatch(r"\d+rip", token):
        return True
    if re.fullmatch(r"(?:x|h)26[45]", token):
        return True
    if re.fullmatch(r"\d+(?:bit|ch)", token):
        return True
    return False


def _token_is_strong_metadata(token: str) -> bool:
    if token in STRONG_METADATA_TOKENS or re.fullmatch(r"\d{3,4}p", token) is not None:
        return True
    if re.fullmatch(r"\d+rip", token):
        return True
    if re.fullmatch(r"\d+(?:fps|gb|gib|mb|mib|kbps)", token):
        return True
    if token.startswith("hevc") and any(char.isdigit() for char in token):
        return True
    return token.startswith(("ddp", "aac", "ac3", "dts", "truehd", "eac3")) and any(
        char.isdigit() for char in token
    )


def _contains_metadata_id(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    if not normalized:
        return False
    if re.search(r"(?:tmdbid|tmdb|imdbid|imdb|tvdbid|tvdb)(?:tt)?\d{3,}", normalized):
        return True
    return re.search(r"tt\d{7,9}", normalized) is not None


def _segment_edition_markers(value: str) -> list[str]:
    markers: list[str] = []
    for edition_key, pattern in EDITION_PATTERNS:
        if pattern.search(value) and edition_key not in markers:
            markers.append(edition_key)
    dc_markers = _dc_abbreviation_edition_markers(
        value,
        parsed_year=_coerce_year(next(iter(YEAR_PATTERN.findall(str(value or ""))), None)),
        suffix_context=False,
    )
    for marker in dc_markers:
        if marker not in markers:
            markers.append(marker)
    return markers


def _strip_dc_abbreviation_suffix(
    value: str,
    *,
    parsed_year: int | None,
    suffix_context: bool,
) -> tuple[str, bool]:
    working = collapse_spaces(value).strip(" -")
    tokens = _ordered_context_tokens(working)
    if not tokens or tokens[-1] != "dc":
        return working, False
    if not _is_directors_cut_abbreviation_context(
        tokens,
        len(tokens) - 1,
        parsed_year=parsed_year,
        metadata_seen=False,
        suffix_context=suffix_context,
    ):
        return working, False
    prefix = re.sub(r"(?i)(?:^|[\s._-]+)\bdc\b\s*$", "", working).strip(" -")
    if not prefix or prefix.lower() in {"the", "a", "an"}:
        return working, False
    return prefix, True


def _strip_edition_suffixes(
    value: str,
    *,
    parsed_year: int | None = None,
    suffix_context: bool = False,
) -> tuple[str, list[str]]:
    working = collapse_spaces(value).strip(" -")
    extracted: list[str] = []
    changed = True
    while changed and working:
        changed = False
        working_after_dc, stripped_dc = _strip_dc_abbreviation_suffix(
            working,
            parsed_year=parsed_year,
            suffix_context=suffix_context,
        )
        if stripped_dc:
            working = working_after_dc
            if "director's cut" not in extracted:
                extracted.insert(0, "director's cut")
            changed = True
            continue
        for edition_key, pattern in EDITION_PATTERNS:
            match = list(pattern.finditer(working))
            if not match:
                continue
            last_match = match[-1]
            suffix = working[last_match.start() :].strip(" -")
            prefix = working[: last_match.start()].strip(" -")
            if not prefix:
                continue
            if prefix.lower() in {"the", "a", "an"}:
                continue
            if suffix and pattern.fullmatch(suffix):
                working = prefix
                if edition_key not in extracted:
                    extracted.insert(0, edition_key)
                changed = True
                break
    return working, extracted


def _strip_trailing_year(value: str, *, parsed_year: int | None) -> tuple[str, int | None, bool]:
    working = collapse_spaces(value).strip(" -")
    matches = list(YEAR_PATTERN.finditer(working))
    if not matches:
        return working, parsed_year, False
    last_match = matches[-1]
    if last_match.start() == 0 and last_match.end() == len(working):
        return working, parsed_year, False
    suffix = working[last_match.end() :].strip(" -")
    if suffix:
        return working, parsed_year, False
    trailing_year = int(last_match.group(1))
    if parsed_year is not None and trailing_year != parsed_year:
        prefix_before_extra_year = working[: last_match.start()].strip(" -")
        previous_matches = list(YEAR_PATTERN.finditer(prefix_before_extra_year))
        if previous_matches and int(previous_matches[-1].group(1)) == parsed_year:
            previous_match = previous_matches[-1]
            previous_suffix = prefix_before_extra_year[previous_match.end() :].strip(" -")
            if not previous_suffix:
                return prefix_before_extra_year[: previous_match.start()].strip(" -"), parsed_year, True
        return working, parsed_year, False
    prefix = working[: last_match.start()].strip(" -")
    if last_match.start() > 0 and not working[last_match.start() - 1].isspace():
        prefix = prefix.rstrip("!.,_:")
    return prefix.strip(" -"), trailing_year, True


def _strip_trailing_metadata_tokens(value: str) -> tuple[str, bool]:
    tokens = collapse_spaces(value).split()
    removed_any = False
    while len(tokens) > 1 and _token_is_metadata(_canonical_metadata_token(tokens[-1])):
        tokens.pop()
        removed_any = True
    return " ".join(tokens).strip(" -"), removed_any


def _strip_trailing_extras_suffix(value: str, *, parsed_year: int | None) -> tuple[str, bool]:
    working = collapse_spaces(value).strip(" -")
    if parsed_year is None or not working:
        return working, False
    updated = re.sub(
        r"(?i)\s*\+\s*(?:extras?|bonus(?:\s+features?)?|special\s+features?)?\s*$",
        "",
        working,
    ).strip(" -")
    if updated and updated != working:
        return updated, True
    return working, False


def _canonical_metadata_token(token: str) -> str:
    normalized = token.lower().replace("’", "'").replace(".", "").replace("'", "")
    normalized = normalized.strip(" -,;:[](){}~!")
    normalized = normalized.replace("e-ac3", "eac3").replace("eac-3", "eac3").replace("ac-3", "ac3")
    normalized = normalized.replace("dts:x", "dtsx").replace("dts-x", "dtsx")
    normalized = normalized.replace("10-bit", "10bit").replace("8-bit", "8bit")
    return normalized.strip(" -,;:[](){}~!")


def _strip_metadata_tokens_from_edges(value: str) -> str:
    tokens = collapse_spaces(value).split()
    while tokens and _token_is_metadata(_canonical_metadata_token(tokens[-1])):
        tokens.pop()
    while tokens and _token_is_metadata(_canonical_metadata_token(tokens[0])):
        tokens.pop(0)
    return " ".join(tokens)


def _smart_case_display_title(value: str) -> str:
    working = collapse_spaces(value).strip()
    if not working or not _needs_display_smart_casing(working):
        return working

    words = working.split()
    smart_cased_words: list[str] = []
    for index, word in enumerate(words):
        smart_cased_words.append(
            _smart_case_word(
                word,
                is_first=index == 0,
                is_last=index == len(words) - 1,
            )
        )
    return " ".join(smart_cased_words)


def _needs_display_smart_casing(value: str) -> bool:
    letters = [char for char in str(value or "") if char.isalpha()]
    if not letters:
        return False
    has_lower = any(char.islower() for char in letters)
    has_upper = any(char.isupper() for char in letters)
    return not (has_lower and has_upper)


def _smart_case_word(word: str, *, is_first: bool, is_last: bool) -> str:
    match = re.fullmatch(r"([^A-Za-z0-9]*)(.*?)([^A-Za-z0-9]*)", word)
    if not match:
        return word
    prefix, core, suffix = match.groups()
    if not core or not any(char.isalpha() for char in core):
        return word

    is_segment_first = is_first or any(char in prefix for char in "([{")
    is_segment_last = is_last or any(char in suffix for char in ")]}")
    if core.lower() in SMART_CASE_STOPWORDS and not is_segment_first and not is_segment_last:
        return f"{prefix}{core.lower()}{suffix}"
    return f"{prefix}{_smart_case_compound_token(core)}{suffix}"


def _smart_case_compound_token(token: str) -> str:
    pieces = re.split(r"(-)", token)
    smart_cased_pieces: list[str] = []
    for piece in pieces:
        if piece == "-":
            smart_cased_pieces.append(piece)
            continue
        smart_cased_pieces.append(_smart_case_apostrophe_token(piece))
    return "".join(smart_cased_pieces)


def _smart_case_apostrophe_token(token: str) -> str:
    pieces = re.split(r"([’'])", token)
    smart_cased_pieces: list[str] = []
    segment_index = 0
    for piece in pieces:
        if piece in {"'", "’"}:
            smart_cased_pieces.append(piece)
            continue
        if not piece:
            continue
        smart_cased_pieces.append(
            _smart_case_fragment(
                piece,
                lower_contraction=segment_index > 0,
            )
        )
        segment_index += 1
    return "".join(smart_cased_pieces)


def _smart_case_fragment(value: str, *, lower_contraction: bool) -> str:
    normalized = value.lower()
    if normalized == "v/h/s":
        return "V/H/S"
    if normalized.replace("/", "") in SMART_CASE_ACRONYMS:
        return normalized.replace("/", "").upper() if "/" not in normalized else "V/H/S"
    if ROMAN_NUMERAL_PATTERN.fullmatch(normalized):
        return normalized.upper()
    if normalized.isdigit():
        return normalized
    if lower_contraction and normalized in SMART_CASE_CONTRACTION_SUFFIXES:
        return normalized
    if any(char.isdigit() for char in normalized) and any(char.isalpha() for char in normalized):
        return "".join(char.upper() if char.isalpha() else char for char in normalized)

    first_alpha_found = False
    transformed: list[str] = []
    for char in normalized:
        if char.isalpha() and not first_alpha_found:
            transformed.append(char.upper())
            first_alpha_found = True
        else:
            transformed.append(char)
    return "".join(transformed)


def _cleanup_title_text(value: str) -> str:
    working = collapse_spaces(value)
    working = EMPTY_BRACKET_PATTERN.sub(" ", working)
    working = working.replace(" - ", " - ").strip(" -")
    working = _space_unspaced_alternate_title_dash(working)
    working = re.sub(r"(?i)\bwho\s+s\b", "who's", working)
    working = re.sub(r"\s+([)\]}])", r"\1", working)
    working = re.sub(r"([([{])\s+", r"\1", working)
    working = collapse_spaces(working)
    return working


def _space_unspaced_alternate_title_dash(value: str) -> str:
    working = str(value or "")
    pattern = re.compile(
        r"(?P<left>(?:[A-Za-zÀ-ÖØ-öø-ÿ'’]+\s+){2,}[A-Za-zÀ-ÖØ-öø-ÿ'’]+)-(?P<right>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'’]+){2,})"
    )
    return pattern.sub(lambda match: f"{match.group('left')} - {match.group('right')}", working)


def _merge_edition_identities(*values: object) -> str:
    parts: list[str] = []
    for value in values:
        for part in str(value or "").split("|"):
            normalized = part.strip().lower()
            if normalized and normalized != "standard" and normalized not in parts:
                parts.append(normalized)
    return "|".join(parts) if parts else "standard"


def _comparison_key(value: str) -> str:
    normalized = value.lower().strip()
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("'", "").replace("’", "")
    normalized = re.sub(r"[-,/:._–—]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9\s]+", " ", normalized)
    return collapse_spaces(normalized)


def _coerce_year(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_standalone_year(value: str) -> bool:
    return YEAR_PATTERN.fullmatch(value.strip()) is not None


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


def collapse_spaces(value: str) -> str:
    return " ".join(str(value or "").split())
