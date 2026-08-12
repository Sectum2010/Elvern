#!/usr/bin/env python3
"""Run approved Elvern checks and record final dirty-worktree evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parents[1]
REPORT_PATH: Final = ROOT / "tmp" / "final-validation-proof.json"
PYTHON: Final = ROOT / ".venv" / "bin" / "python"

BROWSER_PROJECTS: Final = (
    "chromium-desktop-production",
    "firefox-desktop-production",
    "chromium-assistant-navigation",
    "firefox-assistant-navigation",
    "chromium-settings-navigation",
    "firefox-settings-navigation",
    "chromium-control-center-baseline",
    "chromium-service-worker-network-guard",
    "firefox-service-worker-network-guard",
    "chromium-wss-network-guard",
    "firefox-wss-network-guard",
)

FRONTEND_TARGETS: Final = (
    "src/auth/ProviderAuthContext.test.jsx",
    "src/lib/providerAuth.test.js",
    "src/lib/api.test.mjs",
    "src/lib/pageLifecycle.test.js",
    "src/lib/pageResume.test.js",
    "src/lib/connectivityRecoveryStore.test.js",
    "src/pages/SettingsPage.test.jsx",
    "src/components/DesktopControlCenterLayout.test.jsx",
    "src/components/SystemStatusRail.test.jsx",
    "src/components/meridian/MeridianSettingsView.test.jsx",
    "src/lib/controlCenterQueries.test.js",
    "src/lib/userSettingsQueries.test.jsx",
    "src/lib/externalNavigationCoordinator.test.js",
)


def command(*argv: str, cwd: str = ".") -> dict[str, object]:
    return {"argv": list(argv), "cwd": cwd}


def browser_arguments() -> tuple[str, ...]:
    return tuple(
        argument
        for project in BROWSER_PROJECTS
        for argument in ("--project", project)
    )


CHECKS: Final = {
    "backend-targeted": [command(
        str(PYTHON), "-m", "pytest",
        "backend/tests/test_backup_v2.py",
        "backend/tests/test_google_account_reconnect_identity.py",
        "backend/tests/test_google_oauth_operation_lifecycle.py",
        "backend/tests/test_google_oauth_state_security.py",
        "-q",
    )],
    "backend-full": [command(
        str(PYTHON), "-m", "pytest", "--junitxml=tmp/backend-junit.xml",
    )],
    "frontend-targeted": [command(
        "npm", "test", "--", "--run", *FRONTEND_TARGETS, cwd="frontend",
    )],
    "frontend-full": [command("npm", "test", cwd="frontend")],
    "frontend-build": [command("npm", "run", "build", cwd="frontend")],
    "browser-production": [
        command("node", "scripts/build-phase7-production.mjs", cwd="frontend"),
        command(
            "node", "scripts/run-cross-browser-playwright.mjs", "--use-existing-build",
            *browser_arguments(),
            cwd="frontend",
        ),
    ],
    "docker-smoke": [command("./scripts/docker-smoke.sh")],
    "helper": [
        command(
            "dotnet", "build", "clients/desktop-vlc-opener/Elvern.VlcOpener.csproj",
            "--configuration", "Release",
        ),
        command(
            "dotnet", "test", "clients/desktop-vlc-opener/Tests/Elvern.VlcOpener.Tests.csproj",
            "--configuration", "Release",
        ),
    ],
    "security": [
        command(str(PYTHON), "-m", "pip_audit", "-r", "backend/requirements.lock.txt", "--strict"),
        command(str(PYTHON), "-m", "pip_audit", "-r", "backend/requirements-test.lock.txt", "--strict"),
        command(
            str(PYTHON), "-m", "bandit", "-r", "backend/app", "-ll", "-ii",
            "--exclude", "backend/app/__pycache__",
        ),
        command("npm", "audit", "--audit-level=high", cwd="frontend"),
    ],
    "fresh-ci": [command("./scripts/elvern-ci-local.sh", "--fresh")],
    "hygiene": [command("git", "diff", "--check")],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_text(argv: list[str]) -> str:
    result = subprocess.run(
        argv,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_sha256(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path) or ("0" * 64)))
    return digest.hexdigest()


def repository_evidence() -> dict[str, object]:
    backend_locks = (
        ROOT / "backend" / "requirements.lock.txt",
        ROOT / "backend" / "requirements-test.lock.txt",
        ROOT / "backend" / "requirements-ci-tools.lock.txt",
    )
    return {
        "head": run_text(["git", "rev-parse", "HEAD"]),
        "status_short": run_text(["git", "status", "--short"]).splitlines(),
        "diff_stat": run_text(["git", "diff", "--stat"]).splitlines(),
        "hashes": {
            "frontend_package_lock_sha256": sha256_file(ROOT / "frontend" / "package-lock.json"),
            "backend_locks_combined_sha256": combined_sha256(backend_locks),
            "visual_baseline_manifest_sha256": sha256_file(
                ROOT / "frontend" / "tests-phase7" / "baselines" / "control-center" / "manifest.json"
            ),
        },
    }


def write_report(report: dict[str, object]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(REPORT_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="append", choices=sorted(CHECKS))
    parser.add_argument("--list", action="store_true", help="List approved check names and exit.")
    args = parser.parse_args()
    if args.list:
        print("\n".join(sorted(CHECKS)))
        return 0
    if not args.check:
        parser.error("at least one --check is required")

    report: dict[str, object] = {
        "schema_version": "elvern-final-validation-proof-v1",
        "started_at_utc": utc_now(),
        "commands": [],
        "repository_before": repository_evidence(),
    }
    for check_name in args.check:
        for specification in CHECKS[check_name]:
            argv = list(specification["argv"])
            cwd = ROOT / str(specification["cwd"])
            started_at = utc_now()
            started = time.monotonic()
            print(f"\n=== {check_name}: {' '.join(argv)} ===", flush=True)
            try:
                result = subprocess.run(argv, cwd=cwd, check=False)
                exit_code = int(result.returncode)
            except OSError as error:
                print(f"Unable to start command: {error}", file=sys.stderr, flush=True)
                exit_code = 127
            record = {
                "check": check_name,
                "argv": argv,
                "cwd": str(cwd.relative_to(ROOT) or "."),
                "started_at_utc": started_at,
                "finished_at_utc": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "exit_code": exit_code,
            }
            report["commands"].append(record)
            report["finished_at_utc"] = utc_now()
            report["repository_after"] = repository_evidence()
            write_report(report)
            if exit_code != 0:
                print(f"Validation stopped after exit code {exit_code}.", file=sys.stderr)
                return exit_code

    print(f"\nValidation proof: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
