from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlencode, urlsplit
from xml.sax.saxutils import escape

from fastapi import HTTPException, status

from ..config import Settings
from .local_library_source_service import get_effective_shared_local_library_path


def map_media_path_for_platform(settings: Settings, resolved_file: Path, platform: str) -> str | None:
    media_root = get_effective_shared_local_library_path(settings).resolve()
    try:
        relative_parts = resolved_file.relative_to(media_root).parts
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Media path escapes configured media root",
        ) from exc

    if platform == "linux":
        base_root = settings.library_root_linux.strip()
        if not base_root:
            return None
        return str(PurePosixPath(base_root).joinpath(*relative_parts))

    if platform == "mac":
        if not settings.library_root_mac:
            return None
        return str(PurePosixPath(settings.library_root_mac).joinpath(*relative_parts))

    if platform == "windows":
        if not settings.library_root_windows:
            return None
        return str(PureWindowsPath(settings.library_root_windows).joinpath(*relative_parts))

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported desktop platform",
    )


def infer_target_kind(target: str) -> str:
    lowered = target.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return "url"
    return "path"


def build_vlc_launch_command(
    vlc_path: str | None,
    target: str,
    resume_seconds: float,
    *,
    rc_host_port: int | None = None,
) -> list[str]:
    if not vlc_path:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VLC was not found on this machine.",
        )
    command = [vlc_path]
    if rc_host_port is not None:
        command.extend(["--extraintf", "rc", "--rc-fake-tty", "--rc-host", f"127.0.0.1:{rc_host_port}"])
    if infer_target_kind(target) == "url":
        command.append("--network-caching=1000")
    if resume_seconds > 0:
        command.append(f"--start-time={resume_seconds:.3f}")
    command.append(target)
    return command


def _rewrite_stream_url_for_linux_same_host(settings: Settings, *, stream_url: str) -> str:
    parsed = urlsplit(stream_url)
    if not parsed.scheme or not parsed.netloc:
        return stream_url
    host = settings.bind_host.strip()
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{settings.port}"
    return parsed._replace(netloc=netloc).geturl()


def build_vlc_location_uri(platform: str, target: str) -> str:
    if platform == "windows":
        if target.startswith("\\\\"):
            parts = [part for part in target.lstrip("\\").split("\\") if part]
            if len(parts) < 2:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid Windows UNC path configured for VLC playback",
                )
            host, *segments = parts
            escaped_segments = "/".join(quote(segment) for segment in segments)
            return f"file://{host}/{escaped_segments}"

        normalized = target.replace("\\", "/")
        drive, _, remainder = normalized.partition(":")
        if len(drive) == 1 and remainder.startswith("/"):
            escaped_path = quote(remainder)
            return f"file:///{drive.upper()}:{escaped_path}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Windows VLC playback target must be a drive path or UNC path",
        )

    escaped_path = quote(target, safe="/:")
    return f"file://{escaped_path if escaped_path.startswith('/') else f'/{escaped_path}'}"


def build_xspf_playlist(
    *,
    title: str,
    location: str,
    resume_seconds: float,
    duration_seconds: object,
) -> str:
    extension_lines = []
    if resume_seconds > 0:
        extension_lines.append(
            f"        <vlc:option>start-time={escape(f'{resume_seconds:.3f}')}</vlc:option>"
        )

    duration_line = ""
    if isinstance(duration_seconds, (int, float)) and duration_seconds > 0:
        duration_line = f"      <duration>{int(float(duration_seconds) * 1000)}</duration>\n"

    extension_block = ""
    if extension_lines:
        extension_body = "\n".join(extension_lines)
        extension_block = (
            "      <extension application=\"http://www.videolan.org/vlc/playlist/0\">\n"
            f"{extension_body}\n"
            "      </extension>\n"
        )

    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<playlist version=\"1\" xmlns=\"http://xspf.org/ns/0/\" "
        "xmlns:vlc=\"http://www.videolan.org/vlc/playlist/ns/0/\">\n"
        f"  <title>{escape(title)}</title>\n"
        "  <trackList>\n"
        "    <track>\n"
        f"      <location>{escape(location)}</location>\n"
        f"      <title>{escape(title)}</title>\n"
        f"{duration_line}"
        f"{extension_block}"
        "    </track>\n"
        "  </trackList>\n"
        "</playlist>\n"
    )


def build_playlist_filename(title: str) -> str:
    safe = "".join(character if character.isalnum() or character in {" ", "-", "_"} else " " for character in title)
    normalized = "-".join(part for part in safe.split() if part).strip("-") or "elvern-vlc"
    return f"{normalized}.xspf"


def build_vlc_helper_protocol_url(
    settings: Settings,
    *,
    backend_origin: str,
    handoff_id: str,
    access_token: str,
) -> str:
    params = urlencode(
        {
            "api": backend_origin,
            "handoff": handoff_id,
            "token": access_token,
        }
    )
    return f"{settings.vlc_helper_protocol}://play?{params}"


def build_vlc_helper_started_url(
    *,
    backend_origin: str,
    handoff_id: str,
    access_token: str,
) -> str:
    params = urlencode({"token": access_token})
    return (
        f"{backend_origin.rstrip('/')}/api/desktop-playback/handoff/"
        f"{quote(handoff_id, safe='')}/started?{params}"
    )


def _public_app_origin(settings: Settings) -> str:
    configured = settings.public_app_origin.strip().rstrip("/")
    if configured:
        return configured
    host = settings.frontend_host
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.frontend_port}"


def _desktop_backend_origin(settings: Settings) -> str:
    configured = settings.backend_origin.strip().rstrip("/")
    configured_host = (urlsplit(configured).hostname or "").strip().lower()
    if configured and configured_host not in {"127.0.0.1", "localhost", "::1"}:
        return configured
    public_origin = settings.public_app_origin.strip().rstrip("/")
    if public_origin:
        parsed = urlsplit(public_origin)
        host = (parsed.hostname or settings.bind_host).strip().lower()
        if host in {"", "0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{settings.port}"
    host = settings.bind_host
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.port}"


def _desktop_helper_supported(settings: Settings) -> bool:
    if not settings.vlc_helper_protocol:
        return False
    return bool(settings.backend_origin.strip() or settings.public_app_origin.strip())
