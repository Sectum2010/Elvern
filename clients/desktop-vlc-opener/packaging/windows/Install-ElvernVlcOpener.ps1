param()

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$packageRoot = Split-Path -Parent $scriptRoot
$privateRoot = Join-Path $packageRoot ".elvern"
$manifestPath = Join-Path $privateRoot "manifest.json"
$installerManifestPath = Join-Path $privateRoot "installer-manifest.tsv"
$treeManifestPath = Join-Path $privateRoot "tree-manifest.tsv"
$installParent = Join-Path $env:LocalAppData "Programs"
$installRoot = Join-Path $env:LocalAppData "Programs\Elvern VLC Opener"
$installLockPath = Join-Path $installParent "Elvern VLC Opener.install.lock"
$transactionNonce = [Guid]::NewGuid().ToString("N")
$stagingRoot = "$installRoot.staging.$transactionNonce"
$backupRoot = "$installRoot.backup.$transactionNonce"
$installedExe = Join-Path $installRoot "Elvern.VlcOpener.exe"
$protocolKey = "Registry::HKEY_CURRENT_USER\Software\Classes\elvern-vlc"
$commandKey = "$protocolKey\shell\open\command"
$uninstallKey = "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\ElvernVlcOpener"
$registryBackupRoot = Join-Path $env:TEMP "elvern-vlc-opener-registry-$transactionNonce"
$protocolBackup = Join-Path $registryBackupRoot "protocol.reg"
$uninstallBackup = Join-Path $registryBackupRoot "uninstall.reg"
$protocolExisted = Test-Path -LiteralPath $protocolKey
$uninstallExisted = Test-Path -LiteralPath $uninstallKey
$stagingCreated = $false
$oldInstallExisted = Test-Path -LiteralPath $installRoot
$oldInstallBackedUp = $false
$newInstallPlaced = $false
$oldRegistrationCaptured = $false
$registrationModified = $false
$finalValidationPassed = $false
$installCommitted = $false
$rollbackSucceeded = $false
$installLockHandle = $null

function Invoke-InjectedFailure([string]$Point) {
    if ($env:ELVERN_INSTALL_TEST_MODE -eq "1" -and $env:ELVERN_INSTALL_TEST_FAIL_AT -eq $Point) {
        throw "Injected failure at $Point."
    }
}

function Get-Win32ErrorCode([Exception]$Exception) {
    return ($Exception.HResult -band 0xFFFF)
}

function Test-SafeRelativePath([string]$Value) {
    if (
        [string]::IsNullOrWhiteSpace($Value) -or
        [IO.Path]::IsPathRooted($Value) -or
        $Value.Contains("\") -or
        $Value.Contains(":") -or
        $Value.IndexOfAny([char[]]@(0, 9, 10, 13)) -ge 0
    ) {
        return $false
    }
    return -not ($Value -match '(^|[\\/])\.{1,2}([\\/]|$)')
}

function Get-SafePackageRelativePath([string]$FullPath) {
    $rootFull = [IO.Path]::GetFullPath($packageRoot).TrimEnd("\") + "\"
    $candidateFull = [IO.Path]::GetFullPath($FullPath)
    if (-not $candidateFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The installer package contains a path outside its root."
    }
    $relative = $candidateFull.Substring($rootFull.Length).Replace("\", "/")
    if (-not (Test-SafeRelativePath $relative)) {
        throw "The installer package contains an unsafe path."
    }
    return $relative
}

function Test-InstallerTree {
    if (-not (Test-Path -LiteralPath $treeManifestPath -PathType Leaf)) {
        throw "The installer tree manifest is missing."
    }
    foreach ($item in Get-ChildItem -LiteralPath $packageRoot -Recurse -Force) {
        if (
            $item.LinkType -or
            (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
        ) {
            throw "The installer package contains an unsafe link."
        }
    }
    $expected = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::Ordinal)
    $expectedCaseFolded = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::OrdinalIgnoreCase)
    $lines = Get-Content -LiteralPath $treeManifestPath
    if ($lines.Count -lt 2 -or $lines[0] -cne "path`tsize_bytes`tsha256`tfile_class") {
        throw "The installer tree manifest header is invalid or the manifest is empty."
    }
    foreach ($line in $lines | Select-Object -Skip 1) {
        $fields = @($line -split "`t", -1)
        if ($fields.Count -ne 4) {
            throw "The installer tree manifest has an invalid row."
        }
        $relativePath, $sizeText, $expectedHash, $fileClass = $fields
        if (
            -not (Test-SafeRelativePath $relativePath) -or
            $sizeText -notmatch '^[0-9]+$' -or
            [decimal]$sizeText -gt 2147483648 -or
            $expectedHash -notmatch '^[0-9a-f]{64}$' -or
            $fileClass -notmatch '^(data|executable)$'
        ) {
            throw "The installer tree manifest contains invalid metadata."
        }
        if (-not $expected.Add($relativePath) -or -not $expectedCaseFolded.Add($relativePath)) {
            throw "The installer tree manifest contains a duplicate or case-colliding path."
        }
        $normalizedRelative = $relativePath.Replace("/", "\")
        $fullPath = [IO.Path]::GetFullPath((Join-Path $packageRoot $normalizedRelative))
        $rootPrefix = [IO.Path]::GetFullPath($packageRoot).TrimEnd('\') + '\'
        if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "The installer tree manifest contains an unsafe path."
        }
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            throw "An installer file is missing."
        }
        $item = Get-Item -LiteralPath $fullPath
        if ($item.LinkType) {
            throw "The installer package contains a link."
        }
        if ($item.Length -ne [long]$sizeText) {
            throw "An installer file size check failed."
        }
        $actualHash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $expectedHash) {
            throw "An installer file SHA-256 check failed."
        }
    }
    $actual = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::Ordinal)
    foreach ($file in Get-ChildItem -LiteralPath $packageRoot -Recurse -File) {
        $relative = Get-SafePackageRelativePath $file.FullName
        if ($relative -eq ".elvern/tree-manifest.tsv" -or $file.Name -eq ".DS_Store") {
            continue
        }
        [void]$actual.Add($relative)
    }
    if (-not $expected.SetEquals($actual)) {
        throw "The installer package contains a missing or unexpected file."
    }
}

function Restore-RegistryState {
    if (Test-Path -LiteralPath $protocolKey) {
        Remove-Item -LiteralPath $protocolKey -Recurse -Force
    }
    if (Test-Path -LiteralPath $uninstallKey) {
        Remove-Item -LiteralPath $uninstallKey -Recurse -Force
    }
    if ($protocolExisted -and (Test-Path -LiteralPath $protocolBackup)) {
        & reg.exe import $protocolBackup | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "The previous elvern-vlc:// registration could not be restored."
        }
    }
    if ($uninstallExisted -and (Test-Path -LiteralPath $uninstallBackup)) {
        & reg.exe import $uninstallBackup | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "The previous uninstall registration could not be restored."
        }
    }
    if ($protocolExisted -ne (Test-Path -LiteralPath $protocolKey)) {
        throw "The previous elvern-vlc:// registration restore could not be verified."
    }
    if ($uninstallExisted -ne (Test-Path -LiteralPath $uninstallKey)) {
        throw "The previous uninstall registration restore could not be verified."
    }
}

function Export-RegistryKey([string]$RegistryPath, [string]$Destination) {
    & reg.exe export $RegistryPath $Destination /y | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        throw "The existing per-user registration could not be backed up safely."
    }
}

function Read-StrictInstallerManifest {
    if (-not (Test-Path -LiteralPath $installerManifestPath -PathType Leaf)) {
        throw "The verified installer manifest is missing."
    }
    $requiredMeta = @(
        "schema_version", "helper_version", "target_framework", "runtime_family",
        "deployment_mode", "package_target", "bound_origin_sha256"
    )
    $metadata = @{}
    $payloads = @()
    $runtimeIds = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::Ordinal)
    $payloadPaths = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::OrdinalIgnoreCase)
    $lines = @(Get-Content -LiteralPath $installerManifestPath)
    if ($lines.Count -eq 0) {
        throw "The verified installer manifest is empty."
    }
    foreach ($line in $lines) {
        $fields = @($line.Split([char[]]@([char]9), [StringSplitOptions]::None))
        if ($fields.Count -eq 3 -and $fields[0] -ceq "meta") {
            $name = $fields[1]
            $value = $fields[2]
            if ($requiredMeta -notcontains $name -or [string]::IsNullOrEmpty($value)) {
                throw "The verified installer manifest contains unknown or invalid metadata."
            }
            if ($metadata.ContainsKey($name)) {
                throw "The verified installer manifest repeats mandatory metadata."
            }
            $metadata[$name] = $value
            continue
        }
        if ($fields.Count -eq 6 -and $fields[0] -ceq "payload") {
            $runtimeId, $relativePath, $sha256, $sizeText, $executableName = $fields[1..5]
            if (
                [string]::IsNullOrEmpty($runtimeId) -or
                -not (Test-SafeRelativePath $relativePath) -or
                $sha256 -cnotmatch '^[0-9a-f]{64}$' -or
                $sizeText -notmatch '^[0-9]+$' -or
                [decimal]$sizeText -gt 2147483648 -or
                $executableName -cne "Elvern.VlcOpener.exe"
            ) {
                throw "The verified installer manifest contains invalid payload metadata."
            }
            if (-not $runtimeIds.Add($runtimeId) -or -not $payloadPaths.Add($relativePath)) {
                throw "The verified installer manifest repeats a runtime or payload path."
            }
            $payloads += [pscustomobject]@{
                runtime_id = $runtimeId
                relative_path = $relativePath
                sha256 = $sha256
                size_bytes = [long]$sizeText
                executable_name = $executableName
            }
            continue
        }
        throw "The verified installer manifest has an invalid row."
    }
    foreach ($name in $requiredMeta) {
        if (-not $metadata.ContainsKey($name)) {
            throw "The verified installer manifest is missing mandatory metadata."
        }
    }
    return [pscustomobject]@{
        metadata = $metadata
        payloads = $payloads
    }
}

try {
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
    catch [System.Security.SecurityException] {
        throw "Elvern could not create its per-user installation lock because access was denied."
    }
    catch [System.IO.DirectoryNotFoundException] {
        throw "Elvern could not create its per-user installation lock because the installation path is unavailable."
    }
    catch [System.IO.PathTooLongException] {
        throw "Elvern could not create its per-user installation lock because the installation path is unavailable."
    }
    catch [System.ArgumentException] {
        throw "Elvern could not create its per-user installation lock because the installation path is unavailable."
    }
    catch [System.NotSupportedException] {
        throw "Elvern could not create its per-user installation lock because the installation path is unavailable."
    }
    catch [System.IO.IOException] {
        $lockErrorCode = Get-Win32ErrorCode $_.Exception
        if ($lockErrorCode -eq 32 -or $lockErrorCode -eq 33) {
            throw "Another Elvern VLC Opener install is already running for this user."
        }
        throw "Elvern could not create its per-user installation lock."
    }
    catch {
        throw "Elvern could not create its per-user installation lock."
    }
    $lockMetadata = [Text.Encoding]::UTF8.GetBytes(
        "pid=$PID`nstarted_at=$([DateTime]::UtcNow.ToString('o'))`ntransaction_nonce=$transactionNonce`n"
    )
    $installLockHandle.SetLength(0)
    $installLockHandle.Write($lockMetadata, 0, $lockMetadata.Length)
    $installLockHandle.Flush()

    Test-InstallerTree
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "The verified JSON package metadata is missing."
    }
    $installerManifest = Read-StrictInstallerManifest
    $manifest = $installerManifest.metadata
    if ($manifest["schema_version"] -cne "desktop-helper-installer-manifest-v2") {
        throw "The verified installer manifest schema is unsupported."
    }
    if (
        $manifest["deployment_mode"] -cne "self_contained" -or
        $manifest["package_target"] -cne "windows-x64"
    ) {
        throw "This is not the standard self-contained Windows package."
    }
    if (
        $manifest["target_framework"] -cne "net10.0" -or
        $manifest["runtime_family"] -cne "10.0" -or
        [string]$manifest["bound_origin_sha256"] -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$manifest["helper_version"] -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$'
    ) {
        throw "The verified installer manifest runtime or origin contract is invalid."
    }
    $helperVersion = [string]$manifest["helper_version"]
    $payloads = @($installerManifest.payloads | Where-Object { $_.runtime_id -ceq "win-x64" })
    if ($payloads.Count -ne 1) {
        throw "The win-x64 payload is missing or duplicated."
    }
    $payload = $payloads[0]
    if ($installerManifest.payloads.Count -ne 1) {
        throw "The Windows package contains an unexpected runtime payload."
    }
    $relativePath = [string]$payload.relative_path
    if (-not (Test-SafeRelativePath $relativePath)) {
        throw "The payload path is unsafe."
    }
    $sourceExe = Join-Path $privateRoot ($relativePath -replace '/', '\')
    if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
        throw "The self-contained win-x64 executable is missing."
    }
    $sourceItem = Get-Item -LiteralPath $sourceExe
    if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "The self-contained win-x64 executable is an unsafe reparse point."
    }
    $actualHash = (Get-FileHash -LiteralPath $sourceExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$payload.sha256).ToLowerInvariant()) {
        throw "The Helper payload SHA-256 check failed."
    }
    if ((Get-Item -LiteralPath $sourceExe).Length -ne [long]$payload.size_bytes) {
        throw "The Helper payload size check failed."
    }
    $uninstallSource = Join-Path $privateRoot "uninstall\Uninstall-ElvernVlcOpener.ps1"
    if (-not (Test-Path -LiteralPath $uninstallSource -PathType Leaf)) {
        throw "The verified Windows uninstaller is missing."
    }

    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    $stagingCreated = $true
    Invoke-InjectedFailure "staging_created"
    $stagedExe = Join-Path $stagingRoot "Elvern.VlcOpener.exe"
    Copy-Item -LiteralPath $sourceExe -Destination $stagedExe -Force
    $stagedUninstaller = Join-Path $stagingRoot "Uninstall-ElvernVlcOpener.ps1"
    Copy-Item -LiteralPath $uninstallSource -Destination $stagedUninstaller -Force
    Unblock-File -LiteralPath $stagedExe
    Unblock-File -LiteralPath $stagedUninstaller
    & $stagedExe --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The staged Helper did not pass its version check."
    }

    New-Item -ItemType Directory -Path $registryBackupRoot -Force | Out-Null
    if ($protocolExisted) {
        Export-RegistryKey "HKCU\Software\Classes\elvern-vlc" $protocolBackup
    }
    if ($uninstallExisted) {
        Export-RegistryKey "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ElvernVlcOpener" $uninstallBackup
    }
    $oldRegistrationCaptured = $true

    if ($oldInstallExisted) {
        Invoke-InjectedFailure "first_backup_move"
        Move-Item -LiteralPath $installRoot -Destination $backupRoot
        $oldInstallBackedUp = $true
    }
    Invoke-InjectedFailure "new_placement"
    Move-Item -LiteralPath $stagingRoot -Destination $installRoot
    $newInstallPlaced = $true

    $protocolCommand = "`"$installedExe`" `"%1`""
    Invoke-InjectedFailure "registration"
    New-Item -Path $protocolKey -Force | Out-Null
    $registrationModified = $true
    Set-Item -Path $protocolKey -Value "URL:Elvern VLC Opener"
    New-ItemProperty -Path $protocolKey -Name "URL Protocol" -Value "" -Force | Out-Null
    New-Item -Path $commandKey -Force | Out-Null
    Set-Item -Path $commandKey -Value $protocolCommand

    $installedUninstall = Join-Path $installRoot "Uninstall-ElvernVlcOpener.ps1"
    New-Item -Path $uninstallKey -Force | Out-Null
    Set-ItemProperty -Path $uninstallKey -Name "DisplayName" -Value "Elvern VLC Opener"
    Set-ItemProperty -Path $uninstallKey -Name "DisplayVersion" -Value $helperVersion
    Set-ItemProperty -Path $uninstallKey -Name "Publisher" -Value "Elvern"
    Set-ItemProperty -Path $uninstallKey -Name "InstallLocation" -Value $installRoot
    Set-ItemProperty -Path $uninstallKey -Name "UninstallString" -Value "powershell.exe -ExecutionPolicy Bypass -File `"$installedUninstall`""

    Invoke-InjectedFailure "registration_validation"
    $registeredCommand = (Get-Item -LiteralPath $commandKey).GetValue("")
    if ($registeredCommand -ne $protocolCommand) {
        throw "The elvern-vlc:// registration could not be verified."
    }
    Invoke-InjectedFailure "final_binary_validation"
    & $installedExe --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The installed Helper failed its final version check."
    }
    $finalValidationPassed = $true
    $installCommitted = $true
    if ($oldInstallBackedUp -and (Test-Path -LiteralPath $backupRoot)) {
        Remove-Item -LiteralPath $backupRoot -Recurse -Force
    }
    Write-Host "Installed Elvern VLC Opener $helperVersion into $installRoot"
    Write-Host "Registered elvern-vlc:// for this user. No separate .NET installation is required."
}
catch {
    $installError = $_.Exception.Message
    if (-not $installCommitted) {
        try {
            if ($newInstallPlaced -and (Test-Path -LiteralPath $installRoot)) {
                Remove-Item -LiteralPath $installRoot -Recurse -Force
            }
            if ($oldInstallBackedUp) {
                if (Test-Path -LiteralPath $installRoot) {
                    throw "The new installation could not be removed before rollback."
                }
                if (-not (Test-Path -LiteralPath $backupRoot)) {
                    throw "The previous installation backup is missing."
                }
                Copy-Item -LiteralPath $backupRoot -Destination $installRoot -Recurse
                if (-not (Test-Path -LiteralPath $installRoot)) {
                    throw "The previous installation restore could not be verified."
                }
            }
            if ($registrationModified -and $oldRegistrationCaptured) {
                Restore-RegistryState
            }
            $rollbackSucceeded = $true
            if ($oldInstallBackedUp -and (Test-Path -LiteralPath $backupRoot)) {
                Remove-Item -LiteralPath $backupRoot -Recurse -Force
            }
        }
        catch {
            [Console]::Error.WriteLine(
                "Elvern VLC Opener was not installed: $installError Registry rollback also failed: $($_.Exception.Message)"
            )
            if (Test-Path -LiteralPath $registryBackupRoot) {
                [Console]::Error.WriteLine("Preserved registry recovery files: $registryBackupRoot")
            }
            if (Test-Path -LiteralPath $backupRoot) {
                [Console]::Error.WriteLine("Preserved previous installation backup: $backupRoot")
            }
            exit 1
        }
    }
    Write-Error "Elvern VLC Opener was not installed: $installError"
    exit 1
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        try { Remove-Item -LiteralPath $stagingRoot -Recurse -Force }
        catch { Write-Warning "The private staging directory could not be removed: $stagingRoot" }
    }
    if (
        ( $installCommitted -or $rollbackSucceeded ) -and
        (Test-Path -LiteralPath $registryBackupRoot)
    ) {
        try { Remove-Item -LiteralPath $registryBackupRoot -Recurse -Force }
        catch { Write-Warning "The private registry backup directory could not be removed: $registryBackupRoot" }
    }
    if ($null -ne $installLockHandle) {
        $installLockHandle.Dispose()
        $installLockHandle = $null
    }
}
