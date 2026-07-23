from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER_ROOT = ROOT / "clients" / "desktop-vlc-opener"
LINUX_INSTALLER = (
    HELPER_ROOT / "packaging" / "linux" / "Install-ElvernVlcOpener.sh"
)
LINUX_UNINSTALLER = (
    HELPER_ROOT / "packaging" / "linux" / "Uninstall-ElvernVlcOpener.sh"
)
PLATFORM_SELECTORS = (
    HELPER_ROOT / "packaging" / "common" / "platform-selectors.sh"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_tree_manifest(package_root: Path) -> None:
    tree_manifest = package_root / ".elvern" / "tree-manifest.tsv"
    lines = ["path\tsize_bytes\tsha256\tfile_class"]
    for path in sorted(package_root.rglob("*")):
        if path == tree_manifest or path.is_dir():
            continue
        relative = path.relative_to(package_root).as_posix()
        file_class = "executable" if os.access(path, os.X_OK) else "data"
        lines.append(
            f"{relative}\t{path.stat().st_size}\t{_sha256(path)}\t{file_class}"
        )
    tree_manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _create_linux_package(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    package_root = tmp_path / "Elvern VLC Opener Linux Installer"
    private = package_root / ".elvern"
    payload = private / "payloads" / "linux-x64" / "Elvern.VlcOpener"
    selector = private / "lib" / "platform-selectors.sh"
    uninstaller = private / "uninstall" / "Uninstall-ElvernVlcOpener.sh"
    payload.parent.mkdir(parents=True)
    selector.parent.mkdir(parents=True)
    uninstaller.parent.mkdir(parents=True)
    shutil.copy2(LINUX_INSTALLER, package_root / "Install-ElvernVlcOpener.sh")
    shutil.copy2(PLATFORM_SELECTORS, selector)
    shutil.copy2(LINUX_UNINSTALLER, uninstaller)
    (package_root / "README.txt").write_text("Elvern test package\n", encoding="utf-8")
    _write_executable(
        payload,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "[ \"${1:-}\" = \"--version\" ] && { echo 0.9.0; exit 0; }\n"
        "exit 0\n",
    )
    manifest = private / "installer-manifest.tsv"
    manifest.write_text(
        "\n".join([
            "meta\tschema_version\tdesktop-helper-installer-manifest-v2",
            "meta\thelper_version\t0.9.0",
            "meta\ttarget_framework\tnet10.0",
            "meta\truntime_family\t10.0",
            "meta\tdeployment_mode\tself_contained",
            "meta\tpackage_target\tlinux-universal",
            f"payload\tlinux-x64\tpayloads/linux-x64/Elvern.VlcOpener\t{_sha256(payload)}\t{payload.stat().st_size}\tElvern.VlcOpener",
        ]) + "\n",
        encoding="utf-8",
    )
    _write_tree_manifest(package_root)

    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    state = tmp_path / "xdg-default"
    home.mkdir()
    fake_bin.mkdir()
    state.write_text("old-handler.desktop\n", encoding="utf-8")
    _write_executable(
        fake_bin / "xdg-mime",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ \"${1:-}\" == \"query\" && \"${2:-}\" == \"default\" ]]; then\n"
        "  cat \"${ELVERN_TEST_XDG_STATE}\"\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"${1:-}\" == \"default\" ]]; then\n"
        "  printf '%s\\n' \"${2}\" > \"${ELVERN_TEST_XDG_STATE}\"\n"
        "  if [[ \"${ELVERN_TEST_XDG_FAIL_NEW:-0}\" == \"1\" && \"${2}\" == \"elvern-vlc-opener.desktop\" ]]; then\n"
        "    exit 41\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"${1:-}\" == \"uninstall\" && \"${2:-}\" == \"--mode\" && \"${3:-}\" == \"user\" ]]; then\n"
        "  : > \"${ELVERN_TEST_XDG_STATE}\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "ELVERN_TEST_XDG_STATE": str(state),
    }
    return package_root, state, env


def _run_installer(
    package_root: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(package_root / "Install-ElvernVlcOpener.sh"),
            "--runtime",
            "linux-x64",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_linux_installer_is_user_scoped_and_idempotent(tmp_path: Path) -> None:
    package_root, state, env = _create_linux_package(tmp_path)

    first = _run_installer(package_root, env)
    second = _run_installer(package_root, env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    install_dir = Path(env["HOME"]) / ".local" / "lib" / "elvern-vlc-opener"
    desktop_file = (
        Path(env["HOME"])
        / ".local"
        / "share"
        / "applications"
        / "elvern-vlc-opener.desktop"
    )
    assert (install_dir / "Elvern.VlcOpener").is_file()
    assert (install_dir / "Uninstall-ElvernVlcOpener.sh").is_file()
    assert desktop_file.is_file()
    assert state.read_text(encoding="utf-8").strip() == "elvern-vlc-opener.desktop"


@pytest.mark.parametrize(
    "relative_path",
    [
        "Install-ElvernVlcOpener.sh",
        ".elvern/lib/platform-selectors.sh",
        ".elvern/uninstall/Uninstall-ElvernVlcOpener.sh",
        ".elvern/payloads/linux-x64/Elvern.VlcOpener",
    ],
)
def test_linux_installer_rejects_tampered_package_files(
    tmp_path: Path,
    relative_path: str,
) -> None:
    package_root, _state, env = _create_linux_package(tmp_path)
    with (package_root / relative_path).open("a", encoding="utf-8") as handle:
        handle.write("\n# tampered\n")

    result = _run_installer(package_root, env)

    assert result.returncode != 0
    assert any(
        marker in result.stderr.lower()
        for marker in ("integrity", "sha-256", "size check")
    )
    assert not (
        Path(env["HOME"]) / ".local" / "lib" / "elvern-vlc-opener"
    ).exists()


def test_linux_installer_rejects_unlisted_extra_file(tmp_path: Path) -> None:
    package_root, _state, env = _create_linux_package(tmp_path)
    (package_root / ".elvern" / "unexpected.txt").write_text(
        "not listed\n",
        encoding="utf-8",
    )

    result = _run_installer(package_root, env)

    assert result.returncode != 0
    assert "unexpected file" in result.stderr.lower()


def test_linux_installer_rolls_back_binary_desktop_and_protocol(
    tmp_path: Path,
) -> None:
    package_root, state, env = _create_linux_package(tmp_path)
    home = Path(env["HOME"])
    install_dir = home / ".local" / "lib" / "elvern-vlc-opener"
    desktop_file = (
        home
        / ".local"
        / "share"
        / "applications"
        / "elvern-vlc-opener.desktop"
    )
    install_dir.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True)
    (install_dir / "old-marker.txt").write_text("old install\n", encoding="utf-8")
    desktop_file.write_text("old desktop\n", encoding="utf-8")
    env["ELVERN_TEST_XDG_FAIL_NEW"] = "1"

    result = _run_installer(package_root, env)

    assert result.returncode != 0
    assert (install_dir / "old-marker.txt").read_text(encoding="utf-8") == "old install\n"
    assert desktop_file.read_text(encoding="utf-8") == "old desktop\n"
    assert state.read_text(encoding="utf-8").strip() == "old-handler.desktop"


def test_linux_installer_rolls_back_when_no_protocol_handler_existed(
    tmp_path: Path,
) -> None:
    package_root, state, env = _create_linux_package(tmp_path)
    state.write_text("", encoding="utf-8")
    env["ELVERN_TEST_XDG_FAIL_NEW"] = "1"

    result = _run_installer(package_root, env)

    assert result.returncode != 0
    assert not (
        Path(env["HOME"]) / ".local" / "lib" / "elvern-vlc-opener"
    ).exists()
    assert not (
        Path(env["HOME"])
        / ".local"
        / "share"
        / "applications"
        / "elvern-vlc-opener.desktop"
    ).exists()
    assert state.read_text(encoding="utf-8") == ""
