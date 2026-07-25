from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException

from elvern_shared.desktop_helper_package_contract import (
    PACKAGE_NAME_PREFIX,
    PACKAGE_RUNTIME_CONTRACTS,
    STANDARD_PLATFORM_PACKAGE_TARGET,
    STANDARD_PLATFORM_RUNTIME_ORDER,
    STANDARD_RUNTIME_TO_PLATFORM,
    derive_standard_package_maps,
    expected_package_filename,
)
from backend.app.schemas import DesktopHelperReleaseResponse, DesktopHelperStatusResponse
from backend.app.services import desktop_helper_manifest_service as manifest_service
from backend.app.services import desktop_helper_service
from backend.app.routes import desktop_helper as desktop_helper_route

_LIST_MANIFEST_RECORDS = manifest_service.list_desktop_helper_manifest_records
_RESET_MANIFEST_CACHE = manifest_service.reset_desktop_helper_manifest_cache


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


BOUND_ORIGIN_SHA256 = _sha256(b"https://elvern.example")
ORIGIN_CASES_PATH = (
    Path(__file__).resolve().parents[2]
    / "clients"
    / "desktop-vlc-opener"
    / "packaging"
    / "common"
    / "origin-normalization-cases.json"
)
ORIGIN_BUILD_NORMALIZER = (
    Path(__file__).resolve().parents[2]
    / "clients"
    / "desktop-vlc-opener"
    / "scripts"
    / "normalize-origin.py"
)


def test_standard_package_maps_are_derived_from_the_shared_contract() -> None:
    runtime_to_platform, platform_runtime_order, platform_package_target = (
        derive_standard_package_maps(PACKAGE_RUNTIME_CONTRACTS)
    )
    assert runtime_to_platform == STANDARD_RUNTIME_TO_PLATFORM
    assert platform_runtime_order == STANDARD_PLATFORM_RUNTIME_ORDER
    assert platform_package_target == STANDARD_PLATFORM_PACKAGE_TARGET
    assert desktop_helper_service.STANDARD_PLATFORM_PACKAGE_TARGET is (
        STANDARD_PLATFORM_PACKAGE_TARGET
    )
    assert desktop_helper_service.LEGACY_PLATFORM_RUNTIME_ORDER == {
        "windows": ("win-x64",),
        "mac": ("osx-arm64", "osx-x64"),
        "linux": (
            "linux-x64",
            "linux-arm64",
            "linux-musl-x64",
            "linux-musl-arm64",
        ),
    }
    assert not hasattr(desktop_helper_service, "RUNTIME_TO_PLATFORM")
    assert not hasattr(desktop_helper_service, "PLATFORM_RUNTIME_ORDER")


def test_shared_standard_package_map_fails_fast_on_duplicate_platform() -> None:
    with pytest.raises(ValueError, match="Multiple standard package targets"):
        derive_standard_package_maps({
            **PACKAGE_RUNTIME_CONTRACTS,
            "another-linux": ("linux", ("linux-riscv64",)),
        })


def _write_manifest(
    monkeypatch,
    tmp_path: Path,
    *,
    relative_path: str | None = None,
    release_root: Path | None = None,
) -> dict[str, object]:
    packages_dir = release_root or tmp_path / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)
    package_payload = b"desktop-helper-package"
    package_sha256 = _sha256(package_payload)
    filename = expected_package_filename(
        PACKAGE_NAME_PREFIX,
        "0.9.0",
        "macos-dual-arch",
        package_sha256,
    )
    if relative_path is None:
        relative_path = filename
    artifact = packages_dir / relative_path
    if ".." not in Path(relative_path).parts:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(package_payload)
    manifest = {
        "schema_version": "desktop-helper-release-manifest-v2",
        "helper_version": "0.9.0",
        "channel": "stable",
        "target_framework": "net10.0",
        "runtime_family": "10.0",
        "deployment_mode": "self_contained",
        "generated_at_utc": "2026-07-22T00:00:00Z",
        "bound_origin_sha256": BOUND_ORIGIN_SHA256,
        "packages": [
            {
                "package_target": "macos-dual-arch",
                "platform": "mac",
                "artifact_kind": "zip",
                "filename": filename,
                "relative_path": relative_path,
                "package_root": "Elvern VLC Opener Installer",
                "installer_entrypoint": "Install-ElvernVlcOpener.command",
                "supported_runtime_ids": ["osx-arm64", "osx-x64"],
                "external_runtime_required": False,
                "size_bytes": len(package_payload),
                "sha256": package_sha256,
                "installer_manifest_sha256": "a" * 64,
                "installer_tree_manifest_path": ".elvern/tree-manifest.tsv",
                "installer_tree_manifest_sha256": "b" * 64,
                "bound_origin_sha256": BOUND_ORIGIN_SHA256,
                "minimum_os_version": "14.0",
                "generated_at_utc": "2026-07-22T00:00:00Z",
            }
        ],
    }
    manifest_path = packages_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        manifest_service,
        "HELPER_RELEASE_MANIFEST_PATH",
        manifest_path,
        raising=False,
    )
    monkeypatch.setattr(
        manifest_service,
        "HELPER_RELEASE_PACKAGES_DIR",
        packages_dir,
        raising=False,
    )
    monkeypatch.setattr(
        manifest_service,
        "list_desktop_helper_manifest_records",
        lambda **kwargs: _LIST_MANIFEST_RECORDS(packages_dir, **kwargs),
    )
    monkeypatch.setattr(
        manifest_service,
        "reset_desktop_helper_manifest_cache",
        lambda: _RESET_MANIFEST_CACHE(packages_dir),
    )
    _RESET_MANIFEST_CACHE(packages_dir)
    return manifest


def _manifest_artifact_path(manifest: dict[str, object]) -> Path:
    package = manifest["packages"][0]
    return manifest_service.HELPER_RELEASE_PACKAGES_DIR / package["relative_path"]


def test_manifest_v2_normalizes_one_package_with_self_contained_metadata(monkeypatch, tmp_path) -> None:
    _write_manifest(monkeypatch, tmp_path)

    records = manifest_service.list_desktop_helper_manifest_records(platform="mac")

    assert len(records) == 1
    record = records[0]
    assert record["package_target"] == "macos-dual-arch"
    assert record["runtime_id"] == "macos-dual-arch"
    assert record["supported_runtime_ids"] == ["osx-arm64", "osx-x64"]
    assert record["deployment_mode"] == "self_contained"
    assert record["external_runtime_required"] is False
    assert record["runtime_family"] == "10.0"
    assert record["target_framework"] == "net10.0"
    assert record["installer_manifest_sha256"] == "a" * 64
    assert record["installer_tree_manifest_path"] == ".elvern/tree-manifest.tsv"
    assert record["installer_tree_manifest_sha256"] == "b" * 64
    assert record["bound_origin_sha256"] == BOUND_ORIGIN_SHA256
    assert record["minimum_os_version"] == "14.0"
    assert record["package_binding"] == "unverified"

    bound_records = manifest_service.list_desktop_helper_manifest_records(
        platform="mac",
        expected_bound_origin_sha256=BOUND_ORIGIN_SHA256,
    )
    assert bound_records[0]["package_binding"] == "compatible"


def test_manifest_cache_isolated_by_explicit_release_root(
    monkeypatch,
    tmp_path,
) -> None:
    root_a = tmp_path / "runtime-a"
    manifest_a = _write_manifest(
        monkeypatch,
        tmp_path / "fixture-a",
        release_root=root_a,
    )
    root_b = tmp_path / "runtime-b"
    manifest_b = _write_manifest(
        monkeypatch,
        tmp_path / "fixture-b",
        release_root=root_b,
    )
    manifest_b["channel"] = "beta"
    (root_b / "release-manifest.json").write_text(
        json.dumps(manifest_b),
        encoding="utf-8",
    )
    _RESET_MANIFEST_CACHE()

    records_a = _LIST_MANIFEST_RECORDS(root_a, platform="mac")
    records_b = _LIST_MANIFEST_RECORDS(root_b, platform="mac")
    records_a_again = _LIST_MANIFEST_RECORDS(root_a, platform="mac")

    assert manifest_a["channel"] == "stable"
    assert records_a[0]["channel"] == "stable"
    assert records_b[0]["channel"] == "beta"
    assert records_a_again[0]["channel"] == "stable"
    assert records_a[0]["artifact_root"] == root_a
    assert records_b[0]["artifact_root"] == root_b


def test_service_uses_each_settings_runtime_release_root(
    initialized_settings,
    monkeypatch,
    tmp_path,
) -> None:
    root_a = tmp_path / "docker-a" / "helper_releases"
    _write_manifest(
        monkeypatch,
        tmp_path / "fixture-a",
        release_root=root_a,
    )
    root_b = tmp_path / "docker-b" / "helper_releases"
    manifest_b = _write_manifest(
        monkeypatch,
        tmp_path / "fixture-b",
        release_root=root_b,
    )
    manifest_b["channel"] = "beta"
    (root_b / "release-manifest.json").write_text(
        json.dumps(manifest_b),
        encoding="utf-8",
    )
    _RESET_MANIFEST_CACHE()
    settings_a = replace(initialized_settings, helper_releases_dir=root_a)
    settings_b = replace(initialized_settings, helper_releases_dir=root_b)
    monkeypatch.setattr(
        desktop_helper_service,
        "_desktop_backend_origin_sha256",
        lambda _settings: BOUND_ORIGIN_SHA256,
    )

    records_a = desktop_helper_service._list_helper_releases_from_manifest(
        settings_a,
        platform="mac",
    )
    records_b = desktop_helper_service._list_helper_releases_from_manifest(
        settings_b,
        platform="mac",
    )

    assert records_a is not None and records_a[0]["channel"] == "stable"
    assert records_b is not None and records_b[0]["channel"] == "beta"


@pytest.mark.parametrize(
    "filename_mutator",
    [
        lambda filename: (
            filename[:-5]
            + ("0" if filename[-5] != "0" else "1")
            + ".zip"
        ),
        lambda filename: filename[:-16] + filename[-16:-4].upper() + ".zip",
        lambda filename: filename[:-17] + ".zip",
        lambda filename: filename[:-4] + "0.zip",
        lambda filename: "renamed-" + filename,
    ],
    ids=[
        "wrong-one-character",
        "uppercase-hash",
        "missing-hash",
        "extra-hash",
        "renamed-content",
    ],
)
def test_manifest_v2_rejects_filename_not_bound_to_outer_sha256(
    monkeypatch,
    tmp_path,
    filename_mutator,
) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    filename = filename_mutator(manifest["packages"][0]["filename"])
    manifest["packages"][0]["filename"] = filename
    manifest["packages"][0]["relative_path"] = filename
    manifest_service.HELPER_RELEASE_MANIFEST_PATH.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    manifest_service.reset_desktop_helper_manifest_cache()

    with pytest.raises(
        manifest_service.DesktopHelperManifestError,
        match="filename does not match its content hash",
    ):
        manifest_service.list_desktop_helper_manifest_records(platform="mac")


def test_manifest_v2_rejects_package_path_traversal(monkeypatch, tmp_path) -> None:
    _write_manifest(monkeypatch, tmp_path, relative_path="../escape.zip")

    with pytest.raises(manifest_service.DesktopHelperManifestError, match="escapes packages directory"):
        manifest_service.list_desktop_helper_manifest_records()


def test_manifest_v2_rejects_wrong_runtime_contract(monkeypatch, tmp_path) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    manifest["packages"][0]["supported_runtime_ids"] = ["osx-arm64"]
    manifest_service.HELPER_RELEASE_MANIFEST_PATH.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(manifest_service.DesktopHelperManifestError, match="package contract mismatch"):
        manifest_service.list_desktop_helper_manifest_records()


def test_manifest_hash_verification_is_single_flight_for_concurrent_requests(
    monkeypatch,
    tmp_path,
) -> None:
    _write_manifest(monkeypatch, tmp_path)
    original_sha256 = manifest_service._sha256_for_file
    hash_calls = 0
    hash_lock = threading.Lock()

    def counted_sha256(path: Path, *, handle=None) -> str:
        nonlocal hash_calls
        with hash_lock:
            hash_calls += 1
        time.sleep(0.01)
        return original_sha256(path, handle=handle)

    monkeypatch.setattr(manifest_service, "_sha256_for_file", counted_sha256)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda _index: manifest_service.list_desktop_helper_manifest_records(
                platform="mac",
                expected_bound_origin_sha256=BOUND_ORIGIN_SHA256,
            ),
            range(20),
        ))

    assert all(len(records) == 1 for records in results)
    assert hash_calls == 1


def test_manifest_snapshot_rebuilds_when_manifest_is_replaced(
    monkeypatch,
    tmp_path,
) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    first = manifest_service.list_desktop_helper_manifest_records(platform="mac")
    assert first[0]["channel"] == "stable"

    manifest["channel"] = "beta"
    replacement = manifest_service.HELPER_RELEASE_MANIFEST_PATH.with_suffix(".replacement")
    replacement.write_text(json.dumps(manifest), encoding="utf-8")
    replacement.replace(manifest_service.HELPER_RELEASE_MANIFEST_PATH)

    second = manifest_service.list_desktop_helper_manifest_records(platform="mac")

    assert second[0]["channel"] == "beta"
    assert second[0]["id"] != first[0]["id"]


def test_manifest_snapshot_rejects_a_final_symlink(monkeypatch, tmp_path) -> None:
    _write_manifest(monkeypatch, tmp_path)
    manifest_path = manifest_service.HELPER_RELEASE_MANIFEST_PATH
    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    manifest_path.symlink_to(outside)
    manifest_service.reset_desktop_helper_manifest_cache()

    with pytest.raises(manifest_service.DesktopHelperManifestError):
        manifest_service.list_desktop_helper_manifest_records(platform="mac")


def test_manifest_snapshot_fails_closed_when_inode_changes_during_read(
    monkeypatch,
    tmp_path,
) -> None:
    _write_manifest(monkeypatch, tmp_path)
    manifest_path = manifest_service.HELPER_RELEASE_MANIFEST_PATH
    real_open = manifest_service._open_artifact_no_follow

    class MutatingHandle:
        def __init__(self, handle) -> None:
            self.handle = handle

        def fileno(self):
            return self.handle.fileno()

        def read(self, size=-1):
            payload = self.handle.read(size)
            with manifest_path.open("ab") as mutator:
                mutator.write(b" ")
            return payload

        def close(self):
            self.handle.close()

    def mutating_open(root_dir: Path, relative_path: str):
        handle = real_open(root_dir, relative_path)
        return (
            MutatingHandle(handle)
            if relative_path == manifest_path.name
            else handle
        )

    monkeypatch.setattr(manifest_service, "_open_artifact_no_follow", mutating_open)
    manifest_service.reset_desktop_helper_manifest_cache()

    with pytest.raises(
        manifest_service.DesktopHelperManifestError,
        match="changed during reading",
    ):
        manifest_service.list_desktop_helper_manifest_records(platform="mac")


def test_manifest_snapshot_uses_open_inode_then_observes_atomic_replacement(
    monkeypatch,
    tmp_path,
) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    manifest_path = manifest_service.HELPER_RELEASE_MANIFEST_PATH
    manifest["channel"] = "beta"
    replacement = manifest_path.with_name("replacement.json")
    replacement.write_text(json.dumps(manifest), encoding="utf-8")
    real_open = manifest_service._open_artifact_no_follow
    replaced = False
    open_count = 0

    class ReplacingHandle:
        def __init__(self, handle) -> None:
            self.handle = handle

        def fileno(self):
            return self.handle.fileno()

        def read(self, size=-1):
            nonlocal replaced
            payload = self.handle.read(size)
            if not replaced:
                os.replace(replacement, manifest_path)
                replaced = True
            return payload

        def close(self):
            self.handle.close()

    def replacing_open(root_dir: Path, relative_path: str):
        nonlocal open_count
        open_count += 1
        handle = real_open(root_dir, relative_path)
        return (
            ReplacingHandle(handle)
            if relative_path == manifest_path.name and not replaced
            else handle
        )

    monkeypatch.setattr(manifest_service, "_open_artifact_no_follow", replacing_open)
    manifest_bytes, _fingerprint = manifest_service._read_manifest_snapshot(
        manifest_service.HELPER_RELEASE_PACKAGES_DIR
    )

    assert json.loads(manifest_bytes)["channel"] == "beta"
    assert replaced is True
    assert open_count == 2


def test_manifest_snapshot_retries_same_inode_rewrite_even_when_mtime_is_restored(
    monkeypatch,
    tmp_path,
) -> None:
    _write_manifest(monkeypatch, tmp_path)
    manifest_path = manifest_service.HELPER_RELEASE_MANIFEST_PATH
    real_open = manifest_service._open_artifact_no_follow
    mutated = False
    open_count = 0

    class SameInodeMutatingHandle:
        def __init__(self, handle) -> None:
            self.handle = handle

        def fileno(self):
            return self.handle.fileno()

        def read(self, size=-1):
            nonlocal mutated
            payload = self.handle.read(size)
            if not mutated:
                original_stat = manifest_path.stat()
                replacement = manifest_path.read_bytes().replace(
                    b'"stable"',
                    b'"staple"',
                    1,
                )
                assert len(replacement) == original_stat.st_size
                manifest_path.write_bytes(replacement)
                os.utime(
                    manifest_path,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
                mutated = True
            return payload

        def close(self):
            self.handle.close()

    def mutating_open(root_dir: Path, relative_path: str):
        nonlocal open_count
        open_count += 1
        handle = real_open(root_dir, relative_path)
        return (
            SameInodeMutatingHandle(handle)
            if relative_path == manifest_path.name
            else handle
        )

    monkeypatch.setattr(manifest_service, "_open_artifact_no_follow", mutating_open)
    manifest_bytes, _fingerprint = manifest_service._read_manifest_snapshot(
        manifest_service.HELPER_RELEASE_PACKAGES_DIR
    )

    assert json.loads(manifest_bytes)["channel"] == "staple"
    assert open_count == 2


def test_manifest_snapshot_fails_closed_and_closes_handles_during_continuous_ctime_changes(
    monkeypatch,
    tmp_path,
) -> None:
    _write_manifest(monkeypatch, tmp_path)
    manifest_path = manifest_service.HELPER_RELEASE_MANIFEST_PATH
    real_open = manifest_service._open_artifact_no_follow
    closed = 0

    class ContinuouslyMutatingHandle:
        def __init__(self, handle) -> None:
            self.handle = handle

        def fileno(self):
            return self.handle.fileno()

        def read(self, size=-1):
            payload = self.handle.read(size)
            original_stat = manifest_path.stat()
            current = manifest_path.read_bytes()
            replacement = (
                current.replace(b'"stable"', b'"staple"', 1)
                if b'"stable"' in current
                else current.replace(b'"staple"', b'"stable"', 1)
            )
            manifest_path.write_bytes(replacement)
            os.utime(
                manifest_path,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            return payload

        def close(self):
            nonlocal closed
            self.handle.close()
            closed += 1

    def mutating_open(root_dir: Path, relative_path: str):
        handle = real_open(root_dir, relative_path)
        return (
            ContinuouslyMutatingHandle(handle)
            if relative_path == manifest_path.name
            else handle
        )

    monkeypatch.setattr(manifest_service, "_open_artifact_no_follow", mutating_open)

    with pytest.raises(
        manifest_service.DesktopHelperManifestError,
        match="changed during reading",
    ):
        manifest_service._read_manifest_snapshot(
            manifest_service.HELPER_RELEASE_PACKAGES_DIR
        )

    assert closed == manifest_service._MAX_MANIFEST_READ_ATTEMPTS


def test_manifest_snapshot_stable_read_opens_once(monkeypatch, tmp_path) -> None:
    _write_manifest(monkeypatch, tmp_path)
    real_open = manifest_service._open_artifact_no_follow
    open_count = 0

    def counted_open(root_dir: Path, relative_path: str):
        nonlocal open_count
        open_count += 1
        return real_open(root_dir, relative_path)

    monkeypatch.setattr(manifest_service, "_open_artifact_no_follow", counted_open)

    manifest_service._read_manifest_snapshot(
        manifest_service.HELPER_RELEASE_PACKAGES_DIR
    )

    assert open_count == 1


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xff\xfe", "could not be read"),
        (b"{not-json}", "could not be read"),
    ],
)
def test_manifest_snapshot_rejects_invalid_encoding_or_json(
    monkeypatch,
    tmp_path,
    payload: bytes,
    message: str,
) -> None:
    _write_manifest(monkeypatch, tmp_path)
    manifest_service.HELPER_RELEASE_MANIFEST_PATH.write_bytes(payload)
    manifest_service.reset_desktop_helper_manifest_cache()

    with pytest.raises(manifest_service.DesktopHelperManifestError, match=message):
        manifest_service.list_desktop_helper_manifest_records(platform="mac")


def test_manifest_snapshot_closes_its_read_handle(monkeypatch, tmp_path) -> None:
    _write_manifest(monkeypatch, tmp_path)
    real_open = manifest_service._open_artifact_no_follow
    closed: list[bool] = []

    class TrackingHandle:
        def __init__(self, handle) -> None:
            self.handle = handle

        def fileno(self):
            return self.handle.fileno()

        def read(self, size=-1):
            return self.handle.read(size)

        def close(self):
            self.handle.close()
            closed.append(self.handle.closed)

    def tracking_open(root_dir: Path, relative_path: str):
        handle = real_open(root_dir, relative_path)
        return (
            TrackingHandle(handle)
            if relative_path == "release-manifest.json"
            else handle
        )

    monkeypatch.setattr(manifest_service, "_open_artifact_no_follow", tracking_open)
    manifest_service.list_desktop_helper_manifest_records(platform="mac")

    assert closed == [True]


def test_manifest_only_hashes_artifacts_for_the_requested_platform(monkeypatch, tmp_path) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    windows_payload = b"windows-package"
    windows_sha256 = _sha256(windows_payload)
    windows_filename = expected_package_filename(
        PACKAGE_NAME_PREFIX,
        "0.9.0",
        "windows-x64",
        windows_sha256,
    )
    windows_artifact = manifest_service.HELPER_RELEASE_PACKAGES_DIR / windows_filename
    windows_artifact.write_bytes(windows_payload)
    manifest["packages"].append({
        "package_target": "windows-x64",
        "platform": "windows",
        "artifact_kind": "zip",
        "filename": windows_artifact.name,
        "relative_path": windows_artifact.name,
        "package_root": "Elvern VLC Opener Windows Installer",
        "installer_entrypoint": "Install-ElvernVlcOpener.cmd",
        "supported_runtime_ids": ["win-x64"],
        "external_runtime_required": False,
        "size_bytes": len(windows_payload),
        "sha256": windows_sha256,
        "installer_manifest_sha256": "c" * 64,
        "installer_tree_manifest_path": ".elvern/tree-manifest.tsv",
        "installer_tree_manifest_sha256": "d" * 64,
        "bound_origin_sha256": BOUND_ORIGIN_SHA256,
        "generated_at_utc": "2026-07-22T00:00:00Z",
    })
    manifest_service.HELPER_RELEASE_MANIFEST_PATH.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    manifest_service.reset_desktop_helper_manifest_cache()
    hashed_paths: list[Path] = []
    original_sha256 = manifest_service._sha256_for_file

    def record_sha256(path: Path, *, handle=None) -> str:
        hashed_paths.append(path)
        return original_sha256(path, handle=handle)

    monkeypatch.setattr(manifest_service, "_sha256_for_file", record_sha256)

    records = manifest_service.list_desktop_helper_manifest_records(
        platform="mac",
        expected_bound_origin_sha256=BOUND_ORIGIN_SHA256,
    )

    assert len(records) == 1
    assert [path.name for path in hashed_paths] == [
        manifest["packages"][0]["filename"]
    ]


def test_manifest_rehashes_an_artifact_replaced_with_same_size_and_mtime(
    monkeypatch,
    tmp_path,
) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    artifact = _manifest_artifact_path(manifest)
    original_stat = artifact.stat()
    original_sha256 = manifest_service._sha256_for_file
    hash_calls = 0

    def counted_sha256(path: Path, *, handle=None) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return original_sha256(path, handle=handle)

    monkeypatch.setattr(manifest_service, "_sha256_for_file", counted_sha256)
    manifest_service.list_desktop_helper_manifest_records(platform="mac")
    artifact.write_bytes(b"x" * original_stat.st_size)
    os.utime(artifact, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    with pytest.raises(
        manifest_service.DesktopHelperManifestError,
        match="artifact SHA-256 mismatch",
    ):
        manifest_service.list_desktop_helper_manifest_records(platform="mac")

    assert hash_calls == 2


def test_release_download_rechecks_artifact_fingerprint_and_rejects_corruption(
    initialized_settings,
    monkeypatch,
    tmp_path,
) -> None:
    _write_manifest(
        monkeypatch,
        tmp_path,
        release_root=initialized_settings.helper_releases_dir,
    )
    monkeypatch.setattr(
        desktop_helper_service,
        "_desktop_backend_origin_sha256",
        lambda _settings: BOUND_ORIGIN_SHA256,
    )
    release = manifest_service.list_desktop_helper_manifest_records(
        platform="mac",
        expected_bound_origin_sha256=BOUND_ORIGIN_SHA256,
    )[0]
    first = desktop_helper_service.get_helper_release_download_path(
        initialized_settings,
        int(release["id"]),
    )
    assert first["file_path"].is_file()

    artifact = Path(first["file_path"])
    original_stat = artifact.stat()
    artifact.write_bytes(b"x" * original_stat.st_size)
    os.utime(artifact, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    with pytest.raises(HTTPException) as exc_info:
        desktop_helper_service.get_helper_release_download_path(
            initialized_settings,
            int(release["id"]),
        )

    assert exc_info.value.status_code == 410
    assert "verification failed" in str(exc_info.value.detail)


def test_verified_handle_streams_original_inode_after_path_replacement(monkeypatch, tmp_path) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    artifact = _manifest_artifact_path(manifest)
    original = artifact.read_bytes()
    handle = manifest_service.open_verified_artifact(
        artifact,
        root_dir=manifest_service.HELPER_RELEASE_PACKAGES_DIR,
        size_bytes=len(original),
        sha256=_sha256(original),
        package_target="macos-dual-arch",
    )
    try:
        # Atomically replace the path with a different inode AFTER verification.
        tampered = artifact.with_name("tampered.zip")
        tampered.write_bytes(b"z" * len(original))
        os.replace(tampered, artifact)

        streamed = handle.read()
    finally:
        handle.close()

    # The streamed bytes come from the verified open handle, not a path reopen.
    assert streamed == original
    assert artifact.read_bytes() != original


def test_open_verified_artifact_fails_closed_when_changed_during_hashing(monkeypatch, tmp_path) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    artifact = _manifest_artifact_path(manifest)
    original = artifact.read_bytes()
    real_hash = manifest_service._sha256_for_file

    def mutating_hash(path: Path, *, handle=None) -> str:
        result = real_hash(path, handle=handle)
        # Bump the inode's ctime so the post-hash fstat differs every attempt.
        os.utime(path, ns=(0, 0))
        return result

    monkeypatch.setattr(manifest_service, "_sha256_for_file", mutating_hash)

    with pytest.raises(
        manifest_service.DesktopHelperManifestError,
        match="changed during verification",
    ):
        manifest_service.open_verified_artifact(
            artifact,
            root_dir=manifest_service.HELPER_RELEASE_PACKAGES_DIR,
            size_bytes=len(original),
            sha256=_sha256(original),
            package_target="macos-dual-arch",
        )


def test_open_verified_artifact_refuses_a_symlink(monkeypatch, tmp_path) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    artifact = _manifest_artifact_path(manifest)
    original = artifact.read_bytes()
    link = manifest_service.HELPER_RELEASE_PACKAGES_DIR / "link.zip"
    link.symlink_to(artifact)

    with pytest.raises(manifest_service.DesktopHelperManifestError):
        manifest_service.open_verified_artifact(
            link,
            root_dir=manifest_service.HELPER_RELEASE_PACKAGES_DIR,
            size_bytes=len(original),
            sha256=_sha256(original),
            package_target="macos-dual-arch",
        )


@pytest.mark.parametrize("symlink_component", ["directory", "artifact"])
def test_manifest_artifact_resolution_rejects_symlinks_in_the_real_relative_path(
    monkeypatch,
    tmp_path,
    symlink_component: str,
) -> None:
    packages_dir = tmp_path / "packages"
    artifact = packages_dir / "nested" / "artifact.zip"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"desktop-helper-package")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_artifact = outside / artifact.name
    outside_artifact.write_bytes(artifact.read_bytes())
    if symlink_component == "directory":
        artifact.unlink()
        artifact.parent.rmdir()
        artifact.parent.symlink_to(outside, target_is_directory=True)
    else:
        artifact.unlink()
        artifact.symlink_to(outside_artifact)
    with pytest.raises(
        manifest_service.DesktopHelperManifestError,
        match="artifact is unavailable",
    ):
        handle = manifest_service._open_artifact_no_follow(
            packages_dir,
            "nested/artifact.zip",
        )
        handle.close()


def test_different_artifacts_hash_in_parallel_without_a_global_verification_lock(
    monkeypatch,
    tmp_path,
) -> None:
    root = tmp_path / "packages"
    root.mkdir()
    paths = [root / "one.zip", root / "two.zip"]
    for index, path in enumerate(paths):
        path.write_bytes(f"package-{index}".encode())
    original_sha256 = manifest_service._sha256_for_file
    barrier = threading.Barrier(2)
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()

    def synchronized_hash(path: Path, *, handle=None) -> str:
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        barrier.wait(timeout=2)
        try:
            return original_sha256(path, handle=handle)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(manifest_service, "_sha256_for_file", synchronized_hash)

    def verify(path: Path) -> bytes:
        handle = manifest_service.open_verified_artifact(
            path,
            root_dir=root,
            relative_path=path.name,
            size_bytes=path.stat().st_size,
            sha256=_sha256(path.read_bytes()),
            package_target=path.stem,
        )
        try:
            return handle.read()
        finally:
            handle.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(verify, paths))

    assert results == [b"package-0", b"package-1"]
    assert maximum_active == 2
    assert manifest_service._artifact_locks == {}


def test_verified_artifact_cache_is_bounded_and_singleflight_locks_are_released(
    tmp_path,
) -> None:
    root = tmp_path / "packages"
    root.mkdir()
    manifest_service.reset_desktop_helper_manifest_cache()
    for index in range(manifest_service._MAX_VERIFIED_ARTIFACTS + 7):
        path = root / f"artifact-{index}.zip"
        payload = f"payload-{index}".encode()
        path.write_bytes(payload)
        handle = manifest_service.open_verified_artifact(
            path,
            root_dir=root,
            relative_path=path.name,
            size_bytes=len(payload),
            sha256=_sha256(payload),
            package_target=f"artifact-{index}",
        )
        handle.close()

    assert len(manifest_service._verified_artifacts) == manifest_service._MAX_VERIFIED_ARTIFACTS
    assert manifest_service._artifact_locks == {}


def test_download_verification_reuses_an_exact_fingerprint_cache_hit(
    monkeypatch,
    tmp_path,
) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    artifact = _manifest_artifact_path(manifest)
    original_sha256 = manifest_service._sha256_for_file
    hash_calls = 0

    def counted_sha256(path: Path, *, handle=None) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return original_sha256(path, handle=handle)

    monkeypatch.setattr(manifest_service, "_sha256_for_file", counted_sha256)
    manifest_service.list_desktop_helper_manifest_records(platform="mac")
    handle = manifest_service.open_verified_artifact(
        artifact,
        root_dir=manifest_service.HELPER_RELEASE_PACKAGES_DIR,
        relative_path=artifact.name,
        size_bytes=artifact.stat().st_size,
        sha256=_sha256(artifact.read_bytes()),
        package_target="macos-dual-arch",
    )
    handle.close()

    assert hash_calls == 1


def test_repeated_download_opens_do_not_rehash_unchanged_artifact(
    initialized_settings,
    monkeypatch,
    tmp_path,
) -> None:
    _write_manifest(
        monkeypatch,
        tmp_path,
        release_root=initialized_settings.helper_releases_dir,
    )
    monkeypatch.setattr(
        desktop_helper_service,
        "_desktop_backend_origin_sha256",
        lambda _settings: BOUND_ORIGIN_SHA256,
    )
    original_sha256 = manifest_service._sha256_for_file
    hash_calls = 0

    def counted_sha256(path: Path, *, handle=None) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return original_sha256(path, handle=handle)

    monkeypatch.setattr(manifest_service, "_sha256_for_file", counted_sha256)
    release = manifest_service.list_desktop_helper_manifest_records(
        platform="mac",
        expected_bound_origin_sha256=BOUND_ORIGIN_SHA256,
    )[0]
    for _index in range(12):
        opened = desktop_helper_service.open_helper_release_download(
            initialized_settings,
            int(release["id"]),
        )
        opened["handle"].close()

    assert hash_calls == 1


def test_download_stream_generator_closes_handle_on_success_and_disconnect() -> None:
    class FakeHandle:
        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = list(chunks)
            self.closed = False

        def read(self, _size: int) -> bytes:
            return self._chunks.pop(0) if self._chunks else b""

        def close(self) -> None:
            self.closed = True

    # Full consumption closes the handle.
    released: list[str] = []
    consumed = FakeHandle([b"a", b"b"])
    assert b"".join(desktop_helper_route._stream_and_close(
        consumed,
        release_slot=lambda: released.append("success"),
    )) == b"ab"
    assert consumed.closed is True

    # Early termination (client disconnect) closes the handle.
    disconnected = FakeHandle([b"a", b"b", b"c"])
    generator = desktop_helper_route._stream_and_close(
        disconnected,
        release_slot=lambda: released.append("disconnect"),
    )
    assert next(generator) == b"a"
    generator.close()
    assert disconnected.closed is True
    assert released == ["success", "disconnect"]


def test_download_stream_generator_closes_handle_on_read_error() -> None:
    class FailingHandle:
        def __init__(self) -> None:
            self.closed = False

        def read(self, _size: int) -> bytes:
            raise OSError("stream read failure")

        def close(self) -> None:
            self.closed = True

    handle = FailingHandle()
    released: list[bool] = []
    with pytest.raises(OSError):
        list(desktop_helper_route._stream_and_close(
            handle,
            release_slot=lambda: released.append(True),
        ))
    assert handle.closed is True
    assert released == [True]


def test_download_stream_records_success_and_interruption() -> None:
    class FakeHandle:
        def __init__(self) -> None:
            self.closed = False
            self._chunks = [b"abc", b"def"]

        def read(self, size: int) -> bytes:
            chunk = self._chunks.pop(0) if self._chunks else b""
            return chunk[:size]

        def close(self) -> None:
            self.closed = True

    outcomes: list[str] = []
    complete = FakeHandle()
    assert b"".join(desktop_helper_route._stream_and_close(
        complete,
        remaining=6,
        audit_completion=outcomes.append,
    )) == b"abcdef"
    interrupted = FakeHandle()
    stream = desktop_helper_route._stream_and_close(
        interrupted,
        remaining=6,
        audit_completion=outcomes.append,
    )
    assert next(stream) == b"abc"
    stream.close()

    assert outcomes == ["success", "interrupted"]
    assert complete.closed is True
    assert interrupted.closed is True


def test_helper_download_slots_limit_each_session_and_release_idempotently() -> None:
    desktop_helper_route._reset_download_slots_for_tests()
    release_one = desktop_helper_route._acquire_download_slot(
        user_id=12,
        session_id="session-a",
    )
    release_two = desktop_helper_route._acquire_download_slot(
        user_id=12,
        session_id="session-a",
    )
    other_session = desktop_helper_route._acquire_download_slot(
        user_id=12,
        session_id="session-b",
    )
    with pytest.raises(HTTPException) as exc_info:
        desktop_helper_route._acquire_download_slot(
            user_id=12,
            session_id="session-a",
        )
    assert exc_info.value.status_code == 429

    release_one()
    retry = desktop_helper_route._acquire_download_slot(
        user_id=12,
        session_id="session-a",
    )
    release_one()
    release_two()
    retry()
    other_session()

    assert desktop_helper_route._active_downloads == {}


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("bytes=0-3", (0, 3)),
        ("bytes=4-", (4, 9)),
        ("bytes=-4", (6, 9)),
        ("bytes=0-99", (0, 9)),
    ],
)
def test_helper_download_range_parser_supports_one_bounded_range(
    header: str | None,
    expected: tuple[int, int] | None,
) -> None:
    assert desktop_helper_route._resolve_download_range(header, 10) == expected


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=4-2", "bytes=0-1,3-4", "items=0-1", "bytes=-0"])
def test_helper_download_range_parser_rejects_invalid_or_multiple_ranges(
    header: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        desktop_helper_route._resolve_download_range(header, 10)

    assert exc_info.value.status_code == 416
    assert exc_info.value.headers == {"Content-Range": "bytes */10"}


def test_helper_download_content_disposition_rejects_header_injection() -> None:
    header = desktop_helper_route._download_content_disposition(
        "elvern-vlc-opener-0.9.0-linux-universal.zip"
    )
    assert "filename*=UTF-8''" in header
    with pytest.raises(HTTPException) as exc_info:
        desktop_helper_route._download_content_disposition('helper.zip"\r\nX-Evil: 1')
    assert exc_info.value.status_code == 410


@pytest.mark.parametrize(
    "filename",
    [
        "a" * 177 + ".zip",
        "helper\r\nInjected.zip",
        'helper".zip',
        "nested/helper.zip",
        r"nested\helper.zip",
        "hélper.zip",
    ],
)
def test_helper_download_filename_contract_rejects_unsafe_or_oversized_names(
    filename: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        desktop_helper_route._download_content_disposition(filename)
    assert exc_info.value.status_code == 410


def test_helper_download_filename_contract_accepts_180_character_boundary() -> None:
    filename = "a" * 176 + ".zip"
    assert len(filename) == 180
    header = desktop_helper_route._download_content_disposition(filename)
    assert f'filename="{filename}"' in header


def test_manifest_origin_mismatch_fails_before_artifact_hash(monkeypatch, tmp_path) -> None:
    _write_manifest(monkeypatch, tmp_path)
    monkeypatch.setattr(
        manifest_service,
        "_sha256_for_file",
        lambda _path, **_kwargs: pytest.fail("incompatible artifacts must not be hashed"),
    )

    with pytest.raises(manifest_service.DesktopHelperManifestOriginMismatch):
        manifest_service.list_desktop_helper_manifest_records(
            platform="mac",
            expected_bound_origin_sha256="f" * 64,
        )


def test_origin_mismatch_never_falls_back_to_the_legacy_db(
    initialized_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop_helper_service,
        "_list_helper_releases_from_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            manifest_service.DesktopHelperManifestOriginMismatch("mismatch")
        ),
    )
    monkeypatch.setattr(
        desktop_helper_service,
        "list_helper_releases",
        lambda *_args, **_kwargs: pytest.fail("origin mismatch must not use the DB fallback"),
    )

    releases = desktop_helper_service.build_desktop_helper_release_payloads(
        initialized_settings,
        platform="mac",
    )

    assert releases == []


def test_origin_mismatch_status_uses_the_safe_exact_note(
    initialized_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop_helper_service,
        "_build_desktop_helper_release_payloads_with_diagnostics",
        lambda *_args, **_kwargs: ([], True),
    )

    payload = desktop_helper_service.get_desktop_helper_status(
        initialized_settings,
        user_id=1,
        platform="mac",
        device_id=None,
        browser_user_agent="Macintosh",
        source_ip="203.0.113.8",
        same_host=False,
        same_host_detection_source="client_ip_not_local",
    )

    assert payload["state"] == "release_unavailable"
    assert payload["latest_releases"] == []
    assert payload["notes"] == [
        "The available Helper package was built for a different Elvern server origin."
    ]


@pytest.mark.parametrize(
    "value",
    [
        "https://user@example.test",
        "https://example.test/prefix",
        "https://example.test?query=1",
        "https://example.test/#fragment",
        "https://xn--bcher-kva.example",
        "https://example.test\x7f",
    ],
)
def test_desktop_helper_origin_rejects_non_origin_components(value: str) -> None:
    with pytest.raises(ValueError):
        desktop_helper_service.canonicalize_desktop_helper_origin(value)


def test_backend_and_build_origin_normalizers_match_shared_matrix() -> None:
    cases = json.loads(ORIGIN_CASES_PATH.read_text(encoding="utf-8"))

    for case in cases:
        expected = case["normalized"]
        if expected is None:
            with pytest.raises(ValueError):
                desktop_helper_service.canonicalize_desktop_helper_origin(case["input"])
            result = subprocess.run(
                ["python3", str(ORIGIN_BUILD_NORMALIZER), case["input"]],
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0
            continue

        assert desktop_helper_service.canonicalize_desktop_helper_origin(
            case["input"]
        ) == expected
        result = subprocess.run(
            ["python3", str(ORIGIN_BUILD_NORMALIZER), case["input"]],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        output = result.stdout.splitlines()
        assert output == [expected, _sha256(expected.encode())]


def test_desktop_helper_origin_canonicalizes_case_and_default_ports() -> None:
    assert (
        desktop_helper_service.canonicalize_desktop_helper_origin(
            "HTTPS://ELVERN.EXAMPLE:443/"
        )
        == "https://elvern.example"
    )


def test_legacy_manifest_remains_available_as_framework_dependent_fallback(
    monkeypatch,
    tmp_path,
) -> None:
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    artifact = packages_dir / "legacy-mac.zip"
    artifact.write_bytes(b"legacy")
    manifest = {
        "helper_version": "0.8.0",
        "channel": "stable",
        "dotnet_runtime_major": "8",
        "dotnet_runtime_display": ".NET 8",
        "generated_at_utc": "2026-07-22T00:00:00Z",
        "packages": [{
            "runtime": "osx-arm64",
            "platform_family": "mac",
            "filename": artifact.name,
            "relative_path": artifact.name,
            "package_name": "Legacy Elvern VLC Opener",
            "sha256": _sha256(b"legacy"),
            "size_bytes": len(b"legacy"),
            "artifact_kind": "zip",
            "generated_at_utc": "2026-07-22T00:00:00Z",
        }],
    }
    manifest_path = packages_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        manifest_service,
        "HELPER_RELEASE_MANIFEST_PATH",
        manifest_path,
        raising=False,
    )
    monkeypatch.setattr(
        manifest_service,
        "HELPER_RELEASE_PACKAGES_DIR",
        packages_dir,
        raising=False,
    )
    monkeypatch.setattr(
        manifest_service,
        "list_desktop_helper_manifest_records",
        lambda **kwargs: _LIST_MANIFEST_RECORDS(packages_dir, **kwargs),
    )
    monkeypatch.setattr(
        manifest_service,
        "reset_desktop_helper_manifest_cache",
        lambda: _RESET_MANIFEST_CACHE(packages_dir),
    )
    _RESET_MANIFEST_CACHE(packages_dir)

    records = manifest_service.list_desktop_helper_manifest_records(platform="mac")

    assert len(records) == 1
    assert records[0]["runtime_id"] == "osx-arm64"
    assert records[0]["deployment_mode"] == "framework_dependent"
    assert records[0]["external_runtime_required"] is True
    assert records[0]["dotnet_runtime_required"] == "8.x"


def test_package_release_and_status_schemas_accept_linux_self_contained_contract() -> None:
    release = DesktopHelperReleaseResponse(
        id=12,
        channel="stable",
        runtime_id="linux-universal",
        platform="linux",
        package_target="linux-universal",
        version="0.9.0",
        filename="elvern-vlc-opener-0.9.0-linux-universal.zip",
        package_root="Elvern VLC Opener Linux Installer",
        installer_entrypoint="Install-ElvernVlcOpener.sh",
        size_bytes=123,
        sha256="b" * 64,
        installer_manifest_sha256="c" * 64,
        installer_tree_manifest_path=".elvern/tree-manifest.tsv",
        installer_tree_manifest_sha256="d" * 64,
        package_binding="compatible",
        published_at="2026-07-22T00:00:00Z",
        download_url="/api/desktop-helper/releases/12/download",
        deployment_mode="self_contained",
        external_runtime_required=False,
        runtime_family="10.0",
        supported_runtime_ids=[
            "linux-x64",
            "linux-arm64",
            "linux-musl-x64",
            "linux-musl-arm64",
        ],
        recommended=True,
    )
    status = DesktopHelperStatusResponse(
        platform="linux",
        helper_required=True,
        state="unknown",
        same_host=False,
        same_host_detection_source="client_ip_not_local",
        runtime_included=True,
        latest_releases=[release],
    )

    assert status.latest_releases[0].external_runtime_required is False
    assert status.dotnet_runtime_required is None


def test_linux_same_host_status_does_not_offer_helper(initialized_settings, monkeypatch) -> None:
    monkeypatch.setattr(
        desktop_helper_service,
        "_build_desktop_helper_release_payloads_with_diagnostics",
        lambda *_args, **_kwargs: pytest.fail("same-host Linux must not read helper packages"),
    )

    payload = desktop_helper_service.get_desktop_helper_status(
        initialized_settings,
        user_id=1,
        platform="linux",
        device_id=None,
        browser_user_agent="Linux",
        source_ip="127.0.0.1",
        same_host=True,
        same_host_detection_source="loopback_client_ip",
    )

    assert payload["helper_required"] is False
    assert payload["state"] == "helper_not_required"
    assert payload["same_host"] is True
    assert payload["latest_releases"] == []


def test_linux_remote_status_offers_universal_helper_without_host_vlc_detection(
    initialized_settings,
    monkeypatch,
) -> None:
    release = {
        "id": 22,
        "channel": "stable",
        "runtime_id": "linux-universal",
        "platform": "linux",
        "package_target": "linux-universal",
        "version": "0.9.0",
        "filename": "helper.zip",
        "package_root": "Elvern VLC Opener Linux Installer",
        "installer_entrypoint": "Install-ElvernVlcOpener.sh",
        "size_bytes": 12,
        "sha256": "d" * 64,
        "installer_manifest_sha256": "e" * 64,
        "published_at": "2026-07-22T00:00:00Z",
        "download_url": "/api/desktop-helper/releases/22/download",
        "deployment_mode": "self_contained",
        "external_runtime_required": False,
        "runtime_family": "10.0",
        "supported_runtime_ids": ["linux-x64"],
        "recommended": True,
    }
    monkeypatch.setattr(
        desktop_helper_service,
        "_build_desktop_helper_release_payloads_with_diagnostics",
        lambda *_args, **_kwargs: ([release], False),
    )
    monkeypatch.setattr(
        desktop_helper_service,
        "_probe_linux_vlc_detection",
        lambda *_args, **_kwargs: pytest.fail("remote Linux must not inspect host VLC"),
    )

    payload = desktop_helper_service.get_desktop_helper_status(
        initialized_settings,
        user_id=1,
        platform="linux",
        device_id=None,
        browser_user_agent="Linux",
        source_ip="203.0.113.8",
        same_host=False,
        same_host_detection_source="client_ip_not_local",
    )

    assert payload["helper_required"] is True
    assert payload["state"] == "unknown"
    assert payload["same_host"] is False
    assert payload["vlc_detection_state"] == "detection_unavailable"
    assert payload["latest_releases"] == [release]


def test_linux_status_route_uses_server_same_host_result(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    assert client.post("/api/auth/login", json=admin_credentials).status_code == 200
    monkeypatch.setattr(
        desktop_helper_route,
        "resolve_same_host_request",
        lambda *_args, **_kwargs: {
            "same_host": True,
            "detection_source": "loopback_client_ip",
        },
    )

    response = client.get("/api/desktop-helper/status?platform=linux&device_id=route-test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["helper_required"] is False
    assert payload["state"] == "helper_not_required"
    assert payload["same_host_detection_source"] == "loopback_client_ip"
    assert payload["latest_releases"] == []


def test_linux_remote_status_route_is_conservative_and_does_not_probe_host_vlc(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    assert client.post("/api/auth/login", json=admin_credentials).status_code == 200
    monkeypatch.setattr(
        desktop_helper_route,
        "resolve_same_host_request",
        lambda *_args, **_kwargs: {
            "same_host": False,
            "detection_source": "client_ip_not_local",
        },
    )
    monkeypatch.setattr(
        desktop_helper_service,
        "_probe_linux_vlc_detection",
        lambda *_args, **_kwargs: pytest.fail("remote Linux must not inspect host VLC"),
    )

    response = client.get("/api/desktop-helper/status?platform=linux&device_id=remote-test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["helper_required"] is True
    assert payload["same_host"] is False
    assert payload["same_host_detection_source"] == "client_ip_not_local"
    assert payload["vlc_detection_state"] == "detection_unavailable"


def test_release_route_supports_one_linux_universal_package(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    assert client.post("/api/auth/login", json=admin_credentials).status_code == 200
    release = {
        "id": 30,
        "channel": "stable",
        "runtime_id": "linux-universal",
        "platform": "linux",
        "package_target": "linux-universal",
        "version": "0.9.0",
        "filename": "elvern-vlc-opener-0.9.0-linux-universal.zip",
        "package_root": "Elvern VLC Opener Linux Installer",
        "installer_entrypoint": "Install-ElvernVlcOpener.sh",
        "size_bytes": 123,
        "sha256": "d" * 64,
        "installer_manifest_sha256": "e" * 64,
        "published_at": "2026-07-22T00:00:00Z",
        "dotnet_runtime_required": None,
        "download_url": "/api/desktop-helper/releases/30/download",
        "deployment_mode": "self_contained",
        "external_runtime_required": False,
        "runtime_family": "10.0",
        "supported_runtime_ids": ["linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64"],
        "minimum_os_version": None,
        "recommended": True,
    }
    monkeypatch.setattr(
        desktop_helper_route,
        "build_desktop_helper_release_payloads",
        lambda *_args, **_kwargs: [release],
    )

    response = client.get("/api/desktop-helper/releases?platform=linux")

    assert response.status_code == 200
    payload = response.json()
    assert payload["platform"] == "linux"
    assert len(payload["releases"]) == 1
    assert payload["releases"][0]["package_target"] == "linux-universal"
    assert payload["releases"][0]["external_runtime_required"] is False


def test_package_level_version_drives_universal_update_state(initialized_settings, monkeypatch) -> None:
    release = {
        "id": 31,
        "channel": "stable",
        "runtime_id": "linux-universal",
        "platform": "linux",
        "package_target": "linux-universal",
        "version": "0.9.0",
        "filename": "helper.zip",
        "package_root": "Elvern VLC Opener Linux Installer",
        "installer_entrypoint": "Install-ElvernVlcOpener.sh",
        "size_bytes": 12,
        "sha256": "d" * 64,
        "installer_manifest_sha256": "e" * 64,
        "published_at": "2026-07-22T00:00:00Z",
        "download_url": "/api/desktop-helper/releases/31/download",
        "deployment_mode": "self_contained",
        "external_runtime_required": False,
        "runtime_family": "10.0",
        "supported_runtime_ids": ["linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64"],
        "minimum_os_version": None,
        "dotnet_runtime_required": None,
        "recommended": True,
    }
    monkeypatch.setattr(
        desktop_helper_service,
        "_build_desktop_helper_release_payloads_with_diagnostics",
        lambda *_args, **_kwargs: ([release], False),
    )
    monkeypatch.setattr(
        desktop_helper_service,
        "record_client_device_app_seen",
        lambda *_args, **_kwargs: {
            "helper_version": "0.8.0",
            "helper_platform": "linux",
            "helper_arch": "linux-x64",
            "helper_last_seen_at": "2026-07-21T00:00:00Z",
            "vlc_detection_state": "installed",
            "vlc_detection_path": "/usr/bin/vlc",
            "vlc_detection_checked_at": "2026-07-21T00:00:00Z",
        },
    )

    payload = desktop_helper_service.get_desktop_helper_status(
        initialized_settings,
        user_id=1,
        platform="linux",
        device_id="device-update",
        browser_user_agent="Linux",
        source_ip="203.0.113.8",
        same_host=False,
        same_host_detection_source="client_ip_not_local",
    )

    assert payload["state"] == "update_available"
    assert payload["runtime_included"] is True
    assert payload["dotnet_runtime_required"] is None


def test_legacy_db_catalog_remains_a_per_runtime_fallback(initialized_settings, monkeypatch) -> None:
    monkeypatch.setattr(desktop_helper_service, "_list_helper_releases_from_manifest", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        desktop_helper_service,
        "list_helper_releases",
        lambda *_args, **_kwargs: [
            {
                "id": 41,
                "channel": "stable",
                "runtime_id": "osx-arm64",
                "platform": "mac",
                "version": "0.8.0",
                "filename": "arm.zip",
                "relative_path": "arm.zip",
                "sha256": "a" * 64,
                "size_bytes": 1,
                "dotnet_runtime_required": "8.x",
                "published_at": "2026-07-20T00:00:00Z",
            },
            {
                "id": 42,
                "channel": "stable",
                "runtime_id": "osx-x64",
                "platform": "mac",
                "version": "0.8.0",
                "filename": "x64.zip",
                "relative_path": "x64.zip",
                "sha256": "b" * 64,
                "size_bytes": 1,
                "dotnet_runtime_required": "8.x",
                "published_at": "2026-07-20T00:00:00Z",
            },
        ],
    )

    releases = desktop_helper_service.build_desktop_helper_release_payloads(
        initialized_settings,
        platform="mac",
        helper_arch="arm64",
    )

    assert [release["runtime_id"] for release in releases] == ["osx-arm64", "osx-x64"]


def test_manifest_absent_is_the_only_state_that_allows_db_fallback(
    initialized_settings,
    tmp_path,
) -> None:
    settings = replace(initialized_settings, helper_releases_dir=tmp_path)

    assert desktop_helper_service._list_helper_releases_from_manifest(
        settings,
        platform="mac",
    ) is None
    assert desktop_helper_service._get_helper_release_from_manifest(settings, 123) is None


@pytest.mark.parametrize(
    "manifest_bytes",
    [
        b"{",
        json.dumps({"schema_version": "wrong", "packages": []}).encode(),
        json.dumps({
            "schema_version": "desktop-helper-release-manifest-v2",
            "helper_version": "0.9.0",
            "channel": "stable",
            "target_framework": "net10.0",
            "runtime_family": "10.0",
            "deployment_mode": "self_contained",
            "generated_at_utc": "2026-07-22T00:00:00Z",
            "bound_origin_sha256": BOUND_ORIGIN_SHA256,
            "packages": [],
        }).encode(),
    ],
)
def test_present_invalid_manifest_forbids_db_fallback_and_download(
    initialized_settings,
    tmp_path,
    monkeypatch,
    manifest_bytes,
) -> None:
    (tmp_path / "release-manifest.json").write_bytes(manifest_bytes)
    settings = replace(initialized_settings, helper_releases_dir=tmp_path)
    monkeypatch.setattr(
        desktop_helper_service,
        "list_helper_releases",
        lambda *_args, **_kwargs: pytest.fail("invalid authority must not use DB"),
    )

    assert desktop_helper_service.build_desktop_helper_release_payloads(
        settings,
        platform="mac",
    ) == []
    with pytest.raises(HTTPException) as exc_info:
        desktop_helper_service.get_helper_release_download_path(settings, 123)
    assert exc_info.value.status_code == 410


@pytest.mark.parametrize(
    "kind",
    [
        "final_symlink",
        "broken_symlink",
        "directory",
        "fifo",
        "socket",
        "root_symlink",
    ],
)
def test_unsafe_manifest_objects_are_present_invalid(tmp_path, kind) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    manifest = root / "release-manifest.json"
    if kind == "final_symlink":
        target = tmp_path / "target.json"
        target.write_text("{}")
        manifest.symlink_to(target)
    elif kind == "broken_symlink":
        manifest.symlink_to(tmp_path / "missing.json")
    elif kind == "directory":
        manifest.mkdir()
    elif kind == "fifo":
        os.mkfifo(manifest)
    elif kind == "socket":
        unix_socket = socket.socket(socket.AF_UNIX)
        try:
            unix_socket.bind(str(manifest))
        finally:
            unix_socket.close()
    else:
        actual = tmp_path / "actual"
        actual.mkdir()
        root.rmdir()
        root.symlink_to(actual, target_is_directory=True)

    with pytest.raises(manifest_service.DesktopHelperManifestError) as exc_info:
        _LIST_MANIFEST_RECORDS(root)
    assert not isinstance(exc_info.value, manifest_service.DesktopHelperManifestAbsent)


def test_manifest_permission_error_is_present_invalid(tmp_path, monkeypatch) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "release-manifest.json").write_text("{}")
    real_open = manifest_service.os.open

    def denied_open(path, flags, *args, **kwargs):
        if path == "release-manifest.json":
            raise PermissionError("denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(manifest_service.os, "open", denied_open)
    with pytest.raises(manifest_service.DesktopHelperManifestError) as exc_info:
        _LIST_MANIFEST_RECORDS(root)
    assert not isinstance(exc_info.value, manifest_service.DesktopHelperManifestAbsent)


def test_valid_v2_missing_platform_remains_authoritative(
    initialized_settings,
    monkeypatch,
    tmp_path,
) -> None:
    _write_manifest(monkeypatch, tmp_path, release_root=tmp_path)
    settings = replace(initialized_settings, helper_releases_dir=tmp_path)
    monkeypatch.setattr(
        desktop_helper_service,
        "list_helper_releases",
        lambda *_args, **_kwargs: pytest.fail("valid v2 authority must not use DB"),
    )

    assert desktop_helper_service.build_desktop_helper_release_payloads(
        settings,
        platform="windows",
    ) == []
