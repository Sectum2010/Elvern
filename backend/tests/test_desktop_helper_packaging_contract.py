from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER_ROOT = ROOT / "clients" / "desktop-vlc-opener"


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _sh(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_macos_runtime_selector_handles_native_and_rosetta() -> None:
    selectors = HELPER_ROOT / "packaging" / "common" / "platform-selectors.sh"
    cases = {
        ("0", "arm64"): "osx-arm64",
        ("0", "x86_64"): "osx-x64",
        ("1", "x86_64"): "osx-arm64",
    }
    for (translated, machine), expected in cases.items():
        result = _bash(f'source "{selectors}"; select_macos_runtime "{translated}" "{machine}"')
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    unsupported = _bash(f'source "{selectors}"; select_macos_runtime "0" "ppc64"')
    assert unsupported.returncode != 0


def test_linux_runtime_selector_handles_cpu_and_libc_matrix() -> None:
    selectors = HELPER_ROOT / "packaging" / "common" / "platform-selectors.sh"
    cases = {
        ("x86_64", "glibc"): "linux-x64",
        ("amd64", "glibc"): "linux-x64",
        ("aarch64", "glibc"): "linux-arm64",
        ("arm64", "musl"): "linux-musl-arm64",
        ("x86_64", "musl"): "linux-musl-x64",
    }
    for (machine, libc), expected in cases.items():
        result = _sh(f'. "{selectors}"; select_linux_runtime "{machine}" "{libc}"')
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    assert _sh(f'. "{selectors}"; select_linux_runtime "x86_64" "unknown"').returncode != 0
    assert _sh(f'. "{selectors}"; select_linux_runtime "ppc64" "glibc"').returncode != 0


def test_installer_scripts_are_syntactically_valid() -> None:
    bash_scripts = [
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command",
        HELPER_ROOT / "scripts" / "publish-bundles.sh",
    ]
    for script in bash_scripts:
        result = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"

    sh_scripts = [
        HELPER_ROOT / "packaging" / "common" / "platform-selectors.sh",
        HELPER_ROOT / "packaging" / "linux" / "Install-ElvernVlcOpener.sh",
        HELPER_ROOT / "packaging" / "linux" / "Uninstall-ElvernVlcOpener.sh",
    ]
    for script in sh_scripts:
        result = subprocess.run(
            ["/bin/sh", "-n", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_publish_script_keeps_standard_packages_self_contained() -> None:
    source = (HELPER_ROOT / "scripts" / "publish-bundles.sh").read_text(encoding="utf-8")

    assert 'PUBLISH_MODE="self-contained"' in source
    assert "--self-contained true" in source
    assert "PublishSingleFile=true" in source
    assert "IncludeNativeLibrariesForSelfExtract=true" in source
    assert "PublishTrimmed=false" in source
    assert "ELVERN_DOTNET_NUGET_SOURCE" in source
    assert '--source "${NUGET_SOURCE}"' in source
    assert "linux-musl-arm64" not in source
    assert "desktop_helper_package_contract --json" in source
    assert "desktop-helper-release-manifest-v2" in source


def test_package_sources_keep_visible_roots_clean_and_payloads_private() -> None:
    source = (HELPER_ROOT / "scripts" / "publish-bundles.sh").read_text(encoding="utf-8")

    assert 'cp "${PACKAGING_DIR}/macos/Install-ElvernVlcOpener.command" "${root}/"' in source
    assert 'cp "${PACKAGING_DIR}/linux/Install-ElvernVlcOpener.sh" "${root}/"' in source
    assert 'write_package_readme "${root}/README.txt"' in source
    assert 'copy_payloads "${private}"' in source
    assert 'write_inner_manifests "${private}" "macos-dual-arch"' in source
    assert 'write_tree_manifest "${root}"' in source
    assert 'cp "${PACKAGING_DIR}/macos/ElvernVlcOpener.applescript" "${private}/bridge/"' in source
    assert 'cp "${PACKAGING_DIR}/linux/Uninstall-ElvernVlcOpener.sh" "${private}/uninstall/"' in source
    assert source.index("if path.is_symlink():") < source.index("if path.is_dir():")


def test_macos_installer_trust_commands_are_scoped_to_elvern_apps() -> None:
    source = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    assert 'codesign --force --sign - "${APP_PAYLOAD_DIR}/Elvern.VlcOpener"' in source
    assert 'codesign --force --sign - "${STAGED_APP}"' in source
    assert 'codesign --verify --deep --strict "${STAGED_APP}"' in source
    assert 'xattr -dr com.apple.quarantine "${STAGED_APP}"' in source
    assert 'xattr -dr com.apple.quarantine "${DEST_APP}"' in source
    assert 'verify_quarantine_cleared "${STAGED_APP}"' in source
    assert 'verify_quarantine_cleared "${DEST_APP}"' in source
    assert '|| fail "macOS quarantine could not be removed' in source
    assert "spctl --master-disable" not in source
    assert "sudo" not in source
    assert 'xattr -dr com.apple.quarantine "${HOME}/Applications"' not in source
    assert 'xattr -dr com.apple.quarantine "${HOME}/Downloads"' not in source
    assert "mapfile" not in source


def test_macos_installer_generates_versioned_info_plist_for_macos_14() -> None:
    source = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    assert 'plist_set "CFBundleShortVersionString" string "${HELPER_VERSION}"' in source
    assert 'plist_set "CFBundleVersion" string "${HELPER_VERSION}"' in source
    assert 'plist_set "LSMinimumSystemVersion" string "14.0"' in source


def test_installers_verify_the_full_tree_before_using_package_code() -> None:
    mac = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")
    linux = (
        HELPER_ROOT / "packaging" / "linux" / "Install-ElvernVlcOpener.sh"
    ).read_text(encoding="utf-8")
    windows = (
        HELPER_ROOT / "packaging" / "windows" / "Install-ElvernVlcOpener.ps1"
    ).read_text(encoding="utf-8")

    assert mac.index("verify_package_tree") < mac.index('source "${SELECTORS}"')
    assert linux.index("verify_package_tree") < linux.index('. "${SELECTORS}"')
    assert windows.index("Test-InstallerTree") < windows.index(
        "$installerManifest = Read-StrictInstallerManifest"
    )
    assert "python" not in mac.lower()
    assert "python" not in linux.lower()
    assert '"${SOURCE_PAYLOAD}" --version' not in mac
    assert '"${PAYLOAD}" --version' not in linux
    assert "& $sourceExe --version" not in windows
    assert "$item.LinkType" in windows
    assert "[IO.FileAttributes]::ReparsePoint" in windows
    assert "Unblock-File -LiteralPath $stagedExe" in windows
    assert "Unblock-File -LiteralPath $stagedUninstaller" in windows


def test_windows_installer_checks_registry_backup_and_restore_failures() -> None:
    source = (
        HELPER_ROOT / "packaging" / "windows" / "Install-ElvernVlcOpener.ps1"
    ).read_text(encoding="utf-8")

    assert "function Export-RegistryKey" in source
    assert "The existing per-user registration could not be backed up safely." in source
    assert "The previous elvern-vlc:// registration could not be restored." in source
    assert "The previous uninstall registration could not be restored." in source
    assert "Registry rollback also failed:" in source
    assert "Preserved registry recovery files:" in source
    assert "Preserved previous installation backup:" in source
    assert "( $installCommitted -or $rollbackSucceeded )" in source


def test_windows_installer_uses_a_powershell_51_exclusive_per_user_file_lock() -> None:
    source = (
        HELPER_ROOT / "packaging" / "windows" / "Install-ElvernVlcOpener.ps1"
    ).read_text(encoding="utf-8")

    assert "Elvern VLC Opener.install.lock" in source
    assert "[System.IO.FileMode]::OpenOrCreate" in source
    assert "[System.IO.FileAccess]::ReadWrite" in source
    assert "[System.IO.FileShare]::None" in source
    assert "[System.IO.FileOptions]::DeleteOnClose" in source
    assert (
        "Another Elvern VLC Opener install or uninstall is already running for this user."
        in source
    )
    assert "Get-Win32ErrorCode" in source
    assert "$lockErrorCode -eq 32 -or $lockErrorCode -eq 33" in source
    assert (
        "Elvern could not create its per-user installation lock because access was denied."
        in source
    )
    assert (
        "Elvern could not create its per-user installation lock because the installation path is unavailable."
        in source
    )
    assert "Elvern could not create its per-user installation lock." in source
    assert source.index("$installLockHandle = New-Object System.IO.FileStream") < source.index(
        "\n    Test-InstallerTree\n"
    )
    assert "$installLockHandle.Dispose()" in source
    assert "Remove-Item -LiteralPath $installLockPath" not in source


def test_windows_installer_is_powershell_51_safe_and_uses_strict_tsv_contract() -> None:
    source = (
        HELPER_ROOT / "packaging" / "windows" / "Install-ElvernVlcOpener.ps1"
    ).read_text(encoding="utf-8")

    assert "[IO.Path]::GetRelativePath" not in source
    assert "pwsh" not in source.lower()
    assert "Read-StrictInstallerManifest" in source
    assert 'path`tsize_bytes`tsha256`tfile_class' in source
    assert "repeats mandatory metadata" in source
    assert "repeats a runtime or payload path" in source
    assert source.index('Invoke-InjectedFailure "first_backup_move"') < source.index(
        "$oldInstallBackedUp = $true"
    )
    assert source.index('Invoke-InjectedFailure "registration"') < source.index(
        "$registrationModified = $true"
    )


def test_macos_transaction_commits_before_finder_reveal_and_backup_flag_is_safe() -> None:
    source = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    assert "REPLACEMENT_STARTED" not in source
    assert source.index('inject_failure "first_backup_move"') < source.index(
        "OLD_INSTALL_BACKED_UP=1"
    )
    assert source.index("INSTALL_COMMITTED=1") < source.index('open -R "${DEST_APP}"')
    assert "Warning: Finder could not reveal" in source
    assert 'xattr -dr com.apple.quarantine "${DEST_APP}"' in source
    assert '"${LSREGISTER}" -u "${DEST_APP}"' in source
    assert source.index('"${LSREGISTER}" -u "${DEST_APP}"') < source.index(
        'rm -rf "${DEST_APP}"'
    )
    assert '/bin/cp -a "${BACKUP_APP}" "${DEST_APP}"' in source
    assert "lsregister -kill" not in source.lower()
    assert "Preserved rollback workspace:" in source
    assert "LOCK_HELD=0" in source
    assert "LOCK_HELD=1" in source
    assert "lock_is_owned()" in source
    assert 'grep -F -x "transaction_nonce=${INSTALL_NONCE}"' in source
    assert "cleanup_failure_injected \"stage\"" in source
    assert "cleanup_failure_injected \"backup\"" in source
    assert "cleanup_failure_injected \"lock\"" in source
    assert "Warning: committed staged App cleanup failed:" in source
    assert "Warning: committed previous App backup cleanup failed:" in source


def test_macos_backup_targets_are_prepared_before_unregister_or_move() -> None:
    installer = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")
    uninstaller = (
        HELPER_ROOT / "packaging" / "macos" / "Uninstall-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    for source in (installer, uninstaller):
        assert "prepare_backup_target()" in source
        assert '[[ -d "${DEST_DIR}" && ! -L "${DEST_DIR}"' in source
        assert '[[ ! -e "${candidate}" && ! -L "${candidate}"' in source
        assert 'while [[ ${attempt} -lt 8 ]]' in source
        assert '"$(dirname "${candidate}")" == "${DEST_DIR}"' in source
    assert uninstaller.index(
        'BACKUP_APP="$(prepare_backup_target ".elvern-vlc-opener-uninstall-backup")"'
    ) < uninstaller.index('"${LSREGISTER}" -u "${DEST_APP}"')
    assert installer.index(
        'BACKUP_APP="$(prepare_backup_target ".elvern-vlc-opener-backup")"'
    ) < installer.index('mv "${DEST_APP}" "${BACKUP_APP}"')
    assert "lsregister -kill" not in uninstaller.lower()


def test_linux_installer_uses_one_xdg_data_root_and_preserves_failed_rollback_materials() -> None:
    source = (
        HELPER_ROOT / "packaging" / "linux" / "Install-ElvernVlcOpener.sh"
    ).read_text(encoding="utf-8")

    assert 'DESKTOP_DIR="${XDG_DATA_ROOT}/applications"' in source
    assert 'MIME_DATA_FILE="${DESKTOP_DIR}/mimeapps.list"' in source
    assert 'DESKTOP_DIR="${HOME}/.local/share/applications"' not in source
    assert "Preserved MIME and desktop registration backups:" in source
    assert 'cp -a "${BACKUP_DIR}" "${INSTALL_DIR}"' in source
    assert "LOCK_HELD=0" in source
    assert "LOCK_HELD=1" in source
    assert "lock_is_owned()" in source
    assert 'grep -F -x "transaction_nonce=${INSTALL_NONCE}"' in source


def test_publish_requires_explicit_activation_and_immutable_artifact_names() -> None:
    source = (HELPER_ROOT / "scripts" / "publish-bundles.sh").read_text(encoding="utf-8")

    assert 'ACTIVATE=0' in source
    assert '--activate' in source
    assert 'ALLOW_PARTIAL_ACTIVATE=0' in source
    assert '--allow-partial-activate' in source
    assert (
        'PACKAGE_CONTRACT="${REPO_ROOT}/elvern_shared/desktop_helper_package_contract.py"'
        in source
    )
    assert 'PYTHONPATH="${REPO_ROOT}" python3 "${PACKAGE_CONTRACT}"' in source
    assert "--active-dir" in source
    assert 'ACTIVE_DIR="${ACTIVE_DIR_CLI:-${ELVERN_HELPER_RELEASES_DIR:-}}"' in source
    assert "--activate requires --active-dir or ELVERN_HELPER_RELEASES_DIR." in source
    assert "--replace-corrupt-active-manifest" in source
    assert "Active desktop helper manifest is invalid; activation was not attempted." in source
    assert "release-manifest.corrupt-${corrupt_digest:0:12}.json" in source
    assert "Immutable active artifact collision:" in source
    assert 'mv "${manifest_temp}" "${ACTIVE_DIR}/release-manifest.json"' in source
    assert 'mkdir "${lock_dir}"' in source
    assert 'ACTIVATION_LOCK_DIR="${lock_dir}"' in source
    assert "authority_lock_is_owned()" in source
    assert "release_authority_lock()" in source
    assert 'grep -F -x "transaction_nonce=${BUILD_ID}"' in source


def test_shared_package_prefix_has_one_runtime_authority() -> None:
    shared = (
        ROOT / "elvern_shared" / "desktop_helper_package_contract.py"
    ).read_text(encoding="utf-8")
    metadata = (
        HELPER_ROOT / "packaging" / "helper-release.env"
    ).read_text(encoding="utf-8")
    backend = (
        ROOT
        / "backend"
        / "app"
        / "services"
        / "desktop_helper_manifest_service.py"
    ).read_text(encoding="utf-8")
    validator = (
        HELPER_ROOT / "scripts" / "validate-package.py"
    ).read_text(encoding="utf-8")
    publisher = (
        HELPER_ROOT / "scripts" / "publish-bundles.sh"
    ).read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'PACKAGE_NAME_PREFIX = "elvern-vlc-opener"' in shared
    assert "PACKAGE_NAME_PREFIX" not in metadata
    assert "from elvern_shared.desktop_helper_package_contract import" in backend
    assert "from elvern_shared.desktop_helper_package_contract import" in validator
    assert "desktop_helper_package_contract --json" in publisher
    assert "PACKAGE_RUNTIME_CONTRACTS" in validator
    assert "linux-musl-arm64" not in validator
    assert "linux-musl-arm64" not in publisher
    assert "COPY elvern_shared ./elvern_shared" in dockerfile
    assert not (ROOT / "clients" / "desktop_helper_package_contract.py").exists()


def test_macos_install_and_uninstall_cleanup_use_nonce_owned_targets() -> None:
    installer = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")
    uninstaller = (
        HELPER_ROOT / "packaging" / "macos" / "Uninstall-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    assert 'grep -F -x "transaction_nonce=${INSTALL_NONCE}"' in installer
    assert 'grep -F -x "transaction_nonce=${TRANSACTION_NONCE}"' in uninstaller
    for source in (installer, uninstaller):
        assert "lock_is_owned()" in source
        assert "backup_is_owned()" in source
        assert "transaction-owner" in source
        assert '&& ! -L "${LOCK_DIR}"' in source
        assert '&& ! -L "${LOCK_DIR}/owner"' in source
    assert "INSTALL_COMMITTED=1" in installer
    assert "UNINSTALL_COMMITTED=1" in uninstaller
    assert "Warning: install lock cleanup failed:" in installer
    assert "Warning: uninstall lock cleanup failed:" in uninstaller


def test_windows_committed_backup_cleanup_is_warning_only() -> None:
    source = (
        HELPER_ROOT / "packaging" / "windows" / "Install-ElvernVlcOpener.ps1"
    ).read_text(encoding="utf-8")

    commit_index = source.index("$installCommitted = $true")
    warning_index = source.index(
        'Write-Warning "The committed previous installation backup could not be removed:'
    )
    catch_index = source.rfind("catch {", commit_index, warning_index)
    assert commit_index < catch_index < warning_index


def test_uninstallers_share_installer_locks_and_transaction_boundaries() -> None:
    linux = (
        HELPER_ROOT / "packaging" / "linux" / "Uninstall-ElvernVlcOpener.sh"
    ).read_text(encoding="utf-8")
    mac = (
        HELPER_ROOT / "packaging" / "macos" / "Uninstall-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")
    windows = (
        HELPER_ROOT / "packaging" / "windows" / "Uninstall-ElvernVlcOpener.ps1"
    ).read_text(encoding="utf-8")
    mac_installer = (
        HELPER_ROOT / "packaging" / "macos" / "Install-ElvernVlcOpener.command"
    ).read_text(encoding="utf-8")

    assert '.elvern-vlc-opener-install.lock"' in linux
    assert '.elvern-vlc-opener-install.lock"' in mac
    assert '"Elvern VLC Opener.install.lock"' in windows
    assert "install-state.tsv" in linux
    assert "install-state.plist" in mac
    assert "install-state.json" in windows
    assert 'package_target) package_target=${value}' in linux
    assert '[ "${package_target}" = "linux-universal" ]' in linux
    assert 'STATE_PACKAGE_TARGET="$("${PLISTBUDDY}"' in mac
    assert '"macos-dual-arch"' in mac
    assert '$state.package_target -cne "windows-x64"' in windows
    assert "$protocolMutationStarted" in windows
    assert "$uninstallMutationStarted" in windows
    assert "xdg-mime uninstall" not in linux
    assert "CURRENT_DEFAULT" in linux and "PREVIOUS_DEFAULT" in linux
    assert 'rm -rf "${INSTALL_DIR}"' not in linux
    assert '"${LSREGISTER}" -u "${DEST_APP}"' in mac
    assert '|| true' not in mac
    assert "lsregister -kill" not in mac.lower()
    assert "[System.IO.FileShare]::None" in windows
    assert "powershell.exe" in windows
    assert "SourceInstallRoot" not in windows
    assert "Protected uninstall bootstrap authorization is required." in windows
    assert "elvern-uninstall-bootstrap-v1" in windows
    assert "installed_uninstaller_sha256" in windows
    assert "$uninstallCommitted = $true" in windows
    assert windows.index("$uninstallCommitted = $true") < windows.index(
        'Invoke-InjectedFailure "backup_delete"'
    )
    assert "Import-RegistryKey" in windows
    assert "[IO.Path]::GetRelativePath" not in windows
    assert "pwsh" not in windows.lower()
    assert 'UNINSTALL_SOURCE="${PRIVATE_DIR}/uninstall/Uninstall-ElvernVlcOpener.command"' in mac_installer
    assert 'cp "${UNINSTALL_SOURCE}" "${RESOURCES_DIR}/Uninstall-ElvernVlcOpener.command"' in mac_installer
    assert mac_installer.index('cp "${UNINSTALL_SOURCE}"') < mac_installer.index(
        'codesign --force --sign - "${STAGED_APP}"'
    )
    assert mac.index("UNINSTALL_COMMITTED=1") < mac.index(
        'rm -rf "${BACKUP_APP}"'
    )


def test_docker_runtime_copies_shared_contract_and_mounts_helper_releases() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts" / "docker-smoke.sh").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY elvern_shared ./elvern_shared" in dockerfile
    assert "COPY clients" not in dockerfile
    assert "ELVERN_HELPER_RELEASES_DIR: /data/helper_releases" in compose
    assert "./docker-data/data:/data" in compose
    assert 'mkdir -p "${ELVERN_HELPER_RELEASES_DIR}"' in entrypoint
    assert 'docker build --tag "${IMAGE}" "${PROJECT_ROOT}"' in smoke
    assert "import backend.app.main" in smoke
    assert "elvern_shared.desktop_helper_package_contract" in smoke
    assert "/data/helper_releases" in smoke
    assert "/health" in smoke
    assert "/_elvern/frontend-health" in smoke
    assert "The empty Helper release mount was unexpectedly modified." in smoke
    assert "trap cleanup EXIT" in smoke
    assert "--env-file" not in smoke
    for ignored in (
        "tmp/",
        "frontend/test-results/",
        "frontend/playwright-report/",
        "frontend/blob-report/",
        "frontend/tmp/",
    ):
        assert ignored in dockerignore


def test_ci_keeps_docker_and_production_browser_jobs_independent() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    browser_config = (
        ROOT / "frontend" / "playwright.cross-browser.config.js"
    ).read_text(encoding="utf-8")
    browser_fixture = (
        ROOT / "frontend" / "tests-phase7" / "phase7-cross-browser.pw.js"
    ).read_text(encoding="utf-8")

    docker_job = workflow.split("  docker_smoke:", 1)[1].split(
        "  production_browser_tests:", 1
    )[0]
    browser_job = workflow.split("  production_browser_tests:", 1)[1]
    assert "needs:" not in docker_job
    assert "needs:" not in browser_job
    assert "./scripts/docker-smoke.sh" in docker_job
    assert "npx playwright install --with-deps chromium firefox" in browser_job
    assert "--project chromium-desktop-production" in browser_job
    assert "--project firefox-desktop-production" in browser_job
    assert "node scripts/build-phase7-production.mjs" in browser_job
    assert "--use-existing-build" in browser_job
    assert "tmp/playwright-phase7-*" in browser_job
    assert 'name: "chromium-desktop-production"' in browser_config
    assert 'name: "firefox-desktop-production"' in browser_config
    assert 'name: "chromium-service-worker-network-guard"' in browser_config
    assert 'name: "firefox-service-worker-network-guard"' in browser_config
    assert 'serviceWorkers: "block"' in browser_config
    assert 'serviceWorkers: "allow"' in browser_config
    assert "ELVERN_PHASE7_NETWORK_PROXY" in browser_config
    assert "--project chromium-service-worker-network-guard" in browser_job
    assert "--project firefox-service-worker-network-guard" in browser_job
    for public_probe in (
        "https://www.cloudflare.com/cdn-cgi/trace",
        "https://api64.ipify.org/",
        "https://httpbin.org/status/204",
    ):
        assert public_probe in browser_fixture
    assert "await page.route(probe" in browser_fixture
    assert "installExternalNetworkGuard" in browser_fixture
    assert "registerInterceptedExternalFixture" in browser_fixture
    assert 'route.abort("blockedbyclient")' in browser_fixture
    assert "externalRequests" in browser_fixture
