from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "data" / "elvern.db"
DEFAULT_VIDEO_EXTENSIONS = (
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
)


class ConfigError(ValueError):
    """Raised when the environment configuration is invalid."""


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value")


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _get_first_int(names: tuple[str, ...], default: int) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer") from exc
    return default


def _get_path(name: str, default: Path | None = None) -> Path:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        if default is None:
            raise ConfigError(f"{name} is required")
        value = default
    else:
        value = Path(raw.strip()).expanduser()
    if not value.is_absolute():
        value = (PROJECT_ROOT / value).resolve()
    return value


def _get_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    parts = []
    for item in raw.split(","):
        extension = item.strip().lower()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = f".{extension}"
        parts.append(extension)
    if not parts:
        raise ConfigError(f"{name} must contain at least one extension")
    return tuple(dict.fromkeys(parts))


def _resolve_binary(*env_names: str, default: str | None = None) -> str | None:
    for name in env_names:
        raw = os.getenv(name, "").strip()
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return str(candidate) if candidate.exists() else None
        return shutil.which(raw)
    if default is None:
        return None
    return shutil.which(default)


@dataclass(frozen=True)
class Settings:
    app_name: str
    media_root: Path
    db_path: Path
    admin_username: str
    admin_password_hash: str | None
    admin_bootstrap_password: str | None
    session_secret: str
    session_cookie_name: str
    session_ttl_hours: int
    cookie_secure: bool
    enable_multiuser: bool
    private_network_only: bool
    bind_host: str
    port: int
    frontend_host: str
    frontend_port: int
    public_app_origin: str
    backend_origin: str
    scan_on_startup: bool
    allowed_video_extensions: tuple[str, ...]
    transcode_enabled: bool
    transcode_dir: Path
    transcode_ttl_minutes: int
    poster_display_cache_enabled: bool
    poster_display_cache_dir: Path
    poster_card_cache_max_width: int
    poster_card_cache_jpeg_quality: int
    max_concurrent_transcodes: int
    max_concurrent_mobile_workers: int
    mobile_queue_timeout_seconds: int
    mobile_session_idle_seconds: int
    mobile_session_ttl_minutes: int
    mobile_cache_ttl_hours: int
    browser_playback_route2_enabled: bool
    route2_cpu_budget_percent: int
    route2_min_worker_threads: int
    route2_max_worker_threads: int
    route2_max_replacement_epochs_per_session: int
    native_playback_enabled: bool
    native_playback_session_minutes: int
    native_player_protocol: str
    playback_token_ttl_seconds: int
    external_player_stream_ttl_seconds: int
    assistant_attachment_external_open_ttl_seconds: int
    desktop_playback_mode: str
    vlc_helper_protocol: str
    helper_releases_dir: Path
    helper_default_channel: str
    google_oauth_client_id: str | None
    google_oauth_client_secret: str | None
    vlc_path_linux: str | None
    vlc_path_windows: str | None
    vlc_path_mac: str | None
    library_root_linux: str
    library_root_windows: str | None
    library_root_mac: str | None
    ffmpeg_path: str | None
    ffprobe_path: str | None
    log_level: str
    login_window_seconds: int
    login_max_attempts: int
    login_lockout_seconds: int


def load_settings() -> Settings:
    total_cpu_cores = max(1, os.cpu_count() or 1)
    media_root = _get_path("ELVERN_MEDIA_ROOT")
    db_path = _get_path("ELVERN_DB_PATH", DEFAULT_DB_PATH)
    admin_username = os.getenv("ELVERN_ADMIN_USERNAME", "admin").strip()
    admin_password_hash = os.getenv("ELVERN_ADMIN_PASSWORD_HASH", "").strip() or None
    admin_bootstrap_password = os.getenv("ELVERN_ADMIN_BOOTSTRAP_PASSWORD", "").strip() or None
    session_secret = os.getenv("ELVERN_SESSION_SECRET", "").strip()
    transcode_dir = _get_path(
        "ELVERN_TRANSCODE_DIR",
        PROJECT_ROOT / "backend" / "data" / "transcodes",
    )
    settings = Settings(
        app_name="Elvern",
        media_root=media_root.resolve(),
        db_path=db_path,
        admin_username=admin_username,
        admin_password_hash=admin_password_hash,
        admin_bootstrap_password=admin_bootstrap_password,
        session_secret=session_secret,
        session_cookie_name=os.getenv("ELVERN_SESSION_COOKIE_NAME", "elvern_session").strip()
        or "elvern_session",
        session_ttl_hours=_get_int("ELVERN_SESSION_TTL_HOURS", 24 * 30),
        cookie_secure=_get_bool("ELVERN_COOKIE_SECURE", True),
        enable_multiuser=_get_bool("ELVERN_ENABLE_MULTIUSER", True),
        private_network_only=_get_bool("ELVERN_PRIVATE_NETWORK_ONLY", True),
        bind_host=os.getenv("ELVERN_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_get_int("ELVERN_PORT", 8000),
        frontend_host=os.getenv("ELVERN_FRONTEND_HOST", "127.0.0.1").strip()
        or "127.0.0.1",
        frontend_port=_get_int("ELVERN_FRONTEND_PORT", 4173),
        public_app_origin=os.getenv("ELVERN_PUBLIC_APP_ORIGIN", "").strip() or "",
        backend_origin=os.getenv("ELVERN_BACKEND_ORIGIN", "").strip() or "",
        scan_on_startup=_get_bool("ELVERN_SCAN_ON_STARTUP", True),
        allowed_video_extensions=_get_csv(
            "ELVERN_ALLOWED_VIDEO_EXTENSIONS",
            DEFAULT_VIDEO_EXTENSIONS,
        ),
        transcode_enabled=_get_bool("ELVERN_TRANSCODE_ENABLED", True),
        transcode_dir=transcode_dir,
        transcode_ttl_minutes=_get_int("ELVERN_TRANSCODE_TTL_MINUTES", 60),
        poster_display_cache_enabled=_get_bool("ELVERN_POSTER_DISPLAY_CACHE_ENABLED", True),
        poster_display_cache_dir=_get_path(
            "ELVERN_POSTER_DISPLAY_CACHE_DIR",
            PROJECT_ROOT / "backend" / "data" / "poster_display_cache",
        ),
        poster_card_cache_max_width=_get_int("ELVERN_POSTER_CARD_CACHE_MAX_WIDTH", 1400),
        poster_card_cache_jpeg_quality=_get_int("ELVERN_POSTER_CARD_CACHE_JPEG_QUALITY", 97),
        max_concurrent_transcodes=_get_int("ELVERN_MAX_CONCURRENT_TRANSCODES", 1),
        max_concurrent_mobile_workers=_get_int("ELVERN_MAX_CONCURRENT_MOBILE_WORKERS", 2),
        mobile_queue_timeout_seconds=_get_int("ELVERN_MOBILE_QUEUE_TIMEOUT_SECONDS", 12),
        mobile_session_idle_seconds=_get_int("ELVERN_MOBILE_SESSION_IDLE_SECONDS", 45),
        mobile_session_ttl_minutes=_get_int("ELVERN_MOBILE_SESSION_TTL_MINUTES", 15),
        mobile_cache_ttl_hours=_get_int("ELVERN_MOBILE_CACHE_TTL_HOURS", 24),
        browser_playback_route2_enabled=_get_bool("ELVERN_BROWSER_PLAYBACK_ROUTE2_ENABLED", True),
        route2_cpu_budget_percent=_get_first_int(
            ("ELVERN_ROUTE2_CPU_UPBOUND_PERCENT", "ELVERN_ROUTE2_CPU_BUDGET_PERCENT"),
            90,
        ),
        route2_min_worker_threads=_get_int("ELVERN_ROUTE2_MIN_WORKER_THREADS", 1),
        route2_max_worker_threads=_get_int(
            "ELVERN_ROUTE2_MAX_WORKER_THREADS",
            min(8, total_cpu_cores),
        ),
        route2_max_replacement_epochs_per_session=_get_int(
            "ELVERN_ROUTE2_MAX_REPLACEMENT_EPOCHS_PER_SESSION",
            3,
        ),
        native_playback_enabled=_get_bool("ELVERN_NATIVE_PLAYBACK_ENABLED", True),
        native_playback_session_minutes=_get_int("ELVERN_NATIVE_PLAYBACK_SESSION_MINUTES", 20),
        native_player_protocol=(
            os.getenv("ELVERN_NATIVE_PLAYER_PROTOCOL", "elvern").strip().lower().rstrip(":")
            or "elvern"
        ),
        playback_token_ttl_seconds=_get_int("ELVERN_PLAYBACK_TOKEN_TTL_SECONDS", 300),
        external_player_stream_ttl_seconds=_get_int("ELVERN_EXTERNAL_PLAYER_STREAM_TTL_SECONDS", 43200),
        assistant_attachment_external_open_ttl_seconds=_get_int(
            "ELVERN_ASSISTANT_ATTACHMENT_EXTERNAL_OPEN_TTL_SECONDS",
            300,
        ),
        desktop_playback_mode=os.getenv("ELVERN_DESKTOP_PLAYBACK_MODE", "vlc_direct").strip().lower() or "vlc_direct",
        vlc_helper_protocol=(
            os.getenv("ELVERN_VLC_HELPER_PROTOCOL", "elvern-vlc").strip().lower().rstrip(":")
            or "elvern-vlc"
        ),
        helper_releases_dir=_get_path(
            "ELVERN_HELPER_RELEASES_DIR",
            PROJECT_ROOT / "backend" / "data" / "helper_releases",
        ),
        helper_default_channel=(
            os.getenv("ELVERN_HELPER_DEFAULT_CHANNEL", "stable").strip().lower()
            or "stable"
        ),
        google_oauth_client_id=os.getenv("ELVERN_GOOGLE_OAUTH_CLIENT_ID", "").strip() or None,
        google_oauth_client_secret=os.getenv("ELVERN_GOOGLE_OAUTH_CLIENT_SECRET", "").strip() or None,
        vlc_path_linux=_resolve_binary("ELVERN_VLC_PATH_LINUX", default="vlc"),
        vlc_path_windows=os.getenv("ELVERN_VLC_PATH_WINDOWS", "").strip() or None,
        vlc_path_mac=os.getenv("ELVERN_VLC_PATH_MAC", "").strip() or None,
        library_root_linux=os.getenv("ELVERN_LIBRARY_ROOT_LINUX", str(media_root)).strip() or str(media_root),
        library_root_windows=os.getenv("ELVERN_LIBRARY_ROOT_WINDOWS", "").strip() or None,
        library_root_mac=os.getenv("ELVERN_LIBRARY_ROOT_MAC", "").strip() or None,
        ffmpeg_path=_resolve_binary("ELVERN_FFMPEG_PATH", default="ffmpeg"),
        ffprobe_path=_resolve_binary(
            "ELVERN_FFPROBE_PATH",
            "ELVERN_FFPROBE_BIN",
            default="ffprobe",
        ),
        log_level=os.getenv("ELVERN_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        login_window_seconds=_get_int("ELVERN_LOGIN_WINDOW_SECONDS", 300),
        login_max_attempts=_get_int("ELVERN_LOGIN_MAX_ATTEMPTS", 10),
        login_lockout_seconds=_get_int("ELVERN_LOGIN_LOCKOUT_SECONDS", 600),
    )
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    total_cpu_cores = max(1, os.cpu_count() or 1)
    if not settings.media_root.exists():
        raise ConfigError(
            f"ELVERN_MEDIA_ROOT does not exist: {settings.media_root}"
        )
    if not settings.media_root.is_dir():
        raise ConfigError(
            f"ELVERN_MEDIA_ROOT must be a directory: {settings.media_root}"
        )
    if not settings.admin_username:
        raise ConfigError("ELVERN_ADMIN_USERNAME must not be empty")
    if not settings.session_secret or len(settings.session_secret) < 32:
        raise ConfigError(
            "ELVERN_SESSION_SECRET must be set to a value with at least 32 characters"
        )
    if not settings.admin_password_hash and not settings.admin_bootstrap_password:
        raise ConfigError(
            "Set ELVERN_ADMIN_PASSWORD_HASH or ELVERN_ADMIN_BOOTSTRAP_PASSWORD"
        )
    if settings.poster_card_cache_max_width < 400 or settings.poster_card_cache_max_width > 4096:
        raise ConfigError("ELVERN_POSTER_CARD_CACHE_MAX_WIDTH must be between 400 and 4096")
    if settings.poster_card_cache_jpeg_quality < 85 or settings.poster_card_cache_jpeg_quality > 100:
        raise ConfigError("ELVERN_POSTER_CARD_CACHE_JPEG_QUALITY must be between 85 and 100")
    if settings.native_playback_session_minutes < 1:
        raise ConfigError("ELVERN_NATIVE_PLAYBACK_SESSION_MINUTES must be at least 1")
    if settings.playback_token_ttl_seconds < 30:
        raise ConfigError("ELVERN_PLAYBACK_TOKEN_TTL_SECONDS must be at least 30")
    if settings.external_player_stream_ttl_seconds < 600 or settings.external_player_stream_ttl_seconds > 86400:
        raise ConfigError("ELVERN_EXTERNAL_PLAYER_STREAM_TTL_SECONDS must be between 600 and 86400")
    if settings.assistant_attachment_external_open_ttl_seconds < 30:
        raise ConfigError("ELVERN_ASSISTANT_ATTACHMENT_EXTERNAL_OPEN_TTL_SECONDS must be at least 30")
    if settings.max_concurrent_mobile_workers < 1:
        raise ConfigError("ELVERN_MAX_CONCURRENT_MOBILE_WORKERS must be at least 1")
    if settings.mobile_queue_timeout_seconds < 1:
        raise ConfigError("ELVERN_MOBILE_QUEUE_TIMEOUT_SECONDS must be at least 1")
    if settings.mobile_session_idle_seconds < 5:
        raise ConfigError("ELVERN_MOBILE_SESSION_IDLE_SECONDS must be at least 5")
    if settings.mobile_session_ttl_minutes < 1:
        raise ConfigError("ELVERN_MOBILE_SESSION_TTL_MINUTES must be at least 1")
    if settings.mobile_cache_ttl_hours < 1:
        raise ConfigError("ELVERN_MOBILE_CACHE_TTL_HOURS must be at least 1")
    if settings.route2_cpu_budget_percent < 10 or settings.route2_cpu_budget_percent > 95:
        raise ConfigError(
            "ELVERN_ROUTE2_CPU_UPBOUND_PERCENT or ELVERN_ROUTE2_CPU_BUDGET_PERCENT must be between 10 and 95"
        )
    if settings.route2_min_worker_threads < 1:
        raise ConfigError("ELVERN_ROUTE2_MIN_WORKER_THREADS must be at least 1")
    if (
        settings.route2_max_worker_threads < settings.route2_min_worker_threads
        or settings.route2_max_worker_threads > total_cpu_cores
    ):
        raise ConfigError(
            "ELVERN_ROUTE2_MAX_WORKER_THREADS must be at least the min worker threads and no more than os.cpu_count()"
        )
    if settings.route2_max_replacement_epochs_per_session < 0:
        raise ConfigError("ELVERN_ROUTE2_MAX_REPLACEMENT_EPOCHS_PER_SESSION must be at least 0")
    if not settings.native_player_protocol.replace("-", "").isalnum():
        raise ConfigError(
            "ELVERN_NATIVE_PLAYER_PROTOCOL must be alphanumeric and may include hyphens"
        )
    if settings.desktop_playback_mode not in {"vlc_direct"}:
        raise ConfigError("ELVERN_DESKTOP_PLAYBACK_MODE must currently be set to 'vlc_direct'")
    if not settings.vlc_helper_protocol.replace("-", "").isalnum():
        raise ConfigError(
            "ELVERN_VLC_HELPER_PROTOCOL must be alphanumeric and may include hyphens"
        )
    if not settings.helper_default_channel.replace("-", "").isalnum():
        raise ConfigError(
            "ELVERN_HELPER_DEFAULT_CHANNEL must be alphanumeric and may include hyphens"
        )
    if settings.public_app_origin:
        parsed_public_app_origin = urlsplit(settings.public_app_origin)
        if parsed_public_app_origin.scheme not in {"http", "https"} or not parsed_public_app_origin.netloc:
            raise ConfigError(
                "ELVERN_PUBLIC_APP_ORIGIN must be an absolute http(s) origin, for example https://dgx.tailnet.example.ts.net"
            )
        if parsed_public_app_origin.path not in {"", "/"} or parsed_public_app_origin.query or parsed_public_app_origin.fragment:
            raise ConfigError("ELVERN_PUBLIC_APP_ORIGIN must be an origin only, without a path or query")
    if settings.backend_origin:
        parsed_backend_origin = urlsplit(settings.backend_origin)
        if parsed_backend_origin.scheme not in {"http", "https"} or not parsed_backend_origin.netloc:
            raise ConfigError(
                "ELVERN_BACKEND_ORIGIN must be an absolute http(s) origin, for example http://dgx.tailnet.example.ts.net:8000"
            )
        if parsed_backend_origin.path not in {"", "/"} or parsed_backend_origin.query or parsed_backend_origin.fragment:
            raise ConfigError("ELVERN_BACKEND_ORIGIN must be an origin only, without a path or query")
    settings.helper_releases_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.transcode_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def refresh_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
