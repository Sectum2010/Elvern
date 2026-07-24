#!/usr/bin/env python3
"""Strictly validate staged Elvern desktop Helper packages without extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

for candidate_root in Path(__file__).resolve().parents:
    if (candidate_root / "clients" / "desktop_helper_package_contract.py").is_file():
        sys.path.insert(0, str(candidate_root))
        break

from clients.desktop_helper_package_contract import (  # noqa: E402
    PACKAGE_NAME_PREFIX,
    expected_package_filename,
)


MANIFEST_SCHEMA = "desktop-helper-release-manifest-v2"
INNER_SCHEMA = "desktop-helper-installer-manifest-v2"
TARGET_FRAMEWORK = "net10.0"
RUNTIME_FAMILY = "10.0"
DEPLOYMENT_MODE = "self_contained"
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILENAME_LENGTH = 180
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*\.zip$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

ROOT_KEYS = {
    "schema_version",
    "helper_version",
    "channel",
    "target_framework",
    "runtime_family",
    "deployment_mode",
    "generated_at_utc",
    "bound_origin_sha256",
    "packages",
}
PACKAGE_KEYS = {
    "package_target",
    "platform",
    "artifact_kind",
    "filename",
    "relative_path",
    "package_root",
    "installer_entrypoint",
    "supported_runtime_ids",
    "external_runtime_required",
    "size_bytes",
    "sha256",
    "installer_manifest_sha256",
    "installer_tree_manifest_path",
    "installer_tree_manifest_sha256",
    "bound_origin_sha256",
    "generated_at_utc",
}
INNER_KEYS = {
    "schema_version",
    "helper_version",
    "target_framework",
    "runtime_family",
    "deployment_mode",
    "external_runtime_required",
    "package_target",
    "bound_origin_sha256",
    "payloads",
}
PAYLOAD_KEYS = {
    "runtime_id",
    "relative_path",
    "sha256",
    "size_bytes",
    "executable_name",
}
META_ORDER = (
    "schema_version",
    "helper_version",
    "target_framework",
    "runtime_family",
    "deployment_mode",
    "package_target",
    "bound_origin_sha256",
)
CONTRACTS = {
    "windows-x64": {
        "platform": "windows",
        "rids": ("win-x64",),
        "package_root": "Elvern VLC Opener Windows Installer",
        "installer": "Install-ElvernVlcOpener.cmd",
        "minimum_os_version": None,
    },
    "macos-dual-arch": {
        "platform": "mac",
        "rids": ("osx-arm64", "osx-x64"),
        "package_root": "Elvern VLC Opener Installer",
        "installer": "Install-ElvernVlcOpener.command",
        "minimum_os_version": "14.0",
    },
    "linux-universal": {
        "platform": "linux",
        "rids": (
            "linux-x64",
            "linux-arm64",
            "linux-musl-x64",
            "linux-musl-arm64",
        ),
        "package_root": "Elvern VLC Opener Linux Installer",
        "installer": "Install-ElvernVlcOpener.sh",
        "minimum_os_version": None,
    },
}


class PackageValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageValidationError(message)


def stream_sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def zip_member_digest(bundle: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with bundle.open(info, "r") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def read_zip_metadata(
    bundle: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    label: str,
) -> bytes:
    require(info.file_size <= MAX_METADATA_BYTES, f"{label} is too large")
    with bundle.open(info, "r") as handle:
        payload = handle.read(MAX_METADATA_BYTES + 1)
    require(len(payload) <= MAX_METADATA_BYTES, f"{label} is too large")
    require(len(payload) == info.file_size, f"{label} size changed while reading")
    return payload


def safe_relative(value: object, label: str) -> str:
    require(isinstance(value, str) and bool(value), f"{label} is required")
    require(
        "\\" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value),
        f"{label} is unsafe",
    )
    path = PurePosixPath(value)
    require(not path.is_absolute(), f"{label} is unsafe")
    require(all(part not in {"", ".", ".."} for part in path.parts), f"{label} is unsafe")
    require(value == path.as_posix(), f"{label} is not canonical")
    return value


def require_exact_keys(payload: dict[str, object], expected: set[str], label: str) -> None:
    require(set(payload) == expected, f"{label} contains missing or unknown fields")


def require_sha(value: object, label: str) -> str:
    require(isinstance(value, str) and SHA256_RE.fullmatch(value) is not None, f"{label} is invalid")
    return value


def require_size(value: object, label: str) -> int:
    require(
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_PAYLOAD_BYTES,
        f"{label} is invalid",
    )
    return value


def member_mode(info: zipfile.ZipInfo) -> int:
    return stat.S_IMODE(info.external_attr >> 16)


def validate_member_names(
    bundle: zipfile.ZipFile,
    package_root: str,
) -> tuple[dict[str, zipfile.ZipInfo], list[zipfile.ZipInfo]]:
    files: dict[str, zipfile.ZipInfo] = {}
    directories: list[zipfile.ZipInfo] = []
    seen: set[str] = set()
    seen_casefolded: set[str] = set()
    root_prefix = f"{package_root}/"
    for info in bundle.infolist():
        name = info.filename
        require(name not in seen, "ZIP contains a duplicate path")
        folded = name.casefold()
        require(folded not in seen_casefolded, "ZIP contains a case-colliding path")
        seen.add(name)
        seen_casefolded.add(folded)
        require("\\" not in name and "\x00" not in name, "ZIP contains an unsafe path")
        normalized = name[:-1] if name.endswith("/") else name
        path = PurePosixPath(normalized)
        require(
            normalized
            and not path.is_absolute()
            and all(part not in {"", ".", ".."} for part in path.parts),
            "ZIP contains an unsafe path",
        )
        require(
            normalized == path.as_posix()
            and not any(ord(character) < 32 or ord(character) == 127 for character in normalized),
            "ZIP contains a non-canonical path",
        )
        require(
            normalized == package_root or normalized.startswith(root_prefix),
            "ZIP contains an entry outside the exact package root",
        )
        require(not (info.flag_bits & 0x1), "ZIP contains an encrypted entry")
        raw_mode = info.external_attr >> 16
        require(not stat.S_ISLNK(raw_mode), "ZIP contains an unsupported symlink")
        if info.is_dir():
            require(not raw_mode or stat.S_ISDIR(raw_mode), "ZIP contains an unsupported directory entry")
            directories.append(info)
            continue
        require(not raw_mode or stat.S_ISREG(raw_mode), "ZIP contains an unsupported entry")
        files[name] = info
    require(bool(files), "ZIP contains no files")
    return files, directories


def parse_tree_manifest(payload: bytes) -> list[dict[str, object]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageValidationError("Tree manifest is not valid UTF-8") from exc
    lines = text.splitlines()
    require(lines and lines[0] == "path\tsize_bytes\tsha256\tfile_class", "Tree manifest header is invalid")
    require(len(lines) > 1, "Tree manifest is empty")
    rows: list[dict[str, object]] = []
    paths: set[str] = set()
    folded_paths: set[str] = set()
    for line in lines[1:]:
        fields = line.split("\t")
        require(len(fields) == 4, "Tree manifest row is invalid")
        relative, size_text, digest, file_class = fields
        relative = safe_relative(relative, "Tree manifest path")
        require(relative not in paths and relative.casefold() not in folded_paths, "Tree manifest path is duplicated")
        require(size_text.isascii() and size_text.isdigit(), "Tree manifest size is invalid")
        size = int(size_text)
        require(size <= MAX_PAYLOAD_BYTES, "Tree manifest size is invalid")
        require_sha(digest, "Tree manifest SHA-256")
        require(file_class in {"data", "executable"}, "Tree manifest file_class is invalid")
        paths.add(relative)
        folded_paths.add(relative.casefold())
        rows.append({
            "relative_path": relative,
            "size_bytes": size,
            "sha256": digest,
            "file_class": file_class,
        })
    return rows


def parse_installer_tsv(payload: bytes) -> tuple[dict[str, str], list[dict[str, object]]]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PackageValidationError("Installer TSV is not valid UTF-8") from exc
    metadata: dict[str, str] = {}
    payloads: list[dict[str, object]] = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) == 3 and fields[0] == "meta":
            key, value = fields[1:]
            require(key in META_ORDER and key not in metadata and bool(value), "Installer TSV metadata is invalid")
            metadata[key] = value
            continue
        require(len(fields) == 6 and fields[0] == "payload", "Installer TSV row is invalid")
        runtime_id, relative, digest, size_text, executable = fields[1:]
        require(size_text.isascii() and size_text.isdigit(), "Installer TSV payload size is invalid")
        payloads.append({
            "runtime_id": runtime_id,
            "relative_path": safe_relative(relative, "Installer TSV payload path"),
            "sha256": require_sha(digest, "Installer TSV payload SHA-256"),
            "size_bytes": int(size_text),
            "executable_name": executable,
        })
    require(tuple(metadata) == META_ORDER, "Installer TSV metadata order or set is invalid")
    return metadata, payloads


def validate_archive(
    archive: Path,
    record: dict[str, object],
    manifest: dict[str, object],
) -> None:
    target = str(record["package_target"])
    contract = CONTRACTS[target]
    package_root = str(record["package_root"])
    with zipfile.ZipFile(archive, "r") as bundle:
        files, directories = validate_member_names(bundle, package_root)
        for directory in directories:
            require(member_mode(directory) == 0o755, "Package directory mode is invalid")
        prefix = f"{package_root}/"
        visible = {
            name[len(prefix):]
            for name in files
            if name.startswith(prefix) and "/" not in name[len(prefix):]
        }
        require(
            visible == {str(record["installer_entrypoint"]), "README.txt"},
            "Package root visible files do not match the contract",
        )
        tree_relative = safe_relative(
            record["installer_tree_manifest_path"],
            "installer_tree_manifest_path",
        )
        tree_name = prefix + tree_relative
        inner_name = prefix + ".elvern/manifest.json"
        tsv_name = prefix + ".elvern/installer-manifest.tsv"
        require(tree_name in files and inner_name in files and tsv_name in files, "Package metadata file is missing")
        tree_bytes = read_zip_metadata(bundle, files[tree_name], "Tree manifest")
        inner_bytes = read_zip_metadata(bundle, files[inner_name], "Inner manifest")
        tsv_bytes = read_zip_metadata(bundle, files[tsv_name], "Installer TSV")
        require(hashlib.sha256(tree_bytes).hexdigest() == record["installer_tree_manifest_sha256"], "Tree manifest SHA-256 mismatch")
        require(hashlib.sha256(inner_bytes).hexdigest() == record["installer_manifest_sha256"], "Inner manifest SHA-256 mismatch")

        rows = parse_tree_manifest(tree_bytes)
        expected_names = {prefix + str(row["relative_path"]) for row in rows}
        actual_names = set(files) - {tree_name}
        require(expected_names == actual_names, "Package tree contains missing or extra files")
        for row in rows:
            name = prefix + str(row["relative_path"])
            info = files[name]
            size, digest = zip_member_digest(bundle, info)
            require(size == row["size_bytes"] and digest == row["sha256"], "Package tree file integrity mismatch")
            expected_mode = 0o755 if row["file_class"] == "executable" else 0o644
            require(member_mode(info) == expected_mode, "Package file mode does not match file_class")
        require(member_mode(files[tree_name]) == 0o644, "Tree manifest mode is invalid")

        try:
            inner = json.loads(inner_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageValidationError("Inner manifest JSON is invalid") from exc
        require(isinstance(inner, dict), "Inner manifest root is invalid")
        require_exact_keys(inner, INNER_KEYS, "Inner manifest")
        expected_meta = {
            "schema_version": INNER_SCHEMA,
            "helper_version": manifest["helper_version"],
            "target_framework": TARGET_FRAMEWORK,
            "runtime_family": RUNTIME_FAMILY,
            "deployment_mode": DEPLOYMENT_MODE,
            "package_target": target,
            "bound_origin_sha256": manifest["bound_origin_sha256"],
        }
        for key, value in expected_meta.items():
            require(inner.get(key) == value, f"Inner manifest {key} mismatch")
        require(inner.get("external_runtime_required") is False, "Inner manifest runtime contract is invalid")
        inner_payloads = inner.get("payloads")
        require(isinstance(inner_payloads, list), "Inner manifest payloads are invalid")
        require(tuple(str(item.get("runtime_id")) for item in inner_payloads if isinstance(item, dict)) == contract["rids"], "Inner manifest RID order is invalid")
        for item in inner_payloads:
            require(isinstance(item, dict), "Inner manifest payload is invalid")
            require_exact_keys(item, PAYLOAD_KEYS, "Inner manifest payload")
            safe_relative(item["relative_path"], "Inner manifest payload path")
            require_sha(item["sha256"], "Inner manifest payload SHA-256")
            require_size(item["size_bytes"], "Inner manifest payload size")

        tsv_meta, tsv_payloads = parse_installer_tsv(tsv_bytes)
        require(tsv_meta == expected_meta, "Installer TSV metadata does not match JSON")
        require(tsv_payloads == inner_payloads, "Installer TSV payloads do not match JSON")
        require(tuple(item["runtime_id"] for item in inner_payloads) == contract["rids"], "Payload RID set or order is invalid")
        for item in inner_payloads:
            rid = str(item["runtime_id"])
            executable = "Elvern.VlcOpener.exe" if rid.startswith("win-") else "Elvern.VlcOpener"
            expected_relative = f"payloads/{rid}/{executable}"
            require(item["relative_path"] == expected_relative and item["executable_name"] == executable, "Payload path or executable is invalid")
            member = prefix + ".elvern/" + expected_relative
            require(member in files, "Payload file is missing")
            size, digest = zip_member_digest(bundle, files[member])
            require(size == item["size_bytes"] and digest == item["sha256"], "Payload integrity mismatch")
            require(member_mode(files[member]) == 0o755, "Payload executable mode is invalid")


def validate_manifest(
    manifest_path: Path,
    artifacts_dir: Path,
    *,
    package_name_prefix: str = PACKAGE_NAME_PREFIX,
    expected_origin: str | None = None,
    expected_targets: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    require(manifest_path.is_file() and not manifest_path.is_symlink(), "Release manifest is missing or unsafe")
    require(manifest_path.stat().st_size <= MAX_METADATA_BYTES, "Release manifest is too large")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError("Release manifest JSON is invalid") from exc
    require(isinstance(manifest, dict), "Release manifest root is invalid")
    require_exact_keys(manifest, ROOT_KEYS, "Release manifest")
    require(manifest["schema_version"] == MANIFEST_SCHEMA, "Release manifest schema is invalid")
    require(
        isinstance(manifest["helper_version"], str)
        and VERSION_RE.fullmatch(manifest["helper_version"]) is not None,
        "Release helper version is invalid",
    )
    require(isinstance(manifest["channel"], str) and bool(manifest["channel"]), "Release channel is invalid")
    require(manifest["target_framework"] == TARGET_FRAMEWORK, "Release target framework is invalid")
    require(manifest["runtime_family"] == RUNTIME_FAMILY, "Release runtime family is invalid")
    require(manifest["deployment_mode"] == DEPLOYMENT_MODE, "Release deployment mode is invalid")
    origin = require_sha(manifest["bound_origin_sha256"], "Release origin hash")
    if expected_origin is not None:
        require(origin == expected_origin, "Release origin hash does not match the requested build")
    packages = manifest["packages"]
    require(isinstance(packages, list) and bool(packages), "Release package list is invalid")
    seen_targets: set[str] = set()
    normalized: list[dict[str, object]] = []
    for raw_record in packages:
        require(isinstance(raw_record, dict), "Release package record is invalid")
        target = str(raw_record.get("package_target") or "")
        require(target in CONTRACTS and target not in seen_targets, "Release package target is invalid or duplicated")
        seen_targets.add(target)
        expected_keys = set(PACKAGE_KEYS)
        if CONTRACTS[target]["minimum_os_version"] is not None:
            expected_keys.add("minimum_os_version")
        require_exact_keys(raw_record, expected_keys, "Release package")
        contract = CONTRACTS[target]
        filename = str(raw_record["filename"])
        require(
            1 <= len(filename) <= MAX_FILENAME_LENGTH
            and SAFE_FILENAME.fullmatch(filename) is not None,
            "Release filename is invalid",
        )
        require(raw_record["relative_path"] == filename, "Release relative_path must equal filename")
        require(raw_record["artifact_kind"] == "zip", "Release artifact kind is invalid")
        require(raw_record["platform"] == contract["platform"], "Release platform is invalid")
        require(raw_record["package_root"] == contract["package_root"], "Release package root is invalid")
        require(raw_record["installer_entrypoint"] == contract["installer"], "Release installer entrypoint is invalid")
        supported_runtime_ids = raw_record["supported_runtime_ids"]
        require(
            isinstance(supported_runtime_ids, list)
            and all(isinstance(runtime_id, str) for runtime_id in supported_runtime_ids)
            and tuple(supported_runtime_ids) == contract["rids"],
            "Release RID order is invalid",
        )
        require(raw_record["external_runtime_required"] is False, "Release runtime inclusion contract is invalid")
        require(raw_record["bound_origin_sha256"] == origin, "Package origin hash mismatch")
        require(raw_record["generated_at_utc"] == manifest["generated_at_utc"], "Package timestamp mismatch")
        require(raw_record.get("minimum_os_version") == contract["minimum_os_version"], "Package minimum OS is invalid")
        require(raw_record["installer_tree_manifest_path"] == ".elvern/tree-manifest.tsv", "Tree manifest path is invalid")
        package_sha256 = require_sha(
            raw_record["sha256"],
            "Release package SHA-256",
        )
        require_sha(raw_record["installer_manifest_sha256"], "Inner manifest SHA-256")
        require_sha(raw_record["installer_tree_manifest_sha256"], "Tree manifest SHA-256")
        expected_size = require_size(raw_record["size_bytes"], "Release package size")
        archive = artifacts_dir / filename
        require(archive.parent == artifacts_dir and archive.is_file() and not archive.is_symlink(), "Release package is missing or unsafe")
        actual_size, digest = stream_sha256(archive)
        require(actual_size == expected_size and digest == package_sha256, "Outer package integrity mismatch")
        try:
            required_filename = expected_package_filename(
                package_name_prefix,
                manifest["helper_version"],
                target,
                digest,
            )
        except ValueError as exc:
            raise PackageValidationError(
                "Release package filename contract is invalid"
            ) from exc
        require(
            filename == required_filename
            and raw_record["relative_path"] == required_filename,
            "Release package filename does not match its content hash",
        )
        validate_archive(archive, raw_record, manifest)
        normalized.append(raw_record)
    if expected_targets:
        require(tuple(record["package_target"] for record in normalized) == expected_targets, "Release package set or order does not match requested targets")
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--artifacts-dir", required=True, type=Path)
    parser.add_argument(
        "--package-name-prefix",
        default=PACKAGE_NAME_PREFIX,
    )
    parser.add_argument("--expected-origin-sha256")
    parser.add_argument("--expected-package-target", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        packages = validate_manifest(
            args.manifest,
            args.artifacts_dir,
            package_name_prefix=args.package_name_prefix,
            expected_origin=args.expected_origin_sha256,
            expected_targets=tuple(args.expected_package_target),
        )
    except (OSError, zipfile.BadZipFile, PackageValidationError) as exc:
        print(f"Desktop Helper package validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Validated {len(packages)} desktop Helper package(s).")
    for package in packages:
        print(
            f"{package['package_target']}: {package['filename']} "
            f"({package['size_bytes']} bytes, {package['sha256']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
