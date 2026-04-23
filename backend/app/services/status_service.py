from __future__ import annotations

from urllib.parse import urlsplit

from ..config import Settings
from ..db import get_connection
from .local_library_source_service import get_effective_shared_local_library_path


def get_scan_job_summary(settings: Settings) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, started_at, finished_at, status, reason, files_seen, files_changed, files_removed, message
            FROM scan_jobs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "running": row["status"] == "running",
            "job_id": row["id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "reason": row["reason"],
            "files_seen": row["files_seen"],
            "files_changed": row["files_changed"],
            "files_removed": row["files_removed"],
            "message": row["message"],
        }


def get_system_status(
    settings: Settings,
    *,
    scan_state: dict[str, object],
    transcode_state: dict[str, object],
    native_playback_state: dict[str, object],
    desktop_playback_state: dict[str, object],
) -> dict[str, object]:
    with get_connection(settings) as connection:
        media_count = connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
        user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    public_app_origin = _public_app_origin(settings)
    backend_api_origin = _backend_api_origin(settings)
    shared_media_root = get_effective_shared_local_library_path(settings)
    return {
        "app_name": settings.app_name,
        "status": "ok",
        "public_app_origin": public_app_origin,
        "backend_api_origin": backend_api_origin,
        "media_root": str(shared_media_root),
        "media_root_exists": shared_media_root.exists(),
        "db_path": str(settings.db_path),
        "ffprobe_available": bool(settings.ffprobe_path),
        "total_media_items": media_count,
        "total_users": user_count,
        "startup_scan_enabled": settings.scan_on_startup,
        "backend_bind": f"{settings.bind_host}:{settings.port}",
        "frontend_bind": f"{settings.frontend_host}:{settings.frontend_port}",
        "scan": scan_state,
        "transcode": transcode_state,
        "native_playback": native_playback_state,
        "desktop_playback": desktop_playback_state,
        "security": {
            "multiuser_enabled": settings.enable_multiuser,
            "private_network_only": settings.private_network_only,
            "session_ttl_hours": settings.session_ttl_hours,
            "playback_token_ttl_seconds": settings.playback_token_ttl_seconds,
        },
        "last_scan": get_scan_job_summary(settings),
    }


def _public_app_origin(settings: Settings) -> str:
    configured = settings.public_app_origin.strip().rstrip("/")
    if configured:
        return configured
    return f"http://{_local_host(settings.frontend_host)}:{settings.frontend_port}"


def _backend_api_origin(settings: Settings) -> str:
    configured = settings.backend_origin.strip().rstrip("/")
    if configured:
        return configured
    public_origin = settings.public_app_origin.strip().rstrip("/")
    if public_origin:
        parsed = urlsplit(public_origin)
        host = parsed.hostname or _local_host(settings.bind_host)
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{settings.port}"
    return f"http://{_local_host(settings.bind_host)}:{settings.port}"


def _local_host(bind_host: str) -> str:
    if bind_host in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return bind_host
