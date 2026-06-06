from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, status

from ..config import PROJECT_ROOT, Settings


LIBRARY_REFERENCE_HELP_TEXT = (
    "Choose a media folder such as /home/<user>/Videos, /mnt/media, "
    "/media/drive/Movies, or /srv/media."
)
LIBRARY_REFERENCE_PATH_FORMAT_DETAIL = (
    "Library reference location must be an absolute Linux directory path or local file:// URI."
)
LIBRARY_REFERENCE_LOCAL_URI_DETAIL = (
    "Library reference location must resolve to a local directory on this host."
)
LIBRARY_REFERENCE_SYSTEM_PATH_DETAIL = (
    "Library reference location cannot be a system or broad host directory. "
    f"{LIBRARY_REFERENCE_HELP_TEXT}"
)
LIBRARY_REFERENCE_ELVERN_PATH_DETAIL = (
    "Library reference location cannot point inside Elvern application data. "
    f"{LIBRARY_REFERENCE_HELP_TEXT}"
)

RESTRICTED_SYSTEM_ROOTS = (
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
    Path("/run"),
    Path("/root"),
    Path("/boot"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/lib"),
    Path("/lib64"),
    Path("/var"),
    Path("/tmp"),
)


class LocalPathValidationError(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _raise_http(detail: str) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _reject(detail: str) -> None:
    raise LocalPathValidationError(detail)


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _resolve_for_compare(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def is_path_same_or_inside(path: str | Path, root: str | Path) -> bool:
    try:
        _resolve_for_compare(path).relative_to(_resolve_for_compare(root))
        return True
    except ValueError:
        return False


def normalize_local_path_candidate(value: str | Path | None) -> Path:
    raw_value = str(value or "").strip()
    if not raw_value or _contains_control_character(raw_value):
        _reject(LIBRARY_REFERENCE_PATH_FORMAT_DETAIL)

    parsed = urlsplit(raw_value)
    if parsed.scheme:
        if parsed.scheme.lower() != "file":
            _reject(LIBRARY_REFERENCE_PATH_FORMAT_DETAIL)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            _reject(LIBRARY_REFERENCE_LOCAL_URI_DETAIL)
        if parsed.query or parsed.fragment:
            _reject(LIBRARY_REFERENCE_PATH_FORMAT_DETAIL)
        decoded_path = unquote(parsed.path or "")
        if not decoded_path or _contains_control_character(decoded_path):
            _reject(LIBRARY_REFERENCE_PATH_FORMAT_DETAIL)
        candidate_path = Path(decoded_path).expanduser()
    else:
        candidate_path = Path(raw_value).expanduser()

    if not candidate_path.is_absolute():
        _reject("Library reference location must be an absolute Linux directory path.")

    try:
        return candidate_path.resolve(strict=True)
    except FileNotFoundError:
        _reject("Library reference location does not exist on this host.")
    except (OSError, RuntimeError):
        _reject("Library reference location could not be resolved on this host.")


def _is_home_root(path: Path) -> bool:
    home_root = Path("/home")
    return path == home_root or path.parent == home_root


def _is_configured_media_root_path(path: Path, settings: Settings | None) -> bool:
    if settings is None:
        return False
    return is_path_same_or_inside(path, settings.media_root)


def is_restricted_system_path(path: str | Path, settings: Settings | None = None) -> bool:
    resolved = _resolve_for_compare(path)
    if _is_configured_media_root_path(resolved, settings):
        return False
    if resolved == Path("/") or _is_home_root(resolved):
        return True
    return any(is_path_same_or_inside(resolved, root) for root in RESTRICTED_SYSTEM_ROOTS)


def is_restricted_elvern_internal_path(path: str | Path, settings: Settings) -> bool:
    resolved = _resolve_for_compare(path)
    internal_roots = (
        PROJECT_ROOT,
        settings.data_dir,
        settings.transcode_dir,
        settings.poster_display_cache_dir,
        settings.helper_releases_dir,
    )
    return any(is_path_same_or_inside(resolved, root) for root in internal_roots)


def is_restricted_library_reference_path(settings: Settings, path: str | Path) -> bool:
    if is_restricted_elvern_internal_path(path, settings):
        return True
    return is_restricted_system_path(path, settings=settings)


def validate_safe_library_reference_path(settings: Settings, value: str | Path | None) -> str:
    raw_value = str(value or "").strip()
    try:
        normalized_path = (
            Path(settings.media_root).expanduser().resolve(strict=True)
            if not raw_value
            else normalize_local_path_candidate(raw_value)
        )
    except LocalPathValidationError as exc:
        _raise_http(exc.detail)

    if not normalized_path.is_dir():
        _raise_http("Library reference location must be a directory.")
    if not os.access(normalized_path, os.R_OK | os.X_OK):
        _raise_http("Library reference location must be a readable directory.")
    if is_restricted_elvern_internal_path(normalized_path, settings):
        _raise_http(LIBRARY_REFERENCE_ELVERN_PATH_DETAIL)
    if is_restricted_system_path(normalized_path, settings=settings):
        _raise_http(LIBRARY_REFERENCE_SYSTEM_PATH_DETAIL)
    return str(normalized_path)
