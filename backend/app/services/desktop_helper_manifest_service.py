from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import PROJECT_ROOT


HELPER_RELEASE_MANIFEST_PATH = (
    PROJECT_ROOT
    / "clients"
    / "desktop-vlc-opener"
    / "artifacts"
    / "packages"
    / "release-manifest.json"
)
HELPER_RELEASE_PACKAGES_DIR = HELPER_RELEASE_MANIFEST_PATH.parent
JS_SAFE_INTEGER_MASK = (1 << 53) - 1
SUPPORTED_DISTRIBUTABLE_ARTIFACT_KINDS = frozenset({"zip"})
MANIFEST_PLATFORM_FAMILY_MAP = {
    "windows": "windows",
    "macos": "mac",
}


class DesktopHelperManifestError(RuntimeError):
    """Raised when the desktop helper release manifest cannot be used safely."""


def list_desktop_helper_manifest_records(
    *,
    platform: str | None = None,
    channel: str | None = None,
) -> list[dict[str, object]]:
    manifest = _load_manifest_document()
    normalized_records = _normalize_manifest_records(manifest)
    return [
        dict(record)
        for record in normalized_records
        if (platform is None or record["platform"] == platform)
        and (channel is None or record["channel"] == channel)
    ]


def get_desktop_helper_manifest_record_by_id(release_id: int) -> dict[str, object] | None:
    manifest = _load_manifest_document()
    normalized_records = _normalize_manifest_records(manifest)
    for record in normalized_records:
        if int(record["id"]) == release_id:
            return dict(record)
    return None


def _load_manifest_document() -> dict[str, object]:
    if not HELPER_RELEASE_MANIFEST_PATH.exists():
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest is missing: {HELPER_RELEASE_MANIFEST_PATH}"
        )
    try:
        payload = json.loads(HELPER_RELEASE_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest could not be read: {HELPER_RELEASE_MANIFEST_PATH}"
        ) from exc
    if not isinstance(payload, dict):
        raise DesktopHelperManifestError("Desktop helper release manifest root must be an object")
    return payload


def _normalize_manifest_records(payload: dict[str, object]) -> list[dict[str, object]]:
    helper_version = _require_non_empty_string(payload.get("helper_version"), "helper_version")
    channel = _require_non_empty_string(payload.get("channel"), "channel")
    dotnet_runtime_major = _require_non_empty_string(
        payload.get("dotnet_runtime_major"),
        "dotnet_runtime_major",
    )
    _require_non_empty_string(payload.get("dotnet_runtime_display"), "dotnet_runtime_display")
    _require_non_empty_string(payload.get("package_name_prefix"), "package_name_prefix")
    created_at = _require_non_empty_string(payload.get("generated_at_utc"), "generated_at_utc")
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list):
        raise DesktopHelperManifestError("Desktop helper release manifest packages must be a list")

    normalized_records: list[dict[str, object]] = []
    seen_ids: dict[int, tuple[str, str, str, str]] = {}
    for index, raw_record in enumerate(raw_packages):
        if not isinstance(raw_record, dict):
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest package at index {index} must be an object"
            )
        record = _normalize_manifest_record(
            raw_record,
            helper_version=helper_version,
            channel=channel,
            dotnet_runtime_major=dotnet_runtime_major,
            created_at=created_at,
        )
        release_id = int(record["id"])
        record_identity = _manifest_record_identity_tuple(record)
        collision_identity = seen_ids.get(release_id)
        if collision_identity is not None and collision_identity != record_identity:
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest ID collision detected for release_id={release_id}"
            )
        seen_ids[release_id] = record_identity
        normalized_records.append(record)
    return normalized_records


def _normalize_manifest_record(
    payload: dict[str, object],
    *,
    helper_version: str,
    channel: str,
    dotnet_runtime_major: str,
    created_at: str,
) -> dict[str, object]:
    runtime_id = _require_non_empty_string(payload.get("runtime"), "runtime")
    platform_family = _require_non_empty_string(payload.get("platform_family"), "platform_family")
    platform = _normalize_platform_family(platform_family)
    artifact_kind = _require_non_empty_string(payload.get("artifact_kind"), "artifact_kind")
    if artifact_kind not in SUPPORTED_DISTRIBUTABLE_ARTIFACT_KINDS:
        raise DesktopHelperManifestError(
            f"Unsupported desktop helper artifact kind in manifest: {artifact_kind}"
        )
    package_name = _require_non_empty_string(payload.get("package_name"), "package_name")
    filename = _require_non_empty_string(payload.get("filename"), "filename")
    relative_path = _require_non_empty_string(payload.get("relative_path"), "relative_path")
    sha256 = _require_non_empty_string(payload.get("sha256"), "sha256")
    published_at = _require_non_empty_string(payload.get("generated_at_utc"), "generated_at_utc")

    raw_size_bytes = payload.get("size_bytes")
    if not isinstance(raw_size_bytes, int) or raw_size_bytes < 0:
        raise DesktopHelperManifestError("Desktop helper release manifest size_bytes must be a non-negative integer")
    size_bytes = int(raw_size_bytes)

    file_path = (HELPER_RELEASE_PACKAGES_DIR / relative_path).resolve()
    try:
        file_path.relative_to(HELPER_RELEASE_PACKAGES_DIR.resolve())
    except ValueError as exc:
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest path escapes packages directory: {relative_path}"
        ) from exc
    if file_path.name != filename:
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest filename mismatch for {relative_path}"
        )
    if not file_path.exists() or not file_path.is_file():
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest artifact is missing: {file_path}"
        )

    release_id = _generate_stable_release_id(
        channel=channel,
        runtime_id=runtime_id,
        version=helper_version,
        filename=filename,
    )
    return {
        "id": release_id,
        "channel": channel,
        "runtime_id": runtime_id,
        "platform": platform,
        "version": helper_version,
        "filename": filename,
        "relative_path": relative_path,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "dotnet_runtime_required": f"{dotnet_runtime_major}.x",
        "published_at": published_at,
        "created_at": created_at,
        "file_path": file_path,
        "artifact_kind": artifact_kind,
        "package_name": package_name,
    }


def _generate_stable_release_id(
    *,
    channel: str,
    runtime_id: str,
    version: str,
    filename: str,
) -> int:
    seed = "\0".join((channel, runtime_id, version, filename)).encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    candidate = int.from_bytes(digest[:8], "big") & JS_SAFE_INTEGER_MASK
    return candidate or 1


def _normalize_platform_family(platform_family: str) -> str:
    normalized = platform_family.strip().lower()
    platform = MANIFEST_PLATFORM_FAMILY_MAP.get(normalized)
    if platform is None:
        raise DesktopHelperManifestError(
            f"Unsupported desktop helper platform family in manifest: {platform_family}"
        )
    return platform


def _manifest_record_identity_tuple(record: dict[str, object]) -> tuple[str, str, str, str]:
    return (
        str(record["channel"]),
        str(record["runtime_id"]),
        str(record["version"]),
        str(record["filename"]),
    )


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest field {field_name} must be a string"
        )
    normalized = value.strip()
    if not normalized:
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest field {field_name} must not be empty"
        )
    return normalized
