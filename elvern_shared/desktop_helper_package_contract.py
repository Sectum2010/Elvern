from __future__ import annotations

import argparse
import json
import re


PACKAGE_NAME_PREFIX = "elvern-vlc-opener"
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
    parser.add_argument("helper_version", nargs="?")
    parser.add_argument("package_target", nargs="?")
    parser.add_argument("sha256", nargs="?")
    args = parser.parse_args()
    if args.json:
        if any((args.helper_version, args.package_target, args.sha256)):
            parser.error("--json does not accept filename arguments")
        print(json.dumps({
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
