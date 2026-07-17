from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha1
import logging
from pathlib import Path
import re
from threading import RLock
from types import MappingProxyType
from typing import Mapping

from .title_normalization import normalize_poster_title_key


LOGGER = logging.getLogger(__name__)
POSTER_INDEX_ALGORITHM_VERSION = "poster-index-v1"
POSTER_INDEX_MAX_ROOTS = 4
_SUPPORTED_SUFFIXES = {".jpg", ".png"}
_YEARFUL_STEM_PATTERN = re.compile(r"(.+)\s+\((\d{4})\)")


@dataclass(frozen=True, slots=True)
class PosterIndexEntry:
    path: Path
    name: str
    stem: str
    suffix: str
    title_key: str | None
    year: int | None
    size: int
    mtime_ns: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class PosterIndexSnapshot:
    root: Path
    fingerprint: tuple[object, ...]
    entries: tuple[PosterIndexEntry, ...]
    exact_filename_map: Mapping[str, PosterIndexEntry]
    normalized_yearful_map: Mapping[str, tuple[PosterIndexEntry, ...]]
    yearless_map: Mapping[str, tuple[PosterIndexEntry, ...]]
    year_entries_map: Mapping[int, tuple[PosterIndexEntry, ...]]
    algorithm_version: str = POSTER_INDEX_ALGORITHM_VERSION

    @property
    def entry_count(self) -> int:
        return len(self.entries)


_CACHE_LOCK = RLock()
_SNAPSHOTS: OrderedDict[str, PosterIndexSnapshot] = OrderedDict()
_FAILED_FINGERPRINTS: set[tuple[object, ...]] = set()
_METRICS = {
    "build_count": 0,
    "directory_iteration_count": 0,
    "entry_stat_count": 0,
    "cache_hit_count": 0,
    "build_failure_count": 0,
}


def _resolved_root(root: Path) -> Path:
    return Path(root).expanduser().resolve(strict=False)


def _root_fingerprint(root: Path) -> tuple[object, ...]:
    resolved_root = _resolved_root(root)
    try:
        stat = resolved_root.stat()
    except FileNotFoundError:
        return ("missing", str(resolved_root))
    return (
        "present",
        str(resolved_root),
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


def _is_temporary_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered.startswith(".")
        or lowered.endswith("~")
        or ".tmp." in lowered
        or ".part." in lowered
    )


def _freeze_entry_groups(
    groups: dict[object, list[PosterIndexEntry]],
) -> Mapping[object, tuple[PosterIndexEntry, ...]]:
    return MappingProxyType({key: tuple(values) for key, values in groups.items()})


def _empty_snapshot(root: Path, fingerprint: tuple[object, ...]) -> PosterIndexSnapshot:
    return PosterIndexSnapshot(
        root=root,
        fingerprint=fingerprint,
        entries=(),
        exact_filename_map=MappingProxyType({}),
        normalized_yearful_map=MappingProxyType({}),
        yearless_map=MappingProxyType({}),
        year_entries_map=MappingProxyType({}),
    )


def _build_poster_index_snapshot(
    root: Path,
    fingerprint: tuple[object, ...],
) -> PosterIndexSnapshot:
    _METRICS["build_count"] += 1
    if fingerprint[0] == "missing":
        return _empty_snapshot(root, fingerprint)
    if not root.is_dir():
        return _empty_snapshot(root, fingerprint)

    _METRICS["directory_iteration_count"] += 1
    candidates = sorted(root.iterdir(), key=lambda candidate: candidate.name.lower())
    entries: list[PosterIndexEntry] = []
    exact_filename_map: dict[str, PosterIndexEntry] = {}
    normalized_yearful_map: dict[str, list[PosterIndexEntry]] = {}
    yearless_map: dict[str, list[PosterIndexEntry]] = {}
    year_entries_map: dict[int, list[PosterIndexEntry]] = {}

    for candidate in candidates:
        if candidate.suffix.lower() not in _SUPPORTED_SUFFIXES or _is_temporary_name(candidate.name):
            continue
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(root)
            stat = resolved_candidate.stat()
            _METRICS["entry_stat_count"] += 1
        except (OSError, ValueError):
            continue

        title_key: str | None = None
        year: int | None = None
        yearful_match = _YEARFUL_STEM_PATTERN.fullmatch(candidate.stem)
        if yearful_match:
            title_key = normalize_poster_title_key(yearful_match.group(1))
            year = int(yearful_match.group(2))
        else:
            title_key = normalize_poster_title_key(candidate.stem)
        entry = PosterIndexEntry(
            path=resolved_candidate,
            name=candidate.name,
            stem=candidate.stem,
            suffix=candidate.suffix.lower(),
            title_key=title_key or None,
            year=year,
            size=int(stat.st_size),
            mtime_ns=int(stat.st_mtime_ns),
            ordinal=len(entries),
        )
        entries.append(entry)
        exact_filename_map[candidate.name] = entry
        if entry.title_key and entry.year is not None:
            normalized_yearful_map.setdefault(f"{entry.title_key}|{entry.year}", []).append(entry)
            year_entries_map.setdefault(entry.year, []).append(entry)
        elif entry.title_key:
            yearless_map.setdefault(entry.title_key, []).append(entry)

    return PosterIndexSnapshot(
        root=root,
        fingerprint=fingerprint,
        entries=tuple(entries),
        exact_filename_map=MappingProxyType(exact_filename_map),
        normalized_yearful_map=_freeze_entry_groups(normalized_yearful_map),
        yearless_map=_freeze_entry_groups(yearless_map),
        year_entries_map=_freeze_entry_groups(year_entries_map),
    )


def _warn_build_failure_once(fingerprint: tuple[object, ...]) -> None:
    if fingerprint in _FAILED_FINGERPRINTS:
        return
    _FAILED_FINGERPRINTS.add(fingerprint)
    root_identity = sha1(str(fingerprint[1]).encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    LOGGER.warning("Poster index build failed; using safe legacy lookup (root_id=%s)", root_identity)


def get_poster_index_snapshot(root: Path) -> PosterIndexSnapshot | None:
    resolved_root = _resolved_root(root)
    cache_key = str(resolved_root)
    with _CACHE_LOCK:
        try:
            fingerprint = _root_fingerprint(resolved_root)
        except OSError:
            fingerprint = ("unreadable", cache_key)
            _METRICS["build_failure_count"] += 1
            _warn_build_failure_once(fingerprint)
            return None

        cached = _SNAPSHOTS.get(cache_key)
        if cached is not None and cached.fingerprint == fingerprint:
            _SNAPSHOTS.move_to_end(cache_key)
            _METRICS["cache_hit_count"] += 1
            return cached
        if fingerprint in _FAILED_FINGERPRINTS:
            _METRICS["cache_hit_count"] += 1
            return None
        try:
            snapshot = _build_poster_index_snapshot(resolved_root, fingerprint)
        except Exception:
            _METRICS["build_failure_count"] += 1
            _warn_build_failure_once(fingerprint)
            return None
        _SNAPSHOTS[cache_key] = snapshot
        _SNAPSHOTS.move_to_end(cache_key)
        while len(_SNAPSHOTS) > POSTER_INDEX_MAX_ROOTS:
            _SNAPSHOTS.popitem(last=False)
        return snapshot


def invalidate_poster_indexes(root: Path | None = None) -> None:
    with _CACHE_LOCK:
        if root is None:
            _SNAPSHOTS.clear()
            _FAILED_FINGERPRINTS.clear()
            for key in _METRICS:
                _METRICS[key] = 0
            return
        _SNAPSHOTS.pop(str(_resolved_root(root)), None)


def get_poster_index_metrics() -> dict[str, int]:
    with _CACHE_LOCK:
        return {
            **{key: int(value) for key, value in _METRICS.items()},
            "cached_root_count": len(_SNAPSHOTS),
            "failed_fingerprint_count": len(_FAILED_FINGERPRINTS),
        }
