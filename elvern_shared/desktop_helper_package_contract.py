from __future__ import annotations

import argparse
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
    parser.add_argument("helper_version")
    parser.add_argument("package_target")
    parser.add_argument("sha256")
    args = parser.parse_args()
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
