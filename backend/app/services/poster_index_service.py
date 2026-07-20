from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha1, sha256
import logging
from pathlib import Path
import re
from threading import RLock
import time
from types import MappingProxyType
from typing import Mapping

from ..config import Settings
from ..db import get_connection, utcnow_iso
from .library_revision_mutation_service import bump_library_revision_layers
from .title_normalization import normalize_poster_title_key


LOGGER = logging.getLogger(__name__)
POSTER_INDEX_ALGORITHM_VERSION = "poster-index-v1"
POSTER_INDEX_MAX_ROOTS = 4
POSTER_INDEX_ENTRY_RECHECK_SECONDS = 30.0
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
    ctime_ns: int
    inode: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class PosterIndexSnapshot:
    root: Path
    root_fingerprint: tuple[object, ...]
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
_SNAPSHOT_VALIDATED_AT: dict[str, float] = {}
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


def _snapshot_content_hash(entries: tuple[PosterIndexEntry, ...]) -> str:
    digest = sha256()
    for entry in entries:
        name_bytes = entry.name.encode("utf-8")
        digest.update(len(name_bytes).to_bytes(8, "big"))
        digest.update(name_bytes)
        for value in (entry.size, entry.mtime_ns, entry.ctime_ns, entry.inode):
            encoded = str(int(value)).encode("ascii")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _empty_snapshot(root: Path, root_fingerprint: tuple[object, ...]) -> PosterIndexSnapshot:
    entries: tuple[PosterIndexEntry, ...] = ()
    return PosterIndexSnapshot(
        root=root,
        root_fingerprint=root_fingerprint,
        fingerprint=("content", _snapshot_content_hash(entries)),
        entries=entries,
        exact_filename_map=MappingProxyType({}),
        normalized_yearful_map=MappingProxyType({}),
        yearless_map=MappingProxyType({}),
        year_entries_map=MappingProxyType({}),
    )


def _build_poster_index_snapshot(
    root: Path,
    root_fingerprint: tuple[object, ...],
) -> PosterIndexSnapshot:
    _METRICS["build_count"] += 1
    if root_fingerprint[0] == "missing":
        return _empty_snapshot(root, root_fingerprint)
    if not root.is_dir():
        return _empty_snapshot(root, root_fingerprint)

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
            ctime_ns=int(stat.st_ctime_ns),
            inode=int(stat.st_ino),
            ordinal=len(entries),
        )
        entries.append(entry)
        exact_filename_map[candidate.name] = entry
        if entry.title_key and entry.year is not None:
            normalized_yearful_map.setdefault(f"{entry.title_key}|{entry.year}", []).append(entry)
            year_entries_map.setdefault(entry.year, []).append(entry)
        elif entry.title_key:
            yearless_map.setdefault(entry.title_key, []).append(entry)

    frozen_entries = tuple(entries)
    return PosterIndexSnapshot(
        root=root,
        root_fingerprint=root_fingerprint,
        fingerprint=("content", _snapshot_content_hash(frozen_entries)),
        entries=frozen_entries,
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


def _cached_entries_unchanged(snapshot: PosterIndexSnapshot) -> bool:
    for entry in snapshot.entries:
        try:
            stat = entry.path.stat()
        except OSError:
            return False
        if (
            int(stat.st_size) != entry.size
            or int(stat.st_mtime_ns) != entry.mtime_ns
            or int(stat.st_ctime_ns) != entry.ctime_ns
            or int(stat.st_ino) != entry.inode
        ):
            return False
    return True


def _publish_poster_fingerprint(
    settings: Settings,
    *,
    root: Path,
    snapshot: PosterIndexSnapshot,
) -> None:
    if not settings.library_revision_enabled:
        return
    root_identity_hash = sha256(str(root).encode("utf-8")).hexdigest()
    fingerprint_hash = str(snapshot.fingerprint[1])
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT fingerprint_hash
            FROM poster_index_fingerprints
            WHERE root_identity_hash = ?
            """,
            (root_identity_hash,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO poster_index_fingerprints (
                    root_identity_hash, fingerprint_hash, updated_at
                ) VALUES (?, ?, ?)
                """,
                (root_identity_hash, fingerprint_hash, utcnow_iso()),
            )
        elif str(row["fingerprint_hash"]) != fingerprint_hash:
            connection.execute(
                """
                UPDATE poster_index_fingerprints
                SET fingerprint_hash = ?, updated_at = ?
                WHERE root_identity_hash = ?
                """,
                (fingerprint_hash, utcnow_iso(), root_identity_hash),
            )
            bump_library_revision_layers(
                settings,
                connection,
                global_layers=("catalog",),
            )
        connection.commit()


def get_poster_index_snapshot(
    root: Path,
    *,
    settings: Settings | None = None,
) -> PosterIndexSnapshot | None:
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
        if cached is not None and cached.root_fingerprint == fingerprint:
            last_validated = _SNAPSHOT_VALIDATED_AT.get(cache_key, 0.0)
            if (
                time.monotonic() - last_validated < POSTER_INDEX_ENTRY_RECHECK_SECONDS
                or _cached_entries_unchanged(cached)
            ):
                _SNAPSHOT_VALIDATED_AT[cache_key] = time.monotonic()
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
        _SNAPSHOT_VALIDATED_AT[cache_key] = time.monotonic()
        _SNAPSHOTS.move_to_end(cache_key)
        while len(_SNAPSHOTS) > POSTER_INDEX_MAX_ROOTS:
            evicted_key, _ = _SNAPSHOTS.popitem(last=False)
            _SNAPSHOT_VALIDATED_AT.pop(evicted_key, None)
    if settings is not None:
        _publish_poster_fingerprint(settings, root=resolved_root, snapshot=snapshot)
    return snapshot


def invalidate_poster_indexes(root: Path | None = None) -> None:
    with _CACHE_LOCK:
        if root is None:
            _SNAPSHOTS.clear()
            _SNAPSHOT_VALIDATED_AT.clear()
            _FAILED_FINGERPRINTS.clear()
            for key in _METRICS:
                _METRICS[key] = 0
            return
        cache_key = str(_resolved_root(root))
        _SNAPSHOTS.pop(cache_key, None)
        _SNAPSHOT_VALIDATED_AT.pop(cache_key, None)


def get_poster_index_metrics() -> dict[str, int]:
    with _CACHE_LOCK:
        return {
            **{key: int(value) for key, value in _METRICS.items()},
            "cached_root_count": len(_SNAPSHOTS),
            "failed_fingerprint_count": len(_FAILED_FINGERPRINTS),
        }
