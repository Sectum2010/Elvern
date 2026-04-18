param()

$ErrorActionPreference = "Stop"

$installRoot = Join-Path $env:LocalAppData "Programs\Elvern VLC Opener"
$protocolKey = "Registry::HKEY_CURRENT_USER\Software\Classes\elvern-vlc"
$uninstallKey = "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\ElvernVlcOpener"

if (Test-Path $protocolKey) {
    Remove-Item $protocolKey -Recurse -Force
}

if (Test-Path $uninstallKey) {
    Remove-Item $uninstallKey -Recurse -Force
}

if (Test-Path $installRoot) {
    Remove-Item $installRoot -Recurse -Force
    Write-Host "Removed $installRoot"
} else {
    Write-Host "$installRoot is not installed."
}
