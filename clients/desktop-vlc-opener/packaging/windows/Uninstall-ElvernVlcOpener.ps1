param(
    [switch]$Transaction,
    [string]$BootstrapState,
    [string]$BootstrapNonce
)

$ErrorActionPreference = "Stop"
$installRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:LocalAppData "Programs\Elvern VLC Opener")
)
$installParent = Split-Path -Parent $installRoot
$installedExe = Join-Path $installRoot "Elvern.VlcOpener.exe"
$installedUninstaller = Join-Path $installRoot "Uninstall-ElvernVlcOpener.ps1"
$statePath = Join-Path $installRoot "install-state.json"
$protocolKey = "Registry::HKEY_CURRENT_USER\Software\Classes\elvern-vlc"
$commandKey = "$protocolKey\shell\open\command"
$uninstallKey = "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\ElvernVlcOpener"
$installLockPath = Join-Path $installParent "Elvern VLC Opener.install.lock"
$transactionNonce = [Guid]::NewGuid().ToString("N")
$backupRoot = "$installRoot.uninstall-backup.$transactionNonce"
$recoveryRoot = Join-Path $env:TEMP "elvern-vlc-opener-uninstall-$transactionNonce"
$protocolBackup = Join-Path $recoveryRoot "protocol.reg"
$uninstallBackup = Join-Path $recoveryRoot "uninstall.reg"
$installLockHandle = $null
$installMoved = $false
$protocolOwned = $false
$uninstallOwned = $false
$protocolExisted = $false
$uninstallExisted = $false
$protocolCommandExisted = $false
$protocolCommand = $null
$uninstallLocation = $null
$uninstallDisplayName = $null
$protocolMutationStarted = $false
$uninstallMutationStarted = $false
$uninstallCommitted = $false
$rollbackSucceeded = $false

function Get-Win32ErrorCode([Exception]$Exception) {
    return ($Exception.HResult -band 0xFFFF)
}

function Invoke-InjectedFailure([string]$Point) {
    if (
        $env:ELVERN_UNINSTALL_TEST_MODE -eq "1" -and
        $env:ELVERN_UNINSTALL_TEST_FAIL_AT -eq $Point
    ) {
        throw "Injected failure at $Point."
    }
}

function Export-RegistryKey([string]$RegistryPath, [string]$OutputPath) {
    $process = Start-Process -FilePath "reg.exe" -ArgumentList @(
        "export", $RegistryPath, $OutputPath, "/y"
    ) -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "The existing per-user registration could not be backed up safely."
    }
}

function Import-RegistryKey([string]$InputPath) {
    $process = Start-Process -FilePath "reg.exe" -ArgumentList @(
        "import", $InputPath
    ) -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "A previous per-user registration could not be restored."
    }
}

if (-not $Transaction) {
    $currentScript = [IO.Path]::GetFullPath($MyInvocation.MyCommand.Path)
    $rootPrefix = $installRoot.TrimEnd("\") + "\"
    if (-not $currentScript.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Run the installed Elvern uninstaller from its canonical per-user installation."
    }
    $bootstrapRoot = Join-Path $env:TEMP "elvern-vlc-opener-uninstall-bootstrap-$transactionNonce"
    New-Item -ItemType Directory -Path $bootstrapRoot | Out-Null
    $bootstrapScript = Join-Path $bootstrapRoot "Uninstall-ElvernVlcOpener.ps1"
    $bootstrapStatePath = Join-Path $bootstrapRoot "bootstrap-state.json"
    Copy-Item -LiteralPath $currentScript -Destination $bootstrapScript
    $uninstallerHash = (Get-FileHash -LiteralPath $currentScript -Algorithm SHA256).Hash.ToLowerInvariant()
    @{
        schema_version = "elvern-uninstall-bootstrap-v1"
        nonce = $transactionNonce
        canonical_install_root = $installRoot
        installed_uninstaller_sha256 = $uninstallerHash
        expires_at_utc = [DateTime]::UtcNow.AddMinutes(2).ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $bootstrapStatePath -Encoding UTF8
    try {
        $process = Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", "`"$bootstrapScript`"",
            "-Transaction",
            "-BootstrapState", "`"$bootstrapStatePath`"",
            "-BootstrapNonce", "`"$transactionNonce`""
        ) -Wait -PassThru
        exit $process.ExitCode
    }
    finally {
        try { Remove-Item -LiteralPath $bootstrapRoot -Recurse -Force }
        catch { Write-Warning "The temporary uninstaller copy could not be removed: $bootstrapRoot" }
    }
}

try {
    if (-not $BootstrapState -or -not $BootstrapNonce) {
        throw "Protected uninstall bootstrap authorization is required."
    }
    $bootstrapStateFull = [IO.Path]::GetFullPath($BootstrapState)
    $bootstrapDirectory = Split-Path -Parent $bootstrapStateFull
    $currentTransactionScript = [IO.Path]::GetFullPath($MyInvocation.MyCommand.Path)
    if (
        (Split-Path -Parent $currentTransactionScript) -cne $bootstrapDirectory -or
        -not $bootstrapDirectory.StartsWith(
            ([IO.Path]::GetFullPath($env:TEMP).TrimEnd("\") + "\elvern-vlc-opener-uninstall-bootstrap-"),
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "The uninstall bootstrap location is invalid."
    }
    foreach ($bootstrapPath in @($bootstrapDirectory, $bootstrapStateFull, $currentTransactionScript)) {
        $bootstrapItem = Get-Item -LiteralPath $bootstrapPath -Force
        if (($bootstrapItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "The uninstall bootstrap contains an unsafe reparse point."
        }
    }
    try {
        $bootstrap = Get-Content -LiteralPath $bootstrapStateFull -Raw | ConvertFrom-Json
    }
    catch {
        throw "The uninstall bootstrap state is invalid."
    }
    $expires = [DateTime]::Parse(
        [string]$bootstrap.expires_at_utc,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
    ).ToUniversalTime()
    if (
        $bootstrap.schema_version -cne "elvern-uninstall-bootstrap-v1" -or
        [string]$bootstrap.nonce -cne $BootstrapNonce -or
        [string]$bootstrap.canonical_install_root -cne $installRoot -or
        $expires -lt [DateTime]::UtcNow -or
        $expires -gt [DateTime]::UtcNow.AddMinutes(2)
    ) {
        throw "The uninstall bootstrap authorization is invalid or expired."
    }
    foreach ($ownedPath in @($installedExe, $installedUninstaller)) {
        if (-not (Test-Path -LiteralPath $ownedPath -PathType Leaf)) {
            throw "The installed Elvern ownership files are missing."
        }
        $ownedItem = Get-Item -LiteralPath $ownedPath -Force
        if (($ownedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "An installed Elvern ownership file is unsafe."
        }
    }
    $installedUninstallerHash = (
        Get-FileHash -LiteralPath $installedUninstaller -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
        $installedUninstallerHash -cne [string]$bootstrap.installed_uninstaller_sha256 -or
        (Get-FileHash -LiteralPath $currentTransactionScript -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            [string]$bootstrap.installed_uninstaller_sha256
    ) {
        throw "The installed uninstaller does not match its bootstrap authorization."
    }
    Remove-Item -LiteralPath $bootstrapStateFull -Force
    if (Test-Path -LiteralPath $bootstrapStateFull) {
        throw "The uninstall bootstrap authorization could not be consumed."
    }

    New-Item -ItemType Directory -Path $installParent -Force | Out-Null
    try {
        $installLockHandle = New-Object System.IO.FileStream(
            $installLockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None,
            4096,
            [System.IO.FileOptions]::DeleteOnClose
        )
    }
    catch [System.UnauthorizedAccessException] {
        throw "Elvern could not create its per-user installation lock because access was denied."
    }
    catch [System.IO.IOException] {
        $lockErrorCode = Get-Win32ErrorCode $_.Exception
        if ($lockErrorCode -eq 32 -or $lockErrorCode -eq 33) {
            throw "Another Elvern VLC Opener install or uninstall is already running for this user."
        }
        throw "Elvern could not create its per-user installation lock."
    }
    $lockMetadata = [Text.Encoding]::UTF8.GetBytes(
        "pid=$PID`nstarted_at=$([DateTime]::UtcNow.ToString('o'))`ntransaction_nonce=$transactionNonce`n"
    )
    $installLockHandle.SetLength(0)
    $installLockHandle.Write($lockMetadata, 0, $lockMetadata.Length)
    $installLockHandle.Flush()

    if (-not (Test-Path -LiteralPath $installRoot)) {
        Write-Host "$installRoot is not installed."
        $uninstallCommitted = $true
        return
    }
    $installItem = Get-Item -LiteralPath $installRoot -Force
    if (
        -not $installItem.PSIsContainer -or
        (($installItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
    ) {
        throw "The installed Elvern path is not a safe directory."
    }
    $hasValidInstallState = $false
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        $stateItem = Get-Item -LiteralPath $statePath -Force
        if (($stateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "The installed ownership state is unsafe."
        }
        try {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        }
        catch {
            throw "The installed ownership state is invalid."
        }
        $stateHelperVersion = [string]$state.helper_version
        $stateTransactionNonce = [string]$state.transaction_nonce
        if (
            $state.schema_version -cne "elvern-desktop-helper-install-state-v1" -or
            $state.product_id -cne "ElvernVlcOpener" -or
            $state.package_target -cne "windows-x64" -or
            $stateHelperVersion -cnotmatch "^[A-Za-z0-9][A-Za-z0-9._+-]*$" -or
            $stateTransactionNonce -cnotmatch "^[A-Za-z0-9][A-Za-z0-9._-]*$"
        ) {
            throw "The installed ownership state does not belong to Elvern."
        }
        $hasValidInstallState = $true
    }

    $expectedCommand = "`"$installedExe`" `"%1`""
    $protocolExisted = Test-Path -LiteralPath $protocolKey
    if (Test-Path -LiteralPath $commandKey) {
        $protocolCommandExisted = $true
        $protocolCommand = (Get-Item -LiteralPath $commandKey).GetValue("")
        $protocolOwned = $protocolCommand -ceq $expectedCommand
    }
    $uninstallExisted = Test-Path -LiteralPath $uninstallKey
    if ($uninstallExisted) {
        $uninstallItem = Get-Item -LiteralPath $uninstallKey
        $uninstallLocation = [string]$uninstallItem.GetValue("InstallLocation")
        $uninstallDisplayName = [string]$uninstallItem.GetValue("DisplayName")
        $uninstallOwned = (
            $uninstallLocation -ceq $installRoot -and
            $uninstallDisplayName -ceq "Elvern VLC Opener"
        )
    }
    if (-not $hasValidInstallState -and -not ($protocolOwned -or $uninstallOwned)) {
        throw "Legacy Elvern ownership could not be proven. Remove the canonical per-user installation manually."
    }

    New-Item -ItemType Directory -Path $recoveryRoot -Force | Out-Null
    if ($protocolExisted) {
        Export-RegistryKey "HKCU\Software\Classes\elvern-vlc" $protocolBackup
    }
    if ($uninstallExisted) {
        Export-RegistryKey "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ElvernVlcOpener" $uninstallBackup
    }

    Invoke-InjectedFailure "install_move"
    Move-Item -LiteralPath $installRoot -Destination $backupRoot
    $installMoved = $true
    if ($protocolOwned) {
        Invoke-InjectedFailure "protocol_delete"
        $protocolMutationStarted = $true
        Remove-Item -LiteralPath $protocolKey -Recurse -Force
    }
    if ($uninstallOwned) {
        Invoke-InjectedFailure "uninstall_key_delete"
        $uninstallMutationStarted = $true
        Remove-Item -LiteralPath $uninstallKey -Recurse -Force
    }
    if ($protocolOwned -and (Test-Path -LiteralPath $protocolKey)) {
        throw "The owned elvern-vlc:// registration is still present."
    }
    if ($uninstallOwned -and (Test-Path -LiteralPath $uninstallKey)) {
        throw "The owned uninstall registration is still present."
    }
    if (Test-Path -LiteralPath $installRoot) {
        throw "The active Elvern installation path is still present."
    }
    Invoke-InjectedFailure "final_verification"
    $uninstallCommitted = $true
    try {
        Invoke-InjectedFailure "backup_delete"
        Remove-Item -LiteralPath $backupRoot -Recurse -Force
        $installMoved = $false
    }
    catch {
        Write-Warning "The committed uninstall backup could not be removed: $backupRoot"
    }
    Write-Host "Removed $installRoot"
}
catch {
    $uninstallError = $_.Exception.Message
    if (-not $uninstallCommitted) {
        try {
            if ($installMoved) {
                if (Test-Path -LiteralPath $installRoot) {
                    throw "The active installation path blocks rollback."
                }
                Move-Item -LiteralPath $backupRoot -Destination $installRoot
                $installMoved = $false
            }
            if ($protocolMutationStarted -and (Test-Path -LiteralPath $protocolBackup)) {
                Import-RegistryKey $protocolBackup
            }
            if ($uninstallMutationStarted -and (Test-Path -LiteralPath $uninstallBackup)) {
                Import-RegistryKey $uninstallBackup
            }
            if (-not (Test-Path -LiteralPath $installRoot -PathType Container)) {
                throw "The installation rollback could not be verified."
            }
            if ($protocolMutationStarted) {
                if (-not (Test-Path -LiteralPath $protocolKey)) {
                    throw "The protocol registration rollback could not be verified."
                }
                if ($protocolCommandExisted) {
                    if (-not (Test-Path -LiteralPath $commandKey)) {
                        throw "The protocol command rollback could not be verified."
                    }
                    $restoredCommand = (Get-Item -LiteralPath $commandKey).GetValue("")
                    if ($restoredCommand -cne $protocolCommand) {
                        throw "The protocol command rollback could not be verified."
                    }
                }
            }
            if ($uninstallMutationStarted) {
                if (-not (Test-Path -LiteralPath $uninstallKey)) {
                    throw "The uninstall registration rollback could not be verified."
                }
                $restoredUninstall = Get-Item -LiteralPath $uninstallKey
                $restoredLocation = [string]$restoredUninstall.GetValue("InstallLocation")
                $restoredDisplayName = [string]$restoredUninstall.GetValue("DisplayName")
                if (
                    $restoredLocation -cne $uninstallLocation -or
                    $restoredDisplayName -cne $uninstallDisplayName
                ) {
                    throw "The uninstall registration rollback could not be verified."
                }
            }
            $rollbackSucceeded = $true
        }
        catch {
            [Console]::Error.WriteLine(
                "Elvern VLC Opener was not removed: $uninstallError Rollback also failed: $($_.Exception.Message)"
            )
            if (Test-Path -LiteralPath $backupRoot) {
                [Console]::Error.WriteLine("Preserved installation backup: $backupRoot")
            }
            if (Test-Path -LiteralPath $recoveryRoot) {
                [Console]::Error.WriteLine("Preserved registry recovery files: $recoveryRoot")
            }
            exit 1
        }
    }
    Write-Error "Elvern VLC Opener was not removed: $uninstallError"
    exit 1
}
finally {
    if (
        ($uninstallCommitted -or $rollbackSucceeded) -and
        (Test-Path -LiteralPath $recoveryRoot)
    ) {
        try { Remove-Item -LiteralPath $recoveryRoot -Recurse -Force }
        catch { Write-Warning "The registry recovery directory could not be removed: $recoveryRoot" }
    }
    if ($null -ne $installLockHandle) {
        $installLockHandle.Dispose()
        $installLockHandle = $null
    }
}
