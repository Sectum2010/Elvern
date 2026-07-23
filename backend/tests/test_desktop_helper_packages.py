from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.schemas import DesktopHelperReleaseResponse, DesktopHelperStatusResponse
from backend.app.services import desktop_helper_manifest_service as manifest_service
from backend.app.services import desktop_helper_service
from backend.app.routes import desktop_helper as desktop_helper_route


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


BOUND_ORIGIN_SHA256 = _sha256(b"https://elvern.example")


def _write_manifest(
    monkeypatch,
    tmp_path: Path,
    *,
    relative_path: str = "elvern-vlc-opener-0.9.0-macos-dual-arch.zip",
) -> dict[str, object]:
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    artifact = packages_dir / relative_path
    if ".." not in Path(relative_path).parts:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"desktop-helper-package")
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
                "filename": "elvern-vlc-opener-0.9.0-macos-dual-arch.zip",
                "relative_path": relative_path,
                "package_root": "Elvern VLC Opener Installer",
                "installer_entrypoint": "Install-ElvernVlcOpener.command",
                "supported_runtime_ids": ["osx-arm64", "osx-x64"],
                "external_runtime_required": False,
                "size_bytes": len(b"desktop-helper-package"),
                "sha256": _sha256(b"desktop-helper-package"),
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
    monkeypatch.setattr(manifest_service, "HELPER_RELEASE_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(manifest_service, "HELPER_RELEASE_PACKAGES_DIR", packages_dir)
    manifest_service.reset_desktop_helper_manifest_cache()
    return manifest


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
    assert first[0]["version"] == "0.9.0"

    manifest["helper_version"] = "0.9.1"
    replacement = manifest_service.HELPER_RELEASE_MANIFEST_PATH.with_suffix(".replacement")
    replacement.write_text(json.dumps(manifest), encoding="utf-8")
    replacement.replace(manifest_service.HELPER_RELEASE_MANIFEST_PATH)

    second = manifest_service.list_desktop_helper_manifest_records(platform="mac")

    assert second[0]["version"] == "0.9.1"
    assert second[0]["id"] != first[0]["id"]


def test_manifest_only_hashes_artifacts_for_the_requested_platform(monkeypatch, tmp_path) -> None:
    manifest = _write_manifest(monkeypatch, tmp_path)
    windows_payload = b"windows-package"
    windows_artifact = manifest_service.HELPER_RELEASE_PACKAGES_DIR / "windows.zip"
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
        "sha256": _sha256(windows_payload),
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
        "elvern-vlc-opener-0.9.0-macos-dual-arch.zip"
    ]


def test_manifest_rehashes_an_artifact_replaced_with_same_size_and_mtime(
    monkeypatch,
    tmp_path,
) -> None:
    _write_manifest(monkeypatch, tmp_path)
    artifact = (
        manifest_service.HELPER_RELEASE_PACKAGES_DIR
        / "elvern-vlc-opener-0.9.0-macos-dual-arch.zip"
    )
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
    _write_manifest(monkeypatch, tmp_path)
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
    _write_manifest(monkeypatch, tmp_path)
    artifact = (
        manifest_service.HELPER_RELEASE_PACKAGES_DIR
        / "elvern-vlc-opener-0.9.0-macos-dual-arch.zip"
    )
    original = artifact.read_bytes()
    handle = manifest_service.open_verified_artifact(
        artifact,
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
    _write_manifest(monkeypatch, tmp_path)
    artifact = (
        manifest_service.HELPER_RELEASE_PACKAGES_DIR
        / "elvern-vlc-opener-0.9.0-macos-dual-arch.zip"
    )
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
            size_bytes=len(original),
            sha256=_sha256(original),
            package_target="macos-dual-arch",
        )


def test_open_verified_artifact_refuses_a_symlink(monkeypatch, tmp_path) -> None:
    _write_manifest(monkeypatch, tmp_path)
    artifact = (
        manifest_service.HELPER_RELEASE_PACKAGES_DIR
        / "elvern-vlc-opener-0.9.0-macos-dual-arch.zip"
    )
    original = artifact.read_bytes()
    link = manifest_service.HELPER_RELEASE_PACKAGES_DIR / "link.zip"
    link.symlink_to(artifact)

    with pytest.raises(manifest_service.DesktopHelperManifestError):
        manifest_service.open_verified_artifact(
            link,
            size_bytes=len(original),
            sha256=_sha256(original),
            package_target="macos-dual-arch",
        )


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
    consumed = FakeHandle([b"a", b"b"])
    assert b"".join(desktop_helper_route._stream_and_close(consumed)) == b"ab"
    assert consumed.closed is True

    # Early termination (client disconnect) closes the handle.
    disconnected = FakeHandle([b"a", b"b", b"c"])
    generator = desktop_helper_route._stream_and_close(disconnected)
    assert next(generator) == b"a"
    generator.close()
    assert disconnected.closed is True


def test_download_stream_generator_closes_handle_on_read_error() -> None:
    class FailingHandle:
        def __init__(self) -> None:
            self.closed = False

        def read(self, _size: int) -> bytes:
            raise OSError("stream read failure")

        def close(self) -> None:
            self.closed = True

    handle = FailingHandle()
    with pytest.raises(OSError):
        list(desktop_helper_route._stream_and_close(handle))
    assert handle.closed is True


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
    ],
)
def test_desktop_helper_origin_rejects_non_origin_components(value: str) -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        desktop_helper_service.canonicalize_desktop_helper_origin(value)


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
    monkeypatch.setattr(manifest_service, "HELPER_RELEASE_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(manifest_service, "HELPER_RELEASE_PACKAGES_DIR", packages_dir)
    manifest_service.reset_desktop_helper_manifest_cache()

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
    assert sum(bool(release["recommended"]) for release in releases) == 1
    assert all(release["external_runtime_required"] is True for release in releases)
