#!/usr/bin/env python3
"""Inspect or safely migrate the Desktop Helper runtime release authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "clients/desktop-vlc-opener/scripts/validate-package.py"
MANIFEST_NAME = "release-manifest.json"
EXPECTED_TARGETS = ("windows-x64", "macos-dual-arch", "linux-universal")


class AuthorityError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _absolute_no_symlink(path: str, *, allow_missing_leaf: bool) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise AuthorityError("Release directory paths must be absolute.")
    normalized = Path(os.path.abspath(candidate))
    cursor = Path(normalized.anchor)
    for index, component in enumerate(normalized.parts[1:]):
        cursor /= component
        if cursor.is_symlink():
            raise AuthorityError("Release directory paths cannot contain symlinks.")
        if not cursor.exists():
            if allow_missing_leaf and index == len(normalized.parts[1:]) - 1:
                break
            if not allow_missing_leaf:
                raise AuthorityError("Release directory does not exist.")
    return normalized


def _load_manifest(directory: Path) -> dict[str, object]:
    path = directory / MANIFEST_NAME
    if path.is_symlink():
        raise AuthorityError("Release manifest is not a safe regular file.")
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise AuthorityError("Release manifest is not a safe regular file.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthorityError("Release manifest is invalid.") from exc
    if not isinstance(payload, dict):
        raise AuthorityError("Release manifest root is invalid.")
    return payload


def _validate(
    directory: Path,
    *,
    expected_origin: str | None,
) -> dict[str, object]:
    payload = _load_manifest(directory)
    command = [
        sys.executable,
        str(VALIDATOR),
        "--manifest",
        str(directory / MANIFEST_NAME),
        "--artifacts-dir",
        str(directory),
    ]
    for target in EXPECTED_TARGETS:
        command.extend(("--expected-package-target", target))
    if expected_origin:
        command.extend(("--expected-origin-sha256", expected_origin))
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AuthorityError("Strict package validation failed.")
    return payload


def _summary(directory: Path, payload: dict[str, object]) -> dict[str, object]:
    packages = payload.get("packages")
    if not isinstance(packages, list):
        raise AuthorityError("Release package list is invalid.")
    return {
        "runtime_dir": str(directory),
        "manifest_state": "valid",
        "helper_version": payload.get("helper_version"),
        "package_targets": [
            {
                "package_target": package.get("package_target"),
                "filename": package.get("filename"),
                "size_bytes": package.get("size_bytes"),
                "sha256": package.get("sha256"),
            }
            for package in packages
            if isinstance(package, dict)
        ],
        "origin_compatible": True,
    }


def inspect_authority(directory: Path, expected_origin: str | None) -> int:
    try:
        payload = _validate(directory, expected_origin=expected_origin)
    except FileNotFoundError:
        print(json.dumps({
            "runtime_dir": str(directory),
            "manifest_state": "absent",
        }, indent=2))
        return 2
    except AuthorityError as exc:
        print(json.dumps({
            "runtime_dir": str(directory),
            "manifest_state": "invalid",
            "error": str(exc),
        }, indent=2))
        return 3
    print(json.dumps(_summary(directory, payload), indent=2))
    return 0


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _same_file(path: Path, source: Path) -> bool:
    return (
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size == source.stat().st_size
        and _sha256(path) == _sha256(source)
    )


def migrate_authority(
    source: Path,
    destination: Path,
    expected_origin: str,
    *,
    apply: bool,
) -> int:
    payload = _validate(source, expected_origin=expected_origin)
    package_names = [
        str(package["filename"])
        for package in payload["packages"]
        if isinstance(package, dict)
    ]
    source_files = [source / name for name in package_names]
    manifest_source = source / MANIFEST_NAME

    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise AuthorityError("Runtime release destination is unsafe.")
        entries = tuple(destination.iterdir())
        allowed = set(package_names) | {MANIFEST_NAME}
        if any(entry.name not in allowed for entry in entries):
            raise AuthorityError("Runtime release destination contains another authority.")
        existing_manifest = destination / MANIFEST_NAME
        if existing_manifest.exists():
            if not _same_file(existing_manifest, manifest_source):
                raise AuthorityError("Runtime release destination manifest conflicts.")
            if all(_same_file(destination / item.name, item) for item in source_files):
                print("Runtime release authority is already identical.")
                return 0
            raise AuthorityError("Runtime release destination artifacts conflict.")

    if not apply:
        print(
            f"Dry run: validated {len(source_files)} packages for migration "
            f"into {destination}."
        )
        return 0

    destination.mkdir(mode=0o755, parents=False, exist_ok=True)
    created: list[Path] = []
    temporary: list[Path] = []
    try:
        for index, source_file in enumerate((*source_files, manifest_source)):
            final_path = destination / source_file.name
            if final_path.exists():
                if _same_file(final_path, source_file):
                    continue
                raise AuthorityError("Runtime release destination artifact conflicts.")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{source_file.name}.new.",
                dir=destination,
            )
            temporary_path = Path(temporary_name)
            temporary.append(temporary_path)
            try:
                with os.fdopen(descriptor, "wb") as output, source_file.open("rb") as source_handle:
                    shutil.copyfileobj(source_handle, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if _sha256(temporary_path) != _sha256(source_file):
                    raise AuthorityError("Copied release artifact failed verification.")
                os.chmod(temporary_path, 0o444)
                if os.environ.get("ELVERN_RUNTIME_MIGRATION_TEST_FAIL_AT") == str(index):
                    raise AuthorityError("Injected migration failure.")
                os.replace(temporary_path, final_path)
                temporary.remove(temporary_path)
                created.append(final_path)
                _fsync_directory(destination)
            finally:
                temporary_path.unlink(missing_ok=True)
        _validate(destination, expected_origin=expected_origin)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        for path in temporary:
            path.unlink(missing_ok=True)
        _fsync_directory(destination)
        raise
    print(f"Migrated {len(source_files)} immutable packages; manifest activated last.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--runtime-dir", required=True)
    inspect_parser.add_argument("--expected-origin-sha256")
    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--source-dir", required=True)
    migrate_parser.add_argument("--runtime-dir", required=True)
    migrate_parser.add_argument("--expected-origin-sha256", required=True)
    migrate_parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "inspect":
            runtime = _absolute_no_symlink(args.runtime_dir, allow_missing_leaf=True)
            return inspect_authority(runtime, args.expected_origin_sha256)
        source = _absolute_no_symlink(args.source_dir, allow_missing_leaf=False)
        runtime = _absolute_no_symlink(args.runtime_dir, allow_missing_leaf=True)
        return migrate_authority(
            source,
            runtime,
            args.expected_origin_sha256,
            apply=args.apply,
        )
    except AuthorityError as exc:
        print(f"Desktop Helper runtime authority error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
