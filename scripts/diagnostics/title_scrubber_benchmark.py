from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.media_title_parser import (  # noqa: E402
    BRACKET_GROUP_PATTERN,
    GENRE_DESCRIPTOR_TOKENS,
    TITLE_PARSER_VERSION,
    YEAR_PATTERN,
    _classify_segment,
    _canonical_metadata_token,
    _classification_tokens,
    _contains_compound_localization_token,
    _contains_compound_metadata_token,
    _is_standalone_year,
    _looks_like_edition_bracket_span,
    _looks_like_episode_identity_token,
    _looks_like_metadata_bracket_span,
    _looks_like_release_group_span_content,
    _looks_like_release_group_token,
    _ordered_context_tokens,
    _prepare_candidate_text,
    _segment_edition_markers,
    _suffix_metadata_metrics,
    _token_is_localization_metadata,
    _token_is_metadata,
    _token_is_strong_metadata,
    _token_is_subtitle_metadata,
    collapse_spaces,
    parse_media_title,
)

TMP_DIR = REPO_ROOT / "tmp"
PHASE20_REPORT_PATH = TMP_DIR / "elvern-title-scrub-75k-phase20-classified-report.json"
PHASE20_BASELINE_SUMMARY = {
    "true_failures": 1719,
    "classification_counts": {
        "PASS": 66041,
        "FALSE_POSITIVE_CLEAN_OUTPUT": 4488,
        "EXPECTED_COLLECTION_OR_RANGE": 2636,
        "EXPECTED_EVENT_OR_SPORTS": 863,
        "TRUE_FAIL_RELEASE_YEAR_GRAMMAR": 685,
        "TRUE_FAIL_METADATA_SUFFIX": 550,
        "TRUE_FAIL_OVERTRIM_REAL": 199,
        "TRUE_FAIL_DASH_TITLE": 180,
        "TRUE_FAIL_BRACKET_SPAN": 105,
        "EXPECTED_EDITION_STRIP": 67,
    },
}

REPORT_PATH = TMP_DIR / "title-scrubber-v1.0.0-report.json"
SUMMARY_PATH = TMP_DIR / "title-scrubber-v1.0.0-summary.txt"
FAILED_SAMPLE_PATH = TMP_DIR / "title-scrubber-v1.0.0-failed-sample.txt"
CLASSIFIED_REPORT_PATH = TMP_DIR / "title-scrubber-v1.0.0-classified-report.json"
CLASSIFIED_SUMMARY_PATH = TMP_DIR / "title-scrubber-v1.0.0-classified-summary.txt"
MANUAL_BUCKETS_PATH = TMP_DIR / "title-scrubber-v1.0.0-manual-review-buckets.md"
ALL_FAILED_TEXT_PATH = TMP_DIR / "title-scrubber-v1.0.0-all-failed-results.txt"

COLLECTION_RANGE_RE = re.compile(
    r"(?i)\b(?:all\s+parts|complete|collection|saga|trilogy|quadrilogy|duology|dilogy|anthology|franchise|movies|films|movie\s+pack|film\s+pack|pack|volume|volumes?|season|series)\b"
    r".{0,120}\b(?:19\d{2}|20\d{2})\s*(?:[-–/]|to)\s*(?:19\d{2}|20\d{2})\b"
)
COLLECTION_KEYWORD_RE = re.compile(
    r"(?i)\b(?:all\s+parts|complete|collection|saga|trilogy|quadrilogy|duology|dilogy|anthology|franchise|movies|films|movie\s+pack|film\s+pack|pack|volume|volumes?|season|series)\b"
)
DATE_EVENT_RE = re.compile(r"\b\d{1,2}[._-]\d{1,2}[._-](?:19\d{2}|20\d{2})\b")
SPORTS_EVENT_RE = re.compile(
    r"(?i)\b(?:ufc|wwe|nxt|aew|bellator|nba|nfl|nhl|mlb|fifa|formula\s*1|f1|grand\s+prix|wrestlemania|raw|smackdown)\b"
)
KNOWN_ONE_WORD_TITLES = {
    "alive",
    "annie",
    "apex",
    "avatar",
    "coco",
    "dune",
    "grabbers",
    "hondo",
    "hybrid",
    "jfk",
    "legend",
    "ponyo",
    "psycho",
    "saw",
    "spawn",
    "thor",
    "troy",
}
EXPECTED_EDITION_OVERTRIM_REASONS = {
    "lost dash title continuation",
    "collapsed to franchise-only title",
    "implausibly short output",
}
FRANCHISE_ONLY_TITLES = {
    "aliens",
    "avatar",
    "dune",
    "f1",
    "lego",
    "pirates",
    "venom",
}


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9]+", str(value or "").lower()) if token]


def _meaningful_tokens(value: str) -> list[str]:
    result: list[str] = []
    for token in _tokens(value):
        canonical = _canonical_metadata_token(token)
        if not canonical or _is_standalone_year(canonical):
            continue
        if (
            _token_is_metadata(canonical)
            or _token_is_strong_metadata(canonical)
            or _token_is_localization_metadata(canonical)
            or _token_is_subtitle_metadata(canonical)
        ):
            continue
        result.append(canonical)
    return result


def _looks_like_genre_descriptor_phrase(value: str) -> bool:
    tokens = [_canonical_metadata_token(token) for token in _tokens(value)]
    tokens = [token for token in tokens if token]
    return bool(tokens) and all(token in GENRE_DESCRIPTOR_TOKENS for token in tokens)


def _year_is_range(value: str, match: re.Match[str]) -> bool:
    start = match.start()
    end = match.end()
    before = value[max(0, start - 2) : start]
    after = value[end : end + 8]
    if "-" in before or "/" in before or re.match(r"\s*[-–/]\s*(?:19\d{2}|20\d{2})", after):
        return True
    lowered = value.lower()
    if _is_collection_or_range(value) or DATE_EVENT_RE.search(value):
        return True
    return False


def _year_values(value: str) -> list[int]:
    result: list[int] = []
    for raw_year in YEAR_PATTERN.findall(value):
        year = int(raw_year)
        if year not in result:
            result.append(year)
    return result


def _is_collection_or_range(value: str) -> bool:
    prepared = _prepare_candidate_text(value)
    lowered = prepared.lower()
    if COLLECTION_RANGE_RE.search(prepared):
        return True
    if re.search(r"\((?:19\d{2}|20\d{2})\)\s*(?:&|\band\b)\s+.+\((?:19\d{2}|20\d{2})\)", prepared, re.IGNORECASE):
        return True
    years = _year_values(prepared)
    if len(years) < 2:
        return False
    if COLLECTION_KEYWORD_RE.search(prepared):
        return True
    if re.search(r"(?i)\b(?:james\s+bond|home\s+alone|star\s+wars)\b", prepared) and re.search(
        r"\b(?:19\d{2}|20\d{2})\s*[-–/]\s*(?:19\d{2}|20\d{2})\b",
        prepared,
    ):
        return True
    if len(years) >= 3 and len(re.findall(r"\((?:19\d{2}|20\d{2})\)", prepared)) >= 3:
        return True
    if re.search(r"\b\d+\s*,\s*\d+\b", prepared) and re.search(
        r"\b(?:19\d{2}|20\d{2})\s*[-–/]\s*(?:19\d{2}|20\d{2})\b",
        prepared,
    ):
        return True
    first_year_match = YEAR_PATTERN.search(prepared)
    title_region = prepared[: first_year_match.start()] if first_year_match else prepared
    if re.search(r"(?i)\b(?:and|&)\b", title_region):
        return True
    if re.search(r"(?i)\b(?:double|triple|two|three|quadruple)\s+(?:feature|bill|movie|film)\b", lowered):
        return True
    return False


def _actual_episode_identity_tokens(value: str) -> list[str]:
    result: list[str] = []
    for token in _ordered_context_tokens(value):
        canonical = _canonical_metadata_token(token)
        if not canonical:
            continue
        if re.fullmatch(r"(?:4|16|21)x(?:3|9)", canonical):
            continue
        if re.fullmatch(r"\d+x(?:264|265)", canonical):
            continue
        if re.fullmatch(r"\d+x\d", canonical):
            continue
        if canonical == "e0":
            continue
        if _looks_like_episode_identity_token(canonical):
            result.append(canonical)
    return result


def _is_episode_or_series_row(value: str) -> bool:
    return bool(_actual_episode_identity_tokens(value))


def _is_episode_date_range_review(value: str) -> bool:
    prepared = _prepare_candidate_text(value)
    if not re.search(r"\b(?:18\d{2}|19\d{2}|20\d{2})\s*[-–/]\s*(?:18\d{2}|19\d{2}|20\d{2})\b", prepared):
        return False
    tokens = _ordered_context_tokens(prepared)
    return any(
        _looks_like_episode_identity_token(token) or re.fullmatch(r"s\d{1,2}", token) is not None
        for token in tokens
    )


def _is_bare_season_pack_review(value: str) -> bool:
    prepared = _prepare_candidate_text(value)
    return re.search(r"\b(?:19\d{2}|20\d{2})[-\s]+s\d{1,2}\b", prepared, re.IGNORECASE) is not None


def _clear_release_year_failure(raw: str, parsed_year: object) -> bool:
    if parsed_year is not None or _is_collection_or_range(raw) or DATE_EVENT_RE.search(raw):
        return False
    prepared = _prepare_candidate_text(raw)
    for match in YEAR_PATTERN.finditer(prepared):
        if _year_is_range(prepared, match):
            continue
        prefix = prepared[: match.start()]
        suffix = prepared[match.end() :]
        prefix_tokens = _meaningful_tokens(prefix)
        if not prefix_tokens:
            continue
        suffix_tokens = suffix.split()
        metrics = _suffix_metadata_metrics(suffix_tokens)
        suffix_has_localization = any(
            _token_is_localization_metadata(_canonical_metadata_token(token))
            or _token_is_subtitle_metadata(_canonical_metadata_token(token))
            or _contains_compound_localization_token(token)
            for token in suffix_tokens
        )
        if metrics["strong_hits"] >= 1 or (suffix_has_localization and metrics["metadata_hits"] >= 1):
            return True
    return False


def _metadata_suffix_leak_categories(display: str) -> set[str]:
    tokens = [token for token in _classification_tokens(display) if token]
    suffix: list[str] = []
    for raw_token in reversed(tokens):
        canonical = _canonical_metadata_token(raw_token)
        if (
            _is_standalone_year(canonical)
            or _token_is_metadata(canonical)
            or _token_is_strong_metadata(canonical)
            or _token_is_localization_metadata(canonical)
            or _token_is_subtitle_metadata(canonical)
            or _contains_compound_localization_token(raw_token)
            or _contains_compound_metadata_token(raw_token)
        ):
            suffix.append(raw_token)
            continue
        break
    if len(suffix) < 2:
        return set()
    has_technical_anchor = any(
        _token_is_metadata(_canonical_metadata_token(token))
        or _token_is_strong_metadata(_canonical_metadata_token(token))
        or _token_is_subtitle_metadata(_canonical_metadata_token(token))
        or _contains_compound_metadata_token(token)
        or _contains_compound_localization_token(token)
        for token in suffix
    )
    if not has_technical_anchor:
        return set()
    categories: set[str] = set()
    for raw_token in suffix:
        canonical = _canonical_metadata_token(raw_token)
        if _token_is_metadata(canonical):
            categories.add("metadata token suffix chain")
        if _token_is_strong_metadata(canonical):
            categories.add("source/video/codec token")
        if _token_is_localization_metadata(canonical) or _contains_compound_localization_token(raw_token):
            categories.add("language token suffix chain")
        if _token_is_subtitle_metadata(canonical):
            categories.add("subtitle token suffix chain")
    return categories


def _bracket_failure_reason(content: str) -> str | None:
    cleaned = collapse_spaces(content).strip()
    if not cleaned:
        return None
    if cleaned.upper() == "REC" or cleaned == "18+":
        return None
    if re.fullmatch(r"(?:19\d{2}|20\d{2})(?:\s*[-–/]\s*(?:19\d{2}|20\d{2}))?", cleaned):
        return None
    if re.fullmatch(r"(?i)(?:19\d{2}|20\d{2})\s*[-–/]\s*[a-z][a-z .'-]{2,40}", cleaned):
        return None
    if re.fullmatch(r"(?i)[a-z][a-z .'-]{2,40}\s*[-–/]\s*(?:19\d{2}|20\d{2})", cleaned):
        return None
    if _looks_like_metadata_bracket_span(cleaned):
        return "bracket metadata"
    if _looks_like_release_group_span_content(cleaned):
        return "bracket release group"
    classification = _classify_segment(cleaned)
    if classification["kind"] == "metadata":
        return "bracket metadata"
    if _looks_like_edition_bracket_span(cleaned, classification):
        return "bracket edition remains"
    tokens = _classification_tokens(cleaned)
    if any(_token_is_strong_metadata(token) for token in tokens):
        return "bracket metadata"
    return None


def _metadata_leak_categories(display: str) -> list[str]:
    categories: list[str] = []
    tokens = _classification_tokens(display)
    suffix_categories = _metadata_suffix_leak_categories(display)
    if _contains_compound_metadata_token(display):
        categories.append("compound metadata token")
    categories.extend(sorted(suffix_categories))
    if _contains_compound_localization_token(display):
        categories.append("compound language token")
    if any(_looks_like_release_group_token(token) for token in display.split()):
        categories.append("release group")
    for match in BRACKET_GROUP_PATTERN.finditer(display):
        reason = _bracket_failure_reason(match.group(2))
        if reason:
            categories.append(reason)
    if (
        not categories
        and len(tokens) >= 2
        and all(
            _token_is_strong_metadata(_canonical_metadata_token(token))
            or _token_is_metadata(_canonical_metadata_token(token))
            for token in tokens
        )
    ):
        categories.append("source/video/codec token")
    return sorted(set(categories))


def _likely_title_region(raw: str) -> str:
    prepared = _prepare_candidate_text(raw)
    for match in YEAR_PATTERN.finditer(prepared):
        if _year_is_range(prepared, match):
            continue
        prefix = prepared[: match.start()].strip(" -")
        if prefix:
            return prefix
    return prepared


def _overtrim_reasons(raw: str, display: str, parsed: dict[str, object]) -> list[str]:
    reasons: list[str] = []
    display_clean = collapse_spaces(display)
    display_tokens = _meaningful_tokens(display_clean)
    likely_title = _likely_title_region(raw)
    likely_tokens = _meaningful_tokens(likely_title)
    display_key = " ".join(display_tokens)
    if display_clean.lower() in {"the", "a", "an"}:
        reasons.append("article-only output")
    if "display_title_lost_meaningful_number_token" in parsed.get("warnings", []):
        title_number_match = re.search(
            r"\b(?:Blade\s+Runner\s+2049|Wonder\s+Woman\s+1984|Argentina\s+1985|Love\s+Story\s+2050|Death\s+Race\s+2000|Equalizer\s+2000|Frankenstein\s+1970|Pastorale\s+1943)\b",
            _prepare_candidate_text(raw),
            re.IGNORECASE,
        )
        if title_number_match:
            title_number = re.search(r"\b\d{4}\b", title_number_match.group(0))
            if title_number and title_number.group(0) not in display_clean:
                reasons.append("lost meaningful number/roman token")
    if _actual_episode_identity_tokens(raw) and not _actual_episode_identity_tokens(display):
        reasons.append("lost episode identity")
    if " - " in likely_title and " - " not in display_clean:
        right = likely_title.split(" - ", 1)[1]
        right_tokens = _meaningful_tokens(right)
        right_context = _ordered_context_tokens(right)
        starts_as_year_or_metadata = bool(
            right_context
            and (
                _is_standalone_year(_canonical_metadata_token(right_context[0]))
                or _token_is_metadata(_canonical_metadata_token(right_context[0]))
                or _token_is_strong_metadata(_canonical_metadata_token(right_context[0]))
            )
        )
        if len(right_tokens) >= 2 and not starts_as_year_or_metadata and not _looks_like_genre_descriptor_phrase(right):
            reasons.append("lost dash title continuation")
    if display_key in FRANCHISE_ONLY_TITLES and len(likely_tokens) >= 3:
        reasons.append("collapsed to franchise-only title")
    if (
        len(likely_tokens) >= 4
        and len(display_tokens) < 2
        and display_key not in KNOWN_ONE_WORD_TITLES
        and not _looks_like_clean_single_word_title_after_metadata(raw, display, parsed)
    ):
        reasons.append("implausibly short output")
    return reasons


def _looks_like_clean_single_word_title_after_metadata(raw: str, display: str, parsed: dict[str, object]) -> bool:
    display_tokens = _meaningful_tokens(display)
    if len(display_tokens) != 1 or parsed.get("parsed_year") is None:
        return False
    raw_tokens = _ordered_context_tokens(raw)
    if any(
        _looks_like_episode_identity_token(token) or re.fullmatch(r"s\d{1,2}", token) is not None
        for token in raw_tokens
    ):
        return False
    likely_tokens = _meaningful_tokens(_likely_title_region(raw))
    if not likely_tokens or likely_tokens[0] != display_tokens[0]:
        return False
    warnings = set(str(warning) for warning in parsed.get("warnings") or [])
    if not {"standalone_release_year_cut", "metadata_suffix_removed"}.issubset(warnings):
        return False
    return re.search(
        r"(?i)\b(?:full[-\s]+movie|the[-\s]+movie|dubbed|dual[-\s]+audio|subtitles?|movie[-\s]+download|playlist|trailer|dvdrip|blu[- ]?ray|web[- ]?dl|webrip|hindi|tamil|malayalam|korean|english|bollywood|hollywood)\b",
        _prepare_candidate_text(raw),
    ) is not None


def _expected_edition_strip(raw: str, display: str, parsed: dict[str, object], overtrim: list[str]) -> bool:
    edition_markers = _segment_edition_markers(raw)
    edition_identity = str(parsed.get("edition_identity") or "")
    has_nonstandard_edition = bool(edition_markers) or edition_identity not in {"", "standard"}
    if not has_nonstandard_edition:
        return False
    if not overtrim:
        return False
    if not display.strip():
        return False
    if parsed.get("suspicious_output") or parsed.get("parse_confidence") == "low":
        return False
    if overtrim and not set(overtrim).issubset(EXPECTED_EDITION_OVERTRIM_REASONS):
        return False
    return True


def _expected_genre_descriptor_strip(raw: str, display: str, parsed: dict[str, object], overtrim: list[str]) -> bool:
    if not overtrim:
        return False
    if parsed.get("suspicious_output") or parsed.get("parse_confidence") == "low":
        return False
    display_tokens = _meaningful_tokens(display)
    if not display_tokens:
        return False
    display_key = " ".join(display_tokens)
    if len(display_tokens) == 1 and display_key not in KNOWN_ONE_WORD_TITLES:
        return False
    prepared = _prepare_candidate_text(raw)
    if " - " in prepared:
        for right_side in prepared.split(" - ")[1:]:
            right_prefix = YEAR_PATTERN.split(right_side, maxsplit=1)[0]
            if _looks_like_genre_descriptor_phrase(right_prefix):
                return True
    return re.search(
        r"(?i)\s+-\s+(?:sci[-\s]+fi|horror|comedy|drama|fantasy|thriller|action|western|war|romance)(?:\s+(?:horror|comedy|drama|fantasy|thriller|action|western|war|romance))*\s+(?:19\d{2}|20\d{2})\b",
        prepared,
    ) is not None


def _legacy_categories(raw: str, parsed: dict[str, object]) -> list[str]:
    categories: list[str] = []
    display = str(parsed.get("display_title") or "")
    metadata = _metadata_leak_categories(display)
    if metadata:
        categories.extend(f"legacy metadata leak: {name}" for name in metadata)
    if YEAR_PATTERN.search(raw) and parsed.get("parsed_year") is None:
        categories.append("legacy year extraction failed")
    if parsed.get("suspicious_output"):
        categories.append("legacy suspicious parser output")
    if parsed.get("parse_confidence") == "low":
        categories.append("legacy fallback/low confidence")
    overtrim = _overtrim_reasons(raw, display, parsed)
    if overtrim:
        categories.extend(f"legacy overtrim: {name}" for name in overtrim)
    return sorted(set(categories))


def classify(raw: str, parsed: dict[str, object]) -> tuple[str, list[str], str]:
    display = str(parsed.get("display_title") or "")
    if _is_episode_or_series_row(raw):
        return "EXPECTED_COLLECTION_OR_RANGE", ["series/episode identity pattern"], "series or episode row needs review"
    if _is_collection_or_range(raw):
        return "EXPECTED_COLLECTION_OR_RANGE", ["collection/year-range pattern"], "collection or date range needs review"
    if _is_episode_date_range_review(raw):
        return "EXPECTED_COLLECTION_OR_RANGE", ["episode/date-range pattern"], "series or date range needs review"
    if _is_bare_season_pack_review(raw):
        return "EXPECTED_COLLECTION_OR_RANGE", ["bare season-pack pattern"], "series season pack needs review"
    if DATE_EVENT_RE.search(raw) or SPORTS_EVENT_RE.search(raw):
        return "EXPECTED_EVENT_OR_SPORTS", ["event/date/sports pattern"], "event naming is not a movie-title scrub failure"
    metadata = _metadata_leak_categories(display)
    if any(reason.startswith("bracket ") for reason in metadata):
        return "TRUE_FAIL_BRACKET_SPAN", metadata, "visible bracket metadata remains"
    if _clear_release_year_failure(raw, parsed.get("parsed_year")):
        return "TRUE_FAIL_RELEASE_YEAR_GRAMMAR", ["clear release year was not parsed"], "clear title-year-metadata suffix pattern"
    overtrim = _overtrim_reasons(raw, display, parsed)
    if _expected_edition_strip(raw, display, parsed, overtrim):
        return "EXPECTED_EDITION_STRIP", ["edition marker intentionally stripped"], "edition/cut marker collapsed to base title"
    if _expected_genre_descriptor_strip(raw, display, parsed, overtrim):
        return "EXPECTED_GENRE_DESCRIPTOR_STRIP", ["genre descriptor intentionally stripped"], "genre descriptor collapsed to base title"
    if "lost dash title continuation" in overtrim:
        return "TRUE_FAIL_DASH_TITLE", overtrim, "display lost real dash-title continuation"
    if metadata:
        return "TRUE_FAIL_METADATA_SUFFIX", metadata, "visible metadata suffix remains"
    if overtrim:
        return "TRUE_FAIL_OVERTRIM_REAL", overtrim, "display lost real title identity"
    legacy = _legacy_categories(raw, parsed)
    if legacy:
        return "FALSE_POSITIVE_CLEAN_OUTPUT", legacy, "legacy heuristic flagged warning/shape but display is clean enough"
    if parsed.get("suspicious_output") or parsed.get("parse_confidence") == "low":
        return "NEEDS_HUMAN_REVIEW", ["low confidence or suspicious parser output"], "not automatically counted as true failure"
    return "PASS", [], "clean output"


def _row(line_number: int, raw: str, parsed: dict[str, object], classification: str, reasons: list[str], explanation: str) -> dict[str, object]:
    return {
        "line_number": line_number,
        "raw": raw,
        "display_title": parsed.get("display_title"),
        "base_title": parsed.get("base_title"),
        "poster_match_title": parsed.get("poster_match_title"),
        "poster_match_year": parsed.get("poster_match_year"),
        "parsed_year": parsed.get("parsed_year"),
        "title_source": parsed.get("title_source"),
        "parse_confidence": parsed.get("parse_confidence"),
        "edition_identity": parsed.get("edition_identity"),
        "suspicious_output": parsed.get("suspicious_output"),
        "warnings": parsed.get("warnings"),
        "legacy_categories": _legacy_categories(raw, parsed),
        "classification": classification,
        "classification_reasons": reasons,
        "classification_explanation": explanation,
    }


def _write_text_report(path: Path, title: str, rows: list[dict[str, object]], limit: int | None = None) -> None:
    lines = [title, f"Rows: {len(rows)}", ""]
    selected = rows if limit is None else rows[:limit]
    for index, row in enumerate(selected, 1):
        lines.extend(
            [
                f"Failure #{index}",
                f"Input line: {row['line_number']}",
                f"Raw: {row['raw']}",
                f"Display title: {row['display_title']}",
                f"Base title: {row['base_title']}",
                f"Poster match: {row['poster_match_title']} ({row['poster_match_year']})",
                f"Parsed year: {row['parsed_year']}",
                f"Classification: {row['classification']}",
                f"Reasons: {', '.join(row['classification_reasons'])}",
                f"Warnings: {', '.join(row['warnings'] or [])}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _manual_buckets(rows_by_class: dict[str, list[dict[str, object]]]) -> str:
    order = [
        "TRUE_FAIL_BRACKET_SPAN",
        "TRUE_FAIL_RELEASE_YEAR_GRAMMAR",
        "TRUE_FAIL_METADATA_SUFFIX",
        "TRUE_FAIL_DASH_TITLE",
        "TRUE_FAIL_OVERTRIM_REAL",
        "EXPECTED_GENRE_DESCRIPTOR_STRIP",
        "EXPECTED_EDITION_STRIP",
        "EXPECTED_COLLECTION_OR_RANGE",
        "EXPECTED_EVENT_OR_SPORTS",
        "FALSE_POSITIVE_CLEAN_OUTPUT",
        "NEEDS_HUMAN_REVIEW",
    ]
    guidance = {
        "TRUE_FAIL_BRACKET_SPAN": "Future handling: improve balanced bracket-span removal only where bracket content is technical metadata.",
        "TRUE_FAIL_RELEASE_YEAR_GRAMMAR": "Future handling: extend release-year grammar only when a clear title/year/technical-suffix pattern exists.",
        "TRUE_FAIL_DASH_TITLE": "Future handling: protect real title continuations after dashes; do not collapse franchise subtitles.",
        "TRUE_FAIL_METADATA_SUFFIX": "Future handling: add suffix rules for visible technical, language, source, and release-group chains.",
        "TRUE_FAIL_OVERTRIM_REAL": "Future handling: inspect manually before changing parser; over-trim fixes are high risk.",
        "EXPECTED_GENRE_DESCRIPTOR_STRIP": "Future handling: keep as expected unless product requirements decide genre descriptors should be preserved in display titles.",
        "EXPECTED_EDITION_STRIP": "Future handling: keep as expected unless product requirements decide editions/cuts should become separate age-group keys.",
        "EXPECTED_COLLECTION_OR_RANGE": "Future handling: keep in review buckets; collection/range titles are intentionally not aggressively scrubbed.",
        "EXPECTED_EVENT_OR_SPORTS": "Future handling: keep separate from movie-title scrub failures unless product scope changes.",
        "FALSE_POSITIVE_CLEAN_OUTPUT": "Future handling: classifier should ignore these; parser output is acceptable.",
        "NEEDS_HUMAN_REVIEW": "Future handling: inspect before deciding whether parser or source data should change.",
    }
    lines = [
        "# Title Scrubber v1.0.0 Manual Review Buckets",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for name in order:
        bucket = rows_by_class.get(name, [])
        lines.extend([f"## {name}", "", f"Count: {len(bucket)}", "", guidance[name], ""])
        for row in bucket[:30]:
            lines.extend(
                [
                    f"- line {row['line_number']}: `{row['raw']}`",
                    f"  - output: `{row['display_title']}` / year `{row['parsed_year']}`",
                    f"  - reasons: {', '.join(row['classification_reasons']) or 'n/a'}",
                ]
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Title Scrubber v1.0.0 benchmark classifier.")
    parser.add_argument(
        "--input",
        default=str(REPO_ROOT / "LLM" / "data" / "75k_obfuscated_file_names.txt"),
        help="Path to the newline-delimited movie title sample file.",
    )
    args = parser.parse_args()
    sample_path = Path(args.input)
    if not sample_path.is_absolute():
        sample_path = REPO_ROOT / sample_path

    lines = [
        line.strip()
        for line in sample_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    rows: list[dict[str, object]] = []
    true_failures: list[dict[str, object]] = []
    legacy_failures: list[dict[str, object]] = []
    rows_by_class: dict[str, list[dict[str, object]]] = defaultdict(list)
    classification_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    legacy_count = 0
    for line_number, raw in enumerate(lines, 1):
        parsed = parse_media_title(title=None, original_filename=raw, year=None)
        classification, reasons, explanation = classify(raw, parsed)
        row = _row(line_number, raw, parsed, classification, reasons, explanation)
        rows.append(row)
        rows_by_class[classification].append(row)
        classification_counts[classification] += 1
        reason_counts.update(f"{classification}: {reason}" for reason in reasons)
        if row["legacy_categories"]:
            legacy_count += 1
            legacy_failures.append(row)
        if classification.startswith("TRUE_FAIL"):
            true_failures.append(row)

    phase20_summary = PHASE20_BASELINE_SUMMARY
    if PHASE20_REPORT_PATH.exists():
        phase20_summary = json.loads(PHASE20_REPORT_PATH.read_text(encoding="utf-8")).get("summary", {})

    true_class_names = [
        "TRUE_FAIL_BRACKET_SPAN",
        "TRUE_FAIL_RELEASE_YEAR_GRAMMAR",
        "TRUE_FAIL_DASH_TITLE",
        "TRUE_FAIL_METADATA_SUFFIX",
        "TRUE_FAIL_OVERTRIM_REAL",
    ]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "sample_file": str(sample_path),
        "parser_version": TITLE_PARSER_VERSION,
        "total_lines": len(lines),
        "legacy_suspected_failures": legacy_count,
        "true_failures": len(true_failures),
        "classification_counts": dict(classification_counts.most_common()),
        "top_true_failure_patterns": {
            key: value for key, value in reason_counts.most_common(40) if key.startswith("TRUE_FAIL")
        },
        "phase20_comparison": {
            "phase20_true_failures": phase20_summary.get("true_failures"),
            "v1_true_failures": len(true_failures),
            "true_failure_delta": (
                len(true_failures) - int(phase20_summary.get("true_failures"))
                if phase20_summary.get("true_failures") is not None
                else None
            ),
            "phase20_classification_counts": phase20_summary.get("classification_counts"),
            "phase20_top_true_failure_patterns": phase20_summary.get("top_true_failure_patterns"),
        },
        "true_failure_class_names": true_class_names,
        "output_files": {
            "report": str(REPORT_PATH),
            "classified_report": str(CLASSIFIED_REPORT_PATH),
            "summary": str(SUMMARY_PATH),
            "classified_summary": str(CLASSIFIED_SUMMARY_PATH),
            "failed_sample": str(FAILED_SAMPLE_PATH),
            "all_failed_text": str(ALL_FAILED_TEXT_PATH),
            "manual_buckets": str(MANUAL_BUCKETS_PATH),
        },
    }

    payload = {"summary": summary, "rows": rows, "true_failures": true_failures}
    REPORT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    CLASSIFIED_REPORT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    summary_lines = [
        "Elvern title scrubbing diagnostic - Title Scrubber v1.0.0",
        f"Generated: {summary['generated_at']}",
        f"Sample file: {summary['sample_file']}",
        f"Parser version: {summary['parser_version']}",
        f"Total lines: {summary['total_lines']}",
        f"Legacy suspected failures: {summary['legacy_suspected_failures']}",
        f"TRUE failures: {summary['true_failures']}",
        f"Phase 2.0 TRUE failures: {summary['phase20_comparison']['phase20_true_failures']}",
        f"TRUE failure delta vs 2.0: {summary['phase20_comparison']['true_failure_delta']}",
        "",
        "Classification counts:",
    ]
    summary_lines.extend(f"- {name}: {count}" for name, count in classification_counts.most_common())
    summary_lines.extend(["", "Top TRUE failure patterns:"])
    summary_lines.extend(
        f"- {name}: {count}"
        for name, count in reason_counts.most_common(40)
        if name.startswith("TRUE_FAIL")
    )
    summary_lines.extend(["", "Output files:"])
    summary_lines.extend(f"- {name}: {path}" for name, path in summary["output_files"].items())
    summary_text = "\n".join(summary_lines) + "\n"
    SUMMARY_PATH.write_text(summary_text, encoding="utf-8")
    CLASSIFIED_SUMMARY_PATH.write_text(summary_text, encoding="utf-8")
    _write_text_report(FAILED_SAMPLE_PATH, "Elvern title scrubbing diagnostic - Title Scrubber v1.0.0 failed sample", true_failures, limit=500)
    _write_text_report(ALL_FAILED_TEXT_PATH, "Elvern title scrubbing diagnostic - Title Scrubber v1.0.0 ALL failed results", true_failures)
    MANUAL_BUCKETS_PATH.write_text(_manual_buckets(rows_by_class), encoding="utf-8")
    print(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
