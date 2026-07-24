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
from ..security import generate_session_token, hash_session_token, hash_token_hmac
from .desktop_helper_manifest_service import (
    DesktopHelperManifestError,
    DesktopHelperManifestOriginMismatch,
    desktop_helper_release_manifest_exists,
    get_desktop_helper_manifest_record_by_id,
    list_desktop_helper_manifest_records,
    open_verified_artifact,
    validate_artifact_relative_path,
)


RUNTIME_TO_PLATFORM = {
    "win-x64": "windows",
    "osx-arm64": "mac",
    "osx-x64": "mac",
    "linux-x64": "linux",
    "linux-arm64": "linux",
    "linux-musl-x64": "linux",
    "linux-musl-arm64": "linux",
}
PLATFORM_RUNTIME_ORDER = {
    "windows": ("win-x64",),
    "mac": ("osx-arm64", "osx-x64"),
    "linux": ("linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64"),
}
PLATFORM_PACKAGE_TARGET = {
    "windows": "windows-x64",
    "mac": "macos-dual-arch",
    "linux": "linux-universal",
}
SUPPORTED_HELPER_PLATFORMS = frozenset({"windows", "mac", "linux"})
RELEASE_NAME_PATTERN = re.compile(
    r"^elvern-vlc-opener-(?P<version>.+)-(?P<runtime>win-x64|osx-arm64|osx-x64|linux-x64|linux-arm64|linux-musl-x64|linux-musl-arm64)(?:\.zip)?$"
)
logger = logging.getLogger(__name__)
DESKTOP_HELPER_VERIFICATION_ACCESS_HASH_PURPOSE = "desktop.helper.verification.access"
DESKTOP_HELPER_ORIGIN_MISMATCH_NOTE = (
    "The available Helper package was built for a different Elvern server origin."
)


def _desktop_helper_verification_access_token_hash(settings: Settings, access_token: str) -> str:
    return hash_token_hmac(settings, purpose=DESKTOP_HELPER_VERIFICATION_ACCESS_HASH_PURPOSE, token=access_token)


def _legacy_desktop_helper_verification_access_token_hash(settings: Settings, access_token: str) -> str:
    return hash_session_token(access_token, settings.session_secret)


def _desktop_helper_verification_access_token_hash_candidates(settings: Settings, access_token: str) -> tuple[str, str]:
    return (
        _desktop_helper_verification_access_token_hash(settings, access_token),
        _legacy_desktop_helper_verification_access_token_hash(settings, access_token),
    )


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
    runtime_requirement: str,
) -> list[dict[str, object]]:
    normalized_channel = _normalize_channel(channel or settings.helper_default_channel)
    normalized_runtime_requirement = runtime_requirement.strip()
    if not re.fullmatch(r"[1-9][0-9]*\.x", normalized_runtime_requirement):
        raise ValueError("Legacy helper runtime requirement must use the form <major>.x")
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
                runtime_requirement=normalized_runtime_requirement,
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
    payloads, _origin_mismatch = _build_desktop_helper_release_payloads_with_diagnostics(
        settings,
        platform=platform,
        channel=channel,
        helper_arch=helper_arch,
    )
    return payloads


def _build_desktop_helper_release_payloads_with_diagnostics(
    settings: Settings,
    *,
    platform: str,
    channel: str | None = None,
    helper_arch: str | None = None,
) -> tuple[list[dict[str, object]], bool]:
    normalized_platform = normalize_desktop_helper_platform(platform)
    recommended_runtime_id = determine_recommended_runtime_id(normalized_platform, helper_arch=helper_arch)
    origin_mismatch = False
    try:
        manifest_releases = _list_helper_releases_from_manifest(
            settings,
            platform=normalized_platform,
            channel=channel,
        )
    except DesktopHelperManifestOriginMismatch:
        manifest_releases = []
        origin_mismatch = True
    release_source = manifest_releases
    if release_source is None:
        release_source = list_helper_releases(settings, platform=normalized_platform, channel=channel)
    package_target = PLATFORM_PACKAGE_TARGET[normalized_platform]
    package_releases = [
        row
        for row in release_source
        if str(row.get("deployment_mode") or "") == "self_contained"
        and str(row.get("package_target") or row.get("runtime_id") or "") == package_target
    ]
    if package_releases:
        latest_package = max(
            package_releases,
            key=lambda row: (_version_key(str(row["version"])), str(row["published_at"])),
        )
        return [_build_release_payload(latest_package, recommended=True)], origin_mismatch

    latest_by_runtime = _latest_release_by_runtime(release_source)
    payloads: list[dict[str, object]] = []
    for runtime_id in PLATFORM_RUNTIME_ORDER.get(normalized_platform, ()):
        row = latest_by_runtime.get(runtime_id)
        if row is None:
            continue
        payloads.append(_build_release_payload(row, recommended=runtime_id == recommended_runtime_id))
    return payloads, origin_mismatch


def _build_release_payload(row: dict[str, object], *, recommended: bool) -> dict[str, object]:
    runtime_id = str(row["runtime_id"])
    package_target = str(row.get("package_target") or runtime_id)
    external_runtime_required = bool(row.get("external_runtime_required", True))
    deployment_mode = str(
        row.get("deployment_mode")
        or ("framework_dependent" if external_runtime_required else "self_contained")
    )
    return {
        "id": int(row["id"]),
        "channel": str(row["channel"]),
        "runtime_id": runtime_id,
        "platform": str(row["platform"]),
        "package_target": package_target,
        "version": str(row["version"]),
        "filename": str(row["filename"]),
        "package_root": str(row.get("package_root") or ""),
        "installer_entrypoint": str(row.get("installer_entrypoint") or ""),
        "size_bytes": int(row["size_bytes"]),
        "sha256": str(row["sha256"]),
        "installer_manifest_sha256": (
            str(row["installer_manifest_sha256"])
            if row.get("installer_manifest_sha256")
            else None
        ),
        "installer_tree_manifest_path": (
            str(row["installer_tree_manifest_path"])
            if row.get("installer_tree_manifest_path")
            else None
        ),
        "installer_tree_manifest_sha256": (
            str(row["installer_tree_manifest_sha256"])
            if row.get("installer_tree_manifest_sha256")
            else None
        ),
        "package_binding": str(row.get("package_binding") or "legacy_unverified"),
        "published_at": str(row["published_at"]),
        "dotnet_runtime_required": (
            str(row["dotnet_runtime_required"])
            if row.get("dotnet_runtime_required")
            else None
        ),
        "download_url": f"/api/desktop-helper/releases/{int(row['id'])}/download",
        "deployment_mode": deployment_mode,
        "external_runtime_required": external_runtime_required,
        "runtime_family": str(row.get("runtime_family") or ""),
        "supported_runtime_ids": list(row.get("supported_runtime_ids") or [runtime_id]),
        "minimum_os_version": (
            str(row["minimum_os_version"])
            if row.get("minimum_os_version")
            else None
        ),
        "recommended": recommended,
    }


def get_desktop_helper_status(
    settings: Settings,
    *,
    user_id: int,
    platform: str,
    device_id: str | None,
    browser_user_agent: str | None,
    source_ip: str | None,
    same_host: bool = False,
    same_host_detection_source: str = "not_evaluated",
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

    notes: list[str] = []
    if normalized_platform == "linux" and same_host:
        vlc_detection = _resolve_vlc_detection(
            settings,
            normalized_platform,
            device_row,
            linux_same_host=True,
        )
        notes.append("Linux same-host playback does not require the desktop helper. Open in VLC launches installed VLC directly on the Elvern host.")
        notes.append("Keep using the same DGX Elvern URL for library browsing; browser playback remains a fallback only.")
        return {
            "device_id": normalized_device_id,
            "platform": normalized_platform,
            "helper_required": False,
            "state": "helper_not_required",
            "same_host": True,
            "same_host_detection_source": same_host_detection_source,
            "vlc_detection_state": vlc_detection["state"],
            "vlc_detection_path": vlc_detection["path"],
            "vlc_detection_checked_at": vlc_detection["checked_at"],
            "recommended_runtime_id": None,
            "last_seen_helper_version": device_row["helper_version"] if device_row else None,
            "last_seen_helper_platform": device_row["helper_platform"] if device_row else None,
            "last_seen_helper_arch": device_row["helper_arch"] if device_row else None,
            "last_seen_helper_at": device_row["helper_last_seen_at"] if device_row else None,
            "dotnet_runtime_required": None,
            "runtime_included": False,
            "latest_releases": [],
            "notes": notes,
        }

    latest_releases, origin_mismatch = _build_desktop_helper_release_payloads_with_diagnostics(
        settings,
        platform=normalized_platform,
        helper_arch=str(device_row["helper_arch"]) if device_row and device_row.get("helper_arch") else None,
    )
    runtime_included = bool(latest_releases) and all(
        release.get("external_runtime_required") is False for release in latest_releases
    )
    external_runtime_required = next(
        (
            str(release["dotnet_runtime_required"])
            for release in latest_releases
            if release.get("dotnet_runtime_required")
        ),
        None,
    )
    vlc_detection = _resolve_vlc_detection(
        settings,
        normalized_platform,
        device_row,
        linux_same_host=False,
    )
    recommended_runtime_id = determine_recommended_runtime_id(
        normalized_platform,
        helper_arch=str(device_row["helper_arch"]) if device_row and device_row["helper_arch"] else None,
    )

    if not latest_releases:
        notes.append(
            DESKTOP_HELPER_ORIGIN_MISMATCH_NOTE
            if origin_mismatch
            else "No official helper package is imported for this platform yet."
        )
        return {
            "device_id": normalized_device_id,
            "platform": normalized_platform,
            "helper_required": True,
            "state": "release_unavailable",
            "same_host": bool(same_host),
            "same_host_detection_source": same_host_detection_source,
            "vlc_detection_state": vlc_detection["state"],
            "vlc_detection_path": vlc_detection["path"],
            "vlc_detection_checked_at": vlc_detection["checked_at"],
            "recommended_runtime_id": recommended_runtime_id,
            "last_seen_helper_version": device_row["helper_version"] if device_row else None,
            "last_seen_helper_platform": device_row["helper_platform"] if device_row else None,
            "last_seen_helper_arch": device_row["helper_arch"] if device_row else None,
            "last_seen_helper_at": device_row["helper_last_seen_at"] if device_row else None,
            "dotnet_runtime_required": None,
            "runtime_included": False,
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

    if runtime_included:
        notes.append("The standard desktop helper package includes its runtime.")
    else:
        notes.append("Legacy compatibility packages may require an external runtime.")

    return {
        "device_id": normalized_device_id,
        "platform": normalized_platform,
        "helper_required": True,
        "state": state,
        "same_host": bool(same_host),
        "same_host_detection_source": same_host_detection_source,
        "vlc_detection_state": vlc_detection["state"],
        "vlc_detection_path": vlc_detection["path"],
        "vlc_detection_checked_at": vlc_detection["checked_at"],
        "recommended_runtime_id": recommended_runtime_id,
        "last_seen_helper_version": last_seen_helper_version,
        "last_seen_helper_platform": last_seen_helper_platform,
        "last_seen_helper_arch": last_seen_helper_arch,
        "last_seen_helper_at": last_seen_helper_at,
        "dotnet_runtime_required": external_runtime_required,
        "runtime_included": runtime_included,
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
    try:
        relative_path = validate_artifact_relative_path(payload["relative_path"])
    except DesktopHelperManifestError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Desktop helper release file is missing from the server",
        ) from exc
    file_path = settings.helper_releases_dir / relative_path
    payload["file_path"] = file_path
    payload["artifact_root"] = settings.helper_releases_dir
    payload["artifact_relative_path"] = relative_path
    return payload


def open_helper_release_download(settings: Settings, release_id: int) -> dict[str, object]:
    """Resolve a release, then open and verify its artifact via a single handle.

    The returned ``handle`` is the exact verified file description that must be
    streamed to the client, so the bytes served are guaranteed to be the ones
    that were hashed — the path is never reopened after verification.
    """
    record = get_helper_release_download_path(settings, release_id)
    file_path = Path(record["file_path"])
    try:
        size_bytes = int(record["size_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Desktop helper release file is missing from the server",
        ) from exc
    package_target = str(
        record.get("package_target") or record.get("runtime_id") or "unknown"
    )
    try:
        artifact_root = Path(record.get("artifact_root") or file_path.parent)
        artifact_relative_path = str(
            record.get("artifact_relative_path")
            or record.get("relative_path")
            or file_path.name
        )
        handle = open_verified_artifact(
            file_path,
            root_dir=artifact_root,
            relative_path=artifact_relative_path,
            size_bytes=size_bytes,
            sha256=str(record["sha256"]),
            package_target=package_target,
        )
    except DesktopHelperManifestError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Desktop helper release is unavailable because its package verification failed",
        ) from exc
    return {
        **record,
        "handle": handle,
        "size_bytes": size_bytes,
        "package_target": record.get("package_target"),
    }


def _list_helper_releases_from_manifest(
    settings: Settings,
    *,
    platform: str,
    channel: str | None = None,
) -> list[dict[str, object]] | None:
    normalized_channel = _normalize_channel(channel) if channel else None
    try:
        manifest_releases = list_desktop_helper_manifest_records(
            settings.helper_releases_dir,
            platform=platform,
            channel=normalized_channel,
            expected_bound_origin_sha256=_desktop_backend_origin_sha256(settings),
        )
    except DesktopHelperManifestOriginMismatch:
        raise
    except DesktopHelperManifestError as exc:
        logger.warning(
            "Desktop helper manifest validation failed for release listing (%s)",
            type(exc).__name__,
        )
        return (
            []
            if desktop_helper_release_manifest_exists(settings.helper_releases_dir)
            else None
        )
    if not manifest_releases:
        return None
    _ensure_no_manifest_db_release_collisions(settings, manifest_releases)
    return manifest_releases


def _get_helper_release_from_manifest(
    settings: Settings,
    release_id: int,
) -> dict[str, object] | None:
    try:
        manifest_release = get_desktop_helper_manifest_record_by_id(
            settings.helper_releases_dir,
            release_id,
            expected_bound_origin_sha256=_desktop_backend_origin_sha256(settings),
        )
    except DesktopHelperManifestOriginMismatch as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DESKTOP_HELPER_ORIGIN_MISMATCH_NOTE,
        ) from exc
    except DesktopHelperManifestError as exc:
        logger.warning(
            "Desktop helper manifest validation failed for release download (%s)",
            type(exc).__name__,
        )
        if desktop_helper_release_manifest_exists(settings.helper_releases_dir):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Desktop helper release is unavailable because its package verification failed",
            ) from exc
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
    same_host: bool = False,
    same_host_detection_source: str = "not_evaluated",
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

    if normalized_platform == "linux" and same_host:
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
            same_host=True,
            same_host_detection_source=same_host_detection_source,
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
    access_token_hash = _desktop_helper_verification_access_token_hash(settings, access_token)
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
            SET access_token_hash = ?,
                helper_version = ?,
                helper_platform = ?,
                helper_arch = ?,
                helper_vlc_detection_state = ?,
                helper_vlc_detection_path = ?,
                helper_vlc_detection_checked_at = ?,
                resolved_at = ?
            WHERE verification_id = ?
              AND access_token_hash = ?
            """,
            (
                _desktop_helper_verification_access_token_hash(settings, access_token),
                normalized_helper_version,
                normalized_helper_platform,
                normalized_helper_arch,
                normalized_helper_vlc_detection_state,
                normalized_helper_vlc_detection_path,
                now if normalized_helper_vlc_detection_state else None,
                now,
                verification_id,
                str(verification["access_token_hash"]),
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
    runtime_requirement: str,
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
                runtime_requirement,
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
    *,
    linux_same_host: bool,
) -> dict[str, str | None]:
    if platform == "linux" and linux_same_host:
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
        """,  # nosec B608 - assignments list is internal fixed column set
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
        if host in {"", "0.0.0.0", "::", "[::]"}:  # nosec B104 - intentional bind for Tailscale/LAN access
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{settings.port}"
    host = settings.bind_host
    if host in {"", "0.0.0.0", "::", "[::]"}:  # nosec B104 - intentional bind for Tailscale/LAN access
        host = "127.0.0.1"
    return f"http://{host}:{settings.port}"


def canonicalize_desktop_helper_origin(value: str) -> str:
    candidate = (value or "").strip()
    if (
        not candidate
        or candidate != value
        or "%" in candidate
        or any(
            ord(character) > 127
            or ord(character) < 32
            or ord(character) == 127
            for character in candidate
        )
    ):
        raise ValueError("Desktop helper origin is invalid")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Desktop helper origin is invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Desktop helper origin must be an absolute HTTP(S) origin")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if any(label.startswith("xn--") for label in host.split(".")):
        raise ValueError("Desktop helper origin IDN hostnames are unsupported")
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if scheme == "http" else 443
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{scheme}://{authority}"


def _desktop_backend_origin_sha256(settings: Settings) -> str:
    canonical_origin = canonicalize_desktop_helper_origin(_desktop_backend_origin(settings))
    return hashlib.sha256(canonical_origin.encode("utf-8")).hexdigest()


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
    token_hash, legacy_token_hash = _desktop_helper_verification_access_token_hash_candidates(settings, access_token)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                verification_id,
                access_token_hash,
                user_id,
                platform,
                device_id,
                expires_at,
                resolved_at
            FROM desktop_helper_verifications v
            JOIN users u ON u.id = v.user_id
            WHERE v.verification_id = ?
              AND v.access_token_hash IN (?, ?)
              AND v.expires_at > ?
              AND u.enabled = 1
            ORDER BY CASE v.access_token_hash WHEN ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (verification_id, token_hash, legacy_token_hash, now, token_hash),
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
