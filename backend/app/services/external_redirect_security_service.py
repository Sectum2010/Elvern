from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


FORBIDDEN_CUSTOM_APP_SCHEMES = frozenset({
    "about",
    "blob",
    "data",
    "file",
    "http",
    "https",
    "javascript",
    "vbscript",
})

_SAFE_CUSTOM_APP_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]{1,31}$")


class ExternalRedirectSafetyError(ValueError):
    def __init__(self, reason: str, *, scheme: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.scheme = scheme or ""


def _raise(reason: str, *, scheme: str | None = None) -> None:
    raise ExternalRedirectSafetyError(reason, scheme=scheme)


def _scheme_has_whitespace_or_controls(value: str) -> bool:
    return any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)


def validate_safe_custom_app_scheme(value: str, *, setting_name: str) -> str:
    raw_value = str(value or "").strip()
    candidate = raw_value[:-1] if raw_value.endswith(":") else raw_value
    normalized = candidate.lower()
    if not normalized:
        _raise(f"{setting_name} must not be empty")
    if _scheme_has_whitespace_or_controls(candidate):
        _raise(f"{setting_name} must not contain whitespace or control characters", scheme=normalized)
    if normalized in FORBIDDEN_CUSTOM_APP_SCHEMES:
        _raise(f"{setting_name} must not use the forbidden scheme '{normalized}'", scheme=normalized)
    if not _SAFE_CUSTOM_APP_SCHEME_RE.fullmatch(normalized):
        _raise(
            f"{setting_name} must match ^[a-z][a-z0-9+.-]{{1,31}}$",
            scheme=normalized,
        )
    return normalized


def _origin_tuple(value: str) -> tuple[str, str]:
    parsed = urlsplit(str(value or "").strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _raise("expected origin must be absolute http(s)", scheme=parsed.scheme)
    return parsed.scheme.lower(), parsed.netloc.lower()


def _validate_http_url(
    value: str,
    *,
    expected_origin: str | None,
    path_prefix: str | None = None,
    allowed_library_path: bool = False,
    field_name: str,
) -> None:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _raise(f"{field_name} must be an absolute http(s) URL", scheme=parsed.scheme)
    if expected_origin:
        expected_scheme, expected_netloc = _origin_tuple(expected_origin)
        if (parsed.scheme.lower(), parsed.netloc.lower()) != (expected_scheme, expected_netloc):
            _raise(f"{field_name} origin is not allowed", scheme=parsed.scheme)
    if path_prefix and not parsed.path.startswith(path_prefix):
        _raise(f"{field_name} path is not allowed", scheme=parsed.scheme)
    if allowed_library_path and not _is_safe_elvern_library_path(parsed.path):
        _raise(f"{field_name} path is not an allowed library return path", scheme=parsed.scheme)
    if parsed.fragment:
        _raise(f"{field_name} must not contain a fragment", scheme=parsed.scheme)


def _single_non_empty_query_value(params: dict[str, list[str]], name: str, *, field_name: str) -> str:
    values = params.get(name) or []
    if len(values) != 1 or not values[0]:
        _raise(f"{field_name} must include one non-empty {name} value")
    return values[0]


def _is_safe_elvern_library_path(path: str) -> bool:
    if path in {"/library", "/library/", "/library/local", "/library/cloud"}:
        return True
    prefix = "/library/"
    if not path.startswith(prefix):
        return False
    item_id = path[len(prefix):]
    return item_id.isdecimal() and int(item_id) > 0


def normalize_safe_elvern_library_return_path(return_path: str | None, *, item_id: int) -> str:
    fallback = f"/library/{int(item_id)}"
    candidate = str(return_path or "").strip()
    parsed = urlsplit(candidate)
    if (
        not candidate
        or not candidate.startswith("/")
        or candidate.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or not _is_safe_elvern_library_path(parsed.path)
    ):
        return fallback
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def validate_ios_external_launch_redirect(
    url: str,
    *,
    app: str,
    expected_stream_origin: str,
    expected_callback_origin: str,
) -> str:
    normalized_app = str(app or "").strip().lower()
    parsed = urlsplit(str(url or "").strip())
    params = parse_qs(parsed.query, keep_blank_values=True)

    if normalized_app == "vlc":
        expected = ("vlc-x-callback", "x-callback-url", "/stream")
        forbidden_callbacks = "x-success" in params or "x-error" in params
    elif normalized_app == "infuse":
        expected = ("infuse", "x-callback-url", "/play")
        forbidden_callbacks = False
    else:
        _raise("unsupported external app target", scheme=parsed.scheme)

    expected_scheme, expected_netloc, expected_path = expected
    if parsed.scheme != expected_scheme or parsed.netloc != expected_netloc or parsed.path != expected_path:
        _raise("external app redirect target is not allowlisted", scheme=parsed.scheme)
    if forbidden_callbacks:
        _raise("vlc launch redirect must not include callback parameters", scheme=parsed.scheme)

    stream_url = _single_non_empty_query_value(params, "url", field_name="external app redirect")
    _validate_http_url(
        stream_url,
        expected_origin=expected_stream_origin,
        path_prefix="/api/native-playback/session/",
        field_name="stream URL",
    )

    if normalized_app == "infuse":
        success_url = _single_non_empty_query_value(params, "x-success", field_name="infuse redirect")
        error_url = _single_non_empty_query_value(params, "x-error", field_name="infuse redirect")
        for callback_url in (success_url, error_url):
            _validate_http_url(
                callback_url,
                expected_origin=expected_callback_origin,
                allowed_library_path=True,
                field_name="callback URL",
            )

    return url


def validate_desktop_helper_redirect(
    url: str,
    *,
    expected_scheme: str,
    expected_api_origin: str | None = None,
) -> str:
    normalized_expected_scheme = validate_safe_custom_app_scheme(
        expected_scheme,
        setting_name="ELVERN_VLC_HELPER_PROTOCOL",
    )
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme != normalized_expected_scheme:
        _raise("desktop helper redirect scheme is not allowed", scheme=parsed.scheme)
    if parsed.netloc != "play" or parsed.path not in {"", "/"}:
        _raise("desktop helper redirect target is not allowlisted", scheme=parsed.scheme)
    params = parse_qs(parsed.query, keep_blank_values=True)
    api_url = _single_non_empty_query_value(params, "api", field_name="desktop helper redirect")
    _single_non_empty_query_value(params, "handoff", field_name="desktop helper redirect")
    _single_non_empty_query_value(params, "token", field_name="desktop helper redirect")
    _validate_http_url(
        api_url,
        expected_origin=expected_api_origin,
        field_name="desktop helper API URL",
    )
    return url
