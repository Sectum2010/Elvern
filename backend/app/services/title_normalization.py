from __future__ import annotations

from pathlib import Path
import re

from .media_title_parser import (
    extract_edition_identity_anywhere as extract_edition_identity_anywhere_from_parser,
    parse_media_title,
)

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

def clean_title_for_matching(value: object, year: object) -> str | None:
    parsed = parse_media_title(title=None, year=year, original_filename=value)
    display_title = str(parsed["display_title"] or "").strip()
    return display_title or None


def split_title_and_edition(cleaned_title: str) -> tuple[str, str]:
    parsed = parse_media_title(title=cleaned_title, year=None, original_filename=None)
    return (
        str(parsed["base_title"] or cleaned_title or "").strip(),
        str(parsed["edition_identity"] or "standard"),
    )


def extract_title_metadata(value: object, year: object) -> dict[str, object]:
    parsed = parse_media_title(title=None, year=year, original_filename=value)
    cleaned_title = str(parsed["display_title"] or "").strip()
    if not cleaned_title:
        return {
            "cleaned_title": None,
            "base_title": None,
            "edition_identity": "standard",
            "display_title": None,
            "parsed_year": None,
            "title_source": None,
            "parse_confidence": "low",
            "warnings": [],
        }
    return {
        "cleaned_title": cleaned_title,
        "base_title": str(parsed["base_title"] or cleaned_title),
        "edition_identity": str(parsed["edition_identity"] or "standard"),
        "display_title": cleaned_title,
        "parsed_year": str(parsed["parsed_year"]) if parsed["parsed_year"] is not None else None,
        "title_source": str(parsed["title_source"] or ""),
        "parse_confidence": str(parsed["parse_confidence"] or "low"),
        "warnings": [str(value) for value in parsed["warnings"]],
    }


def resolve_title_metadata(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> dict[str, object]:
    parsed = parse_media_title(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    display_title = str(parsed["display_title"] or "").strip()
    base_title = str(parsed["base_title"] or display_title).strip()
    if not base_title:
        return {
            "cleaned_title": None,
            "base_title": None,
            "edition_identity": "standard",
            "display_title": None,
            "parsed_year": None,
            "title_source": None,
            "parse_confidence": "low",
            "warnings": [],
        }
    return {
        "cleaned_title": display_title or base_title,
        "base_title": base_title,
        "edition_identity": str(parsed["edition_identity"] or "standard"),
        "display_title": display_title or base_title,
        "poster_match_title": str(parsed.get("poster_match_title") or base_title),
        "poster_match_year": str(parsed["poster_match_year"]) if parsed.get("poster_match_year") is not None else None,
        "poster_match_source": str(parsed.get("poster_match_source") or parsed["title_source"] or ""),
        "poster_match_identity": dict(parsed.get("poster_match_identity") or {}),
        "parsed_year": str(parsed["parsed_year"]) if parsed["parsed_year"] is not None else None,
        "title_source": str(parsed["title_source"] or ""),
        "parse_confidence": str(parsed["parse_confidence"] or "low"),
        "warnings": [str(value) for value in parsed["warnings"]],
        "parser_version": str(parsed.get("parser_version") or ""),
        "suspicious_output": bool(parsed.get("suspicious_output")),
    }


def resolve_poster_match_identity(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> dict[str, object]:
    parsed = parse_media_title(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    poster_identity = dict(parsed.get("poster_match_identity") or {})
    poster_match_title = str(poster_identity.get("title") or parsed.get("poster_match_title") or "").strip()
    poster_match_year = poster_identity.get("year", parsed.get("poster_match_year"))
    return {
        "title": poster_match_title or None,
        "year": int(poster_match_year) if poster_match_year not in {None, ""} else None,
        "source": str(
            poster_identity.get("source")
            or parsed.get("poster_match_source")
            or parsed["title_source"]
            or ""
        ),
        "parse_confidence": str(parsed["parse_confidence"] or "low"),
        "warnings": [str(value) for value in parsed["warnings"]],
        "parser_version": str(parsed.get("parser_version") or ""),
        "suspicious_output": bool(parsed.get("suspicious_output")),
    }


def extract_edition_identity_anywhere(*values: object) -> str:
    return extract_edition_identity_anywhere_from_parser(*values)


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
        metadata.get("display_title"),
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
