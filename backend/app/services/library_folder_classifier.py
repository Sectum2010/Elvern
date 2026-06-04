from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
from pathlib import Path
import re


logger = logging.getLogger(__name__)

FOLDER_CATEGORY_SUFFIXES = {
    "M": "movies",
    "TV": "tv",
    "AN": "anime",
    "C": "cartoon",
}
FOLDER_ROLE_SUFFIXES = {
    "L": "list",
    "S": "single",
}
FOLDER_EXCLUDE_SUFFIX = "X"
RECOGNIZED_FOLDER_SUFFIXES = frozenset(
    [*FOLDER_CATEGORY_SUFFIXES, *FOLDER_ROLE_SUFFIXES, FOLDER_EXCLUDE_SUFFIX]
)
SIDECAR_HELPER_EXTENSIONS = {
    ".ass",
    ".chapters",
    ".gif",
    ".idx",
    ".jpeg",
    ".jpg",
    ".json",
    ".nfo",
    ".png",
    ".smi",
    ".srt",
    ".ssa",
    ".sub",
    ".sup",
    ".txt",
    ".vtt",
    ".webp",
    ".xml",
}

_TRAILING_SUFFIX_TOKEN_RE = re.compile(r"\s+-([A-Z]{1,3})\s*$")


@dataclass(frozen=True, slots=True)
class ParsedFolderSuffixes:
    original_name: str
    display_name: str
    suffix_tokens: tuple[str, ...]
    recognized_suffixes: tuple[str, ...]
    unknown_suffixes: tuple[str, ...]

    @property
    def excluded(self) -> bool:
        return FOLDER_EXCLUDE_SUFFIX in self.recognized_suffixes

    @property
    def explicit_category(self) -> str | None:
        categories = [
            FOLDER_CATEGORY_SUFFIXES[token]
            for token in self.recognized_suffixes
            if token in FOLDER_CATEGORY_SUFFIXES
        ]
        return categories[-1] if categories else None

    @property
    def explicit_role(self) -> str | None:
        if "L" in self.recognized_suffixes:
            return "list"
        if "S" in self.recognized_suffixes:
            return "single"
        return None


@dataclass(frozen=True, slots=True)
class FolderScanMetadata:
    reference_root: Path
    folder_path: Path
    folder_display_name: str
    category: str | None = None
    category_path: Path | None = None
    category_display_name: str | None = None
    role: str = "legacy"
    series_folder_key: str | None = None
    series_folder_name: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredMediaFile:
    path: Path
    metadata: FolderScanMetadata


@dataclass(slots=True)
class LibraryFolderDiscovery:
    files: list[DiscoveredMediaFile] = field(default_factory=list)
    warnings: list[dict[str, object]] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    category_roots: dict[str, list[str]] = field(default_factory=lambda: {
        "movies": [],
        "tv": [],
        "cartoon": [],
        "anime": [],
    })


def parse_folder_suffixes(folder_name: str) -> ParsedFolderSuffixes:
    working_name = str(folder_name or "").rstrip()
    suffix_tokens_reversed: list[str] = []
    while True:
        match = _TRAILING_SUFFIX_TOKEN_RE.search(working_name)
        if match is None:
            break
        suffix_tokens_reversed.append(match.group(1))
        working_name = working_name[: match.start()].rstrip()

    suffix_tokens = tuple(reversed(suffix_tokens_reversed))
    recognized_suffixes = tuple(
        token for token in suffix_tokens if token in RECOGNIZED_FOLDER_SUFFIXES
    )
    unknown_suffixes = tuple(
        token for token in suffix_tokens if token not in RECOGNIZED_FOLDER_SUFFIXES
    )
    display_name = working_name.strip()
    if unknown_suffixes:
        display_name = " ".join(
            part
            for part in [
                display_name,
                " ".join(f"-{token}" for token in unknown_suffixes),
            ]
            if part
        )
    if not display_name:
        display_name = str(folder_name or "").strip() or "Untitled folder"
    return ParsedFolderSuffixes(
        original_name=str(folder_name or ""),
        display_name=display_name,
        suffix_tokens=suffix_tokens,
        recognized_suffixes=recognized_suffixes,
        unknown_suffixes=unknown_suffixes,
    )


def path_is_same_or_inside(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _folder_key(path: Path) -> str:
    encoded = str(path.resolve()).encode("utf-8", errors="surrogatepass")
    return f"local-folder:{hashlib.sha1(encoded).hexdigest()[:16]}"


def _is_video_file(path: Path, allowed_video_extensions: set[str]) -> bool:
    return path.is_file() and path.suffix.lower() in allowed_video_extensions


def _is_sidecar_or_helper_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("."):
        return True
    return path.suffix.lower() in SIDECAR_HELPER_EXTENSIONS


def _classify_folder_role(
    *,
    parsed: ParsedFolderSuffixes,
    explicit_category: str | None,
    entries: list[Path],
    allowed_video_extensions: set[str],
    warnings: list[dict[str, object]],
    folder_path: Path,
) -> str:
    explicit_role = parsed.explicit_role
    video_entries = [
        entry for entry in entries if _is_video_file(entry, allowed_video_extensions)
    ]
    if explicit_role == "single" and len(video_entries) > 1:
        warnings.append(
            {
                "code": "explicit_single_folder_has_multiple_videos",
                "path": str(folder_path),
                "video_count": len(video_entries),
            }
        )
    if explicit_role:
        return explicit_role
    if explicit_category:
        return "category"
    if len(video_entries) == 1 and all(
        _is_video_file(entry, allowed_video_extensions)
        or _is_sidecar_or_helper_file(entry)
        for entry in entries
    ):
        return "smart_single"
    return "legacy"


def discover_library_folders(
    reference_roots: list[Path],
    *,
    allowed_video_extensions: set[str],
    poster_reference_path: Path | None = None,
) -> LibraryFolderDiscovery:
    discovery = LibraryFolderDiscovery()
    normalized_roots: list[Path] = []
    seen_roots: set[str] = set()
    for root in reference_roots:
        resolved_root = root.expanduser().resolve()
        root_key = str(resolved_root)
        if root_key in seen_roots:
            continue
        normalized_roots.append(resolved_root)
        seen_roots.add(root_key)

    for root in normalized_roots:
        if not root.exists() or not root.is_dir():
            discovery.warnings.append(
                {
                    "code": "library_reference_location_unavailable",
                    "path": str(root),
                }
            )
            continue
        _walk_library_folder(
            root,
            reference_root=root,
            allowed_video_extensions=allowed_video_extensions,
            poster_reference_path=poster_reference_path,
            inherited_category=None,
            inherited_category_path=None,
            inherited_category_display_name=None,
            inherited_series_folder_key=None,
            inherited_series_folder_name=None,
            discovery=discovery,
        )
    return discovery


def _walk_library_folder(
    folder_path: Path,
    *,
    reference_root: Path,
    allowed_video_extensions: set[str],
    poster_reference_path: Path | None,
    inherited_category: str | None,
    inherited_category_path: Path | None,
    inherited_category_display_name: str | None,
    inherited_series_folder_key: str | None,
    inherited_series_folder_name: str | None,
    discovery: LibraryFolderDiscovery,
) -> None:
    resolved_folder = folder_path.resolve()
    if poster_reference_path is not None and path_is_same_or_inside(resolved_folder, poster_reference_path):
        discovery.excluded_paths.append(str(resolved_folder))
        return

    parsed = parse_folder_suffixes(resolved_folder.name)
    if parsed.excluded:
        discovery.excluded_paths.append(str(resolved_folder))
        return

    try:
        entries = sorted(resolved_folder.iterdir(), key=lambda entry: entry.name.lower())
    except OSError as exc:
        logger.warning("Skipping unreadable library folder %s: %s", resolved_folder, exc)
        discovery.warnings.append(
            {
                "code": "library_folder_unreadable",
                "path": str(resolved_folder),
            }
        )
        return

    explicit_category = parsed.explicit_category
    category = explicit_category or inherited_category
    category_path = inherited_category_path
    category_display_name = inherited_category_display_name
    if explicit_category:
        category_path = resolved_folder
        category_display_name = parsed.display_name
        if str(resolved_folder) not in discovery.category_roots.setdefault(explicit_category, []):
            discovery.category_roots[explicit_category].append(str(resolved_folder))

    role = _classify_folder_role(
        parsed=parsed,
        explicit_category=explicit_category,
        entries=entries,
        allowed_video_extensions=allowed_video_extensions,
        warnings=discovery.warnings,
        folder_path=resolved_folder,
    )
    series_folder_key = inherited_series_folder_key
    series_folder_name = inherited_series_folder_name
    if role == "list":
        series_folder_key = _folder_key(resolved_folder)
        series_folder_name = parsed.display_name

    metadata = FolderScanMetadata(
        reference_root=reference_root,
        folder_path=resolved_folder,
        folder_display_name=parsed.display_name,
        category=category,
        category_path=category_path,
        category_display_name=category_display_name,
        role=role,
        series_folder_key=series_folder_key,
        series_folder_name=series_folder_name,
    )
    for entry in entries:
        if _is_video_file(entry, allowed_video_extensions):
            discovery.files.append(
                DiscoveredMediaFile(
                    path=entry.resolve(),
                    metadata=metadata,
                )
            )

    for entry in entries:
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue
        _walk_library_folder(
            entry,
            reference_root=reference_root,
            allowed_video_extensions=allowed_video_extensions,
            poster_reference_path=poster_reference_path,
            inherited_category=category,
            inherited_category_path=category_path,
            inherited_category_display_name=category_display_name,
            inherited_series_folder_key=series_folder_key,
            inherited_series_folder_name=series_folder_name,
            discovery=discovery,
        )
