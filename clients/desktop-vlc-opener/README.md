# Elvern VLC Opener

`Elvern VLC Opener` resolves a short-lived Elvern handoff and launches the user's
installed VLC. It does not play media itself, and media paths are never exposed to
the browser.

## Standard packages

The normal release is self-contained, non-trimmed .NET 10. Users do not install a
separate .NET Runtime. `Directory.Build.props` is the single source for the helper
version and target framework.

From the helper directory, set the exact effective backend origin used by Helper
handoffs, then build all packages into an ignored staging directory:

```bash
export ELVERN_BACKEND_ORIGIN="https://your-elvern-origin.example"
./scripts/publish-bundles.sh
```

Build one platform for isolated validation:

```bash
./scripts/publish-bundles.sh --platform windows
./scripts/publish-bundles.sh --platform macos
./scripts/publish-bundles.sh --platform linux
```

These commands never change the active release manifest. After all three platforms
have been built and verified together, publish them explicitly:

```bash
./scripts/publish-bundles.sh --activate
```

Activation copies immutable, content-hash-named ZIPs first, flushes package bytes
and directory metadata, and atomically replaces the active manifest last. A lock
with non-sensitive owner diagnostics prevents concurrent activation. Failure
before the final manifest rename leaves the previous active manifest authoritative
and removes temporary active files. The dangerous
`--allow-partial-activate` option exists only for explicit rollback/recovery work.
If the active manifest itself is corrupt, activation fails before copying any new
artifact. An operator may explicitly pair `--replace-corrupt-active-manifest` with
`--activate` after validating the new release set; the publisher preserves the
unreadable authority as an immutable SHA-named backup and does not infer or delete
any artifacts from it.

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
  inner installer-manifest, full tree-manifest, and server-origin binding hashes.

The backend opens every package path component relative to the trusted package
directory without following symlinks. It verifies only the requested platform
package, uses per-artifact single-flight hashing and a bounded cache, and rehashes
the exact opened file handle for every download. The same verified handle is used
for GET, HEAD, and single-range streaming. A package built for a different
effective backend origin is withheld rather than falling back to an incompatible
v2 download.

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

No `sudo`, administrator password, Python installation, or separate .NET Runtime is
used. Finder reveals the installed App when the install succeeds. The App is not
Developer ID signed or notarized, so macOS or managed-device policy may still
require a one-time confirmation. Do not disable Gatekeeper. Elvern's Terminal
fallback verifies the exact ZIP, tree manifest, and every listed file before
removing quarantine from those exact verified Elvern files.

### Linux remote desktop

Unzip the Linux package and run:

```bash
./Install-ElvernVlcOpener.sh
```

The installer detects x64/ARM64 and glibc/musl, verifies the complete package tree
and selected payload, installs under `~/.local/lib/elvern-vlc-opener`, and registers
the protocol with `xdg-mime`. It does not need Python, use `sudo`, or modify `/usr`.
The installer and uninstaller run with POSIX `/bin/sh`; Bash is not required.
`--runtime <rid>` exists only for advanced troubleshooting and must match the
verified manifest allowlist.

Upgrade is transactional. The old install is not considered backed up until its
move succeeds, and a failed registration or final validation restores the old
install, desktop entry, both user `mimeapps.list` locations byte-for-byte, and
their original modes. The installer never uses `xdg-mime uninstall` as rollback.

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

The backend `import-helper-releases` CLI is a rollback-only import path for legacy
framework-dependent artifacts. It requires an explicit
`--runtime-requirement <major>.x`; package-level v2 releases are published through
the manifest lifecycle above.

## Browser return behavior

Third-party VLC and app-store HTTP(S) links open in a separate tab with
`noopener noreferrer`. Helper ZIP links remain ordinary same-tab file downloads,
and custom protocol checks remain explicit user actions. Page-owned status requests
are cancelled on `pagehide`, then one coalesced resume event refreshes status after
return. This prevents a cancelled Firefox request from surfacing its raw
`NetworkError` or overwriting a newer result.

Transport recovery uses a process-memory incident snapshot rather than depending
on one fire-and-forget browser event. Late React query errors can therefore observe
an already-confirmed recovery. Refetch bookkeeping is bounded and keyed by the
exact TanStack query identity and recovery generation, so multiple observers share
one recovery request without crossing user identities.

## Validation

```bash
dotnet build Elvern.VlcOpener.csproj --configuration Release
dotnet test Tests/Elvern.VlcOpener.Tests.csproj --configuration Release
bash -n packaging/macos/Install-ElvernVlcOpener.command
/bin/sh -n packaging/linux/Install-ElvernVlcOpener.sh
/bin/sh -n packaging/linux/Uninstall-ElvernVlcOpener.sh
dash -n packaging/linux/Install-ElvernVlcOpener.sh
busybox sh -n packaging/linux/Install-ElvernVlcOpener.sh
bash -n scripts/publish-bundles.sh
```

Generated binaries and ZIPs remain under ignored `artifacts/`; source, installers,
tests, manifests, and documentation are the reviewable repository changes.
