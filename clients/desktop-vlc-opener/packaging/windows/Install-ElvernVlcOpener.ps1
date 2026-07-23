param()

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$privateRoot = Join-Path $scriptRoot ".elvern"
$manifestPath = Join-Path $privateRoot "manifest.json"
$installRoot = Join-Path $env:LocalAppData "Programs\Elvern VLC Opener"
$stagingRoot = "$installRoot.staging.$PID"
$backupRoot = "$installRoot.backup.$PID"
$installedExe = Join-Path $installRoot "Elvern.VlcOpener.exe"
$protocolKey = "Registry::HKEY_CURRENT_USER\Software\Classes\elvern-vlc"
$commandKey = "$protocolKey\shell\open\command"
$uninstallKey = "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\ElvernVlcOpener"
$installSucceeded = $false
$replacementStarted = $false

try {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "The installer manifest is missing. Download a fresh Elvern package."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema_version -ne "desktop-helper-installer-manifest-v1") {
        throw "The installer manifest schema is unsupported."
    }
    if ($manifest.deployment_mode -ne "self_contained") {
        throw "The standard Windows package must be self-contained."
    }
    $payloads = @($manifest.payloads | Where-Object { $_.runtime_id -eq "win-x64" })
    if ($payloads.Count -ne 1) {
        throw "The win-x64 payload is missing or duplicated."
    }
    $payload = $payloads[0]
    $relativePath = [string]$payload.relative_path
    if ([string]::IsNullOrWhiteSpace($relativePath) -or [IO.Path]::IsPathRooted($relativePath) -or $relativePath -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "The payload path is unsafe."
    }
    $sourceExe = Join-Path $privateRoot ($relativePath -replace '/', '\')
    if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
        throw "The self-contained win-x64 executable is missing."
    }
    $actualHash = (Get-FileHash -LiteralPath $sourceExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$payload.sha256).ToLowerInvariant()) {
        throw "The helper payload SHA-256 check failed."
    }
    if ((Get-Item -LiteralPath $sourceExe).Length -ne [long]$payload.size_bytes) {
        throw "The helper payload size check failed."
    }
    & $sourceExe --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The packaged helper did not pass its version check."
    }

    Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    Copy-Item -LiteralPath $sourceExe -Destination (Join-Path $stagingRoot "Elvern.VlcOpener.exe") -Force
    $uninstallSource = Join-Path $privateRoot "uninstall\Uninstall-ElvernVlcOpener.ps1"
    if (Test-Path -LiteralPath $uninstallSource) {
        Copy-Item -LiteralPath $uninstallSource -Destination (Join-Path $stagingRoot "Uninstall-ElvernVlcOpener.ps1") -Force
    }
    & (Join-Path $stagingRoot "Elvern.VlcOpener.exe") --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The staged helper did not pass its version check."
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
        throw "The installed helper failed its final version check."
    }
    $installSucceeded = $true
    Remove-Item -LiteralPath $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Installed Elvern VLC Opener $($manifest.helper_version) into $installRoot"
    Write-Host "Registered elvern-vlc:// for this user. No separate .NET installation is required."
}
catch {
    if (-not $installSucceeded -and $replacementStarted) {
        Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $backupRoot) {
            Move-Item -LiteralPath $backupRoot -Destination $installRoot -ErrorAction SilentlyContinue
        }
    }
    Write-Error "Elvern VLC Opener was not installed: $($_.Exception.Message)"
    exit 1
}
finally {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
}
