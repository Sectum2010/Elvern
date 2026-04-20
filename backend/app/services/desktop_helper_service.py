from __future__ import annotations

import hashlib
import logging
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, status
from urllib.parse import urlencode
from urllib.parse import urlsplit

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..security import generate_session_token, hash_session_token
from .desktop_helper_manifest_service import (
    DesktopHelperManifestError,
    get_desktop_helper_manifest_record_by_id,
    list_desktop_helper_manifest_records,
)


RUNTIME_TO_PLATFORM = {
    "win-x64": "windows",
    "osx-arm64": "mac",
    "osx-x64": "mac",
}
PLATFORM_RUNTIME_ORDER = {
    "windows": ("win-x64",),
    "mac": ("osx-arm64", "osx-x64"),
}
SUPPORTED_HELPER_PLATFORMS = frozenset({"windows", "mac", "linux"})
RELEASE_NAME_PATTERN = re.compile(
    r"^elvern-vlc-opener-(?P<version>.+)-(?P<runtime>win-x64|osx-arm64|osx-x64)(?:\.zip)?$"
)
logger = logging.getLogger(__name__)


def normalize_desktop_helper_platform(platform: str | None) -> str:
    normalized = (platform or "").strip().lower()
    if normalized not in SUPPORTED_HELPER_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported desktop helper platform",
        )
    return normalized


def normalize_device_id(device_id: str | None) -> str | None:
    normalized = (device_id or "").strip()
    if not normalized:
        return None
    if len(normalized) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device ID is too long",
        )
    return normalized


def import_helper_release_artifacts(
    settings: Settings,
    sources: Iterable[str | Path],
    *,
    channel: str | None = None,
) -> list[dict[str, object]]:
    normalized_channel = _normalize_channel(channel or settings.helper_default_channel)
    imported: list[dict[str, object]] = []
    for source in sources:
        source_path = Path(source).expanduser().resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Helper release source does not exist: {source_path}")
        imported.append(
            _import_helper_release_artifact(
                settings,
                source_path,
                channel=normalized_channel,
            )
        )
    return imported


def list_helper_releases(
    settings: Settings,
    *,
    platform: str | None = None,
    channel: str | None = None,
) -> list[dict[str, object]]:
    query = """
        SELECT
            id,
            channel,
            runtime_id,
            platform,
            version,
            filename,
            relative_path,
            sha256,
            size_bytes,
            dotnet_runtime_required,
            published_at,
            created_at
        FROM helper_releases
    """
    params: list[object] = []
    clauses: list[str] = []
    if platform:
        clauses.append("platform = ?")
        params.append(normalize_desktop_helper_platform(platform))
    if channel:
        clauses.append("channel = ?")
        params.append(_normalize_channel(channel))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    with get_connection(settings) as connection:
        rows = connection.execute(query, params).fetchall()

    releases = [dict(row) for row in rows]
    releases.sort(
        key=lambda row: (
            row["platform"],
            row["runtime_id"],
            _version_key(str(row["version"])),
            row["published_at"],
        ),
        reverse=True,
    )
    return releases


def build_desktop_helper_release_payloads(
    settings: Settings,
    *,
    platform: str,
    channel: str | None = None,
    helper_arch: str | None = None,
) -> list[dict[str, object]]:
    normalized_platform = normalize_desktop_helper_platform(platform)
    recommended_runtime_id = determine_recommended_runtime_id(normalized_platform, helper_arch=helper_arch)
    manifest_releases = _list_helper_releases_from_manifest(
        settings,
        platform=normalized_platform,
        channel=channel,
    )
    release_source = manifest_releases
    if release_source is None:
        release_source = list_helper_releases(settings, platform=normalized_platform, channel=channel)
    latest_by_runtime = _latest_release_by_runtime(release_source)
    payloads: list[dict[str, object]] = []
    for runtime_id in PLATFORM_RUNTIME_ORDER.get(normalized_platform, ()):
        row = latest_by_runtime.get(runtime_id)
        if row is None:
            continue
        payloads.append(
            {
                "id": int(row["id"]),
                "channel": str(row["channel"]),
                "runtime_id": str(row["runtime_id"]),
                "platform": str(row["platform"]),
                "version": str(row["version"]),
                "filename": str(row["filename"]),
                "size_bytes": int(row["size_bytes"]),
                "sha256": str(row["sha256"]),
                "published_at": str(row["published_at"]),
                "dotnet_runtime_required": str(row["dotnet_runtime_required"]),
                "download_url": f"/api/desktop-helper/releases/{int(row['id'])}/download",
                "recommended": runtime_id == recommended_runtime_id,
            }
        )
    return payloads


def get_desktop_helper_status(
    settings: Settings,
    *,
    user_id: int,
    platform: str,
    device_id: str | None,
    browser_user_agent: str | None,
    source_ip: str | None,
) -> dict[str, object]:
    normalized_platform = normalize_desktop_helper_platform(platform)
    normalized_device_id = normalize_device_id(device_id)
    device_row = None
    if normalized_device_id:
        device_row = record_client_device_app_seen(
            settings,
            device_id=normalized_device_id,
            user_id=user_id,
            browser_platform=normalized_platform,
            browser_user_agent=browser_user_agent,
            ip_address=source_ip,
        )
    latest_releases = build_desktop_helper_release_payloads(
        settings,
        platform=normalized_platform,
        helper_arch=str(device_row["helper_arch"]) if device_row and device_row.get("helper_arch") else None,
    )

    notes: list[str] = []
    vlc_detection = _resolve_vlc_detection(settings, normalized_platform, device_row)
    recommended_runtime_id = determine_recommended_runtime_id(
        normalized_platform,
        helper_arch=str(device_row["helper_arch"]) if device_row and device_row["helper_arch"] else None,
    )

    if normalized_platform == "linux":
        notes.append("Linux same-host playback does not require the desktop helper. Open in VLC launches installed VLC directly on the Elvern host.")
        notes.append("Keep using the same DGX Elvern URL for library browsing; browser playback remains a fallback only.")
        return {
            "device_id": normalized_device_id,
            "platform": normalized_platform,
            "helper_required": False,
            "state": "helper_not_required",
            "vlc_detection_state": vlc_detection["state"],
            "vlc_detection_path": vlc_detection["path"],
            "vlc_detection_checked_at": vlc_detection["checked_at"],
            "recommended_runtime_id": None,
            "last_seen_helper_version": device_row["helper_version"] if device_row else None,
            "last_seen_helper_platform": device_row["helper_platform"] if device_row else None,
            "last_seen_helper_arch": device_row["helper_arch"] if device_row else None,
            "last_seen_helper_at": device_row["helper_last_seen_at"] if device_row else None,
            "dotnet_runtime_required": None,
            "latest_releases": [],
            "notes": notes,
        }

    if not latest_releases:
        notes.append("No official helper package is imported for this platform yet.")
        notes.append("Windows and macOS helpers require .NET 8 Runtime on the client machine.")
        return {
            "device_id": normalized_device_id,
            "platform": normalized_platform,
            "helper_required": True,
            "state": "release_unavailable",
            "vlc_detection_state": vlc_detection["state"],
            "vlc_detection_path": vlc_detection["path"],
            "vlc_detection_checked_at": vlc_detection["checked_at"],
            "recommended_runtime_id": recommended_runtime_id,
            "last_seen_helper_version": device_row["helper_version"] if device_row else None,
            "last_seen_helper_platform": device_row["helper_platform"] if device_row else None,
            "last_seen_helper_arch": device_row["helper_arch"] if device_row else None,
            "last_seen_helper_at": device_row["helper_last_seen_at"] if device_row else None,
            "dotnet_runtime_required": ".NET 8 Runtime required",
            "latest_releases": latest_releases,
            "notes": notes,
        }

    last_seen_helper_version = str(device_row["helper_version"]) if device_row and device_row["helper_version"] else None
    last_seen_helper_platform = str(device_row["helper_platform"]) if device_row and device_row["helper_platform"] else None
    last_seen_helper_arch = str(device_row["helper_arch"]) if device_row and device_row["helper_arch"] else None
    last_seen_helper_at = str(device_row["helper_last_seen_at"]) if device_row and device_row["helper_last_seen_at"] else None

    if not last_seen_helper_version or last_seen_helper_platform != normalized_platform:
        state = "unknown"
        notes.append("Helper install state becomes known after this browser/device successfully launches VLC through Elvern at least once.")
    else:
        latest_version = _latest_version_for_platform(
            latest_releases,
            recommended_runtime_id=recommended_runtime_id,
        )
        if latest_version is not None and _version_key(last_seen_helper_version) >= _version_key(latest_version):
            state = "up_to_date"
            notes.append("This device has already reported the latest helper version back to Elvern.")
        else:
            state = "update_available"
            notes.append("A newer helper package is available for this desktop platform.")

    notes.append("Windows and macOS helpers require .NET 8 Runtime on the client machine.")

    return {
        "device_id": normalized_device_id,
        "platform": normalized_platform,
        "helper_required": True,
        "state": state,
        "vlc_detection_state": vlc_detection["state"],
        "vlc_detection_path": vlc_detection["path"],
        "vlc_detection_checked_at": vlc_detection["checked_at"],
        "recommended_runtime_id": recommended_runtime_id,
        "last_seen_helper_version": last_seen_helper_version,
        "last_seen_helper_platform": last_seen_helper_platform,
        "last_seen_helper_arch": last_seen_helper_arch,
        "last_seen_helper_at": last_seen_helper_at,
        "dotnet_runtime_required": ".NET 8 Runtime required",
        "latest_releases": latest_releases,
        "notes": notes,
    }


def get_helper_release_download_path(settings: Settings, release_id: int) -> dict[str, object]:
    manifest_release = _get_helper_release_from_manifest(settings, release_id)
    if manifest_release is not None:
        return manifest_release

    payload = _get_helper_release_row_by_id(settings, release_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Desktop helper release was not found",
        )
    file_path = (settings.helper_releases_dir / str(payload["relative_path"])).resolve()
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Desktop helper release file is missing from the server",
        )
    payload["file_path"] = file_path
    return payload


def _list_helper_releases_from_manifest(
    settings: Settings,
    *,
    platform: str,
    channel: str | None = None,
) -> list[dict[str, object]] | None:
    normalized_channel = _normalize_channel(channel) if channel else None
    try:
        manifest_releases = list_desktop_helper_manifest_records(
            platform=platform,
            channel=normalized_channel,
        )
    except DesktopHelperManifestError as exc:
        logger.warning(
            "Desktop helper manifest unavailable for release listing; falling back to DB catalog: %s",
            exc,
        )
        return None
    if not manifest_releases:
        return None
    _ensure_no_manifest_db_release_collisions(settings, manifest_releases)
    return manifest_releases


def _get_helper_release_from_manifest(
    settings: Settings,
    release_id: int,
) -> dict[str, object] | None:
    try:
        manifest_release = get_desktop_helper_manifest_record_by_id(release_id)
    except DesktopHelperManifestError as exc:
        logger.warning(
            "Desktop helper manifest unavailable for release download; falling back to DB catalog: %s",
            exc,
        )
        return None
    if manifest_release is None:
        return None
    _ensure_no_manifest_db_release_collisions(settings, [manifest_release])
    return manifest_release


def _get_helper_release_row_by_id(
    settings: Settings,
    release_id: int,
) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                id,
                channel,
                runtime_id,
                platform,
                version,
                filename,
                relative_path,
                sha256,
                size_bytes,
                dotnet_runtime_required,
                published_at,
                created_at
            FROM helper_releases
            WHERE id = ?
            LIMIT 1
            """,
            (release_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def _ensure_no_manifest_db_release_collisions(
    settings: Settings,
    manifest_releases: Iterable[dict[str, object]],
) -> None:
    for manifest_release in manifest_releases:
        db_release = _get_helper_release_row_by_id(settings, int(manifest_release["id"]))
        if db_release is None:
            continue
        if _helper_release_identity_tuple(db_release) != _helper_release_identity_tuple(manifest_release):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Desktop helper manifest release ID collides with the DB helper catalog",
            )


def _helper_release_identity_tuple(release: dict[str, object]) -> tuple[str, str, str, str]:
    return (
        str(release["channel"]),
        str(release["runtime_id"]),
        str(release["version"]),
        str(release["filename"]),
    )


def record_client_device_app_seen(
    settings: Settings,
    *,
    device_id: str,
    user_id: int,
    browser_platform: str | None,
    browser_user_agent: str | None,
    ip_address: str | None,
) -> dict[str, object]:
    normalized_device_id = normalize_device_id(device_id)
    if normalized_device_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device ID is required",
        )
    now = utcnow_iso()
    with get_connection(settings) as connection:
        _upsert_client_device(
            connection,
            device_id=normalized_device_id,
            user_id=user_id,
            browser_platform=browser_platform,
            browser_user_agent=browser_user_agent,
            helper_platform=None,
            helper_arch=None,
            helper_version=None,
            helper_channel=None,
            helper_vlc_detection_state=None,
            helper_vlc_detection_path=None,
            helper_vlc_detection_checked_at=None,
            app_seen_at=now,
            helper_seen_at=None,
            ip_address=ip_address,
        )
        row = connection.execute(
            "SELECT * FROM client_devices WHERE device_id = ? LIMIT 1",
            (normalized_device_id,),
        ).fetchone()
        connection.commit()
    return dict(row) if row is not None else {}


def record_helper_resolution(
    settings: Settings,
    *,
    handoff_id: str,
    device_id: str | None,
    user_id: int,
    helper_version: str | None,
    helper_platform: str | None,
    helper_arch: str | None,
    helper_vlc_detection_state: str | None = None,
    helper_vlc_detection_path: str | None = None,
    source_ip: str | None,
) -> None:
    now = utcnow_iso()
    normalized_device_id = normalize_device_id(device_id)
    normalized_helper_platform = _normalize_optional_platform_name(helper_platform)
    normalized_helper_arch = _normalize_optional_arch(helper_arch)
    normalized_helper_version = (helper_version or "").strip() or None
    normalized_helper_vlc_detection_state = _normalize_optional_vlc_detection_state(helper_vlc_detection_state)
    normalized_helper_vlc_detection_path = (
        (helper_vlc_detection_path or "").strip() or None
        if normalized_helper_vlc_detection_state == "installed"
        else None
    )

    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE desktop_vlc_handoffs
            SET helper_version = ?,
                helper_platform = ?,
                helper_arch = ?,
                helper_vlc_detection_state = ?,
                helper_vlc_detection_path = ?,
                helper_vlc_detection_checked_at = ?,
                resolved_at = ?
            WHERE handoff_id = ?
            """,
            (
                normalized_helper_version,
                normalized_helper_platform,
                normalized_helper_arch,
                normalized_helper_vlc_detection_state,
                normalized_helper_vlc_detection_path,
                now if normalized_helper_vlc_detection_state else None,
                now,
                handoff_id,
            ),
        )
        if normalized_device_id:
            _upsert_client_device(
                connection,
                device_id=normalized_device_id,
                user_id=user_id,
                browser_platform=None,
                browser_user_agent=None,
                helper_platform=normalized_helper_platform,
                helper_arch=normalized_helper_arch,
                helper_version=normalized_helper_version,
                helper_channel=settings.helper_default_channel,
                helper_vlc_detection_state=normalized_helper_vlc_detection_state,
                helper_vlc_detection_path=normalized_helper_vlc_detection_path,
                helper_vlc_detection_checked_at=now if normalized_helper_vlc_detection_state else None,
                app_seen_at=None,
                helper_seen_at=now,
                ip_address=source_ip,
            )
        connection.commit()


def create_desktop_helper_verification(
    settings: Settings,
    *,
    user_id: int,
    platform: str,
    device_id: str | None,
    browser_user_agent: str | None,
    source_ip: str | None,
) -> dict[str, object]:
    normalized_platform = normalize_desktop_helper_platform(platform)
    normalized_device_id = normalize_device_id(device_id)
    if normalized_device_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device ID is required for desktop verification",
        )

    record_client_device_app_seen(
        settings,
        device_id=normalized_device_id,
        user_id=user_id,
        browser_platform=normalized_platform,
        browser_user_agent=browser_user_agent,
        ip_address=source_ip,
    )

    if normalized_platform == "linux":
        detection = _probe_linux_vlc_detection(settings)
        _record_helper_device_detection(
            settings,
            device_id=normalized_device_id,
            user_id=user_id,
            helper_platform="linux",
            helper_arch=None,
            helper_version=None,
            helper_vlc_detection_state=detection["state"],
            helper_vlc_detection_path=detection["path"],
            source_ip=source_ip,
        )
        status_payload = get_desktop_helper_status(
            settings,
            user_id=user_id,
            platform=normalized_platform,
            device_id=normalized_device_id,
            browser_user_agent=browser_user_agent,
            source_ip=source_ip,
        )
        return {
            "mode": "host",
            "protocol_url": None,
            "expires_at": None,
            "status": status_payload,
        }

    backend_origin = _desktop_backend_origin(settings)
    if not _desktop_helper_supported(settings) or not backend_origin:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Desktop helper verification needs a configured backend/app origin first.",
        )

    cleanup_desktop_helper_verifications(settings)
    verification_id = generate_session_token()
    access_token = generate_session_token()
    access_token_hash = hash_session_token(access_token, settings.session_secret)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.playback_token_ttl_seconds)
    now_iso = now.isoformat()
    expires_at_iso = expires_at.isoformat()

    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO desktop_helper_verifications (
                verification_id,
                access_token_hash,
                user_id,
                platform,
                device_id,
                created_at,
                expires_at,
                source_ip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verification_id,
                access_token_hash,
                user_id,
                normalized_platform,
                normalized_device_id,
                now_iso,
                expires_at_iso,
                source_ip,
            ),
        )
        connection.commit()

    return {
        "mode": "helper",
        "protocol_url": build_vlc_helper_verify_url(
            settings,
            backend_origin=backend_origin,
            verification_id=verification_id,
            access_token=access_token,
        ),
        "expires_at": expires_at_iso,
        "status": None,
    }


def resolve_desktop_helper_verification(
    settings: Settings,
    *,
    verification_id: str,
    access_token: str,
    helper_version: str | None,
    helper_platform: str | None,
    helper_arch: str | None,
    helper_vlc_detection_state: str | None = None,
    helper_vlc_detection_path: str | None = None,
    source_ip: str | None,
) -> dict[str, object]:
    verification = _require_desktop_helper_verification(
        settings,
        verification_id=verification_id,
        access_token=access_token,
    )
    now = utcnow_iso()
    normalized_helper_platform = _normalize_optional_platform_name(helper_platform)
    normalized_helper_arch = _normalize_optional_arch(helper_arch)
    normalized_helper_version = (helper_version or "").strip() or None
    normalized_helper_vlc_detection_state = _normalize_optional_vlc_detection_state(helper_vlc_detection_state)
    normalized_helper_vlc_detection_path = (
        (helper_vlc_detection_path or "").strip() or None
        if normalized_helper_vlc_detection_state == "installed"
        else None
    )

    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE desktop_helper_verifications
            SET helper_version = ?,
                helper_platform = ?,
                helper_arch = ?,
                helper_vlc_detection_state = ?,
                helper_vlc_detection_path = ?,
                helper_vlc_detection_checked_at = ?,
                resolved_at = ?
            WHERE verification_id = ?
            """,
            (
                normalized_helper_version,
                normalized_helper_platform,
                normalized_helper_arch,
                normalized_helper_vlc_detection_state,
                normalized_helper_vlc_detection_path,
                now if normalized_helper_vlc_detection_state else None,
                now,
                verification_id,
            ),
        )
        connection.commit()

    _record_helper_device_detection(
        settings,
        device_id=str(verification["device_id"]),
        user_id=int(verification["user_id"]),
        helper_platform=normalized_helper_platform,
        helper_arch=normalized_helper_arch,
        helper_version=normalized_helper_version,
        helper_vlc_detection_state=normalized_helper_vlc_detection_state,
        helper_vlc_detection_path=normalized_helper_vlc_detection_path,
        source_ip=source_ip,
    )
    return {
        "message": "Desktop helper verification recorded.",
    }


def cleanup_desktop_helper_verifications(settings: Settings) -> None:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            DELETE FROM desktop_helper_verifications
            WHERE expires_at <= ?
               OR resolved_at IS NOT NULL
            """,
            (now,),
        )
        connection.commit()


def determine_recommended_runtime_id(platform: str, helper_arch: str | None) -> str | None:
    normalized_platform = normalize_desktop_helper_platform(platform)
    if normalized_platform == "windows":
        return "win-x64"
    if normalized_platform == "mac":
        if (helper_arch or "").strip().lower() == "x64":
            return "osx-x64"
        return "osx-arm64"
    return None


def _import_helper_release_artifact(
    settings: Settings,
    source_path: Path,
    *,
    channel: str,
) -> dict[str, object]:
    metadata = _parse_release_artifact_name(source_path.name)
    version = metadata["version"]
    runtime_id = metadata["runtime_id"]
    platform = metadata["platform"]
    filename = f"elvern-vlc-opener-{version}-{runtime_id}.zip"
    destination_dir = settings.helper_releases_dir / channel / runtime_id / version
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_path = destination_dir / filename

    published_at = datetime.fromtimestamp(
        source_path.stat().st_mtime,
        tz=timezone.utc,
    ).isoformat()

    if source_path.is_dir():
        with tempfile.TemporaryDirectory(prefix="elvern-helper-release-") as temporary_dir:
            archive_base = Path(temporary_dir) / f"elvern-vlc-opener-{version}-{runtime_id}"
            archive_path = Path(
                shutil.make_archive(
                    str(archive_base),
                    "zip",
                    root_dir=source_path.parent,
                    base_dir=source_path.name,
                )
            )
            shutil.copy2(archive_path, destination_path)
    else:
        shutil.copy2(source_path, destination_path)

    sha256 = _sha256_for_file(destination_path)
    size_bytes = destination_path.stat().st_size
    relative_path = str(destination_path.relative_to(settings.helper_releases_dir))
    created_at = utcnow_iso()

    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO helper_releases (
                channel,
                runtime_id,
                platform,
                version,
                filename,
                relative_path,
                sha256,
                size_bytes,
                dotnet_runtime_required,
                published_at,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel, runtime_id, version) DO UPDATE SET
                platform = excluded.platform,
                filename = excluded.filename,
                relative_path = excluded.relative_path,
                sha256 = excluded.sha256,
                size_bytes = excluded.size_bytes,
                dotnet_runtime_required = excluded.dotnet_runtime_required,
                published_at = excluded.published_at
            """,
            (
                channel,
                runtime_id,
                platform,
                version,
                filename,
                relative_path,
                sha256,
                size_bytes,
                "8.x",
                published_at,
                created_at,
            ),
        )
        row = connection.execute(
            """
            SELECT
                id,
                channel,
                runtime_id,
                platform,
                version,
                filename,
                relative_path,
                sha256,
                size_bytes,
                dotnet_runtime_required,
                published_at,
                created_at
            FROM helper_releases
            WHERE channel = ? AND runtime_id = ? AND version = ?
            LIMIT 1
            """,
            (channel, runtime_id, version),
        ).fetchone()
        connection.commit()

    return dict(row) if row is not None else {}


def _parse_release_artifact_name(name: str) -> dict[str, str]:
    match = RELEASE_NAME_PATTERN.match(name)
    if match is None:
        raise ValueError(
            "Helper release names must look like elvern-vlc-opener-<version>-<runtime>.zip"
        )
    runtime_id = match.group("runtime")
    platform = RUNTIME_TO_PLATFORM.get(runtime_id)
    if platform is None:
        raise ValueError(f"Unsupported helper runtime: {runtime_id}")
    return {
        "version": match.group("version"),
        "runtime_id": runtime_id,
        "platform": platform,
    }


def _normalize_channel(channel: str) -> str:
    normalized = channel.strip().lower()
    if not normalized or not normalized.replace("-", "").isalnum():
        raise ValueError("Helper release channel must be alphanumeric and may include hyphens")
    return normalized


def _sha256_for_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _latest_release_by_runtime(releases: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for row in releases:
        runtime_id = str(row["runtime_id"])
        existing = latest.get(runtime_id)
        if existing is None or _version_key(str(row["version"])) > _version_key(str(existing["version"])):
            latest[runtime_id] = row
    return latest


def _latest_version_for_platform(
    releases: list[dict[str, object]],
    *,
    recommended_runtime_id: str | None,
) -> str | None:
    if recommended_runtime_id:
        for row in releases:
            if str(row["runtime_id"]) == recommended_runtime_id:
                return str(row["version"])
    if not releases:
        return None
    return max((str(row["version"]) for row in releases), key=_version_key)


def _normalize_optional_platform_name(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in {"windows", "mac", "linux"}:
        return None
    return normalized


def _normalize_optional_arch(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"x64", "amd64"}:
        return "x64"
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    return normalized


def _normalize_optional_vlc_detection_state(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if normalized not in {"installed", "not_detected", "detection_unavailable"}:
        return None
    return normalized


def _resolve_vlc_detection(
    settings: Settings,
    platform: str,
    device_row: dict[str, object] | None,
) -> dict[str, str | None]:
    if platform == "linux":
        return _probe_linux_vlc_detection(settings)

    detection_state = _normalize_optional_vlc_detection_state(
        str(device_row["helper_vlc_detection_state"]) if device_row and device_row.get("helper_vlc_detection_state") else None
    )
    return {
        "state": detection_state or "detection_unavailable",
        "path": str(device_row["helper_vlc_detection_path"]) if device_row and device_row.get("helper_vlc_detection_path") else None,
        "checked_at": (
            str(device_row["helper_vlc_detection_checked_at"])
            if device_row and device_row.get("helper_vlc_detection_checked_at")
            else None
        ),
    }


def _probe_linux_vlc_detection(settings: Settings) -> dict[str, str | None]:
    linux_vlc_path = settings.vlc_path_linux
    if linux_vlc_path and Path(linux_vlc_path).exists():
        return {
            "state": "installed",
            "path": linux_vlc_path,
            "checked_at": utcnow_iso(),
        }
    return {
        "state": "not_detected",
        "path": None,
        "checked_at": utcnow_iso(),
    }


def _record_helper_device_detection(
    settings: Settings,
    *,
    device_id: str,
    user_id: int,
    helper_platform: str | None,
    helper_arch: str | None,
    helper_version: str | None,
    helper_vlc_detection_state: str | None,
    helper_vlc_detection_path: str | None,
    source_ip: str | None,
) -> None:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        _upsert_client_device(
            connection,
            device_id=device_id,
            user_id=user_id,
            browser_platform=None,
            browser_user_agent=None,
            helper_platform=helper_platform,
            helper_arch=helper_arch,
            helper_version=helper_version,
            helper_channel=settings.helper_default_channel,
            helper_vlc_detection_state=helper_vlc_detection_state,
            helper_vlc_detection_path=helper_vlc_detection_path,
            helper_vlc_detection_checked_at=now if helper_vlc_detection_state else None,
            app_seen_at=None,
            helper_seen_at=now,
            ip_address=source_ip,
        )
        connection.commit()


def _upsert_client_device(
    connection: sqlite3.Connection,
    *,
    device_id: str,
    user_id: int | None,
    browser_platform: str | None,
    browser_user_agent: str | None,
    helper_platform: str | None,
    helper_arch: str | None,
    helper_version: str | None,
    helper_channel: str | None,
    helper_vlc_detection_state: str | None,
    helper_vlc_detection_path: str | None,
    helper_vlc_detection_checked_at: str | None,
    app_seen_at: str | None,
    helper_seen_at: str | None,
    ip_address: str | None,
) -> None:
    existing = connection.execute(
        "SELECT id FROM client_devices WHERE device_id = ? LIMIT 1",
        (device_id,),
    ).fetchone()
    now = utcnow_iso()
    if existing is None:
        connection.execute(
            """
            INSERT INTO client_devices (
                device_id,
                last_user_id,
                browser_platform,
                browser_user_agent,
                helper_platform,
                helper_arch,
                helper_version,
                helper_channel,
                helper_last_seen_at,
                helper_vlc_detection_state,
                helper_vlc_detection_path,
                helper_vlc_detection_checked_at,
                app_last_seen_at,
                last_ip_address,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                user_id,
                browser_platform,
                browser_user_agent,
                helper_platform,
                helper_arch,
                helper_version,
                helper_channel or "stable",
                helper_seen_at,
                helper_vlc_detection_state,
                helper_vlc_detection_path,
                helper_vlc_detection_checked_at,
                app_seen_at,
                ip_address,
                now,
                now,
            ),
        )
        return

    assignments = [
        "last_user_id = COALESCE(?, last_user_id)",
        "browser_platform = COALESCE(?, browser_platform)",
        "browser_user_agent = COALESCE(?, browser_user_agent)",
        "helper_platform = COALESCE(?, helper_platform)",
        "helper_arch = COALESCE(?, helper_arch)",
        "helper_version = COALESCE(?, helper_version)",
        "helper_channel = COALESCE(?, helper_channel)",
        "helper_last_seen_at = COALESCE(?, helper_last_seen_at)",
        "helper_vlc_detection_state = COALESCE(?, helper_vlc_detection_state)",
        "helper_vlc_detection_path = COALESCE(?, helper_vlc_detection_path)",
        "helper_vlc_detection_checked_at = COALESCE(?, helper_vlc_detection_checked_at)",
        "app_last_seen_at = COALESCE(?, app_last_seen_at)",
        "last_ip_address = COALESCE(?, last_ip_address)",
        "updated_at = ?",
    ]
    connection.execute(
        f"""
        UPDATE client_devices
        SET {", ".join(assignments)}
        WHERE device_id = ?
        """,
        (
            user_id,
            browser_platform,
            browser_user_agent,
            helper_platform,
            helper_arch,
            helper_version,
            helper_channel,
            helper_seen_at,
            helper_vlc_detection_state,
            helper_vlc_detection_path,
            helper_vlc_detection_checked_at,
            app_seen_at,
            ip_address,
            now,
            device_id,
        ),
    )


def build_vlc_helper_verify_url(
    settings: Settings,
    *,
    backend_origin: str,
    verification_id: str,
    access_token: str,
) -> str:
    params = urlencode(
        {
            "api": backend_origin,
            "verification": verification_id,
            "token": access_token,
        }
    )
    return f"{settings.vlc_helper_protocol}://verify?{params}"


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


def _require_desktop_helper_verification(
    settings: Settings,
    *,
    verification_id: str,
    access_token: str,
) -> dict[str, object]:
    cleanup_desktop_helper_verifications(settings)
    token_hash = hash_session_token(access_token, settings.session_secret)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                verification_id,
                user_id,
                platform,
                device_id,
                expires_at,
                resolved_at
            FROM desktop_helper_verifications
            WHERE verification_id = ?
              AND access_token_hash = ?
              AND expires_at > ?
            LIMIT 1
            """,
            (verification_id, token_hash, now),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Desktop helper verification not found or expired",
        )
    if row["resolved_at"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Desktop helper verification was already used",
        )
    return dict(row)


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    parts = re.split(r"[.\-+_]", version)
    normalized: list[tuple[int, int | str]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            normalized.append((0, int(part)))
        else:
            normalized.append((1, part.lower()))
    return tuple(normalized)
