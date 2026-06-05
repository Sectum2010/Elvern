from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from backend.app.services import app_settings_service as service


def _native_picker_capability() -> dict[str, object]:
    return {
        "native_picker_supported": True,
        "picker_backend": "zenity",
        "gui_session_available": True,
        "display_available": True,
        "wayland_available": False,
        "dbus_session_available": True,
        "missing_dependency": None,
        "reason": None,
    }


def _install_native_picker_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    selected_path: Path,
    returncode: int = 0,
    stderr: str = "",
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def _which(name: str) -> str | None:
        return {"zenity": "/usr/bin/zenity"}.get(name)

    def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=argv,
            returncode=returncode,
            stdout=f"{selected_path}\n" if returncode == 0 else "",
            stderr=stderr,
        )

    monkeypatch.setattr(service.shutil, "which", _which)
    monkeypatch.setattr(service, "get_native_local_directory_picker_capability", _native_picker_capability)
    monkeypatch.setattr(
        "backend.app.services.desktop_playback_service.build_linux_gui_launch_environment",
        lambda: ({}, {}, {}),
    )
    monkeypatch.setattr(service.subprocess, "run", _run)
    return captured


@pytest.mark.parametrize(
    ("purpose", "expected_title"),
    [
        ("library_reference", "Select library reference directory"),
        ("poster_reference", "Select poster reference directory"),
    ],
)
def test_native_directory_picker_purpose_uses_server_defined_titles(
    initialized_settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    purpose: str,
    expected_title: str,
) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    captured = _install_native_picker_mocks(monkeypatch, selected_path=selected_dir)

    result = service.try_pick_local_directory(
        initialized_settings,
        path=str(tmp_path),
        purpose=purpose,
    )

    assert result == {
        "status": "selected",
        "selected_path": str(selected_dir.resolve()),
        "reason": None,
        "picker_backend": "zenity",
    }
    argv = captured["argv"]
    kwargs = captured["kwargs"]
    assert isinstance(argv, list)
    assert argv[0] == "/usr/bin/zenity"
    assert f"--title={expected_title}" in argv
    assert "shell" not in kwargs


def test_native_directory_picker_command_candidates_use_absolute_executables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable_paths = {
        "zenity": "/usr/bin/zenity",
        "qarma": "/opt/elvern/bin/qarma",
        "kdialog": "/usr/bin/kdialog",
    }
    monkeypatch.setattr(service.shutil, "which", lambda name: executable_paths.get(name))

    commands = service._native_directory_picker_command_candidates(tmp_path)

    assert [command.backend for command in commands] == ["zenity", "qarma", "kdialog"]
    assert [command.argv[0] for command in commands] == [
        str(Path(executable_paths["zenity"]).resolve()),
        str(Path(executable_paths["qarma"]).resolve()),
        str(Path(executable_paths["kdialog"]).resolve()),
    ]
    assert all(Path(command.argv[0]).is_absolute() for command in commands)


def test_native_directory_picker_stderr_is_not_returned_to_client_reason(
    initialized_settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    captured = _install_native_picker_mocks(
        monkeypatch,
        selected_path=selected_dir,
        returncode=2,
        stderr="dbus-session=/run/user/1000/bus secret-token",
    )

    result = service.try_pick_local_directory(
        initialized_settings,
        path=str(tmp_path),
        purpose="generic",
    )

    assert result == {
        "status": "unavailable",
        "selected_path": None,
        "reason": service.DIRECTORY_PICKER_FAILURE_MESSAGE,
        "picker_backend": "zenity",
    }
    assert "secret-token" not in str(result["reason"])
    assert "dbus-session" not in str(result["reason"])
    assert captured["argv"][0] == "/usr/bin/zenity"
