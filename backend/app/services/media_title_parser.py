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

METADATA_TOKENS = {
    "2160p",
    "1080p",
    "720p",
    "540p",
    "480p",
    "4k",
    "8k",
    "uhd",
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
    "webrip",
    "webdl",
    "web-dl",
    "web",
    "remux",
    "bdremux",
    "brremux",
    "x264",
    "x265",
    "h264",
    "h265",
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
    "eac3",
    "ddp",
    "dd+",
    "ma",
    "flac",
    "lpcm",
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
    "webrip",
    "webdl",
    "web-dl",
    "remux",
    "bdremux",
    "brremux",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "av1",
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
    "hdr",
    "hdr10",
    "hdr10plus",
    "hdr10+",
    "dv",
    "dovi",
}
EDITION_PATTERNS = (
    ("roadshow", re.compile(r"\broadshow(?:\s+version)?\b", re.IGNORECASE)),
    ("director's cut", re.compile(r"\b(?:director'?s|directors)\s+cut\b|\bdc\b", re.IGNORECASE)),
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

    def replace_bracket_group(match: re.Match[str]) -> str:
        nonlocal parsed_year
        nonlocal signal_score
        content = collapse_spaces(match.group(2))
        if not content:
            warnings.append("empty_bracket_group_removed")
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
        if classification["kind"] in {"metadata", "id"}:
            removed_metadata_bracket_suffix = True
            warnings.append(
                "metadata_id_block_removed"
                if classification["kind"] == "id"
                else "metadata_block_removed"
            )
            warnings.append("metadata_bracket_suffix_removed")
            signal_score += 2 if classification["kind"] == "id" else 1
            return " "
        return f"{match.group(1)}{content}{match.group(3)}"

    working = BRACKET_GROUP_PATTERN.sub(replace_bracket_group, working)
    working = collapse_spaces(working)

    kept_segments: list[str] = []
    for index, segment in enumerate(RIGHT_SIDE_SPLIT_PATTERN.split(working)):
        cleaned_segment = collapse_spaces(segment).strip(" -")
        if not cleaned_segment:
            continue
        classification = _classify_segment(cleaned_segment)
        if index > 0 and classification["kind"] in {"metadata", "id", "edition", "year"}:
            edition_markers.extend(classification["edition_markers"])
            if classification["kind"] in {"metadata", "id"}:
                warnings.append("metadata_segment_removed")
                warnings.append("technical_suffix_density_cut")
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

    if filename_like:
        working, cut_suffix, suffix_hints = _cut_non_title_suffix(working)
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

    working, stripped_year, removed_year = _strip_trailing_year(working, parsed_year=parsed_year)
    if stripped_year is not None:
        parsed_year = stripped_year
    if removed_year:
        warnings.append("trailing_year_removed")
        signal_score += 1

    working, stripped_editions = _strip_edition_suffixes(working)
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

    source_hints = [str(hint) for hint in candidate.get("title_number_hints") or []]
    display_hints = _extract_meaningful_title_number_hints(cleaned, parsed_year=parsed_year)
    if any(hint not in display_hints for hint in source_hints):
        warnings.append("display_title_lost_meaningful_number_token")

    return _dedupe_strings(warnings)


def _prepare_candidate_text(value: object) -> str:
    if value in {None, ""}:
        return ""
    basename = str(value).split("/")[-1].split("\\")[-1]
    without_extension = re.sub(r"\.[a-z0-9]{2,5}$", "", basename, flags=re.IGNORECASE)
    normalized = unicodedata.normalize("NFKC", without_extension)
    normalized = normalized.replace("\u00a0", " ")
    normalized = normalized.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"[._]+", " ", normalized)
    normalized = re.sub(r"\s+-\s*", " - ", normalized)
    normalized = collapse_spaces(normalized)
    return normalized.strip(" -")


def _extract_meaningful_title_number_hints(
    value: object,
    *,
    parsed_year: int | None,
) -> list[str]:
    prepared = _prepare_candidate_text(value)
    if not prepared:
        return []

    title_region, _cut_suffix, suffix_hints = _cut_non_title_suffix(prepared)
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
        _kept, cut_suffix, _suffix_hints = _cut_non_title_suffix(prepared)
        if cut_suffix:
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
        return {"kind": "empty", "edition_markers": []}
    if _contains_metadata_id(cleaned):
        return {"kind": "id", "edition_markers": []}
    if _is_standalone_year(cleaned):
        return {"kind": "year", "edition_markers": []}

    edition_markers = _segment_edition_markers(cleaned)
    cleaned_without_editions = cleaned
    for edition_key, pattern in EDITION_PATTERNS:
        if edition_key in edition_markers:
            cleaned_without_editions = pattern.sub(" ", cleaned_without_editions)
    cleaned_without_editions = collapse_spaces(cleaned_without_editions).strip(" -")
    if edition_markers and not cleaned_without_editions:
        return {"kind": "edition", "edition_markers": edition_markers}

    tokens = _classification_tokens(cleaned_without_editions or cleaned)
    if not tokens:
        return {
            "kind": "edition" if edition_markers else "empty",
            "edition_markers": edition_markers,
        }

    metadata_hits = 0
    strong_hits = 0
    numeric_channel_hits = 0
    for token in tokens:
        if _token_is_metadata(token):
            metadata_hits += 1
        if _token_is_strong_metadata(token):
            strong_hits += 1
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            numeric_channel_hits += 1

    if metadata_hits and strong_hits and metadata_hits >= max(1, len(tokens) - 1):
        return {"kind": "metadata", "edition_markers": edition_markers}
    if metadata_hits >= 2 and strong_hits >= 1 and metadata_hits >= max(2, len(tokens) // 2):
        return {"kind": "metadata", "edition_markers": edition_markers}
    if metadata_hits == len(tokens) and len(tokens) <= 3:
        return {"kind": "metadata", "edition_markers": edition_markers}
    if strong_hits >= 1 and metadata_hits >= 1 and metadata_hits + numeric_channel_hits == len(tokens):
        return {"kind": "metadata", "edition_markers": edition_markers}

    return {"kind": "title", "edition_markers": edition_markers}


def _cut_non_title_suffix(value: str) -> tuple[str, bool, dict[str, object]]:
    working = collapse_spaces(value).strip(" -")
    if not working:
        return working, False, {"edition_markers": [], "parsed_year": None, "rule_markers": []}

    removed_suffix_fragments: list[str] = []
    rule_markers: list[str] = []
    cut_any = False

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

    tokens = working.split()
    boundary_info = _metadata_suffix_boundary(tokens)
    if boundary_info is not None:
        boundary, boundary_markers = boundary_info
        removed_suffix_fragments.append(" ".join(tokens[boundary:]))
        rule_markers.extend(boundary_markers)
        working = " ".join(tokens[:boundary]).strip(" -")
        cut_any = True

    if not cut_any:
        return working, False, {"edition_markers": [], "parsed_year": None, "rule_markers": []}

    suffix = " ".join(fragment for fragment in removed_suffix_fragments if fragment)
    return working, True, _suffix_parse_hints(suffix, *rule_markers)


def _metadata_suffix_boundary(tokens: list[str]) -> tuple[int, list[str]] | None:
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
            and suffix_metrics["metadata_hits"] >= 1
            and (suffix_metrics["strong_hits"] >= 1 or suffix_metrics["release_group_hits"] >= 1)
            and _looks_like_release_year_boundary(tokens, index)
        ):
            return index, ["standalone_release_year_cut", "technical_suffix_density_cut"]
        if _is_metadata_boundary_token(tokens[index], current):
            if suffix_metrics["strong_hits"] >= 1 and suffix_metrics["metadata_hits"] >= 2:
                return index, ["technical_suffix_density_cut"]
        if current in {"ita", "eng", "jpn", "ger", "fra", "spa", "ita", "itaeng", "multi"}:
            if suffix_metrics["strong_hits"] >= 1:
                return index, ["technical_suffix_density_cut"]
        if current in {"proper", "repack", "internal", "limited"}:
            return index, ["technical_suffix_density_cut"]
    return None


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
        canonical = _canonical_metadata_token(token)
        if _token_is_metadata(canonical):
            metadata_hits += 1
        if _token_is_strong_metadata(canonical):
            strong_hits += 1
        if _looks_like_release_group_token(token):
            metadata_hits += 1
            release_group_hits += 1
    return {
        "metadata_hits": metadata_hits,
        "strong_hits": strong_hits,
        "release_group_hits": release_group_hits,
    }


def _suffix_parse_hints(value: str, *rule_markers: str) -> dict[str, object]:
    working = collapse_spaces(value)
    edition_markers = _segment_edition_markers(working)
    year_matches = list(YEAR_PATTERN.finditer(working))
    parsed_year = int(year_matches[0].group(1)) if year_matches else None
    return {
        "edition_markers": edition_markers,
        "parsed_year": parsed_year,
        "rule_markers": _dedupe_strings([str(marker) for marker in rule_markers if marker]),
    }


def _is_metadata_boundary_token(raw_token: str, canonical: str) -> bool:
    if _token_is_metadata(canonical):
        return True
    if _looks_like_release_group_token(raw_token):
        return True
    return canonical in {"ita", "eng", "jpn", "ger", "fra", "spa", "itaeng", "multi", "dub", "sub"}


def _starts_edition_suffix(tokens: list[str], index: int) -> bool:
    suffix = collapse_spaces(" ".join(tokens[index:]))
    if not suffix:
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
    if upper_count >= 2:
        return True
    if upper_count >= 1 and lower_count >= 1 and not (raw[0].isupper() and raw[1:].islower()):
        return True
    return False


def _looks_like_dash_suffix_junk_segment(value: str) -> bool:
    cleaned = collapse_spaces(value).strip(" -")
    if not cleaned:
        return False
    words = cleaned.split()
    if len(words) > 3:
        return False
    if len(words) == 1:
        word = words[0]
        if _looks_like_bare_release_group_token(word):
            return True
        if word.isalpha() and word.islower():
            return True
    return all(word.isalpha() and word.islower() for word in words)


def _classification_tokens(value: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9]+(?:[.'’+-][A-Za-z0-9]+)*", value.lower())
    tokens: list[str] = []
    for token in raw_tokens:
        for part in [token, *token.split("-")]:
            canonical = part.strip(" -").replace("’", "'")
            canonical = canonical.replace(".", "")
            canonical = canonical.replace("'", "")
            if canonical and canonical not in tokens:
                tokens.append(canonical)
    return tokens


def _token_is_metadata(token: str) -> bool:
    if token in METADATA_TOKENS:
        return True
    if token.startswith(("ddp", "aac", "ac3", "dts", "truehd", "eac3")) and any(char.isdigit() for char in token):
        return True
    if re.fullmatch(r"\d{3,4}p", token):
        return True
    if re.fullmatch(r"(?:x|h)26[45]", token):
        return True
    if re.fullmatch(r"\d+(?:bit|ch)", token):
        return True
    return False


def _token_is_strong_metadata(token: str) -> bool:
    if token in STRONG_METADATA_TOKENS or re.fullmatch(r"\d{3,4}p", token) is not None:
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
    return markers


def _strip_edition_suffixes(value: str) -> tuple[str, list[str]]:
    working = collapse_spaces(value).strip(" -")
    extracted: list[str] = []
    changed = True
    while changed and working:
        changed = False
        for edition_key, pattern in EDITION_PATTERNS:
            match = list(pattern.finditer(working))
            if not match:
                continue
            last_match = match[-1]
            suffix = working[last_match.start() :].strip(" -")
            prefix = working[: last_match.start()].strip(" -")
            if not prefix:
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
        return working, parsed_year, False
    return working[: last_match.start()].strip(" -"), trailing_year, True


def _strip_trailing_metadata_tokens(value: str) -> tuple[str, bool]:
    tokens = collapse_spaces(value).split()
    removed_any = False
    while tokens and _token_is_metadata(_canonical_metadata_token(tokens[-1])):
        tokens.pop()
        removed_any = True
    return " ".join(tokens).strip(" -"), removed_any


def _canonical_metadata_token(token: str) -> str:
    normalized = token.lower().replace("’", "'").replace(".", "").replace("'", "")
    return normalized.strip(" -")


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
    working = re.sub(r"\s+([)\]}])", r"\1", working)
    working = re.sub(r"([([{])\s+", r"\1", working)
    working = collapse_spaces(working)
    return working


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
