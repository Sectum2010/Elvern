from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER_ROOT = ROOT / "clients" / "desktop-vlc-opener"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _create_publish_workspace(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    workspace = tmp_path / "desktop-vlc-opener"
    (workspace / "scripts").mkdir(parents=True)
    shutil.copy2(
        HELPER_ROOT / "scripts" / "publish-bundles.sh",
        workspace / "scripts" / "publish-bundles.sh",
    )
    shutil.copy2(
        HELPER_ROOT / "scripts" / "normalize-origin.py",
        workspace / "scripts" / "normalize-origin.py",
    )
    shutil.copy2(
        HELPER_ROOT / "Elvern.VlcOpener.csproj",
        workspace / "Elvern.VlcOpener.csproj",
    )
    shutil.copy2(
        HELPER_ROOT / "Directory.Build.props",
        workspace / "Directory.Build.props",
    )
    shutil.copytree(HELPER_ROOT / "packaging", workspace / "packaging")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "dotnet",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--list-sdks" ]]; then
  echo "10.0.100 [/test/dotnet/sdk]"
  exit 0
fi
if [[ "${1:-}" != "publish" ]]; then
  echo "unsupported fake dotnet command" >&2
  exit 90
fi
shift
RID=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime) RID="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[[ -n "$RID" && -n "$OUTPUT" ]]
if [[ "${ELVERN_TEST_FAIL_RID:-}" == "$RID" ]]; then
  echo "forced fake publish failure for $RID" >&2
  exit 91
fi
mkdir -p "$OUTPUT"
NAME="Elvern.VlcOpener"
[[ "$RID" == win-* ]] && NAME="Elvern.VlcOpener.exe"
printf '#!/usr/bin/env sh\\nprintf "%s\\\\n" "0.9.0"\\n' > "$OUTPUT/$NAME"
chmod 755 "$OUTPUT/$NAME"
""",
    )
    env = {
        **os.environ,
        "ELVERN_BACKEND_ORIGIN": "https://elvern.example.test/",
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    return workspace, env


def _run_publisher(
    workspace: Path,
    env: dict[str, str],
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(workspace / "scripts" / "publish-bundles.sh"), *args],
        cwd=workspace,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _staged_manifests(workspace: Path) -> list[Path]:
    return sorted(
        (workspace / "artifacts" / "staging").glob(
            "*/output/release-manifest.json"
        )
    )


def test_partial_build_stays_staged_and_does_not_replace_active_manifest(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    active_manifest = workspace / "artifacts" / "packages" / "release-manifest.json"
    active_manifest.parent.mkdir(parents=True)
    active_manifest.write_text('{"active":"old"}\n', encoding="utf-8")

    result = _run_publisher(workspace, env, "--platform", "macos")

    assert result.returncode == 0, result.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'
    staged = _staged_manifests(workspace)
    assert len(staged) == 1
    manifest = json.loads(staged[0].read_text(encoding="utf-8"))
    assert [row["package_target"] for row in manifest["packages"]] == [
        "macos-dual-arch"
    ]
    assert all(row["bound_origin_sha256"] for row in manifest["packages"])
    assert "-macos-dual-arch-" in manifest["packages"][0]["filename"]


def test_normal_activation_requires_all_platforms_and_preserves_old_active(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    active_manifest = workspace / "artifacts" / "packages" / "release-manifest.json"
    active_manifest.parent.mkdir(parents=True)
    active_manifest.write_text('{"active":"old"}\n', encoding="utf-8")

    result = _run_publisher(
        workspace,
        env,
        "--platform",
        "linux",
        "--activate",
    )

    assert result.returncode != 0
    assert "Activation requires Windows, macOS, and Linux" in result.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'


def test_full_verified_build_activates_three_package_manifest_atomically(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)

    result = _run_publisher(workspace, env, "--activate")

    assert result.returncode == 0, result.stderr
    active_dir = workspace / "artifacts" / "packages"
    manifest = json.loads(
        (active_dir / "release-manifest.json").read_text(encoding="utf-8")
    )
    assert {row["package_target"] for row in manifest["packages"]} == {
        "windows-x64",
        "macos-dual-arch",
        "linux-universal",
    }
    for package in manifest["packages"]:
        artifact = active_dir / package["filename"]
        assert artifact.is_file()
        assert artifact.stat().st_size == package["size_bytes"]
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o444
    assert stat.S_IMODE((active_dir / "release-manifest.json").stat().st_mode) == 0o444
    assert not list(active_dir.glob(".release-manifest.json.new.*"))


def test_publish_failure_and_invalid_origin_leave_active_manifest_unchanged(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    active_manifest = workspace / "artifacts" / "packages" / "release-manifest.json"
    active_manifest.parent.mkdir(parents=True)
    active_manifest.write_text('{"active":"old"}\n', encoding="utf-8")

    failed_env = {**env, "ELVERN_TEST_FAIL_RID": "linux-arm64"}
    publish_failure = _run_publisher(workspace, failed_env, "--activate")
    assert publish_failure.returncode != 0
    assert "forced fake publish failure" in publish_failure.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'

    invalid_origin = _run_publisher(
        workspace,
        {**env, "ELVERN_BACKEND_ORIGIN": "https://elvern.example.test/private"},
        "--activate",
    )
    assert invalid_origin.returncode != 0
    assert "exact absolute HTTP(S) origin" in invalid_origin.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'


@pytest.mark.parametrize("failure_point", ["artifact_copy", "manifest_rename"])
def test_injected_activation_failure_preserves_old_manifest_and_cleans_temp_files(
    tmp_path: Path,
    failure_point: str,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    active_dir = workspace / "artifacts" / "packages"
    active_dir.mkdir(parents=True)
    active_manifest = active_dir / "release-manifest.json"
    active_manifest.write_text('{"active":"old"}\n', encoding="utf-8")

    result = _run_publisher(
        workspace,
        {
            **env,
            "ELVERN_PUBLISH_TEST_MODE": "1",
            "ELVERN_PUBLISH_TEST_FAIL_AT": failure_point,
        },
        "--activate",
    )

    assert result.returncode != 0
    assert f"Injected activation failure at {failure_point}" in result.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'
    assert not list(active_dir.glob(".*.new.*"))
    assert not (workspace / "artifacts" / ".activation.lock").exists()


def test_activation_lock_is_fail_closed_and_reports_owner_without_changing_active(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    active_dir = workspace / "artifacts" / "packages"
    active_dir.mkdir(parents=True)
    active_manifest = active_dir / "release-manifest.json"
    active_manifest.write_text('{"active":"old"}\n', encoding="utf-8")
    lock_dir = workspace / "artifacts" / ".activation.lock"
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(
        "pid=123\nstarted_at=2026-07-23T00:00:00Z\nbuild_id=existing\n",
        encoding="utf-8",
    )

    result = _run_publisher(workspace, env, "--activate")

    assert result.returncode != 0
    assert "activation is already running" in result.stderr
    assert "pid=123" in result.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'
    assert lock_dir.is_dir()


def test_partial_activation_requires_explicit_dangerous_flag(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)

    blocked = _run_publisher(workspace, env, "--linux", "--activate")
    assert blocked.returncode != 0
    assert "Activation requires Windows, macOS, and Linux" in blocked.stderr

    allowed = _run_publisher(
        workspace,
        env,
        "--linux",
        "--activate",
        "--allow-partial-activate",
    )
    assert allowed.returncode == 0, allowed.stderr
    assert "WARNING: activating an incomplete" in allowed.stderr
