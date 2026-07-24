from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from clients.desktop_helper_package_contract import (
    PACKAGE_NAME_PREFIX,
    expected_package_filename,
)


ROOT = Path(__file__).resolve().parents[2]
HELPER_ROOT = ROOT / "clients" / "desktop-vlc-opener"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _create_publish_workspace(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    clients_root = tmp_path / "clients"
    workspace = clients_root / "desktop-vlc-opener"
    (workspace / "scripts").mkdir(parents=True)
    clients_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "clients" / "desktop_helper_package_contract.py",
        clients_root / "desktop_helper_package_contract.py",
    )
    shutil.copy2(
        HELPER_ROOT / "scripts" / "publish-bundles.sh",
        workspace / "scripts" / "publish-bundles.sh",
    )
    shutil.copy2(
        HELPER_ROOT / "scripts" / "normalize-origin.py",
        workspace / "scripts" / "normalize-origin.py",
    )
    shutil.copy2(
        HELPER_ROOT / "scripts" / "validate-package.py",
        workspace / "scripts" / "validate-package.py",
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
    home = tmp_path / "home"
    temp_dir = tmp_path / "tmp"
    home.mkdir()
    temp_dir.mkdir()
    env = {
        "HOME": str(home),
        "TMPDIR": str(temp_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "ELVERN_BACKEND_ORIGIN": "https://elvern.example.test/",
        "PATH": f"{fake_bin}:/usr/bin:/bin",
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


def _run_package_validator(
    workspace: Path,
    manifest_path: Path,
    *expected_targets: str,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(workspace / "scripts" / "validate-package.py"),
        "--manifest",
        str(manifest_path),
        "--artifacts-dir",
        str(manifest_path.parent),
    ]
    for target in expected_targets:
        command.extend(["--expected-package-target", target])
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _refresh_outer_package_integrity(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for package in manifest["packages"]:
        artifact = manifest_path.parent / package["filename"]
        package["size_bytes"] = artifact.stat().st_size
        package["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        filename = expected_package_filename(
            PACKAGE_NAME_PREFIX,
            manifest["helper_version"],
            package["package_target"],
            package["sha256"],
        )
        if artifact.name != filename:
            replacement = artifact.with_name(filename)
            os.replace(artifact, replacement)
        package["filename"] = filename
        package["relative_path"] = filename
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _append_zip_member(
    archive: Path,
    name: str,
    payload: bytes,
    *,
    mode: int = stat.S_IFREG | 0o644,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(info, payload)


def _rewrite_zip_member(
    archive: Path,
    member_name: str,
    *,
    payload: bytes | None = None,
    mode: int | None = None,
) -> None:
    replacement = archive.with_suffix(".rewrite.zip")
    with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(
        replacement,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as destination:
        for original in source.infolist():
            data = b"" if original.is_dir() else source.read(original)
            if original.filename == member_name:
                if payload is not None:
                    data = payload
                if mode is not None:
                    original.create_system = 3
                    original.external_attr = mode << 16
            destination.writestr(original, data)
    os.replace(replacement, archive)


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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unexpected", True, "missing or unknown fields"),
        ("target_framework", "net8.0", "target framework"),
    ],
)
def test_strict_package_validator_rejects_outer_contract_tampering(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    published = _run_publisher(workspace, env, "--windows")
    assert published.returncode == 0, published.stderr
    manifest_path = _staged_manifests(workspace)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    validated = _run_package_validator(workspace, manifest_path, "windows-x64")

    assert validated.returncode != 0
    assert message in validated.stderr


def test_strict_package_validator_rejects_non_list_runtime_ids(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    published = _run_publisher(workspace, env, "--windows")
    assert published.returncode == 0, published.stderr
    manifest_path = _staged_manifests(workspace)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packages"][0]["supported_runtime_ids"] = "win-x64"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    validated = _run_package_validator(workspace, manifest_path, "windows-x64")

    assert validated.returncode != 0
    assert "Release RID order is invalid" in validated.stderr
    assert "Traceback" not in validated.stderr


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
def test_strict_package_validator_rejects_filename_not_bound_to_content(
    tmp_path: Path,
    filename_mutator,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    published = _run_publisher(workspace, env, "--windows")
    assert published.returncode == 0, published.stderr
    manifest_path = _staged_manifests(workspace)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = manifest["packages"][0]
    old_archive = manifest_path.parent / package["filename"]
    renamed_filename = filename_mutator(package["filename"])
    os.replace(old_archive, old_archive.with_name(renamed_filename))
    package["filename"] = renamed_filename
    package["relative_path"] = renamed_filename
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    validated = _run_package_validator(
        workspace,
        manifest_path,
        "windows-x64",
    )

    assert validated.returncode != 0
    assert "filename does not match its content hash" in validated.stderr


@pytest.mark.parametrize("tamper_kind", ["traversal", "case_collision", "symlink"])
def test_strict_package_validator_rejects_unsafe_zip_members(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    published = _run_publisher(workspace, env, "--windows")
    assert published.returncode == 0, published.stderr
    manifest_path = _staged_manifests(workspace)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = manifest["packages"][0]
    archive = manifest_path.parent / package["filename"]
    package_root = package["package_root"]
    if tamper_kind == "traversal":
        _append_zip_member(archive, "../escape.txt", b"escape")
    elif tamper_kind == "case_collision":
        _append_zip_member(archive, f"{package_root}/README.TXT", b"collision")
    else:
        _append_zip_member(
            archive,
            f"{package_root}/.elvern/unsafe-link",
            b"README.txt",
            mode=stat.S_IFLNK | 0o777,
        )
    _refresh_outer_package_integrity(manifest_path)

    validated = _run_package_validator(workspace, manifest_path, "windows-x64")

    assert validated.returncode != 0
    expected = {
        "traversal": "unsafe path",
        "case_collision": "case-colliding path",
        "symlink": "unsupported symlink",
    }
    assert expected[tamper_kind] in validated.stderr


def test_strict_package_validator_rejects_directory_mode_tampering(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    published = _run_publisher(workspace, env, "--linux")
    assert published.returncode == 0, published.stderr
    manifest_path = _staged_manifests(workspace)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = manifest["packages"][0]
    archive = manifest_path.parent / package["filename"]
    _rewrite_zip_member(
        archive,
        f"{package['package_root']}/",
        mode=stat.S_IFDIR | 0o700,
    )
    _refresh_outer_package_integrity(manifest_path)

    validated = _run_package_validator(workspace, manifest_path, "linux-universal")

    assert validated.returncode != 0
    assert "Package directory mode is invalid" in validated.stderr


def test_strict_package_validator_rejects_duplicate_tree_manifest_paths(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    published = _run_publisher(workspace, env, "--windows")
    assert published.returncode == 0, published.stderr
    manifest_path = _staged_manifests(workspace)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = manifest["packages"][0]
    archive = manifest_path.parent / package["filename"]
    tree_name = f"{package['package_root']}/{package['installer_tree_manifest_path']}"
    with zipfile.ZipFile(archive, "r") as bundle:
        tree_payload = bundle.read(tree_name)
    first_row = tree_payload.decode("utf-8").splitlines()[1]
    tampered_tree = tree_payload + (first_row + "\n").encode("utf-8")
    _rewrite_zip_member(archive, tree_name, payload=tampered_tree)
    manifest["packages"][0]["installer_tree_manifest_sha256"] = hashlib.sha256(
        tampered_tree
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _refresh_outer_package_integrity(manifest_path)

    validated = _run_package_validator(workspace, manifest_path, "windows-x64")

    assert validated.returncode != 0
    assert "Tree manifest path is duplicated" in validated.stderr


def test_strict_package_validator_rejects_inner_json_schema_tampering(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    published = _run_publisher(workspace, env, "--windows")
    assert published.returncode == 0, published.stderr
    manifest_path = _staged_manifests(workspace)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = manifest["packages"][0]
    archive = manifest_path.parent / package["filename"]
    prefix = f"{package['package_root']}/"
    inner_name = prefix + ".elvern/manifest.json"
    tree_name = prefix + package["installer_tree_manifest_path"]
    with zipfile.ZipFile(archive, "r") as bundle:
        inner = json.loads(bundle.read(inner_name))
        tree_lines = bundle.read(tree_name).decode("utf-8").splitlines()
    inner["unexpected"] = True
    inner_payload = (json.dumps(inner, indent=2) + "\n").encode("utf-8")
    inner_digest = hashlib.sha256(inner_payload).hexdigest()
    for index, line in enumerate(tree_lines):
        fields = line.split("\t")
        if fields[0] == ".elvern/manifest.json":
            tree_lines[index] = (
                f"{fields[0]}\t{len(inner_payload)}\t{inner_digest}\t{fields[3]}"
            )
            break
    else:
        raise AssertionError("inner manifest missing from tree manifest")
    tree_payload = ("\n".join(tree_lines) + "\n").encode("utf-8")
    _rewrite_zip_member(archive, inner_name, payload=inner_payload)
    _rewrite_zip_member(archive, tree_name, payload=tree_payload)
    manifest["packages"][0]["installer_manifest_sha256"] = inner_digest
    manifest["packages"][0]["installer_tree_manifest_sha256"] = hashlib.sha256(
        tree_payload
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _refresh_outer_package_integrity(manifest_path)

    validated = _run_package_validator(workspace, manifest_path, "windows-x64")

    assert validated.returncode != 0
    assert "Inner manifest contains missing or unknown fields" in validated.stderr


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


def test_corrupt_active_manifest_is_rejected_before_any_artifact_copy(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    active_dir = workspace / "artifacts" / "packages"
    active_dir.mkdir(parents=True)
    active_manifest = active_dir / "release-manifest.json"
    active_manifest.write_bytes(b"{corrupt-json")

    result = _run_publisher(workspace, env, "--activate")

    assert result.returncode != 0
    assert (
        "Active desktop helper manifest is invalid; activation was not attempted."
        in result.stderr
    )
    assert active_manifest.read_bytes() == b"{corrupt-json"
    assert not list(active_dir.glob("*.zip"))
    assert not list(active_dir.glob("release-manifest.corrupt-*.json"))


def test_explicit_corrupt_manifest_recovery_preserves_old_authority_and_artifacts(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    active_dir = workspace / "artifacts" / "packages"
    active_dir.mkdir(parents=True)
    corrupt_payload = b"{corrupt-json"
    corrupt_digest = hashlib.sha256(corrupt_payload).hexdigest()
    active_manifest = active_dir / "release-manifest.json"
    active_manifest.write_bytes(corrupt_payload)
    old_artifact = active_dir / "preexisting-legacy-artifact.zip"
    old_artifact.write_bytes(b"must remain")

    result = _run_publisher(
        workspace,
        env,
        "--activate",
        "--replace-corrupt-active-manifest",
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING: replacing an invalid active" in result.stderr
    backup = (
        active_dir
        / f"release-manifest.corrupt-{corrupt_digest[:12]}.json"
    )
    assert backup.read_bytes() == corrupt_payload
    assert stat.S_IMODE(backup.stat().st_mode) == 0o444
    assert old_artifact.read_bytes() == b"must remain"
    replacement = json.loads(active_manifest.read_text(encoding="utf-8"))
    assert replacement["schema_version"] == "desktop-helper-release-manifest-v2"
    assert len(replacement["packages"]) == 3


def test_corrupt_manifest_recovery_flag_requires_activation(tmp_path: Path) -> None:
    workspace, env = _create_publish_workspace(tmp_path)

    result = _run_publisher(
        workspace,
        env,
        "--windows",
        "--replace-corrupt-active-manifest",
    )

    assert result.returncode != 0
    assert "only valid with --activate" in result.stderr
    assert not (workspace / "artifacts" / "staging").exists()


def test_active_manifest_symlink_is_rejected_without_copying_artifacts(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    active_dir = workspace / "artifacts" / "packages"
    active_dir.mkdir(parents=True)
    outside = tmp_path / "outside-manifest.json"
    outside.write_text('{"outside":true}\n', encoding="utf-8")
    active_manifest = active_dir / "release-manifest.json"
    active_manifest.symlink_to(outside)

    result = _run_publisher(workspace, env, "--activate")

    assert result.returncode != 0
    assert (
        "Active desktop helper manifest is invalid; activation was not attempted."
        in result.stderr
    )
    assert active_manifest.is_symlink()
    assert outside.read_text(encoding="utf-8") == '{"outside":true}\n'
    assert not list(active_dir.glob("*.zip"))


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
        "--replace-corrupt-active-manifest",
    )

    assert result.returncode != 0
    assert f"Injected activation failure at {failure_point}" in result.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'
    assert not list(active_dir.glob(".*.new.*"))
    assert not list(active_dir.glob("*.zip"))
    assert not (workspace / "artifacts" / ".activation.lock").exists()


def test_activation_cleanup_failure_preserves_old_manifest_and_reports_orphan(
    tmp_path: Path,
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
            "ELVERN_PUBLISH_TEST_FAIL_AT": "orphan_cleanup",
        },
        "--activate",
        "--replace-corrupt-active-manifest",
    )

    assert result.returncode != 0
    assert "Injected activation failure before orphan cleanup." in result.stderr
    assert "Could not remove transaction-created orphan artifact:" in result.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'
    assert len(list(active_dir.glob("*.zip"))) == 1


def test_failed_activation_preserves_preexisting_identical_artifact(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    staged = _run_publisher(workspace, env, "--windows")
    assert staged.returncode == 0, staged.stderr
    staged_manifest_path = _staged_manifests(workspace)[0]
    staged_manifest = json.loads(staged_manifest_path.read_text(encoding="utf-8"))
    package = staged_manifest["packages"][0]
    active_dir = workspace / "artifacts" / "packages"
    active_dir.mkdir(parents=True)
    preexisting = active_dir / package["filename"]
    shutil.copy2(staged_manifest_path.parent / package["filename"], preexisting)
    active_manifest = active_dir / "release-manifest.json"
    active_manifest.write_text('{"active":"old"}\n', encoding="utf-8")

    result = _run_publisher(
        workspace,
        {
            **env,
            "ELVERN_PUBLISH_TEST_MODE": "1",
            "ELVERN_PUBLISH_TEST_FAIL_AT": "artifact_copy",
        },
        "--activate",
        "--replace-corrupt-active-manifest",
    )

    assert result.returncode != 0
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'
    assert preexisting.is_file()


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
