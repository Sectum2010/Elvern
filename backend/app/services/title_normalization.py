from __future__ import annotations

from pathlib import Path
import re


TITLE_NOISE_TOKENS = {
    "2160p",
    "1080p",
    "720p",
    "480p",
    "uhd",
    "hdr",
    "hdr10",
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
    "remux",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "avc",
    "truehd",
    "atmos",
    "dts",
    "aac",
    "ac3",
    "ddp",
    "dd5",
    "ma",
    "proper",
    "repack",
    "imax",
    "criterion",
    "hybrid",
    "multi",
    "subbed",
    "dubbed",
    "10bit",
    "8bit",
}
YEAR_TOKEN_TEMPLATE = r"(?<!\d){year}(?!\d)"
ARTICLE_TOKENS = {"the", "a", "an"}
ROMAN_TO_ARABIC = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}
EDITION_SUFFIX_PATTERNS = (
    ("roadshow", re.compile(r"(?:^|\s)(roadshow(?:\s+version)?)$", re.IGNORECASE)),
    ("director's cut", re.compile(r"(?:^|\s)((?:director'?s|directors)\s+cut|dc)$", re.IGNORECASE)),
    ("theatrical", re.compile(r"(?:^|\s)(theatrical(?:\s+cut|\s+version)?)$", re.IGNORECASE)),
    ("extended", re.compile(r"(?:^|\s)(extended(?:\s+cut|\s+edition)?)$", re.IGNORECASE)),
    ("final cut", re.compile(r"(?:^|\s)(final\s+cut)$", re.IGNORECASE)),
    ("ultimate cut", re.compile(r"(?:^|\s)(ultimate\s+cut)$", re.IGNORECASE)),
    ("special edition", re.compile(r"(?:^|\s)(special\s+edition)$", re.IGNORECASE)),
    ("collector's edition", re.compile(r"(?:^|\s)(collector'?s\s+edition)$", re.IGNORECASE)),
    ("anniversary edition", re.compile(r"(?:^|\s)(anniversary\s+edition)$", re.IGNORECASE)),
    ("unrated", re.compile(r"(?:^|\s)(unrated)$", re.IGNORECASE)),
)
EDITION_ANYWHERE_PATTERNS = (
    ("roadshow", re.compile(r"\broadshow(?:\s+version)?\b", re.IGNORECASE)),
    ("director's cut", re.compile(r"\b(?:director'?s|directors)\s+cut\b", re.IGNORECASE)),
    ("theatrical", re.compile(r"\btheatrical(?:\s+cut|\s+version)?\b", re.IGNORECASE)),
    ("extended", re.compile(r"\bextended(?:\s+cut|\s+edition)?\b", re.IGNORECASE)),
    ("final cut", re.compile(r"\bfinal\s+cut\b", re.IGNORECASE)),
    ("ultimate cut", re.compile(r"\bultimate\s+cut\b", re.IGNORECASE)),
    ("special edition", re.compile(r"\bspecial\s+edition\b", re.IGNORECASE)),
    ("collector's edition", re.compile(r"\bcollector'?s\s+edition\b", re.IGNORECASE)),
    ("anniversary edition", re.compile(r"\banniversary\s+edition\b", re.IGNORECASE)),
    ("unrated", re.compile(r"\bunrated\b", re.IGNORECASE)),
)


def collapse_spaces(value: str) -> str:
    return " ".join(value.split())


def normalize_common_root_variants(value: str) -> str:
    normalized = value
    normalized = re.sub(r"\bsorceror(s)?\b", r"sorcerer\1", normalized)
    return normalized


def normalize_title_source(value: str) -> str:
    without_extension = Path(value).stem
    normalized = without_extension.replace("&", " and ")
    normalized = re.sub(r"[-,/:._–—]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" -,/:._")
    return normalized


def normalize_title_key(value: str) -> str:
    lowered = value.lower().strip()
    lowered = lowered.replace("&", " and ")
    lowered = re.sub(r"^(the|a|an)\s+", "", lowered)
    lowered = lowered.replace("'", "").replace("’", "")
    lowered = re.sub(r"[-,/:._–—]+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    lowered = re.sub(r"\b([a-z0-9]+)\s+s\b", r"\1s", lowered)
    lowered = normalize_common_root_variants(lowered)
    return collapse_spaces(lowered)


def normalize_search_text(value: str, *, drop_leading_articles: bool = False) -> str:
    normalized = value.lower().strip()
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("'", "").replace("’", "")
    normalized = re.sub(r"[-,/:._–—]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9\s]+", " ", normalized)
    normalized = normalize_common_root_variants(normalized)
    normalized = collapse_spaces(normalized)
    if drop_leading_articles:
        normalized = re.sub(r"^(the|a|an)\s+", "", normalized)
    return normalized


def compact_search_text(value: str, *, drop_leading_articles: bool = False) -> str:
    normalized = normalize_search_text(value, drop_leading_articles=drop_leading_articles)
    return normalized.replace(" ", "")


def normalize_search_token(token: str) -> str:
    normalized = normalize_search_text(token)
    if not normalized:
        return ""
    if normalized in ROMAN_TO_ARABIC:
        return ROMAN_TO_ARABIC[normalized]
    return normalized


def tokenize_search_text(value: str, *, drop_leading_articles: bool = False) -> list[str]:
    normalized = normalize_search_text(value, drop_leading_articles=drop_leading_articles)
    tokens: list[str] = []
    for token in normalized.split():
        canonical = normalize_search_token(token)
        if canonical:
            tokens.append(canonical)
    return tokens


def strip_trailing_noise_tokens(value: str) -> str:
    tokens = value.split()
    while tokens:
        token = tokens[-1].strip("()[]{}.,-_/:").lower()
        if (
            token in TITLE_NOISE_TOKENS
            or re.fullmatch(r"\d{3,4}p", token)
            or re.fullmatch(r"[xh]\.?26[45]", token)
            or re.fullmatch(r"\d\.\d", token)
        ):
            tokens.pop()
            continue
        break
    return " ".join(tokens).strip(" -_")


def clean_title_for_matching(value: object, year: object) -> str | None:
    if not value:
        return None
    normalized = normalize_title_source(str(value))
    if not normalized:
        return None

    year_value: int | None = None
    if year not in {None, ""}:
        try:
            year_value = int(year)
        except (TypeError, ValueError):
            year_value = None

    if year_value is not None:
        year_pattern = re.compile(YEAR_TOKEN_TEMPLATE.format(year=year_value))
        matches = list(year_pattern.finditer(normalized))
        if matches:
            normalized = normalized[: matches[-1].start()].strip(" -_()[]")

    normalized = strip_trailing_noise_tokens(normalized)
    normalized = collapse_spaces(normalized)
    return normalized or None


def split_title_and_edition(cleaned_title: str) -> tuple[str, str]:
    working = collapse_spaces(cleaned_title)
    edition_markers: list[str] = []
    while working:
        matched = False
        for edition_key, pattern in EDITION_SUFFIX_PATTERNS:
            match = pattern.search(working)
            if not match:
                continue
            if edition_key not in edition_markers:
                edition_markers.insert(0, edition_key)
            working = working[: match.start()].strip(" -_/:,")
            matched = True
            break
        if not matched:
            break
    base_title = collapse_spaces(working) or collapse_spaces(cleaned_title)
    edition_identity = "|".join(edition_markers) if edition_markers else "standard"
    return base_title, edition_identity


def extract_title_metadata(value: object, year: object) -> dict[str, str | None]:
    cleaned_title = clean_title_for_matching(value, year)
    if not cleaned_title:
        return {
            "cleaned_title": None,
            "base_title": None,
            "edition_identity": "standard",
        }
    base_title, edition_identity = split_title_and_edition(cleaned_title)
    return {
        "cleaned_title": cleaned_title,
        "base_title": base_title,
        "edition_identity": edition_identity,
    }


def resolve_title_metadata(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> dict[str, str | None]:
    resolved_variants: list[dict[str, str | None]] = []
    for source_value in (original_filename, title):
        metadata = extract_title_metadata(source_value, year)
        if metadata["base_title"]:
            resolved_variants.append(metadata)
    if not resolved_variants:
        return {
            "cleaned_title": None,
            "base_title": None,
            "edition_identity": "standard",
        }
    for metadata in resolved_variants:
        if metadata["edition_identity"] != "standard":
            return metadata
    return min(
        resolved_variants,
        key=lambda metadata: len(str(metadata["cleaned_title"] or metadata["base_title"] or "")),
    )


def extract_edition_identity_anywhere(*values: object) -> str:
    markers: list[str] = []
    normalized_sources = [normalize_title_source(str(value or "")) for value in values if value]
    for edition_key, pattern in EDITION_ANYWHERE_PATTERNS:
        if any(pattern.search(source) for source in normalized_sources):
            markers.append(edition_key)
    return "|".join(markers) if markers else "standard"


def apostrophe_title_variants(value: str) -> list[str]:
    variants: list[str] = []
    normalized = collapse_spaces(value)
    if normalized:
        variants.append(normalized)

    without_apostrophe = collapse_spaces(normalized.replace("'", "").replace("’", ""))
    if without_apostrophe and without_apostrophe not in variants:
        variants.append(without_apostrophe)
    return variants


def build_search_aliases(*phrases: str) -> set[str]:
    aliases: set[str] = set()
    for phrase in phrases:
        tokens = tokenize_search_text(phrase)
        if not tokens:
            continue
        for start in range(len(tokens)):
            for length in range(2, min(5, len(tokens) - start) + 1):
                window = tokens[start : start + length]
                while window and window[0] in ARTICLE_TOKENS:
                    window = window[1:]
                if len(window) < 2:
                    continue
                acronym = "".join(token[0] for token in window if token)
                if 2 <= len(acronym) <= 8:
                    aliases.add(acronym)
    return aliases


def build_search_index(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> dict[str, object]:
    metadata = resolve_title_metadata(title=title, year=year, original_filename=original_filename)
    phrases: list[str] = []
    for value in (
        metadata["base_title"],
        metadata["cleaned_title"],
        clean_title_for_matching(original_filename, year),
        clean_title_for_matching(title, year),
    ):
        if value and value not in phrases:
            phrases.append(value)

    normalized_phrases = [
        normalize_search_text(phrase, drop_leading_articles=True)
        for phrase in phrases
        if phrase
    ]
    search_tokens: set[str] = set()
    for phrase in normalized_phrases:
        search_tokens.update(tokenize_search_text(phrase))

    compact_phrases: list[str] = []
    for phrase in phrases:
        if not phrase:
            continue
        for drop_leading_articles in (False, True):
            compact = compact_search_text(phrase, drop_leading_articles=drop_leading_articles)
            if compact and compact not in compact_phrases:
                compact_phrases.append(compact)

    return {
        "base_title": metadata["base_title"],
        "cleaned_title": metadata["cleaned_title"],
        "edition_identity": metadata["edition_identity"],
        "normalized_phrases": [phrase for phrase in normalized_phrases if phrase],
        "compact_phrases": compact_phrases,
        "search_tokens": search_tokens,
        "search_aliases": build_search_aliases(*phrases),
    }


def build_query_terms(query: str) -> list[str]:
    return tokenize_search_text(query, drop_leading_articles=True)


def build_query_compact_forms(query: str) -> list[str]:
    compact_forms: list[str] = []
    for drop_leading_articles in (False, True):
        compact = compact_search_text(query, drop_leading_articles=drop_leading_articles)
        if compact and compact not in compact_forms:
            compact_forms.append(compact)
    return compact_forms


def match_search_query(
    *,
    query: str,
    search_index: dict[str, object],
) -> tuple[bool, int]:
    query_terms = build_query_terms(query)
    if not query_terms:
        return False, 0

    normalized_query = normalize_search_text(query, drop_leading_articles=True)
    normalized_phrases = list(search_index["normalized_phrases"])
    compact_phrases = list(search_index.get("compact_phrases", []))
    search_tokens = set(search_index["search_tokens"])
    search_aliases = set(search_index["search_aliases"])
    search_blob = " ".join(normalized_phrases)
    base_phrase = normalized_phrases[0] if normalized_phrases else ""
    base_compact = compact_phrases[0] if compact_phrases else ""

    query_compact_forms = build_query_compact_forms(query)

    score = 0
    if normalized_query and base_phrase and normalized_query in base_phrase:
        score += 100
    elif normalized_query and normalized_query in search_blob:
        score += 60

    for compact_query in query_compact_forms:
        if len(compact_query) < 4:
            continue
        if base_compact and compact_query in base_compact:
            score += 90
            break
        if any(compact_query in phrase for phrase in compact_phrases):
            score += 50
            break

    for term in query_terms:
        if term in search_aliases:
            score += 18
            continue
        if term in search_tokens:
            score += 12 if term in base_phrase.split() else 8
            continue
        if len(term) >= 4 and compact_phrases:
            if base_compact and term in base_compact:
                score += 10
                continue
            if any(term in phrase for phrase in compact_phrases):
                score += 6
                continue
        if len(term) >= 3 and term in search_blob:
            score += 4
            continue
        return False, 0

    return True, score
