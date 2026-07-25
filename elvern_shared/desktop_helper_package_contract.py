from __future__ import annotations

import argparse
import hashlib
import json
import os
import re


PACKAGE_NAME_PREFIX = "elvern-vlc-opener"
AUTHORITY_MUTATION_LOCK_NAME = ".desktop-helper-authority.lock"
AUTHORITY_MUTATION_LOCK_SCHEMA = "elvern-desktop-helper-authority-lock-v1"
PACKAGE_RUNTIME_CONTRACTS = {
    "windows-x64": ("windows", ("win-x64",)),
    "macos-dual-arch": ("mac", ("osx-arm64", "osx-x64")),
    "linux-universal": (
        "linux",
        ("linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64"),
    ),
}
_SAFE_COMPONENT = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def authority_runtime_path_sha256(runtime_path: str) -> str:
    if not isinstance(runtime_path, str) or not runtime_path:
        raise ValueError("Runtime authority path is required")
    if any(ord(character) < 32 or ord(character) == 127 for character in runtime_path):
        raise ValueError("Runtime authority path is unsafe")
    canonical = os.path.abspath(runtime_path)
    if not os.path.isabs(runtime_path) or canonical != runtime_path:
        raise ValueError("Runtime authority path must be canonical and absolute")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def authority_mutation_lock_basename(runtime_path: str) -> str:
    digest = authority_runtime_path_sha256(runtime_path)
    return f".{digest[:24]}{AUTHORITY_MUTATION_LOCK_NAME}"


def derive_standard_package_maps(
    contracts: dict[str, tuple[str, tuple[str, ...]]],
) -> tuple[dict[str, str], dict[str, tuple[str, ...]], dict[str, str]]:
    runtime_to_platform: dict[str, str] = {}
    platform_runtime_order: dict[str, tuple[str, ...]] = {}
    platform_package_target: dict[str, str] = {}
    for package_target, (platform, runtime_ids) in contracts.items():
        if platform in platform_package_target:
            raise ValueError(
                f"Multiple standard package targets are defined for platform {platform}"
            )
        platform_package_target[platform] = package_target
        platform_runtime_order[platform] = tuple(runtime_ids)
        for runtime_id in runtime_ids:
            existing = runtime_to_platform.get(runtime_id)
            if existing is not None:
                raise ValueError(
                    f"Standard runtime {runtime_id} is repeated for {existing} and {platform}"
                )
            runtime_to_platform[runtime_id] = platform
    return runtime_to_platform, platform_runtime_order, platform_package_target


(
    STANDARD_RUNTIME_TO_PLATFORM,
    STANDARD_PLATFORM_RUNTIME_ORDER,
    STANDARD_PLATFORM_PACKAGE_TARGET,
) = derive_standard_package_maps(PACKAGE_RUNTIME_CONTRACTS)


def expected_package_filename(
    prefix: str,
    helper_version: str,
    package_target: str,
    sha256: str,
) -> str:
    for label, value in (
        ("package prefix", prefix),
        ("helper version", helper_version),
        ("package target", package_target),
    ):
        if not isinstance(value, str) or _SAFE_COMPONENT.fullmatch(value) is None:
            raise ValueError(f"Invalid {label}")
    if not isinstance(sha256, str) or _LOWER_SHA256.fullmatch(sha256) is None:
        raise ValueError("Invalid package SHA-256")
    return f"{prefix}-{helper_version}-{package_target}-{sha256[:12]}.zip"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--authority-lock-json")
    parser.add_argument("helper_version", nargs="?")
    parser.add_argument("package_target", nargs="?")
    parser.add_argument("sha256", nargs="?")
    args = parser.parse_args()
    if args.authority_lock_json is not None:
        if args.json or any((args.helper_version, args.package_target, args.sha256)):
            parser.error("--authority-lock-json does not accept other arguments")
        try:
            runtime_path_sha256 = authority_runtime_path_sha256(
                args.authority_lock_json
            )
            lock_basename = authority_mutation_lock_basename(
                args.authority_lock_json
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps({
            "schema": AUTHORITY_MUTATION_LOCK_SCHEMA,
            "lock_basename": lock_basename,
            "runtime_path_sha256": runtime_path_sha256,
        }, separators=(",", ":"), sort_keys=True))
        return 0
    if args.json:
        if any((args.helper_version, args.package_target, args.sha256)):
            parser.error("--json does not accept filename arguments")
        print(json.dumps({
            "authority_mutation_lock_name": AUTHORITY_MUTATION_LOCK_NAME,
            "authority_mutation_lock_schema": AUTHORITY_MUTATION_LOCK_SCHEMA,
            "prefix": PACKAGE_NAME_PREFIX,
            "packages": {
                package_target: {
                    "platform": platform,
                    "rids": list(runtime_ids),
                }
                for package_target, (platform, runtime_ids)
                in PACKAGE_RUNTIME_CONTRACTS.items()
            },
        }, separators=(",", ":"), sort_keys=True))
        return 0
    if not all((args.helper_version, args.package_target, args.sha256)):
        parser.error("helper_version, package_target, and sha256 are required")
    try:
        filename = expected_package_filename(
            PACKAGE_NAME_PREFIX,
            args.helper_version,
            args.package_target,
            args.sha256,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
