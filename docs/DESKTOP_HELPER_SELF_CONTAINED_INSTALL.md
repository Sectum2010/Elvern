# Desktop Helper Self-Contained Install

This document records the release and install contract for Elvern VLC Opener 0.9.0.

## Release contract

Standard releases target `net10.0`, are non-trimmed, single-file, and
self-contained. `clients/desktop-vlc-opener/Directory.Build.props` owns both the
target framework and helper version. The publisher reads those values instead of
maintaining a second version in shell metadata.

`release-manifest.json` uses schema `desktop-helper-release-manifest-v2`. Its
package targets are:

| Package target | Platform | Included RIDs |
|---|---|---|
| `windows-x64` | Windows | `win-x64` |
| `macos-dual-arch` | macOS | `osx-arm64`, `osx-x64` |
| `linux-universal` | Linux | `linux-x64`, `linux-arm64`, `linux-musl-x64`, `linux-musl-arm64` |

Each package record contains its filename, clean package root, installer entrypoint,
supported RIDs, compressed size, ZIP SHA-256, inner installer-manifest SHA-256,
runtime family, deployment mode, and publication time. Stable release IDs derive
from channel, package target, version, and filename. Manifest paths are rejected if
they are absolute, traverse a parent directory, or disagree with the artifact
filename.

The backend prefers package manifest v2. A platform gets at most one v2 primary
package. Legacy manifest or database releases remain a rollback fallback only when
the package-level primary is unavailable; they are not expanded from a v2 package.
No database migration is required.

## Inner installer manifest

Every ZIP contains `.elvern/manifest.json` with schema
`desktop-helper-installer-manifest-v1`. Each payload record contains:

- runtime ID
- relative path
- executable name
- byte size
- SHA-256

Installers reject unsafe paths, unsupported runtime overrides, missing payloads,
size mismatches, and hash mismatches before executing the helper.

## Package layout

The macOS package root visibly contains only:

```text
Install-ElvernVlcOpener.command
README.txt
.elvern/
```

The Linux package follows the same clean layout with
`Install-ElvernVlcOpener.sh`. Payloads, bridge files, selectors, and uninstall
resources live under `.elvern/`. Windows follows the same package-level contract
with its command entrypoint and private PowerShell resources.

## Local platform selection

macOS first checks `sysctl.proc_translated`. A translated x86_64 shell selects the
Apple Silicon payload. Native `arm64` selects `osx-arm64`; native `x86_64` selects
`osx-x64`. Other CPU values fail clearly. macOS 14 is the minimum supported version.

Linux maps `x86_64`/`amd64` to x64 and `aarch64`/`arm64` to ARM64. It identifies
glibc using `getconf GNU_LIBC_VERSION`, or musl using `ldd`/musl-loader evidence.
Unknown CPU or libc fails instead of guessing. An explicit `--runtime` is an
advanced troubleshooting control and still must be present in the manifest.

## Install and rollback

Windows installs per user under `%LocalAppData%`. macOS installs to
`~/Applications/Elvern VLC Opener.app`. Linux installs under
`~/.local/lib/elvern-vlc-opener` and writes a user desktop entry under
`~/.local/share/applications`.

All installers verify the selected payload, run `--version`, build a staged install,
and only then replace the active install. If a final check or protocol registration
fails after replacement begins, the previous installation is restored. Reinstall
and upgrade are idempotent and do not require `sudo`.

## macOS trust boundary

The macOS installer preserves the AppleScript `open location` bridge because custom
URL events are not ordinary process arguments. After all App contents and
`Info.plist` values are final, it creates an ad-hoc structural signature and runs
`codesign --verify --deep --strict`.

Ad-hoc signing is not Developer ID signing and is not notarization. It cannot
guarantee acceptance under every Gatekeeper or MDM policy.

Quarantine removal is limited to:

- the exact package tree after the outer ZIP or inner manifest hash is verified by
  the Terminal fallback;
- the staged Helper App after its selected payload hash is verified;
- the final Helper App after the verified staged App is installed.

No command disables Gatekeeper, targets all of Downloads or Applications, uses
`sudo`, or pipes a network response into a shell.

## Linux same-host and remote behavior

The server resolves same-host status from trusted request context: resolved client
IP, request hostname, and existing proxy rules. A browser hint cannot force this
status. Uncertain cases are treated as remote.

- Linux same-host: Helper not required, no Helper releases, host VLC detection, and
  existing direct host launch behavior.
- Linux remote: Helper required, one Linux universal package, client callback VLC
  detection, and normal verification/handoff behavior.

Remote status never reports the Elvern server's `/usr/bin/vlc` as if it belonged to
the client. Flatpak VLC is outside this release.

## UI behavior

The Install page shows one primary package per desktop platform and keeps technical
details collapsed. It reports unknown/not verified state without claiming that an
unseen Helper is absent. Test feedback is an `aria-live` status inside the Helper
card. A focus, pageshow, or visible transition triggers one lightweight refresh;
there is no healthy-state polling and no automatic custom-protocol launch.

Legacy per-RID releases appear under low-emphasis `More options...` only when a v2
primary package is unavailable. Unknown browsers are not treated as Linux, and
iPad desktop-class user agents remain iPad.

## Rollback

To roll back server publication, restore the previous release manifest and package
files together. The backend will continue to parse legacy manifest or database
catalog records. To roll back a source deployment, revert this patch and rebuild the
frontend/backend; existing installed helpers remain compatible with the unchanged
handoff and verification endpoints.

## Validation matrix

Automated validation covers manifest contracts, traversal rejection, status schema,
same-host/remote Linux behavior, frontend package presentation, safe Terminal
command generation, platform selectors, installer syntax, and VLC candidate order.

The following remain real-device checks:

- Apple Silicon and Intel macOS 14+ install, Gatekeeper double-click, Terminal
  fallback, Apple Event protocol delivery, and Finder reveal.
- glibc x64/ARM64 and musl x64/ARM64 install and protocol registration.
- Windows x64 install, upgrade rollback, and protocol delivery.
- Safari, Chrome, and Firefox returning to the Install page after installation.
