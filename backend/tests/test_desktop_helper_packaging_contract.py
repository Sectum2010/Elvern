from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER_ROOT = ROOT / "clients" / "desktop-vlc-opener"


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_macos_runtime_selector_handles_native_and_rosetta() -> None:
    selectors = HELPER_ROOT / "packaging" / "common" / "platform-selectors.sh"
    cases = {
        ("0", "arm64"): "osx-arm64",
        ("0", "x86_64"): "osx-x64",
        ("1", "x86_64"): "osx-arm64",
    }
    for (translated, machine), expected in cases.items():
        result = _bash(f'source "{selectors}"; select_macos_runtime "{translated}" "{machine}"')
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    unsupported = _bash(f'source "{selectors}"; select_macos_runtime "0" "ppc64"')
    assert unsupported.returncode != 0


def test_linux_runtime_selector_handles_cpu_and_libc_matrix() -> None:
    selectors = HELPER_ROOT / "packaging" / "common" / "platform-selectors.sh"
    cases = {
        ("x86_64", "glibc"): "linux-x64",
        ("amd64", "glibc"): "linux-x64",
        ("aarch64", "glibc"): "linux-arm64",
        ("arm64", "musl"): "linux-musl-arm64",
        ("x86_64", "musl"): "linux-musl-x64",
    }
    for (machine, libc), expected in cases.items():
        result = _bash(f'source "{selectors}"; select_linux_runtime "{machine}" "{libc}"')
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    assert _bash(f'source "{selectors}"; select_linux_runtime "x86_64" "unknown"').returncode != 0
    assert _bash(f'source "{selectors}"; select_linux_runtime "ppc64" "glibc"').returncode != 0


def test_installer_scripts_are_syntactically_valid() -> None:
    scripts = [
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command",
        HELPER_ROOT / "packaging" / "linux" / "Install-ElvernVlcOpener.sh",
        HELPER_ROOT / "scripts" / "publish-bundles.sh",
    ]
    for script in scripts:
        result = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_publish_script_keeps_standard_packages_self_contained() -> None:
    source = (HELPER_ROOT / "scripts" / "publish-bundles.sh").read_text(encoding="utf-8")

    assert 'PUBLISH_MODE="self-contained"' in source
    assert "--self-contained true" in source
    assert "PublishSingleFile=true" in source
    assert "IncludeNativeLibrariesForSelfExtract=true" in source
    assert "PublishTrimmed=false" in source
    assert "ELVERN_DOTNET_NUGET_SOURCE" in source
    assert '--source "${NUGET_SOURCE}"' in source
    assert "linux-musl-arm64" in source
    assert "desktop-helper-release-manifest-v2" in source


def test_package_sources_keep_visible_roots_clean_and_payloads_private() -> None:
    source = (HELPER_ROOT / "scripts" / "publish-bundles.sh").read_text(encoding="utf-8")

    assert 'cp "${PACKAGING_DIR}/macos/Install-ElvernVlcOpener.command" "${root}/"' in source
    assert 'cp "${PACKAGING_DIR}/linux/Install-ElvernVlcOpener.sh" "${root}/"' in source
    assert 'write_package_readme "${root}/README.txt"' in source
    assert 'copy_payloads "${private}"' in source
    assert 'write_inner_manifests "${private}" "macos-dual-arch"' in source
    assert 'write_tree_manifest "${root}"' in source
    assert 'cp "${PACKAGING_DIR}/macos/ElvernVlcOpener.applescript" "${private}/bridge/"' in source
    assert 'cp "${PACKAGING_DIR}/linux/Uninstall-ElvernVlcOpener.sh" "${private}/uninstall/"' in source
    assert source.index("if path.is_symlink():") < source.index("if path.is_dir():")


def test_macos_installer_trust_commands_are_scoped_to_elvern_apps() -> None:
    source = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    assert 'codesign --force --sign - "${APP_PAYLOAD_DIR}/Elvern.VlcOpener"' in source
    assert 'codesign --force --sign - "${STAGED_APP}"' in source
    assert 'codesign --verify --deep --strict "${STAGED_APP}"' in source
    assert 'xattr -dr com.apple.quarantine "${STAGED_APP}"' in source
    assert 'xattr -dr com.apple.quarantine "${DEST_APP}"' in source
    assert 'verify_quarantine_cleared "${STAGED_APP}"' in source
    assert 'verify_quarantine_cleared "${DEST_APP}"' in source
    assert '|| fail "macOS quarantine could not be removed' in source
    assert "spctl --master-disable" not in source
    assert "sudo" not in source
    assert 'xattr -dr com.apple.quarantine "${HOME}/Applications"' not in source
    assert 'xattr -dr com.apple.quarantine "${HOME}/Downloads"' not in source
    assert "mapfile" not in source


def test_macos_installer_generates_versioned_info_plist_for_macos_14() -> None:
    source = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    assert 'plist_set "CFBundleShortVersionString" string "${HELPER_VERSION}"' in source
    assert 'plist_set "CFBundleVersion" string "${HELPER_VERSION}"' in source
    assert 'plist_set "LSMinimumSystemVersion" string "14.0"' in source


def test_installers_verify_the_full_tree_before_using_package_code() -> None:
    mac = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")
    linux = (
        HELPER_ROOT / "packaging" / "linux" / "Install-ElvernVlcOpener.sh"
    ).read_text(encoding="utf-8")
    windows = (
        HELPER_ROOT / "packaging" / "windows" / "Install-ElvernVlcOpener.ps1"
    ).read_text(encoding="utf-8")

    assert mac.index("verify_package_tree") < mac.index('source "${SELECTORS}"')
    assert linux.index("verify_package_tree") < linux.index('source "${SELECTORS}"')
    assert windows.index("Test-InstallerTree") < windows.index("$manifest = Get-Content")
    assert "python" not in mac.lower()
    assert "python" not in linux.lower()
    assert '"${SOURCE_PAYLOAD}" --version' not in mac
    assert '"${PAYLOAD}" --version' not in linux
    assert "& $sourceExe --version" not in windows
    assert "$item.LinkType" in windows
    assert "[IO.FileAttributes]::ReparsePoint" in windows
    assert "Unblock-File -LiteralPath $stagedExe" in windows
    assert "Unblock-File -LiteralPath $stagedUninstaller" in windows


def test_windows_installer_checks_registry_backup_and_restore_failures() -> None:
    source = (
        HELPER_ROOT / "packaging" / "windows" / "Install-ElvernVlcOpener.ps1"
    ).read_text(encoding="utf-8")

    assert "function Export-RegistryKey" in source
    assert "The existing per-user registration could not be backed up safely." in source
    assert "The previous elvern-vlc:// registration could not be restored." in source
    assert "The previous uninstall registration could not be restored." in source
    assert "Registry rollback also failed:" in source


def test_publish_requires_explicit_activation_and_immutable_artifact_names() -> None:
    source = (HELPER_ROOT / "scripts" / "publish-bundles.sh").read_text(encoding="utf-8")

    assert 'ACTIVATE=0' in source
    assert '--activate' in source
    assert 'ALLOW_PARTIAL_ACTIVATE=0' in source
    assert '--allow-partial-activate' in source
    assert '${digest:0:12}.zip' in source
    assert "Immutable active artifact collision:" in source
    assert 'mv "${manifest_temp}" "${ACTIVE_DIR}/release-manifest.json"' in source
    assert 'mkdir "${lock_dir}"' in source
    assert 'ACTIVATION_LOCK_DIR="${lock_dir}"' in source
    assert 'rmdir "${ACTIVATION_LOCK_DIR}" 2>/dev/null || true' in source
