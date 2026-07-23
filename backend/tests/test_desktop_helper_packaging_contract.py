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
    assert 'write_inner_manifest "${private}/manifest.json"' in source
    assert 'cp "${PACKAGING_DIR}/macos/ElvernVlcOpener.applescript" "${private}/bridge/"' in source
    assert 'cp "${PACKAGING_DIR}/linux/Uninstall-ElvernVlcOpener.sh" "${private}/uninstall/"' in source


def test_macos_installer_trust_commands_are_scoped_to_elvern_apps() -> None:
    source = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    assert 'codesign --force --deep --sign - "${STAGED_APP}"' in source
    assert 'codesign --verify --deep --strict "${STAGED_APP}"' in source
    assert 'xattr -dr com.apple.quarantine "${STAGED_APP}"' in source
    assert 'xattr -dr com.apple.quarantine "${DEST_APP}"' in source
    assert "spctl --master-disable" not in source
    assert "sudo" not in source
    assert 'xattr -dr com.apple.quarantine "${HOME}/Applications"' not in source
    assert 'xattr -dr com.apple.quarantine "${HOME}/Downloads"' not in source
    assert "mapfile" not in source


def test_info_plist_template_tracks_version_placeholder_and_macos_14() -> None:
    source = (HELPER_ROOT / "packaging" / "macos" / "Info.plist.template").read_text(
        encoding="utf-8"
    )

    assert source.count("__VERSION__") == 2
    assert "<key>LSMinimumSystemVersion</key>" in source
    assert "<string>14.0</string>" in source
