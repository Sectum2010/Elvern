from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from clients.desktop_helper_package_contract import (
    PACKAGE_NAME_PREFIX,
    expected_package_filename,
)

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
logger = logging.getLogger(__name__)
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


class DesktopHelperManifestOriginMismatch(DesktopHelperManifestError):
    """Raised when an otherwise valid package belongs to another Elvern origin."""


_snapshot_lock = threading.RLock()
_snapshot_fingerprint: tuple[object, ...] | None = None
_snapshot_payload: dict[str, object] | None = None
_snapshot_records: list[dict[str, object]] | None = None
_artifact_cache_lock = threading.RLock()
_verified_artifacts: OrderedDict[
    str,
    tuple[tuple[object, ...], int, str],
] = OrderedDict()
_artifact_locks: dict[str, tuple[threading.Lock, int]] = {}
_MAX_VERIFIED_ARTIFACTS = 64
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_MANIFEST_READ_ATTEMPTS = 3


def desktop_helper_release_manifest_exists() -> bool:
    return HELPER_RELEASE_MANIFEST_PATH.is_file()


def reset_desktop_helper_manifest_cache() -> None:
    global _snapshot_fingerprint, _snapshot_payload, _snapshot_records
    with _snapshot_lock:
        _snapshot_fingerprint = None
        _snapshot_payload = None
        _snapshot_records = None
    with _artifact_cache_lock:
        _verified_artifacts.clear()
        _artifact_locks.clear()


def list_desktop_helper_manifest_records(
    *,
    platform: str | None = None,
    channel: str | None = None,
    expected_bound_origin_sha256: str | None = None,
) -> list[dict[str, object]]:
    with _snapshot_lock:
        records = [dict(record) for record in _load_normalized_manifest_records_locked()]
    normalized_records = _select_and_verify_records(
        records,
        platform=platform,
        channel=channel,
        expected_bound_origin_sha256=expected_bound_origin_sha256,
    )
    return [dict(record) for record in normalized_records]


def get_desktop_helper_manifest_record_by_id(
    release_id: int,
    *,
    expected_bound_origin_sha256: str | None = None,
) -> dict[str, object] | None:
    with _snapshot_lock:
        records = [dict(record) for record in _load_normalized_manifest_records_locked()]
    for record in _select_and_verify_records(
        records,
        release_id=release_id,
        expected_bound_origin_sha256=expected_bound_origin_sha256,
    ):
        if int(record["id"]) == release_id:
            return dict(record)
    return None


def _load_manifest_document_locked() -> dict[str, object]:
    global _snapshot_fingerprint, _snapshot_payload, _snapshot_records
    try:
        manifest_bytes, fingerprint = _read_manifest_snapshot()
    except DesktopHelperManifestError:
        raise
    except OSError as exc:
        raise DesktopHelperManifestError(
            "Desktop helper release manifest is unavailable"
        ) from exc
    if _snapshot_payload is not None and fingerprint == _snapshot_fingerprint:
        return _snapshot_payload
    try:
        manifest_text = manifest_bytes.decode("utf-8")
        payload = json.loads(manifest_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesktopHelperManifestError(
            "Desktop helper release manifest could not be read"
        ) from exc
    if not isinstance(payload, dict):
        raise DesktopHelperManifestError("Desktop helper release manifest root must be an object")
    _snapshot_fingerprint = fingerprint
    _snapshot_payload = payload
    _snapshot_records = None
    with _artifact_cache_lock:
        _verified_artifacts.clear()
    return payload


def _load_normalized_manifest_records_locked() -> list[dict[str, object]]:
    global _snapshot_records
    payload = _load_manifest_document_locked()
    if _snapshot_records is None:
        _snapshot_records = _normalize_manifest_records(
            payload,
            verify_artifacts=False,
        )
    return _snapshot_records


def _read_manifest_snapshot() -> tuple[bytes, tuple[object, ...]]:
    relative_path = HELPER_RELEASE_MANIFEST_PATH.name
    identity = _artifact_identity(
        HELPER_RELEASE_PACKAGES_DIR,
        relative_path,
    )
    for attempt in range(1, _MAX_MANIFEST_READ_ATTEMPTS + 1):
        handle = _open_artifact_no_follow(
            HELPER_RELEASE_PACKAGES_DIR,
            relative_path,
        )
        try:
            before_stat = os.fstat(handle.fileno())
            before = _fingerprint_from_stat(before_stat, identity)
            before_read_identity = _manifest_read_identity(before_stat)
            if before_stat.st_size > _MAX_MANIFEST_BYTES:
                raise DesktopHelperManifestError(
                    "Desktop helper release manifest is too large"
                )
            manifest_bytes = handle.read(_MAX_MANIFEST_BYTES + 1)
            after_stat = os.fstat(handle.fileno())
            after_read_identity = _manifest_read_identity(after_stat)
        except OSError as exc:
            raise DesktopHelperManifestError(
                "Desktop helper release manifest could not be read"
            ) from exc
        finally:
            handle.close()
        if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
            raise DesktopHelperManifestError(
                "Desktop helper release manifest is too large"
            )
        if (
            before_read_identity == after_read_identity
            and len(manifest_bytes) == before_stat.st_size
        ):
            return manifest_bytes, before
        if attempt >= _MAX_MANIFEST_READ_ATTEMPTS:
            break
    raise DesktopHelperManifestError(
        "Desktop helper release manifest changed during reading"
    )


def _manifest_read_identity(stat_result: os.stat_result) -> tuple[int, ...]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _select_and_verify_records(
    records: list[dict[str, object]],
    *,
    platform: str | None = None,
    channel: str | None = None,
    release_id: int | None = None,
    expected_bound_origin_sha256: str | None = None,
) -> list[dict[str, object]]:
    selected = [
        record
        for record in records
        if (platform is None or record["platform"] == platform)
        and (channel is None or record["channel"] == channel)
        and (release_id is None or int(record["id"]) == release_id)
    ]
    if expected_bound_origin_sha256 is not None and any(
        record.get("bound_origin_sha256") is not None
        and record["bound_origin_sha256"] != expected_bound_origin_sha256
        for record in selected
    ):
        raise DesktopHelperManifestOriginMismatch(
            "Desktop helper package origin binding is incompatible"
        )
    verified: list[dict[str, object]] = []
    for record in selected:
        file_path = _resolve_package_file(
            str(record["relative_path"]),
            str(record["filename"]),
        )
        _verify_artifact(
            file_path,
            root_dir=HELPER_RELEASE_PACKAGES_DIR,
            relative_path=str(record["relative_path"]),
            size_bytes=int(record["size_bytes"]),
            sha256=str(record["sha256"]),
            package_target=str(record["package_target"]),
        )
        package_binding = str(record.get("package_binding") or "legacy_unverified")
        if expected_bound_origin_sha256 is not None and record.get("bound_origin_sha256"):
            package_binding = "compatible"
        verified.append({
            **record,
            "file_path": file_path,
            "artifact_root": HELPER_RELEASE_PACKAGES_DIR,
            "artifact_relative_path": str(record["relative_path"]),
            "package_binding": package_binding,
        })
    return verified


def _normalize_manifest_records(
    payload: dict[str, object],
    *,
    platform: str | None = None,
    release_id: int | None = None,
    expected_bound_origin_sha256: str | None = None,
    verify_artifacts: bool = True,
) -> list[dict[str, object]]:
    if payload.get("schema_version") == RELEASE_MANIFEST_V2_SCHEMA:
        return _normalize_v2_manifest_records(
            payload,
            platform=platform,
            release_id=release_id,
            expected_bound_origin_sha256=expected_bound_origin_sha256,
            verify_artifacts=verify_artifacts,
        )
    return _normalize_legacy_manifest_records(
        payload,
        platform=platform,
        release_id=release_id,
        verify_artifacts=verify_artifacts,
    )


def _normalize_v2_manifest_records(
    payload: dict[str, object],
    *,
    platform: str | None,
    release_id: int | None,
    expected_bound_origin_sha256: str | None,
    verify_artifacts: bool,
) -> list[dict[str, object]]:
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
    manifest_origin_hash = _require_sha256(
        payload.get("bound_origin_sha256"),
        "bound_origin_sha256",
    )
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list):
        raise DesktopHelperManifestError("Desktop helper release manifest packages must be a list")

    records: list[dict[str, object]] = []
    seen_targets: set[tuple[str, str]] = set()
    seen_ids: set[int] = set()
    selected_origin_mismatch = False
    for index, raw_record in enumerate(raw_packages):
        if not isinstance(raw_record, dict):
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest package at index {index} must be an object"
            )
        package_target = _require_non_empty_string(raw_record.get("package_target"), "package_target")
        record_platform = _normalize_platform_family(
            _require_non_empty_string(raw_record.get("platform"), "platform")
        )
        identity = (record_platform, package_target)
        if identity in seen_targets:
            raise DesktopHelperManifestError(
                f"Desktop helper v2 manifest repeats package target {package_target} for {record_platform}"
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
        try:
            required_filename = expected_package_filename(
                PACKAGE_NAME_PREFIX,
                helper_version,
                package_target,
                sha256,
            )
        except ValueError as exc:
            raise DesktopHelperManifestError(
                "Desktop helper v2 package filename contract is invalid"
            ) from exc
        if filename != required_filename or relative_path != required_filename:
            raise DesktopHelperManifestError(
                "Desktop helper v2 package filename does not match its content hash"
            )
        installer_manifest_sha256 = _require_sha256(
            raw_record.get("installer_manifest_sha256"),
            "installer_manifest_sha256",
        )
        tree_manifest_path = _require_safe_relative_path(
            raw_record.get("installer_tree_manifest_path"),
            "installer_tree_manifest_path",
        )
        tree_manifest_sha256 = _require_sha256(
            raw_record.get("installer_tree_manifest_sha256"),
            "installer_tree_manifest_sha256",
        )
        package_origin_hash = _require_sha256(
            raw_record.get("bound_origin_sha256"),
            "bound_origin_sha256",
        )
        if package_origin_hash != manifest_origin_hash:
            raise DesktopHelperManifestError(
                "Desktop helper package origin identity does not match its release manifest"
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
        if record_platform != expected_platform or tuple(supported_runtime_ids) != expected_runtime_ids:
            raise DesktopHelperManifestError(
                f"Desktop helper v2 package contract mismatch for {package_target}"
            )
        if package_target == "macos-dual-arch" and minimum_os_version != "14.0":
            raise DesktopHelperManifestError(
                "Desktop helper macOS package minimum_os_version must be 14.0"
            )
        if raw_record.get("external_runtime_required") is not False:
            raise DesktopHelperManifestError(
                "Desktop helper v2 standard package external_runtime_required must be false"
            )
        size_bytes = _require_non_negative_int(raw_record.get("size_bytes"), "size_bytes")
        published_at = _require_non_empty_string(
            raw_record.get("generated_at_utc"),
            "generated_at_utc",
        )
        stable_release_id = _generate_stable_release_id(
            channel=channel,
            package_target=package_target,
            version=helper_version,
            filename=filename,
        )
        if stable_release_id in seen_ids:
            raise DesktopHelperManifestError(
                f"Desktop helper release manifest ID collision detected for release_id={stable_release_id}"
            )
        seen_ids.add(stable_release_id)
        selected = (
            (platform is None or record_platform == platform)
            and (release_id is None or stable_release_id == release_id)
        )
        if not selected:
            continue
        if (
            expected_bound_origin_sha256 is not None
            and package_origin_hash != expected_bound_origin_sha256
        ):
            selected_origin_mismatch = True
            continue
        file_path = None
        if verify_artifacts:
            file_path = _resolve_package_file(relative_path, filename)
            _verify_artifact(
                file_path,
                root_dir=HELPER_RELEASE_PACKAGES_DIR,
                relative_path=relative_path,
                size_bytes=size_bytes,
                sha256=sha256,
                package_target=package_target,
            )
        records.append(
            {
                "id": stable_release_id,
                "channel": channel,
                "runtime_id": package_target,
                "platform": record_platform,
                "package_target": package_target,
                "version": helper_version,
                "filename": filename,
                "relative_path": relative_path,
                "package_root": package_root,
                "installer_entrypoint": installer_entrypoint,
                "sha256": sha256,
                "installer_manifest_sha256": installer_manifest_sha256,
                "installer_tree_manifest_path": tree_manifest_path,
                "installer_tree_manifest_sha256": tree_manifest_sha256,
                "bound_origin_sha256": package_origin_hash,
                "package_binding": "compatible" if expected_bound_origin_sha256 else "unverified",
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
    if selected_origin_mismatch:
        raise DesktopHelperManifestOriginMismatch(
            "Desktop helper package origin binding is incompatible"
        )
    return records


def _normalize_legacy_manifest_records(
    payload: dict[str, object],
    *,
    platform: str | None,
    release_id: int | None,
    verify_artifacts: bool,
) -> list[dict[str, object]]:
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
        record_platform = _normalize_platform_family(
            _require_non_empty_string(raw_record.get("platform_family"), "platform_family")
        )
        filename = _require_non_empty_string(raw_record.get("filename"), "filename")
        relative_path = _require_safe_relative_path(raw_record.get("relative_path"), "relative_path")
        sha256 = _require_sha256(raw_record.get("sha256"), "sha256")
        size_bytes = _require_non_negative_int(raw_record.get("size_bytes"), "size_bytes")
        published_at = _require_non_empty_string(raw_record.get("generated_at_utc"), "generated_at_utc")
        artifact_kind = _require_non_empty_string(raw_record.get("artifact_kind"), "artifact_kind")
        stable_release_id = _generate_stable_release_id(
            channel=channel,
            package_target=runtime_id,
            version=helper_version,
            filename=filename,
        )
        if (
            (platform is not None and record_platform != platform)
            or (release_id is not None and stable_release_id != release_id)
        ):
            continue
        file_path = None
        if verify_artifacts:
            file_path = _resolve_package_file(relative_path, filename)
            _verify_artifact(
                file_path,
                root_dir=HELPER_RELEASE_PACKAGES_DIR,
                relative_path=relative_path,
                size_bytes=size_bytes,
                sha256=sha256,
                package_target=runtime_id,
            )
        records.append(
            {
                "id": stable_release_id,
                "channel": channel,
                "runtime_id": runtime_id,
                "platform": record_platform,
                "package_target": runtime_id,
                "version": helper_version,
                "filename": filename,
                "relative_path": relative_path,
                "package_root": _require_non_empty_string(raw_record.get("package_name"), "package_name"),
                "installer_entrypoint": "",
                "sha256": sha256,
                "installer_manifest_sha256": None,
                "installer_tree_manifest_path": None,
                "installer_tree_manifest_sha256": None,
                "bound_origin_sha256": None,
                "package_binding": "legacy_unverified",
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


# Bounded retries when the artifact is observed to change during hashing.
_MAX_ARTIFACT_VERIFY_ATTEMPTS = 3


class _ArtifactChangedDuringVerification(RuntimeError):
    pass


def _artifact_identity(root_dir: Path, relative_path: str) -> str:
    return f"{root_dir.absolute()}::{relative_path}"


@contextmanager
def _artifact_singleflight(identity: str):
    with _artifact_cache_lock:
        entry = _artifact_locks.get(identity)
        if entry is None:
            lock = threading.Lock()
            references = 0
        else:
            lock, references = entry
        _artifact_locks[identity] = (lock, references + 1)
    try:
        with lock:
            yield
    finally:
        with _artifact_cache_lock:
            current = _artifact_locks.get(identity)
            if current is not None and current[0] is lock:
                remaining = current[1] - 1
                if remaining <= 0:
                    _artifact_locks.pop(identity, None)
                else:
                    _artifact_locks[identity] = (lock, remaining)


def _open_artifact_no_follow(root_dir: Path, relative_path: str):
    """Open an artifact beneath a trusted root without following any symlink."""
    safe_relative_path = _require_safe_relative_path(relative_path, "relative_path")
    if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise DesktopHelperManifestError(
            "Desktop helper release artifact verification is unsupported on this server"
        )

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    for flag_name in ("O_CLOEXEC", "O_BINARY"):
        directory_flags |= getattr(os, flag_name, 0)
        file_flags |= getattr(os, flag_name, 0)

    directory_fd = -1
    try:
        directory_fd = os.open(root_dir, directory_flags)
        parts = PurePosixPath(safe_relative_path).parts
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
    except OSError as exc:
        raise DesktopHelperManifestError(
            "Desktop helper release artifact is unavailable"
        ) from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)

    try:
        stat_result = os.fstat(file_fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise DesktopHelperManifestError(
                "Desktop helper release artifact is not a regular file"
            )
        return os.fdopen(file_fd, "rb", buffering=1024 * 1024)
    except BaseException:
        os.close(file_fd)
        raise


def _fingerprint_from_stat(stat_result: os.stat_result, resolved_path: str) -> tuple[object, ...]:
    return (
        resolved_path,
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _hash_open_handle(handle, file_path: Path) -> str:
    # Hash the exact opened, fstat'd file description — never a re-open by path.
    return _sha256_for_file(file_path, handle=handle)


def _verify_open_handle(
    handle,
    file_path: Path,
    *,
    artifact_identity: str,
    size_bytes: int,
    sha256: str,
    package_target: str,
    force_hash: bool,
) -> tuple[object, ...]:
    """Verify an already-open handle and return its confirmed fingerprint.

    Hashing reads the same file description that was fstat'd. A second fstat after
    hashing fails closed (with a small bounded retry) if the underlying object
    changed mid-read, and the verification cache is keyed by the complete
    fingerprint so any change forces a rehash.
    """
    before = _fingerprint_from_stat(os.fstat(handle.fileno()), artifact_identity)
    if before[3] != size_bytes:
        raise DesktopHelperManifestError("Desktop helper release artifact size mismatch")
    with _artifact_cache_lock:
        cached = _verified_artifacts.get(artifact_identity)
        if cached == (before, size_bytes, sha256) and not force_hash:
            _verified_artifacts.move_to_end(artifact_identity)
            logger.debug(
                "Desktop helper package verification package_target=%s cache=hit result=valid",
                package_target,
            )
            return before

    started_at = time.monotonic()
    digest = _hash_open_handle(handle, file_path)
    after = _fingerprint_from_stat(os.fstat(handle.fileno()), artifact_identity)
    if after != before:
        raise _ArtifactChangedDuringVerification
    if digest != sha256:
        logger.warning(
            "Desktop helper package verification package_target=%s cache=miss duration_ms=%d result=invalid",
            package_target,
            round((time.monotonic() - started_at) * 1000),
        )
        raise DesktopHelperManifestError("Desktop helper release artifact SHA-256 mismatch")
    with _artifact_cache_lock:
        _verified_artifacts[artifact_identity] = (before, size_bytes, sha256)
        _verified_artifacts.move_to_end(artifact_identity)
        while len(_verified_artifacts) > _MAX_VERIFIED_ARTIFACTS:
            _verified_artifacts.popitem(last=False)
    logger.debug(
        "Desktop helper package verification package_target=%s cache=miss duration_ms=%d result=valid",
        package_target,
        round((time.monotonic() - started_at) * 1000),
    )
    return before


def _open_verified_handle(
    file_path: Path,
    *,
    root_dir: Path,
    relative_path: str,
    size_bytes: int,
    sha256: str,
    package_target: str,
    force_hash: bool = False,
):
    """Open, verify, and return a read handle positioned at the start of the file."""
    identity = _artifact_identity(root_dir, relative_path)
    with _artifact_singleflight(identity):
        for attempt in range(1, _MAX_ARTIFACT_VERIFY_ATTEMPTS + 1):
            handle = _open_artifact_no_follow(root_dir, relative_path)
            try:
                _verify_open_handle(
                    handle,
                    file_path,
                    artifact_identity=identity,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    package_target=package_target,
                    force_hash=force_hash,
                )
            except _ArtifactChangedDuringVerification as exc:
                handle.close()
                if attempt >= _MAX_ARTIFACT_VERIFY_ATTEMPTS:
                    raise DesktopHelperManifestError(
                        "Desktop helper release artifact changed during verification"
                    ) from exc
                continue
            except BaseException:
                handle.close()
                raise
            handle.seek(0)
            return handle
    raise DesktopHelperManifestError(
        "Desktop helper release artifact changed during verification"
    )


def open_verified_artifact(
    file_path: Path,
    *,
    root_dir: Path | None = None,
    relative_path: str | None = None,
    size_bytes: int,
    sha256: str,
    package_target: str,
    force_hash: bool = False,
):
    """Public entry point returning an open, verified handle for streaming."""
    root = root_dir or HELPER_RELEASE_PACKAGES_DIR
    if relative_path is None:
        try:
            relative_path = file_path.relative_to(root).as_posix()
        except ValueError as exc:
            raise DesktopHelperManifestError(
                "Desktop helper release artifact is outside its trusted root"
            ) from exc
    return _open_verified_handle(
        file_path,
        root_dir=root,
        relative_path=relative_path,
        size_bytes=size_bytes,
        sha256=sha256,
        package_target=package_target,
        force_hash=force_hash,
    )


def _verify_artifact(
    file_path: Path,
    *,
    root_dir: Path,
    relative_path: str,
    size_bytes: int,
    sha256: str,
    package_target: str,
) -> None:
    handle = _open_verified_handle(
        file_path,
        root_dir=root_dir,
        relative_path=relative_path,
        size_bytes=size_bytes,
        sha256=sha256,
        package_target=package_target,
    )
    handle.close()


def _resolve_package_file(relative_path: str, filename: str) -> Path:
    safe_relative_path = _require_safe_relative_path(relative_path, "relative_path")
    file_path = HELPER_RELEASE_PACKAGES_DIR / safe_relative_path
    if file_path.name != filename:
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest filename mismatch for {relative_path}"
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
    normalized = _require_non_empty_string(value, field_name)
    if (
        "\\" in normalized
        or "//" in normalized
        or normalized != normalized.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest field {field_name} is not a canonical relative path"
        )
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != normalized
    ):
        raise DesktopHelperManifestError(
            f"Desktop helper release manifest path escapes packages directory: {normalized}"
        )
    return path.as_posix()


def validate_artifact_relative_path(value: object) -> str:
    return _require_safe_relative_path(value, "relative_path")


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


def _sha256_for_file(file_path: Path | None = None, *, handle=None) -> str:
    digest = hashlib.sha256()
    if handle is not None:
        # Hash the already-open verified handle, reading from its start.
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()
    if file_path is None:
        raise DesktopHelperManifestError("Desktop helper release artifact path is required")
    with file_path.open("rb") as opened:
        for chunk in iter(lambda: opened.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
