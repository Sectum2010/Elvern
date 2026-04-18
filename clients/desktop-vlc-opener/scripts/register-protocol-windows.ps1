param(
    [string]$HelperDllPath
)

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

    throw "Could not find dotnet.exe. Install the .NET 8 SDK or .NET 8 Runtime on this Windows machine first."
}

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $HelperDllPath) {
    $HelperDllPath = Join-Path $projectRoot "bin\Debug\net8.0\Elvern.VlcOpener.dll"
}

$resolvedPath = (Resolve-Path $HelperDllPath).Path
$dotnetPath = Resolve-DotnetPath
$protocolKey = "Registry::HKEY_CURRENT_USER\Software\Classes\elvern-vlc"
$commandKey = "$protocolKey\shell\open\command"

New-Item -Path $protocolKey -Force | Out-Null
Set-Item -Path $protocolKey -Value "URL:Elvern VLC Opener"
New-ItemProperty -Path $protocolKey -Name "URL Protocol" -Value "" -Force | Out-Null
New-Item -Path $commandKey -Force | Out-Null
Set-Item -Path $commandKey -Value "`"$dotnetPath`" `"$resolvedPath`" `"%1`""

Write-Host "Registered elvern-vlc:// for $resolvedPath"
