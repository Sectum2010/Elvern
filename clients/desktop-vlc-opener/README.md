# Elvern VLC Opener

`Elvern VLC Opener` resolves a short-lived Elvern handoff and launches the user's
installed VLC. It does not play media itself, and media paths are never exposed to
the browser.

## Standard packages

The normal release is self-contained, non-trimmed .NET 10. Users do not install a
separate .NET Runtime. `Directory.Build.props` is the single source for the helper
version and target framework.

From the helper directory, set the exact public backend origin compiled into the
helper, then publish all packages:

```bash
export ELVERN_BACKEND_ORIGIN="https://your-elvern-origin.example"
./scripts/publish-bundles.sh
```

Build one platform when needed:

```bash
./scripts/publish-bundles.sh --platform windows
./scripts/publish-bundles.sh --platform macos
./scripts/publish-bundles.sh --platform linux
```

Publishing requires the .NET 10 SDK and every requested RID runtime pack. Any
missing RID fails the entire selected publish; the script never substitutes a
framework-dependent DLL or omits an architecture.

The publisher restores Microsoft runtime packs from the official NuGet v3 feed.
Controlled build environments may set `ELVERN_DOTNET_NUGET_SOURCE` to an approved
mirror without changing repository or global NuGet configuration.

The standard output is:

- `windows-x64`: self-contained `win-x64` payload.
- `macos-dual-arch`: self-contained `osx-arm64` and `osx-x64` payloads in one ZIP.
- `linux-universal`: self-contained `linux-x64`, `linux-arm64`,
  `linux-musl-x64`, and `linux-musl-arm64` payloads in one ZIP.
- `release-manifest.json`: package-level release manifest v2 with outer ZIP and
  inner installer-manifest SHA-256 values.

The macOS package is not a universal Mach-O. It is one installer package containing
two real RID payloads; the installer selects locally. The Linux package likewise
selects CPU and libc locally.

## Install

### Windows x64

Unzip the Windows package and double-click `Install-ElvernVlcOpener.cmd`. It installs
per user under `%LocalAppData%\Programs\Elvern VLC Opener` and registers
`elvern-vlc://` without a separate runtime.

### macOS 14 or newer

Unzip the macOS package and double-click `Install-ElvernVlcOpener.command`. The
installer detects Rosetta, Apple Silicon, or Intel locally, verifies the selected
payload, builds the AppleScript URL-event bridge, applies an ad-hoc structural
signature, and installs to:

```text
~/Applications/Elvern VLC Opener.app
```

No `sudo` or administrator password is used. Finder reveals the installed App when
the install succeeds. The App is not Developer ID signed or notarized, so macOS or
managed-device policy may still require a one-time confirmation. Do not disable
Gatekeeper. Elvern's Terminal fallback verifies the exact ZIP or inner manifest
before removing quarantine from that exact Elvern package tree.

### Linux remote desktop

Unzip the Linux package and run:

```bash
./Install-ElvernVlcOpener.sh
```

The installer detects x64/ARM64 and glibc/musl, verifies the selected payload,
installs under `~/.local/lib/elvern-vlc-opener`, and registers the protocol with
`xdg-mime`. It does not use `sudo` or modify `/usr`. `--runtime <rid>` exists only
for advanced troubleshooting and must match the signed manifest allowlist.

VLC discovery checks `ELVERN_VLC_PATH`, executable `vlc` entries on `PATH`,
`/usr/bin/vlc`, `/usr/local/bin/vlc`, and `/snap/bin/vlc`. Flatpak VLC is not
supported in this release.

Linux browsers running on the Elvern host do not need the client Helper; Elvern
continues to launch host VLC directly. Remote Linux desktops use the same callback,
verification, and `elvern-vlc://` handoff model as Windows and macOS.

## Development-only registration

The scripts under `scripts/register-protocol-*` register a framework-dependent
checkout build for development. They require the .NET 10 SDK or Runtime and are not
included in the standard release manifest or shown as the main user download.

```bash
dotnet build
./scripts/register-protocol-linux.sh
```

Equivalent development scripts exist for Windows and macOS. Standard users should
use the self-contained packages instead.

## Validation

```bash
dotnet build Elvern.VlcOpener.csproj --configuration Release
dotnet test Tests/Elvern.VlcOpener.Tests.csproj --configuration Release
bash -n packaging/macos/Install-ElvernVlcOpener.command
bash -n packaging/linux/Install-ElvernVlcOpener.sh
bash -n scripts/publish-bundles.sh
```

Generated binaries and ZIPs remain under ignored `artifacts/`; source, installers,
tests, manifests, and documentation are the reviewable repository changes.
