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
POSTER_REFERENCE_HELP_TEXT = (
    "Choose a poster folder such as /home/<user>/Videos/Elvern Posters, "
    "/mnt/media/Posters, /media/drive/Posters, or /srv/media/Posters."
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
POSTER_REFERENCE_PATH_FORMAT_DETAIL = (
    "Poster reference location must be an absolute Linux directory path or local file:// URI."
)
POSTER_REFERENCE_LOCAL_URI_DETAIL = (
    "Poster reference location must resolve to a local directory on this host."
)
POSTER_REFERENCE_SYSTEM_PATH_DETAIL = (
    "Poster reference location cannot be a system or broad host directory. "
    f"{POSTER_REFERENCE_HELP_TEXT}"
)
POSTER_REFERENCE_ELVERN_PATH_DETAIL = (
    "Poster reference location cannot point inside Elvern application data. "
    f"{POSTER_REFERENCE_HELP_TEXT}"
)
DIRECTORY_BROWSE_PATH_FORMAT_DETAIL = (
    "Directory browse only supports absolute Linux paths or local file:// URIs."
)
DIRECTORY_BROWSE_LOCAL_URI_DETAIL = (
    "Directory browse only supports local directories on the Elvern host."
)
DIRECTORY_BROWSE_RESTRICTED_DETAIL = (
    "Directory browse cannot access system directories or Elvern application data."
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
    # Denylist entry for unsafe media-library roots, not temporary-file creation.
    Path("/tmp"),  # nosec B108 - /tmp is intentionally denylisted as an unsafe media-library root; no temp file is created.
    Path("/snap"),
    Path("/nix"),
    Path("/lost+found"),
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


def normalize_local_path_candidate(value: str | Path | None, *, strict: bool = True) -> Path:
    raw_value = str(value or "").strip()
    if not raw_value or _contains_control_character(raw_value):
        _reject("path_format")

    parsed = urlsplit(raw_value)
    if parsed.scheme:
        if parsed.scheme.lower() != "file":
            _reject("unsupported_uri_scheme")
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            _reject("unsupported_file_authority")
        if parsed.query or parsed.fragment:
            _reject("path_format")
        decoded_path = unquote(parsed.path or "")
        if not decoded_path or _contains_control_character(decoded_path):
            _reject("path_format")
        candidate_path = Path(decoded_path).expanduser()
    else:
        candidate_path = Path(raw_value).expanduser()

    if not candidate_path.is_absolute():
        _reject("relative_path")

    try:
        return candidate_path.resolve(strict=strict)
    except FileNotFoundError:
        _reject("missing_path")
    except (OSError, RuntimeError):
        _reject("resolve_error")


def _is_home_root(path: Path) -> bool:
    home_root = Path("/home")
    return path == home_root or path.parent == home_root


def is_restricted_system_path(path: str | Path, settings: Settings | None = None) -> bool:
    del settings
    resolved = _resolve_for_compare(path)
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


def is_restricted_settings_browse_path(settings: Settings, path: str | Path) -> bool:
    if is_restricted_elvern_internal_path(path, settings):
        return True
    return is_restricted_system_path(path, settings=settings)


def _path_error_detail(code: str, *, path_kind: str) -> str:
    if path_kind == "poster":
        details = {
            "path_format": POSTER_REFERENCE_PATH_FORMAT_DETAIL,
            "unsupported_uri_scheme": POSTER_REFERENCE_PATH_FORMAT_DETAIL,
            "unsupported_file_authority": POSTER_REFERENCE_LOCAL_URI_DETAIL,
            "relative_path": "Poster reference location must be an absolute Linux directory path.",
            "missing_path": "Poster reference location must point to an existing directory.",
            "resolve_error": "Poster reference location could not be resolved on this host.",
        }
        return details.get(code, "Poster reference location is invalid.")
    if path_kind == "browse":
        details = {
            "path_format": DIRECTORY_BROWSE_PATH_FORMAT_DETAIL,
            "unsupported_uri_scheme": DIRECTORY_BROWSE_PATH_FORMAT_DETAIL,
            "unsupported_file_authority": DIRECTORY_BROWSE_LOCAL_URI_DETAIL,
            "relative_path": "Directory browse needs an absolute Linux path or file:// URI.",
            "missing_path": "Directory browse could not find a readable local directory on this host.",
            "resolve_error": "Directory browse could not resolve that directory on the Elvern host.",
        }
        return details.get(code, "Directory browse path is invalid.")
    details = {
        "path_format": LIBRARY_REFERENCE_PATH_FORMAT_DETAIL,
        "unsupported_uri_scheme": LIBRARY_REFERENCE_PATH_FORMAT_DETAIL,
        "unsupported_file_authority": LIBRARY_REFERENCE_LOCAL_URI_DETAIL,
        "relative_path": "Library reference location must be an absolute Linux directory path.",
        "missing_path": "Library reference location does not exist on this host.",
        "resolve_error": "Library reference location could not be resolved on this host.",
    }
    return details.get(code, "Library reference location is invalid.")


def _normalize_value_for_validation(
    settings: Settings,
    value: str | Path | None,
    *,
    path_kind: str,
) -> Path:
    raw_value = str(value or "").strip()
    try:
        if not raw_value:
            return Path(settings.media_root).expanduser().resolve(strict=True)
        return normalize_local_path_candidate(raw_value)
    except LocalPathValidationError as exc:
        _raise_http(_path_error_detail(str(exc), path_kind=path_kind))


def _check_common_directory_requirements(
    settings: Settings,
    path: Path,
    *,
    path_kind: str,
) -> Path:
    if not path.is_dir():
        if path_kind == "poster":
            _raise_http("Poster reference location must point to a directory.")
        if path_kind == "browse":
            _raise_http("Directory browse path must be a directory.")
        _raise_http("Library reference location must be a directory.")
    if not os.access(path, os.R_OK | os.X_OK):
        if path_kind == "poster":
            _raise_http("Poster reference location must point to a readable directory.")
        if path_kind == "browse":
            _raise_http("Directory browse could not read that directory on the Elvern host.")
        _raise_http("Library reference location must be a readable directory.")
    if is_restricted_elvern_internal_path(path, settings):
        if path_kind == "poster":
            _raise_http(POSTER_REFERENCE_ELVERN_PATH_DETAIL)
        if path_kind == "browse":
            _raise_http(DIRECTORY_BROWSE_RESTRICTED_DETAIL)
        _raise_http(LIBRARY_REFERENCE_ELVERN_PATH_DETAIL)
    if is_restricted_system_path(path, settings=settings):
        if path_kind == "poster":
            _raise_http(POSTER_REFERENCE_SYSTEM_PATH_DETAIL)
        if path_kind == "browse":
            _raise_http(DIRECTORY_BROWSE_RESTRICTED_DETAIL)
        _raise_http(LIBRARY_REFERENCE_SYSTEM_PATH_DETAIL)
    return path


def validate_safe_library_reference_path(settings: Settings, value: str | Path | None) -> str:
    normalized_path = _normalize_value_for_validation(settings, value, path_kind="library")
    _check_common_directory_requirements(settings, normalized_path, path_kind="library")
    return str(normalized_path)


def validate_safe_poster_reference_path(settings: Settings, value: str | Path | None) -> str:
    normalized_path = _normalize_value_for_validation(settings, value, path_kind="poster")
    _check_common_directory_requirements(settings, normalized_path, path_kind="poster")
    return str(normalized_path)


def validate_safe_directory_browse_path(settings: Settings, value: str | Path | None) -> Path:
    raw_value = str(value or "").strip()
    if not raw_value:
        normalized_path = _normalize_value_for_validation(settings, value, path_kind="browse")
    else:
        try:
            normalized_path = normalize_local_path_candidate(raw_value, strict=False)
        except LocalPathValidationError as exc:
            _raise_http(_path_error_detail(str(exc), path_kind="browse"))
    if is_restricted_settings_browse_path(settings, normalized_path):
        _raise_http(DIRECTORY_BROWSE_RESTRICTED_DETAIL)
    if not normalized_path.exists():
        _raise_http("Directory browse could not find a readable local directory on this host.")
    if not normalized_path.is_dir():
        normalized_path = normalized_path.parent.resolve(strict=True)
    return _check_common_directory_requirements(
        settings,
        normalized_path,
        path_kind="browse",
    )


def validate_safe_existing_local_directory(settings: Settings, value: str | Path | None) -> str:
    normalized_path = _normalize_value_for_validation(settings, value, path_kind="browse")
    _check_common_directory_requirements(settings, normalized_path, path_kind="browse")
    return str(normalized_path)
