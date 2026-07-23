param()

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$packageRoot = Split-Path -Parent $scriptRoot
$privateRoot = Join-Path $packageRoot ".elvern"
$manifestPath = Join-Path $privateRoot "manifest.json"
$treeManifestPath = Join-Path $privateRoot "tree-manifest.tsv"
$installRoot = Join-Path $env:LocalAppData "Programs\Elvern VLC Opener"
$stagingRoot = "$installRoot.staging.$PID"
$backupRoot = "$installRoot.backup.$PID"
$installedExe = Join-Path $installRoot "Elvern.VlcOpener.exe"
$protocolKey = "Registry::HKEY_CURRENT_USER\Software\Classes\elvern-vlc"
$commandKey = "$protocolKey\shell\open\command"
$uninstallKey = "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\ElvernVlcOpener"
$registryBackupRoot = Join-Path $env:TEMP "elvern-vlc-opener-registry-$PID"
$protocolBackup = Join-Path $registryBackupRoot "protocol.reg"
$uninstallBackup = Join-Path $registryBackupRoot "uninstall.reg"
$protocolExisted = Test-Path -LiteralPath $protocolKey
$uninstallExisted = Test-Path -LiteralPath $uninstallKey
$installSucceeded = $false
$replacementStarted = $false

function Test-SafeRelativePath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or [IO.Path]::IsPathRooted($Value)) {
        return $false
    }
    return -not ($Value -match '(^|[\\/])\.{1,2}([\\/]|$)')
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
    $lines = Get-Content -LiteralPath $treeManifestPath
    foreach ($line in $lines | Select-Object -Skip 1) {
        $fields = @($line -split "`t", -1)
        if ($fields.Count -ne 4) {
            throw "The installer tree manifest has an invalid row."
        }
        $relativePath, $sizeText, $expectedHash, $fileClass = $fields
        if (
            -not (Test-SafeRelativePath $relativePath) -or
            $sizeText -notmatch '^[0-9]+$' -or
            $expectedHash -notmatch '^[0-9a-f]{64}$' -or
            $fileClass -notmatch '^(data|executable)$'
        ) {
            throw "The installer tree manifest contains invalid metadata."
        }
        $normalizedRelative = $relativePath -replace '/', '\'
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
        [void]$expected.Add(($relativePath -replace '\\', '/'))
    }
    $actual = New-Object System.Collections.Generic.HashSet[string]([StringComparer]::Ordinal)
    foreach ($file in Get-ChildItem -LiteralPath $packageRoot -Recurse -File) {
        $relative = [IO.Path]::GetRelativePath($packageRoot, $file.FullName) -replace '\\', '/'
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
    Remove-Item -LiteralPath $protocolKey -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $uninstallKey -Recurse -Force -ErrorAction SilentlyContinue
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
}

function Export-RegistryKey([string]$RegistryPath, [string]$Destination) {
    & reg.exe export $RegistryPath $Destination /y | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        throw "The existing per-user registration could not be backed up safely."
    }
}

try {
    Test-InstallerTree
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "The verified installer manifest is missing."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema_version -ne "desktop-helper-installer-manifest-v2") {
        throw "The verified installer manifest schema is unsupported."
    }
    if ($manifest.deployment_mode -ne "self_contained" -or $manifest.package_target -ne "windows-x64") {
        throw "This is not the standard self-contained Windows package."
    }
    $payloads = @($manifest.payloads | Where-Object { $_.runtime_id -eq "win-x64" })
    if ($payloads.Count -ne 1) {
        throw "The win-x64 payload is missing or duplicated."
    }
    $payload = $payloads[0]
    $relativePath = [string]$payload.relative_path
    if (-not (Test-SafeRelativePath $relativePath)) {
        throw "The payload path is unsafe."
    }
    $sourceExe = Join-Path $privateRoot ($relativePath -replace '/', '\')
    if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
        throw "The self-contained win-x64 executable is missing."
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

    Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
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

    $replacementStarted = $true
    if (Test-Path -LiteralPath $installRoot) {
        Remove-Item -LiteralPath $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $installRoot -Destination $backupRoot
    }
    Move-Item -LiteralPath $stagingRoot -Destination $installRoot

    $protocolCommand = "`"$installedExe`" `"%1`""
    New-Item -Path $protocolKey -Force | Out-Null
    Set-Item -Path $protocolKey -Value "URL:Elvern VLC Opener"
    New-ItemProperty -Path $protocolKey -Name "URL Protocol" -Value "" -Force | Out-Null
    New-Item -Path $commandKey -Force | Out-Null
    Set-Item -Path $commandKey -Value $protocolCommand

    $installedUninstall = Join-Path $installRoot "Uninstall-ElvernVlcOpener.ps1"
    New-Item -Path $uninstallKey -Force | Out-Null
    Set-ItemProperty -Path $uninstallKey -Name "DisplayName" -Value "Elvern VLC Opener"
    Set-ItemProperty -Path $uninstallKey -Name "DisplayVersion" -Value ([string]$manifest.helper_version)
    Set-ItemProperty -Path $uninstallKey -Name "Publisher" -Value "Elvern"
    Set-ItemProperty -Path $uninstallKey -Name "InstallLocation" -Value $installRoot
    Set-ItemProperty -Path $uninstallKey -Name "UninstallString" -Value "powershell.exe -ExecutionPolicy Bypass -File `"$installedUninstall`""

    & $installedExe --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The installed Helper failed its final version check."
    }
    $installSucceeded = $true
    Remove-Item -LiteralPath $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Installed Elvern VLC Opener $($manifest.helper_version) into $installRoot"
    Write-Host "Registered elvern-vlc:// for this user. No separate .NET installation is required."
}
catch {
    $installError = $_.Exception.Message
    if (-not $installSucceeded -and $replacementStarted) {
        Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $backupRoot) {
            Move-Item -LiteralPath $backupRoot -Destination $installRoot -ErrorAction SilentlyContinue
        }
        try {
            Restore-RegistryState
        }
        catch {
            Write-Error "Elvern VLC Opener was not installed: $installError Registry rollback also failed: $($_.Exception.Message)"
            exit 1
        }
    }
    Write-Error "Elvern VLC Opener was not installed: $installError"
    exit 1
}
finally {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $registryBackupRoot -Recurse -Force -ErrorAction SilentlyContinue
}
