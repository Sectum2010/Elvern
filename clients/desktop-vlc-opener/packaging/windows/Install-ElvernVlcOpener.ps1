param()

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$metadataPath = Join-Path (Split-Path -Parent $scriptRoot) "helper-release.env"
$sourceAppRoot = Join-Path $scriptRoot "app"
$sourceExe = Join-Path $sourceAppRoot "Elvern.VlcOpener.exe"
$sourceDll = Join-Path $sourceAppRoot "Elvern.VlcOpener.dll"
$installRoot = Join-Path $env:LocalAppData "Programs\Elvern VLC Opener"
$installedAppRoot = Join-Path $installRoot "app"
$installedExe = Join-Path $installedAppRoot "Elvern.VlcOpener.exe"
$installedDll = Join-Path $installedAppRoot "Elvern.VlcOpener.dll"
$installedUninstall = Join-Path $installRoot "Uninstall-ElvernVlcOpener.ps1"
$protocolKey = "Registry::HKEY_CURRENT_USER\Software\Classes\elvern-vlc"
$commandKey = "$protocolKey\shell\open\command"
$uninstallKey = "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\ElvernVlcOpener"

function Get-HelperReleaseMetadata {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        throw "Missing helper packaging metadata: $Path"
    }

    $metadata = @{}
    foreach ($rawLine in Get-Content -Path $Path) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }

        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $metadata[$key] = $value
    }

    foreach ($requiredKey in @("HELPER_VERSION", "HELPER_CHANNEL", "DOTNET_RUNTIME_MAJOR", "DOTNET_RUNTIME_DISPLAY", "PACKAGE_NAME_PREFIX")) {
        if (-not $metadata.ContainsKey($requiredKey) -or [string]::IsNullOrWhiteSpace($metadata[$requiredKey])) {
            throw "Missing $requiredKey in $Path"
        }
    }

    return $metadata
}

function Resolve-DotnetPath {
    $candidates = @()
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "dotnet\dotnet.exe")
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "dotnet\dotnet.exe")
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
    if ($dotnetCommand) {
        return $dotnetCommand.Source
    }

    return $null
}

$helperMetadata = Get-HelperReleaseMetadata -Path $metadataPath
$helperVersion = $helperMetadata["HELPER_VERSION"]
$helperChannel = $helperMetadata["HELPER_CHANNEL"]
$dotnetRuntimeMajor = $helperMetadata["DOTNET_RUNTIME_MAJOR"]
$dotnetRuntimeDisplay = $helperMetadata["DOTNET_RUNTIME_DISPLAY"]
$packageNamePrefix = $helperMetadata["PACKAGE_NAME_PREFIX"]

if (-not (Test-Path $sourceAppRoot)) {
    throw "Missing app payload next to the installer."
}

if (-not (Test-Path $sourceExe) -and -not (Test-Path $sourceDll)) {
    throw "Missing Elvern VLC Opener payload in app\\."
}

New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
if (Test-Path $installedAppRoot) {
    Remove-Item $installedAppRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $installedAppRoot -Force | Out-Null
Copy-Item (Join-Path $sourceAppRoot "*") $installedAppRoot -Recurse -Force

$localUninstallScript = Join-Path $scriptRoot "Uninstall-ElvernVlcOpener.ps1"
if (Test-Path $localUninstallScript) {
    Copy-Item $localUninstallScript $installedUninstall -Force
}

$protocolCommand = $null
$version = $helperVersion

if (Test-Path $installedExe) {
    $version = (Get-Item $installedExe).VersionInfo.ProductVersion
    if ([string]::IsNullOrWhiteSpace($version)) {
        $version = $helperVersion
    }
    $protocolCommand = "`"$installedExe`" `"%1`""
} elseif (Test-Path $installedDll) {
    $dotnetPath = Resolve-DotnetPath
    if (-not $dotnetPath) {
        throw "This package is framework-dependent. Install the $dotnetRuntimeDisplay on this Windows machine and run the installer again."
    }
    $protocolCommand = "`"$dotnetPath`" `"$installedDll`" `"%1`""
} else {
    throw "Installed payload is missing Elvern.VlcOpener.exe and Elvern.VlcOpener.dll."
}

New-Item -Path $protocolKey -Force | Out-Null
Set-Item -Path $protocolKey -Value "URL:Elvern VLC Opener"
New-ItemProperty -Path $protocolKey -Name "URL Protocol" -Value "" -Force | Out-Null
New-Item -Path $commandKey -Force | Out-Null
Set-Item -Path $commandKey -Value $protocolCommand

New-Item -Path $uninstallKey -Force | Out-Null
Set-ItemProperty -Path $uninstallKey -Name "DisplayName" -Value "Elvern VLC Opener"
Set-ItemProperty -Path $uninstallKey -Name "DisplayVersion" -Value $version
Set-ItemProperty -Path $uninstallKey -Name "Publisher" -Value "Elvern"
Set-ItemProperty -Path $uninstallKey -Name "InstallLocation" -Value $installRoot
Set-ItemProperty -Path $uninstallKey -Name "UninstallString" -Value "powershell.exe -ExecutionPolicy Bypass -File `"$installedUninstall`""

Write-Host "Installed Elvern VLC Opener into $installRoot"
Write-Host "Registered protocol command: $protocolCommand"
Write-Host "You can now click Open in VLC from Elvern on this Windows machine."
