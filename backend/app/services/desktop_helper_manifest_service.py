from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

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
    "mac": "mac",
    "linux": "linux",
}
RELEASE_MANIFEST_V2_SCHEMA = "desktop-helper-release-manifest-v2"
SELF_CONTAINED_MODE = "self_contained"
PACKAGE_RUNTIME_CONTRACTS = {
    "windows-x64": ("windows", ("win-x64",)),
    "macos-dual-arch": ("mac", ("osx-arm64", "osx-x64")),
    "linux-universal": (
        "linux",
        ("linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64"),
    ),
}


class DesktopHelperManifestError(RuntimeError):
    """Raised when the desktop helper release manifest cannot be used safely."""


def list_desktop_helper_manifest_records(
    *,
    platform: str | None = None,
    channel: str | None = None,
) -> list[dict[str, object]]:
    normalized_records = _normalize_manifest_records(_load_manifest_document())
    return [
        dict(record)
        for record in normalized_records
        if (platform is None or record["platform"] == platform)
        and (channel is None or record["channel"] == channel)
    ]


def get_desktop_helper_manifest_record_by_id(release_id: int) -> dict[str, object] | None:
    for record in _normalize_manifest_records(_load_manifest_document()):
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
    if payload.get("schema_version") == RELEASE_MANIFEST_V2_SCHEMA:
        return _normalize_v2_manifest_records(payload)
    return _normalize_legacy_manifest_records(payload)


def _normalize_v2_manifest_records(payload: dict[str, object]) -> list[dict[str, object]]:
    helper_version = _require_non_empty_string(payload.get("helper_version"), "helper_version")
    channel = _require_non_empty_string(payload.get("channel"), "channel")
    target_framework = _require_non_empty_string(payload.get("target_framework"), "target_framework")
    runtime_family = _require_non_empty_string(payload.get("runtime_family"), "runtime_family")
    if target_framework != "net10.0" or runtime_family != "10.0":
        raise DesktopHelperManifestError(
            "Desktop helper v2 standard releases must target net10.0 with runtime family 10.0"
        )
    deployment_mode = _require_non_empty_string(payload.get("deployment_mode"), "deployment_mode")
    if deployment_mode != SELF_CONTAINED_MODE:
        raise DesktopHelperManifestError("Desktop helper v2 standard releases must be self_contained")
    created_at = _require_non_empty_string(payload.get("generated_at_utc"), "generated_at_utc")
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list):
        raise DesktopHelperManifestError("Desktop helper release manifest packages must be a list")

    records: list[dict[str, object]] = []
    seen_targets: set[tuple[str, str]] = set()
    seen_ids: set[int] = set()
    for index, raw_record in enumerate(raw_packages):
        if not isinstance(raw_record, dict):
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest package at index {index} must be an object"
            )
        package_target = _require_non_empty_string(raw_record.get("package_target"), "package_target")
        platform = _normalize_platform_family(
            _require_non_empty_string(raw_record.get("platform"), "platform")
        )
        identity = (platform, package_target)
        if identity in seen_targets:
            raise DesktopHelperManifestError(
                f"Desktop helper v2 manifest repeats package target {package_target} for {platform}"
            )
        seen_targets.add(identity)
        artifact_kind = _require_non_empty_string(raw_record.get("artifact_kind"), "artifact_kind")
        if artifact_kind not in SUPPORTED_DISTRIBUTABLE_ARTIFACT_KINDS:
            raise DesktopHelperManifestError(
                f"Unsupported desktop helper artifact kind in manifest: {artifact_kind}"
            )
        filename = _require_non_empty_string(raw_record.get("filename"), "filename")
        relative_path = _require_safe_relative_path(raw_record.get("relative_path"), "relative_path")
        package_root = _require_safe_relative_path(raw_record.get("package_root"), "package_root")
        installer_entrypoint = _require_safe_relative_path(
            raw_record.get("installer_entrypoint"),
            "installer_entrypoint",
        )
        sha256 = _require_sha256(raw_record.get("sha256"), "sha256")
        installer_manifest_sha256 = _require_sha256(
            raw_record.get("installer_manifest_sha256"),
            "installer_manifest_sha256",
        )
        supported_runtime_ids = _require_string_list(
            raw_record.get("supported_runtime_ids"),
            "supported_runtime_ids",
        )
        minimum_os_version = raw_record.get("minimum_os_version")
        if minimum_os_version is not None:
            minimum_os_version = _require_non_empty_string(minimum_os_version, "minimum_os_version")
        expected_contract = PACKAGE_RUNTIME_CONTRACTS.get(package_target)
        if expected_contract is None:
            raise DesktopHelperManifestError(
                f"Unsupported desktop helper v2 package target: {package_target}"
            )
        expected_platform, expected_runtime_ids = expected_contract
        if platform != expected_platform or tuple(supported_runtime_ids) != expected_runtime_ids:
            raise DesktopHelperManifestError(
                f"Desktop helper v2 package contract mismatch for {package_target}"
            )
        if package_target == "macos-dual-arch" and minimum_os_version != "14.0":
            raise DesktopHelperManifestError(
                "Desktop helper macOS package minimum_os_version must be 14.0"
            )
        external_runtime_required = raw_record.get("external_runtime_required")
        if external_runtime_required is not False:
            raise DesktopHelperManifestError(
                "Desktop helper v2 standard package external_runtime_required must be false"
            )
        size_bytes = _require_non_negative_int(raw_record.get("size_bytes"), "size_bytes")
        published_at = _require_non_empty_string(
            raw_record.get("generated_at_utc"),
            "generated_at_utc",
        )

        file_path = _resolve_package_file(relative_path, filename)
        if file_path.stat().st_size != size_bytes:
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest size mismatch for {relative_path}"
            )
        if _sha256_for_file(file_path) != sha256:
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest SHA-256 mismatch for {relative_path}"
            )
        release_id = _generate_stable_release_id(
            channel=channel,
            package_target=package_target,
            version=helper_version,
            filename=filename,
        )
        if release_id in seen_ids:
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest ID collision detected for release_id={release_id}"
            )
        seen_ids.add(release_id)
        records.append(
            {
                "id": release_id,
                "channel": channel,
                "runtime_id": package_target,
                "platform": platform,
                "package_target": package_target,
                "version": helper_version,
                "filename": filename,
                "relative_path": relative_path,
                "package_root": package_root,
                "installer_entrypoint": installer_entrypoint,
                "sha256": sha256,
                "installer_manifest_sha256": installer_manifest_sha256,
                "size_bytes": size_bytes,
                "published_at": published_at,
                "created_at": created_at,
                "file_path": file_path,
                "artifact_kind": artifact_kind,
                "deployment_mode": deployment_mode,
                "external_runtime_required": False,
                "runtime_family": runtime_family,
                "target_framework": target_framework,
                "supported_runtime_ids": supported_runtime_ids,
                "minimum_os_version": minimum_os_version,
                "dotnet_runtime_required": None,
            }
        )
    return records


def _normalize_legacy_manifest_records(payload: dict[str, object]) -> list[dict[str, object]]:
    helper_version = _require_non_empty_string(payload.get("helper_version"), "helper_version")
    channel = _require_non_empty_string(payload.get("channel"), "channel")
    dotnet_runtime_major = _require_non_empty_string(
        payload.get("dotnet_runtime_major"),
        "dotnet_runtime_major",
    )
    _require_non_empty_string(payload.get("dotnet_runtime_display"), "dotnet_runtime_display")
    created_at = _require_non_empty_string(payload.get("generated_at_utc"), "generated_at_utc")
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list):
        raise DesktopHelperManifestError("Desktop helper release manifest packages must be a list")
    records: list[dict[str, object]] = []
    for index, raw_record in enumerate(raw_packages):
        if not isinstance(raw_record, dict):
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest package at index {index} must be an object"
            )
        runtime_id = _require_non_empty_string(raw_record.get("runtime"), "runtime")
        platform = _normalize_platform_family(
            _require_non_empty_string(raw_record.get("platform_family"), "platform_family")
        )
        filename = _require_non_empty_string(raw_record.get("filename"), "filename")
        relative_path = _require_safe_relative_path(raw_record.get("relative_path"), "relative_path")
        sha256 = _require_sha256(raw_record.get("sha256"), "sha256")
        size_bytes = _require_non_negative_int(raw_record.get("size_bytes"), "size_bytes")
        published_at = _require_non_empty_string(raw_record.get("generated_at_utc"), "generated_at_utc")
        artifact_kind = _require_non_empty_string(raw_record.get("artifact_kind"), "artifact_kind")
        file_path = _resolve_package_file(relative_path, filename)
        records.append(
            {
                "id": _generate_stable_release_id(
                    channel=channel,
                    package_target=runtime_id,
                    version=helper_version,
                    filename=filename,
                ),
                "channel": channel,
                "runtime_id": runtime_id,
                "platform": platform,
                "package_target": runtime_id,
                "version": helper_version,
                "filename": filename,
                "relative_path": relative_path,
                "package_root": _require_non_empty_string(raw_record.get("package_name"), "package_name"),
                "installer_entrypoint": "",
                "sha256": sha256,
                "installer_manifest_sha256": None,
                "size_bytes": size_bytes,
                "published_at": published_at,
                "created_at": created_at,
                "file_path": file_path,
                "artifact_kind": artifact_kind,
                "deployment_mode": "framework_dependent",
                "external_runtime_required": True,
                "runtime_family": f"{dotnet_runtime_major}.0",
                "target_framework": f"net{dotnet_runtime_major}.0",
                "supported_runtime_ids": [runtime_id],
                "minimum_os_version": None,
                "dotnet_runtime_required": f"{dotnet_runtime_major}.x",
            }
        )
    return records


def _resolve_package_file(relative_path: str, filename: str) -> Path:
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
    return file_path


def _generate_stable_release_id(
    *,
    channel: str,
    package_target: str,
    version: str,
    filename: str,
) -> int:
    seed = "\0".join((channel, package_target, version, filename)).encode("utf-8")
    candidate = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") & JS_SAFE_INTEGER_MASK
    return candidate or 1


def _normalize_platform_family(platform_family: str) -> str:
    normalized = platform_family.strip().lower()
    platform = MANIFEST_PLATFORM_FAMILY_MAP.get(normalized)
    if platform is None:
        raise DesktopHelperManifestError(
            f"Unsupported desktop helper platform family in manifest: {platform_family}"
        )
    return platform


def _require_safe_relative_path(value: object, field_name: str) -> str:
    normalized = _require_non_empty_string(value, field_name).replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest path escapes packages directory: {normalized}"
        )
    return path.as_posix()


def _require_sha256(value: object, field_name: str) -> str:
    normalized = _require_non_empty_string(value, field_name).lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest field {field_name} must be a SHA-256 hex digest"
        )
    return normalized


def _require_string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest field {field_name} must be a non-empty list"
        )
    result = [_require_non_empty_string(item, field_name) for item in value]
    if len(result) != len(set(result)):
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest field {field_name} contains duplicates"
        )
    return result


def _require_non_negative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest {field_name} must be a non-negative integer"
        )
    return value


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


def _sha256_for_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
