from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
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
SYSTEM_COMMANDS = (
    "awk",
    "cat",
    "chmod",
    "cmp",
    "cp",
    "date",
    "dirname",
    "find",
    "grep",
    "mkdir",
    "mktemp",
    "mv",
    "rm",
    "rmdir",
    "sed",
    "sha256sum",
    "sort",
    "stat",
    "tr",
    "uname",
    "wc",
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


def _isolated_installer_env(
    tmp_path: Path,
    *,
    home: Path,
    fake_bin: Path,
) -> dict[str, str]:
    config_home = home / ".config"
    data_home = home / ".local" / "share"
    tmp_dir = tmp_path / "tmp"
    system_bin = tmp_path / "system-bin"
    config_home.mkdir(parents=True, exist_ok=True)
    data_home.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    system_bin.mkdir(parents=True, exist_ok=True)
    for command in SYSTEM_COMMANDS:
        source = shutil.which(command)
        assert source is not None, f"required test command is unavailable: {command}"
        destination = system_bin / command
        if not destination.exists():
            destination.symlink_to(Path(source).resolve())
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_DATA_HOME": str(data_home),
        "PATH": f"{fake_bin}:{system_bin}",
        "TMPDIR": str(tmp_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "ELVERN_TEST_XDG_STATE": str(config_home / "mimeapps.list"),
    }
    assert Path(env["ELVERN_TEST_XDG_STATE"]) == (
        Path(env["XDG_CONFIG_HOME"]) / "mimeapps.list"
    )
    return env


def _read_default_handler(state: Path) -> str:
    section = ""
    for line in state.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if (
            section == "[Default Applications]"
            and stripped.startswith("x-scheme-handler/elvern-vlc=")
        ):
            return stripped.split("=", 1)[1].split(";", 1)[0]
    return ""


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
        "#!/bin/sh\n"
        "set -eu\n"
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
            f"meta\tbound_origin_sha256\t{'a' * 64}",
            f"payload\tlinux-x64\tpayloads/linux-x64/Elvern.VlcOpener\t{_sha256(payload)}\t{payload.stat().st_size}\tElvern.VlcOpener",
        ]) + "\n",
        encoding="utf-8",
    )
    _write_tree_manifest(package_root)

    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    state = home / ".config" / "mimeapps.list"
    home.mkdir()
    state.parent.mkdir()
    fake_bin.mkdir()
    state.write_text(
        "[Default Applications]\n"
        "x-scheme-handler/elvern-vlc=old-handler.desktop\n"
        "image/png=keep-viewer.desktop\n",
        encoding="utf-8",
    )
    _write_executable(
        fake_bin / "xdg-mime",
        f"""#!{sys.executable}
import os
import pathlib
import sys

state = pathlib.Path(os.environ["ELVERN_TEST_XDG_STATE"])
mime = "x-scheme-handler/elvern-vlc"
rollback_query_marker = state.with_name(".elvern-test-rollback-query")


def read_default() -> str:
    if not state.exists():
        return ""
    section = ""
    for line in state.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        elif section == "[Default Applications]" and stripped.startswith(mime + "="):
            return stripped.split("=", 1)[1].split(";", 1)[0]
    return ""


def write_default(desktop_file: str) -> None:
    lines = state.read_text(encoding="utf-8").splitlines(keepends=True) if state.exists() else []
    output = []
    section = ""
    replaced = False
    inserted = False
    saw_default_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section == "[Default Applications]" and not replaced:
                output.append(f"{{mime}}={{desktop_file}}\\n")
                inserted = True
            section = stripped
            saw_default_section = saw_default_section or section == "[Default Applications]"
            output.append(line)
            continue
        if section == "[Default Applications]" and stripped.startswith(mime + "="):
            output.append(f"{{mime}}={{desktop_file}}\\n")
            replaced = True
        else:
            output.append(line)
    if not replaced and not inserted:
        if not saw_default_section:
            if output and not output[-1].endswith("\\n"):
                output[-1] += "\\n"
            output.extend(["[Default Applications]\\n", f"{{mime}}={{desktop_file}}\\n"])
        else:
            output.append(f"{{mime}}={{desktop_file}}\\n")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("".join(output), encoding="utf-8")


args = sys.argv[1:]
if args == ["query", "default", mime]:
    if os.environ.get("ELVERN_TEST_XDG_QUERY_ERROR") == "1":
        raise SystemExit(42)
    value = read_default()
    if (
        os.environ.get("ELVERN_TEST_XDG_ROLLBACK_MISMATCH") == "1"
        and rollback_query_marker.exists()
    ):
        value = "elvern-vlc-opener.desktop"
    if value:
        print(value)
    raise SystemExit(0)
if len(args) == 3 and args[0] == "default" and args[2] == mime:
    write_default(args[1])
    if (
        os.environ.get("ELVERN_TEST_XDG_ROLLBACK_MISMATCH") == "1"
        and args[1] != "elvern-vlc-opener.desktop"
    ):
        rollback_query_marker.touch()
    if (
        os.environ.get("ELVERN_TEST_XDG_FAIL_NEW") == "1"
        and args[1] == "elvern-vlc-opener.desktop"
    ):
        raise SystemExit(41)
    raise SystemExit(0)
raise SystemExit(2)
""",
    )
    env = _isolated_installer_env(
        tmp_path,
        home=home,
        fake_bin=fake_bin,
    )
    return package_root, state, env


def _run_installer(
    package_root: Path,
    env: dict[str, str],
    *,
    fail_at: str | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = dict(env)
    if fail_at:
        run_env.update({
            "ELVERN_INSTALL_TEST_MODE": "1",
            "ELVERN_INSTALL_TEST_FAIL_AT": fail_at,
        })
    return subprocess.run(
        [
            "/bin/sh",
            str(package_root / "Install-ElvernVlcOpener.sh"),
            "--runtime",
            "linux-x64",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=run_env,
    )


def test_linux_installer_is_user_scoped_and_idempotent(tmp_path: Path) -> None:
    package_root, state, env = _create_linux_package(tmp_path)
    assert shutil.which("bash", path=env["PATH"]) is None

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
    assert _read_default_handler(state) == "elvern-vlc-opener.desktop"
    assert stat.S_IMODE(install_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((install_dir / "Elvern.VlcOpener").stat().st_mode) == 0o755
    assert stat.S_IMODE((install_dir / "Uninstall-ElvernVlcOpener.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE(desktop_file.stat().st_mode) == 0o644


def test_linux_installer_environment_ignores_polluted_parent_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    outside_config = tmp_path / "outside-config"
    outside_data = tmp_path / "outside-data"
    outside_config.mkdir()
    outside_data.mkdir()
    outside_mime = outside_config / "mimeapps.list"
    outside_marker = outside_data / "must-not-change"
    outside_mime.write_bytes(b"outside mime bytes\n")
    outside_marker.write_bytes(b"outside data bytes\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(outside_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(outside_data))
    monkeypatch.setenv("ELVERN_INSTALL_TEST_FAIL_AT", "unexpected")
    monkeypatch.setenv("ELVERN_INSTALL_TEST_FAIL_ROLLBACK", "1")
    monkeypatch.setenv("ELVERN_TEST_XDG_FAIL_NEW", "1")
    monkeypatch.setenv("BASH_ENV", str(tmp_path / "outside-bash-env"))
    monkeypatch.setenv("ENV", str(tmp_path / "outside-env"))
    monkeypatch.setenv("CDPATH", str(tmp_path / "outside-cdpath"))

    package_root, _state, env = _create_linux_package(tmp_path / "isolated")

    assert set(env) == {
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "ELVERN_TEST_XDG_STATE",
    }
    assert Path(env["ELVERN_TEST_XDG_STATE"]).is_relative_to(tmp_path)
    result = _run_installer(package_root, env)

    assert result.returncode == 0, result.stderr
    assert outside_mime.read_bytes() == b"outside mime bytes\n"
    assert outside_marker.read_bytes() == b"outside data bytes\n"
    assert not list(outside_data.rglob("elvern-vlc-opener.desktop"))


def test_linux_installer_uses_custom_xdg_data_and_config_roots_for_commit_and_rollback(
    tmp_path: Path,
) -> None:
    package_root, _default_state, env = _create_linux_package(tmp_path)
    custom_config = tmp_path / "xdg-config"
    custom_data = tmp_path / "xdg-data"
    custom_config.mkdir()
    custom_state = custom_config / "mimeapps.list"
    custom_state.write_bytes(
        b"[Default Applications]\n"
        b"x-scheme-handler/elvern-vlc=old-handler.desktop\n"
        b"image/png=unrelated-config.desktop\n"
    )
    custom_state.chmod(0o640)
    env.update({
        "XDG_CONFIG_HOME": str(custom_config),
        "XDG_DATA_HOME": str(custom_data),
        "ELVERN_TEST_XDG_STATE": str(custom_state),
    })

    installed = _run_installer(package_root, env)
    assert installed.returncode == 0, installed.stderr
    desktop_file = custom_data / "applications" / "elvern-vlc-opener.desktop"
    data_mime = custom_data / "applications" / "mimeapps.list"
    assert desktop_file.is_file()
    assert _read_default_handler(custom_state) == "elvern-vlc-opener.desktop"
    assert not (
        Path(env["HOME"]) / ".local" / "share" / "applications" / "elvern-vlc-opener.desktop"
    ).exists()

    desktop_before = desktop_file.read_bytes()
    config_before = custom_state.read_bytes()
    data_before = data_mime.read_bytes() if data_mime.exists() else None
    failed = _run_installer(package_root, env, fail_at="final_binary_validation")
    assert failed.returncode != 0
    assert desktop_file.read_bytes() == desktop_before
    assert custom_state.read_bytes() == config_before
    assert (data_mime.read_bytes() if data_mime.exists() else None) == data_before
    assert stat.S_IMODE(desktop_file.stat().st_mode) == 0o644


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
    assert _read_default_handler(state) == "old-handler.desktop"


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


def test_linux_installer_rolls_back_when_mimeapps_did_not_exist(
    tmp_path: Path,
) -> None:
    package_root, state, env = _create_linux_package(tmp_path)
    state.unlink()
    env["ELVERN_TEST_XDG_FAIL_NEW"] = "1"

    result = _run_installer(package_root, env)

    assert result.returncode != 0
    assert not state.exists()
    assert not (
        Path(env["XDG_DATA_HOME"])
        / "applications"
        / "mimeapps.list"
    ).exists()


def test_linux_installer_preserves_unrelated_mime_associations_byte_for_byte(
    tmp_path: Path,
) -> None:
    package_root, state, env = _create_linux_package(tmp_path)
    original = (
        b"[Default Applications]\n"
        b"x-scheme-handler/elvern-vlc=third-party.desktop\n"
        b"image/png=keep-viewer.desktop;\n"
        b"\n[Added Associations]\n"
        b"text/plain=keep-editor.desktop;\n"
    )
    state.write_bytes(original)
    env["ELVERN_TEST_XDG_FAIL_NEW"] = "1"

    result = _run_installer(package_root, env)

    assert result.returncode != 0
    assert state.read_bytes() == original
    assert _read_default_handler(state) == "third-party.desktop"


def test_linux_installer_query_error_fails_closed_and_restores_exact_mime_state(
    tmp_path: Path,
) -> None:
    package_root, state, env = _create_linux_package(tmp_path)
    original = state.read_bytes()
    env["ELVERN_TEST_XDG_QUERY_ERROR"] = "1"

    result = _run_installer(package_root, env)

    assert result.returncode != 0
    assert state.read_bytes() == original


def test_linux_installer_reports_rollback_handler_mismatch_and_preserves_materials(
    tmp_path: Path,
) -> None:
    package_root, state, env = _create_linux_package(tmp_path)
    home = Path(env["HOME"])
    install_dir = home / ".local" / "lib" / "elvern-vlc-opener"
    desktop_file = (
        Path(env["XDG_DATA_HOME"])
        / "applications"
        / "elvern-vlc-opener.desktop"
    )
    install_dir.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True)
    (install_dir / "old-marker.txt").write_bytes(b"old-install\n")
    desktop_file.write_bytes(b"old-desktop\n")
    original_mime = state.read_bytes()
    env["ELVERN_TEST_XDG_ROLLBACK_MISMATCH"] = "1"

    result = _run_installer(
        package_root,
        env,
        fail_at="final_binary_validation",
    )

    assert result.returncode != 0
    assert "rollback could not be verified" in result.stderr
    assert state.read_bytes() == original_mime
    backups = list(install_dir.parent.glob(".elvern-vlc-opener-backup.*"))
    transactions = list(install_dir.parent.glob(".elvern-vlc-opener-transaction.*"))
    assert len(backups) == 1
    assert len(transactions) == 1
    assert (backups[0] / "old-marker.txt").read_bytes() == b"old-install\n"
    assert (transactions[0] / "mimeapps-0").is_file()
    assert (transactions[0] / "elvern-vlc-opener.desktop").is_file()


@pytest.mark.parametrize(
    "failure_point",
    [
        "staging_created",
        "first_backup_move",
        "new_placement",
        "registration",
        "registration_validation",
        "final_binary_validation",
    ],
)
def test_linux_installer_transaction_failures_preserve_existing_install_and_registration(
    tmp_path: Path,
    failure_point: str,
) -> None:
    package_root, state, env = _create_linux_package(tmp_path)
    home = Path(env["HOME"])
    install_dir = home / ".local" / "lib" / "elvern-vlc-opener"
    desktop_file = home / ".local" / "share" / "applications" / "elvern-vlc-opener.desktop"
    data_mime = home / ".local" / "share" / "applications" / "mimeapps.list"
    install_dir.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True, exist_ok=True)
    (install_dir / "old-marker.txt").write_bytes(b"old-install-bytes\n")
    desktop_file.write_bytes(b"old-desktop-bytes\n")
    desktop_file.chmod(0o600)
    state.write_bytes(
        b"[Default Applications]\n"
        b"x-scheme-handler/elvern-vlc=old-handler.desktop\n"
        b"image/png=unrelated-config.desktop\n"
    )
    state.chmod(0o640)
    data_mime.write_bytes(b"unrelated=data\n")
    data_mime.chmod(0o600)
    expected = {
        "install": (install_dir / "old-marker.txt").read_bytes(),
        "desktop": desktop_file.read_bytes(),
        "config": state.read_bytes(),
        "data": data_mime.read_bytes(),
    }

    result = _run_installer(package_root, env, fail_at=failure_point)

    assert result.returncode != 0
    assert (install_dir / "old-marker.txt").read_bytes() == expected["install"]
    assert desktop_file.read_bytes() == expected["desktop"]
    assert state.read_bytes() == expected["config"]
    assert data_mime.read_bytes() == expected["data"]
    assert stat.S_IMODE(desktop_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o640
    assert stat.S_IMODE(data_mime.stat().st_mode) == 0o600
    assert not list(install_dir.parent.glob(".elvern-vlc-opener-stage.*"))


def test_linux_installer_reports_unverified_rollback_and_preserves_backup(
    tmp_path: Path,
) -> None:
    package_root, _state, env = _create_linux_package(tmp_path)
    install_dir = Path(env["HOME"]) / ".local" / "lib" / "elvern-vlc-opener"
    desktop_file = (
        Path(env["HOME"])
        / ".local"
        / "share"
        / "applications"
        / "elvern-vlc-opener.desktop"
    )
    install_dir.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True)
    (install_dir / "old-marker.txt").write_text("old\n", encoding="utf-8")
    desktop_file.write_text("old desktop\n", encoding="utf-8")

    env["ELVERN_INSTALL_TEST_FAIL_ROLLBACK"] = "1"
    result = _run_installer(package_root, env, fail_at="final_binary_validation")

    assert result.returncode != 0
    assert "rollback could not be verified" in result.stderr
    backups = list(install_dir.parent.glob(".elvern-vlc-opener-backup.*"))
    assert len(backups) == 1
    assert (backups[0] / "old-marker.txt").read_text(encoding="utf-8") == "old\n"
    transactions = list(install_dir.parent.glob(".elvern-vlc-opener-transaction.*"))
    assert len(transactions) == 1
    assert (transactions[0] / "mimeapps-0").is_file()
    assert (transactions[0] / "elvern-vlc-opener.desktop").is_file()
    assert str(backups[0]) in result.stderr
    assert str(transactions[0]) in result.stderr


def test_linux_installer_source_is_posix_and_uses_real_xdg_mime_contract() -> None:
    installer = LINUX_INSTALLER.read_text(encoding="utf-8")
    uninstaller = LINUX_UNINSTALLER.read_text(encoding="utf-8")
    selector = PLATFORM_SELECTORS.read_text(encoding="utf-8")
    combined = "\n".join((installer, uninstaller, selector))

    assert installer.startswith("#!/bin/sh\n")
    assert uninstaller.startswith("#!/bin/sh\n")
    assert selector.startswith("#!/bin/sh\n")
    assert "xdg-mime uninstall" not in installer
    assert 'xdg-mime query default x-scheme-handler/elvern-vlc' in installer
    assert 'xdg-mime default elvern-vlc-opener.desktop x-scheme-handler/elvern-vlc' in installer
    for bashism in (
        "[[",
        "declare -A",
        "mapfile",
        "compgen",
        "<(",
        "${value,,}",
        "source ",
        "#!/usr/bin/env bash",
    ):
        assert bashism not in combined
