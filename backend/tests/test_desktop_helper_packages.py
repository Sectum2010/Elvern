from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.app.schemas import DesktopHelperReleaseResponse, DesktopHelperStatusResponse
from backend.app.services import desktop_helper_manifest_service as manifest_service
from backend.app.services import desktop_helper_service
from backend.app.routes import desktop_helper as desktop_helper_route


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
                "minimum_os_version": "14.0",
                "generated_at_utc": "2026-07-22T00:00:00Z",
            }
        ],
    }
    manifest_path = packages_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(manifest_service, "HELPER_RELEASE_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(manifest_service, "HELPER_RELEASE_PACKAGES_DIR", packages_dir)
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
    assert record["minimum_os_version"] == "14.0"


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
        "build_desktop_helper_release_payloads",
        lambda *_args, **_kwargs: [{"package_target": "linux-universal"}],
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
        "build_desktop_helper_release_payloads",
        lambda *_args, **_kwargs: [release],
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
        "build_desktop_helper_release_payloads",
        lambda *_args, **_kwargs: [release],
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
