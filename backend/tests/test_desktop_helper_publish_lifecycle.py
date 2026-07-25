from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from elvern_shared.desktop_helper_package_contract import (
    PACKAGE_NAME_PREFIX,
    authority_mutation_lock_basename,
    expected_package_filename,
)


ROOT = Path(__file__).resolve().parents[2]
HELPER_ROOT = ROOT / "clients" / "desktop-vlc-opener"
RUNTIME_RELEASE_TOOL = ROOT / "scripts" / "desktop-helper-runtime-releases.py"


def _load_runtime_release_tool_module():
    spec = importlib.util.spec_from_file_location(
        "elvern_desktop_helper_runtime_releases_test",
        RUNTIME_RELEASE_TOOL,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _create_publish_workspace(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    clients_root = tmp_path / "clients"
    workspace = clients_root / "desktop-vlc-opener"
    (workspace / "scripts").mkdir(parents=True)
    clients_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "elvern_shared", tmp_path / "elvern_shared")
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
        "ELVERN_HELPER_RELEASES_DIR": str(
            workspace / "artifacts" / "packages"
        ),
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


def _run_runtime_tool(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNTIME_RELEASE_TOOL), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
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


def test_runtime_authority_tool_inspect_migrate_and_idempotency(tmp_path: Path) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    built = _run_publisher(workspace, env)
    assert built.returncode == 0, built.stderr
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    origin = manifest["bound_origin_sha256"]
    runtime = tmp_path / "runtime"

    absent = _run_runtime_tool("inspect", "--runtime-dir", str(runtime))
    assert absent.returncode == 2
    assert json.loads(absent.stdout)["manifest_state"] == "absent"

    dry_run = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(runtime),
        "--expected-origin-sha256", origin,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert not runtime.exists()

    applied = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(runtime),
        "--expected-origin-sha256", origin,
        "--apply",
    )
    assert applied.returncode == 0, applied.stderr
    assert (source / "release-manifest.json").is_file()
    assert stat.S_IMODE((runtime / "release-manifest.json").stat().st_mode) == 0o444
    for package in manifest["packages"]:
        assert stat.S_IMODE((runtime / package["filename"]).stat().st_mode) == 0o444

    inspected = _run_runtime_tool(
        "inspect",
        "--runtime-dir", str(runtime),
        "--expected-origin-sha256", origin,
    )
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["manifest_state"] == "valid"

    repeated = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(runtime),
        "--expected-origin-sha256", origin,
        "--apply",
    )
    assert repeated.returncode == 0, repeated.stderr
    assert "already identical" in repeated.stdout


def test_runtime_authority_tool_rejects_conflict_and_cleans_failed_copy(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    built = _run_publisher(workspace, env)
    assert built.returncode == 0, built.stderr
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    origin = manifest["bound_origin_sha256"]
    conflict = tmp_path / "conflict"
    conflict.mkdir()
    (conflict / "foreign.txt").write_text("foreign")

    rejected = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(conflict),
        "--expected-origin-sha256", origin,
        "--apply",
    )
    assert rejected.returncode != 0
    assert (conflict / "foreign.txt").read_text() == "foreign"

    failed = tmp_path / "failed"
    failure_env = dict(os.environ)
    failure_env["ELVERN_RUNTIME_MIGRATION_TEST_FAIL_AT"] = "0"
    interrupted = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(failed),
        "--expected-origin-sha256", origin,
        "--apply",
        env=failure_env,
    )
    assert interrupted.returncode != 0
    assert list(failed.iterdir()) == []


def test_runtime_authority_tool_reports_invalid_and_rejects_origin_or_symlink(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "release-manifest.json").write_text("{", encoding="utf-8")
    inspected = _run_runtime_tool("inspect", "--runtime-dir", str(invalid))
    assert inspected.returncode == 3
    assert json.loads(inspected.stdout)["manifest_state"] == "invalid"

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "release-manifest.json").symlink_to(broken / "missing.json")
    inspected_broken = _run_runtime_tool("inspect", "--runtime-dir", str(broken))
    assert inspected_broken.returncode == 3
    assert json.loads(inspected_broken.stdout)["manifest_state"] == "invalid"

    workspace, env = _create_publish_workspace(tmp_path)
    built = _run_publisher(workspace, env)
    assert built.returncode == 0, built.stderr
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    origin = manifest["bound_origin_sha256"]

    mismatch = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(tmp_path / "mismatch"),
        "--expected-origin-sha256", "0" * 64,
    )
    assert mismatch.returncode != 0
    assert not (tmp_path / "mismatch").exists()

    linked_source = tmp_path / "linked-source"
    linked_source.symlink_to(source, target_is_directory=True)
    rejected_link = _run_runtime_tool(
        "migrate",
        "--source-dir", str(linked_source),
        "--runtime-dir", str(tmp_path / "linked-destination"),
        "--expected-origin-sha256", origin,
    )
    assert rejected_link.returncode != 0
    assert not (tmp_path / "linked-destination").exists()


def test_runtime_inspect_origin_tristate_and_incompatible_exit(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    assert _run_publisher(workspace, env).returncode == 0
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    origin = manifest["bound_origin_sha256"]

    unchecked = _run_runtime_tool("inspect", "--runtime-dir", str(source))
    unchecked_payload = json.loads(unchecked.stdout)
    assert unchecked.returncode != 0  # Staging files are intentionally writable.
    assert unchecked_payload["origin_check"] == "not_checked"
    assert unchecked_payload["origin_compatible"] is None

    compatible = _run_runtime_tool(
        "inspect",
        "--runtime-dir", str(source),
        "--expected-origin-sha256", origin,
    )
    compatible_payload = json.loads(compatible.stdout)
    assert compatible_payload["origin_check"] == "compatible"
    assert compatible_payload["origin_compatible"] is True

    incompatible = _run_runtime_tool(
        "inspect",
        "--runtime-dir", str(source),
        "--expected-origin-sha256", "0" * 64,
    )
    incompatible_payload = json.loads(incompatible.stdout)
    assert incompatible.returncode == 5
    assert incompatible_payload["manifest_state"] == "invalid"
    assert incompatible_payload["origin_check"] == "incompatible"
    assert incompatible_payload["origin_compatible"] is False


def test_runtime_inspect_absent_and_invalid_origin_states_are_not_incompatible(
    tmp_path: Path,
) -> None:
    expected_origin = "0" * 64
    absent = _run_runtime_tool(
        "inspect",
        "--runtime-dir", str(tmp_path / "absent"),
        "--expected-origin-sha256", expected_origin,
    )
    absent_payload = json.loads(absent.stdout)
    assert absent.returncode == 2
    assert absent_payload["manifest_state"] == "absent"
    assert absent_payload["origin_check"] == "not_checked"
    assert absent_payload["origin_compatible"] is None

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "release-manifest.json").write_text("{", encoding="utf-8")
    invalid = _run_runtime_tool(
        "inspect",
        "--runtime-dir", str(malformed),
        "--expected-origin-sha256", expected_origin,
    )
    invalid_payload = json.loads(invalid.stdout)
    assert invalid.returncode == 3
    assert invalid_payload["manifest_state"] == "invalid"
    assert invalid_payload["origin_check"] == "unknown"
    assert invalid_payload["origin_compatible"] is None

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "release-manifest.json").symlink_to(tmp_path / "missing")
    unsafe = _run_runtime_tool(
        "inspect",
        "--runtime-dir", str(linked),
        "--expected-origin-sha256", expected_origin,
    )
    unsafe_payload = json.loads(unsafe.stdout)
    assert unsafe.returncode == 3
    assert unsafe_payload["origin_check"] == "unknown"
    assert unsafe_payload["origin_compatible"] is None


def test_runtime_mode_inspection_is_metadata_only_for_large_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_runtime_release_tool_module()
    package = tmp_path / "large-package.zip"
    with package.open("wb") as handle:
        handle.truncate(124 * 1024 * 1024)
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    package.chmod(0o444)
    manifest.chmod(0o444)
    before_handles = len(list(Path("/proc/self/fd").iterdir()))

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("mode inspection must not read file content")

    monkeypatch.setattr(module.os, "read", unexpected_read)
    mutable = module._mutable_authority_files(
        tmp_path,
        {"packages": [{"filename": package.name}]},
    )

    assert mutable == []
    assert package.stat().st_size == 124 * 1024 * 1024
    assert len(list(Path("/proc/self/fd").iterdir())) == before_handles


def test_runtime_authority_mutable_modes_are_reported_and_repaired(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    assert _run_publisher(workspace, env).returncode == 0
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    origin = manifest["bound_origin_sha256"]
    source_modes = {
        path.name: stat.S_IMODE(path.stat().st_mode)
        for path in source.iterdir()
        if path.is_file()
    }
    runtime = tmp_path / "runtime"
    applied = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(runtime),
        "--expected-origin-sha256", origin,
        "--apply",
    )
    assert applied.returncode == 0, applied.stderr
    mutable_paths = [
        runtime / "release-manifest.json",
        runtime / manifest["packages"][0]["filename"],
    ]
    mutable_paths[0].chmod(0o644)
    mutable_paths[1].chmod(0o666)

    inspected = _run_runtime_tool(
        "inspect",
        "--runtime-dir", str(runtime),
        "--expected-origin-sha256", origin,
    )
    payload = json.loads(inspected.stdout)
    assert inspected.returncode != 0
    assert payload["manifest_state"] == "valid_but_mutable"
    assert payload["mutable_file_count"] == 2

    dry_run = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(runtime),
        "--expected-origin-sha256", origin,
    )
    assert dry_run.returncode == 0
    assert "would repair immutable mode on 2" in dry_run.stdout
    assert [stat.S_IMODE(path.stat().st_mode) for path in mutable_paths] == [0o644, 0o666]

    repaired = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(runtime),
        "--expected-origin-sha256", origin,
        "--apply",
    )
    assert repaired.returncode == 0, repaired.stderr
    assert "Repaired immutable mode on 2" in repaired.stdout
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o444 for path in mutable_paths)
    assert source_modes == {
        path.name: stat.S_IMODE(path.stat().st_mode)
        for path in source.iterdir()
        if path.is_file()
    }


def test_partial_runtime_migration_restores_preexisting_modes_on_copy_failure(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    assert _run_publisher(workspace, env).returncode == 0
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    destination = tmp_path / "partial-runtime"
    destination.mkdir()
    first_package = manifest["packages"][0]["filename"]
    existing = destination / first_package
    shutil.copy2(source / first_package, existing)
    existing.chmod(0o666)
    failure_env = {
        **os.environ,
        "ELVERN_RUNTIME_MIGRATION_TEST_FAIL_AT": "1",
    }

    failed = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(destination),
        "--expected-origin-sha256", manifest["bound_origin_sha256"],
        "--apply",
        env=failure_env,
    )

    assert failed.returncode != 0
    assert stat.S_IMODE(existing.stat().st_mode) == 0o666
    assert existing.read_bytes() == (source / first_package).read_bytes()
    assert sorted(path.name for path in destination.iterdir()) == [first_package]


def test_partial_runtime_migration_restores_multiple_modes_when_manifest_copy_fails(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    assert _run_publisher(workspace, env).returncode == 0
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    destination = tmp_path / "partial-runtime"
    destination.mkdir()
    original_modes: dict[str, int] = {}
    for index, package in enumerate(manifest["packages"]):
        filename = package["filename"]
        target = destination / filename
        shutil.copy2(source / filename, target)
        mode = 0o666 if index == 0 else 0o644
        target.chmod(mode)
        original_modes[filename] = mode
    failure_env = {
        **os.environ,
        "ELVERN_RUNTIME_MIGRATION_TEST_FAIL_AT": str(len(manifest["packages"])),
    }

    failed = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(destination),
        "--expected-origin-sha256", manifest["bound_origin_sha256"],
        "--apply",
        env=failure_env,
    )

    assert failed.returncode != 0
    assert not (destination / "release-manifest.json").exists()
    assert {
        path.name: stat.S_IMODE(path.stat().st_mode)
        for path in destination.iterdir()
    } == original_modes


def test_runtime_mode_rollback_does_not_chmod_replaced_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    assert _run_publisher(workspace, env).returncode == 0
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    module = _load_runtime_release_tool_module()
    destination = tmp_path / "partial-runtime"
    destination.mkdir()
    first_package = manifest["packages"][0]["filename"]
    repaired = destination / first_package
    shutil.copy2(source / first_package, repaired)
    repaired.chmod(0o666)
    replacement = tmp_path / "replacement.zip"
    replacement.write_bytes(b"replacement owned elsewhere")
    replacement.chmod(0o600)

    def replace_then_fail(*_args, **_kwargs):
        os.replace(replacement, repaired)
        raise module.AuthorityError("Injected migration failure.")

    monkeypatch.setattr(module, "_copy_verified_file", replace_then_fail)
    payload = module._validate(
        source,
        expected_origin=manifest["bound_origin_sha256"],
    )
    package_names = module._package_names(payload)
    source_files = [source / name for name in package_names]
    all_source_files = [*source_files, source / "release-manifest.json"]
    lock = module._AuthorityMutationLock(destination)
    lock.acquire()
    try:
        with pytest.raises(module.AuthorityError, match="fingerprint changed"):
            module._migrate_authority_locked(
                source,
                destination,
                manifest["bound_origin_sha256"],
                payload,
                all_source_files,
                source_files,
                lock,
            )
        assert repaired.read_bytes() == b"replacement owned elsewhere"
        assert stat.S_IMODE(repaired.stat().st_mode) == 0o600
    finally:
        assert lock.release()


def test_runtime_path_requires_existing_safe_parent(tmp_path: Path) -> None:
    missing_leaf = tmp_path / "runtime"
    absent = _run_runtime_tool("inspect", "--runtime-dir", str(missing_leaf))
    assert absent.returncode == 2
    assert json.loads(absent.stdout)["manifest_state"] == "absent"

    missing_parent = tmp_path / "missing" / "runtime"
    invalid = _run_runtime_tool("inspect", "--runtime-dir", str(missing_parent))
    assert invalid.returncode != 0
    assert "parent does not exist" in invalid.stderr

    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    rejected = _run_runtime_tool(
        "inspect",
        "--runtime-dir", str(linked_parent / "runtime"),
    )
    assert rejected.returncode != 0
    assert "symlink" in rejected.stderr


def test_runtime_migration_rejects_broken_destination_artifact_symlink(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    assert _run_publisher(workspace, env).returncode == 0
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    destination = tmp_path / "runtime"
    destination.mkdir()
    package_name = manifest["packages"][0]["filename"]
    broken_link = destination / package_name
    broken_link.symlink_to(destination / "missing-package.zip")

    rejected = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(destination),
        "--expected-origin-sha256", manifest["bound_origin_sha256"],
        "--apply",
    )

    assert rejected.returncode != 0
    assert broken_link.is_symlink()
    assert not (destination / "missing-package.zip").exists()


def test_runtime_migration_rejects_incomplete_active_manifest(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    assert _run_publisher(workspace, env).returncode == 0
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    destination = tmp_path / "runtime"
    destination.mkdir()
    shutil.copy2(
        source / "release-manifest.json",
        destination / "release-manifest.json",
    )

    rejected = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(destination),
        "--expected-origin-sha256", manifest["bound_origin_sha256"],
        "--apply",
    )

    assert rejected.returncode != 0
    assert "incomplete active manifest" in rejected.stderr
    assert sorted(path.name for path in destination.iterdir()) == [
        "release-manifest.json"
    ]


@pytest.mark.parametrize("replacement_kind", ["atomic_replace", "same_inode_rewrite"])
def test_runtime_manifest_read_retries_when_file_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    module = _load_runtime_release_tool_module()
    manifest = tmp_path / "release-manifest.json"
    manifest.write_bytes(b'{"value":"before"}')
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b'{"value":"after!"}')
    original_read = module.os.read
    changed = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        content = original_read(descriptor, size)
        if content and not changed:
            changed = True
            if replacement_kind == "atomic_replace":
                os.replace(replacement, manifest)
            else:
                manifest.write_bytes(b'{"value":"after!"}')
        return content

    monkeypatch.setattr(module.os, "read", mutating_read)
    content, _metadata = module._read_regular_file_at(
        tmp_path,
        "release-manifest.json",
        max_bytes=module.MAX_MANIFEST_BYTES,
    )

    assert content == b'{"value":"after!"}'


def test_runtime_safe_manifest_reader_rejects_bad_inputs_and_closes_handles(
    tmp_path: Path,
) -> None:
    module = _load_runtime_release_tool_module()
    before_handles = len(list(Path("/proc/self/fd").iterdir()))
    (tmp_path / "release-manifest.json").write_bytes(b"\xff")
    with pytest.raises(module.AuthorityError, match="invalid"):
        module._load_manifest(tmp_path)
    (tmp_path / "release-manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(module.AuthorityError, match="invalid"):
        module._load_manifest(tmp_path)
    (tmp_path / "release-manifest.json").unlink()
    (tmp_path / "release-manifest.json").symlink_to(tmp_path / "missing.json")
    with pytest.raises(module.AuthorityError):
        module._load_manifest(tmp_path)
    after_handles = len(list(Path("/proc/self/fd").iterdir()))
    assert after_handles == before_handles


@pytest.mark.parametrize(
    ("runtime_manifest", "legacy_manifest", "expect_warning"),
    [
        (None, "{}", True),
        ("{", "{}", False),
        (None, None, False),
    ],
)
def test_lifecycle_authority_warning_only_reports_a_real_absent_runtime_gap(
    tmp_path: Path,
    runtime_manifest: str | None,
    legacy_manifest: str | None,
    expect_warning: bool,
) -> None:
    fake_root = tmp_path / "repo"
    scripts_dir = fake_root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "elvern-common.sh", scripts_dir / "elvern-common.sh")
    runtime = fake_root / "runtime-releases"
    runtime.mkdir()
    legacy = fake_root / "clients/desktop-vlc-opener/artifacts/packages"
    legacy.mkdir(parents=True)
    if runtime_manifest is not None:
        (runtime / "release-manifest.json").write_text(runtime_manifest, encoding="utf-8")
    if legacy_manifest is not None:
        (legacy / "release-manifest.json").write_text(legacy_manifest, encoding="utf-8")
    command = (
        f'source "{scripts_dir / "elvern-common.sh"}"; '
        'elvern_log_message() { printf "%s:%s\\n" "$1" "$2"; }; '
        "elvern_warn_helper_release_authority_gap"
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "ELVERN_HELPER_RELEASES_DIR": str(runtime),
        },
    )

    assert result.returncode == 0, result.stderr
    assert ("runtime releases are absent" in result.stdout) is expect_warning
    assert "https://" not in result.stdout


def test_runtime_authority_tool_activates_manifest_last_and_cleans_packages(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    built = _run_publisher(workspace, env)
    assert built.returncode == 0, built.stderr
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    destination = tmp_path / "manifest-last"
    failure_env = dict(os.environ)
    failure_env["ELVERN_RUNTIME_MIGRATION_TEST_FAIL_AT"] = str(
        len(manifest["packages"])
    )

    interrupted = _run_runtime_tool(
        "migrate",
        "--source-dir", str(source),
        "--runtime-dir", str(destination),
        "--expected-origin-sha256", manifest["bound_origin_sha256"],
        "--apply",
        env=failure_env,
    )

    assert interrupted.returncode != 0
    assert list(destination.iterdir()) == []
    assert (source / "release-manifest.json").is_file()
    assert all((source / row["filename"]).is_file() for row in manifest["packages"])


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


def _uppercase_filename_hash(filename: str) -> str:
    hash_prefix = filename[-16:-4]
    uppercase_hash_prefix = hash_prefix.upper()
    if uppercase_hash_prefix == hash_prefix:
        uppercase_hash_prefix = f"A{hash_prefix[1:]}"
    return f"{filename[:-16]}{uppercase_hash_prefix}.zip"


@pytest.mark.parametrize(
    "filename_mutator",
    [
        lambda filename: (
            filename[:-5]
            + ("0" if filename[-5] != "0" else "1")
            + ".zip"
        ),
        _uppercase_filename_hash,
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
    assert not (
        active_dir.parent / authority_mutation_lock_basename(str(active_dir))
    ).exists()


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
    lock_dir = (
        active_dir.parent / authority_mutation_lock_basename(str(active_dir))
    )
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(
        "schema=unknown\ntransaction_nonce=existing\n",
        encoding="utf-8",
    )

    result = _run_publisher(workspace, env, "--activate")

    assert result.returncode != 0
    assert "authority mutation is running" in result.stderr
    assert "Do not remove the lock" in result.stderr
    assert active_manifest.read_text(encoding="utf-8") == '{"active":"old"}\n'
    assert lock_dir.is_dir()


def test_shared_authority_lock_blocks_migration_and_publisher_activation(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    assert _run_publisher(workspace, env).returncode == 0
    source = _staged_manifests(workspace)[0].parent
    manifest = json.loads((source / "release-manifest.json").read_text())
    destination = tmp_path / "runtime"
    module = _load_runtime_release_tool_module()
    lock = module._AuthorityMutationLock(destination)
    lock.acquire()
    try:
        migration = _run_runtime_tool(
            "migrate",
            "--source-dir", str(source),
            "--runtime-dir", str(destination),
            "--expected-origin-sha256", manifest["bound_origin_sha256"],
            "--apply",
        )
        activation = _run_publisher(
            workspace,
            {**env, "ELVERN_HELPER_RELEASES_DIR": str(destination)},
            "--activate",
        )
        assert migration.returncode != 0
        assert activation.returncode != 0
        assert "authority mutation" in migration.stderr
        assert "authority mutation" in activation.stderr
        assert not destination.exists()
    finally:
        assert lock.release()


def test_runtime_lock_replacement_is_not_removed_by_previous_owner(
    tmp_path: Path,
) -> None:
    module = _load_runtime_release_tool_module()
    destination = tmp_path / "runtime"
    lock = module._AuthorityMutationLock(destination)
    lock.acquire()
    displaced = tmp_path / "displaced-lock"
    os.replace(lock.path, displaced)
    lock.path.mkdir(mode=0o700)
    new_owner = lock.path / "owner"
    new_owner.write_text(
        "schema=foreign\ntransaction_nonce=new-owner\n",
        encoding="ascii",
    )
    new_owner.chmod(0o600)

    assert lock.release() is False
    assert lock.path.is_dir()
    assert new_owner.read_text(encoding="ascii").endswith("new-owner\n")


def test_activation_requires_an_explicit_runtime_active_directory(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    env.pop("ELVERN_HELPER_RELEASES_DIR")

    result = _run_publisher(workspace, env, "--activate")

    assert result.returncode != 0
    assert "--activate requires --active-dir or ELVERN_HELPER_RELEASES_DIR" in result.stderr


def test_activation_cli_active_directory_overrides_environment(
    tmp_path: Path,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    environment_active = tmp_path / "environment-active"
    cli_active = tmp_path / "docker-data" / "helper_releases"
    env["ELVERN_HELPER_RELEASES_DIR"] = str(environment_active)

    result = _run_publisher(
        workspace,
        env,
        "--activate",
        "--active-dir",
        str(cli_active),
    )

    assert result.returncode == 0, result.stderr
    assert (cli_active / "release-manifest.json").is_file()
    assert not (environment_active / "release-manifest.json").exists()


@pytest.mark.parametrize("unsafe_kind", ["relative", "symlink", "symlink-parent"])
def test_activation_rejects_unsafe_active_directory(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    workspace, env = _create_publish_workspace(tmp_path)
    if unsafe_kind == "relative":
        active_dir = Path("relative/helper_releases")
    elif unsafe_kind == "symlink":
        target = tmp_path / "active-target"
        target.mkdir()
        active_dir = tmp_path / "active-link"
        active_dir.symlink_to(target, target_is_directory=True)
    else:
        target = tmp_path / "active-parent-target"
        target.mkdir()
        parent_link = tmp_path / "active-parent-link"
        parent_link.symlink_to(target, target_is_directory=True)
        active_dir = parent_link / "helper_releases"

    result = _run_publisher(
        workspace,
        env,
        "--activate",
        "--active-dir",
        str(active_dir),
    )

    assert result.returncode != 0
    assert "Active release directory" in result.stderr
    assert _staged_manifests(workspace) == []


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
