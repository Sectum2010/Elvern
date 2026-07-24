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
full tree-manifest path and SHA-256, canonical backend-origin binding hash, runtime
family, deployment mode, and publication time. Stable release IDs derive from
channel, package target, version, and filename. Manifest paths are rejected if they
are absolute, traverse a parent directory, or disagree with the artifact filename.

The build command writes a verified build under `artifacts/staging/<build-id>/` by
default. A platform-only build never changes active releases. Explicit `--activate`
requires Windows, macOS, and Linux plus an absolute runtime destination supplied by
`--active-dir` or `ELVERN_HELPER_RELEASES_DIR`. The CLI value wins. Activation
verifies the full staged set, copies immutable content-hash-named ZIPs, and
atomically replaces the active manifest last. Its lock lives in that same runtime
directory, preventing two publishers from targeting one authority concurrently.
Existing immutable files are reused only when their content hash matches. Package
bytes and directory metadata are flushed before the read-only manifest is
atomically renamed. Failure injection at artifact copy or manifest rename leaves
the old manifest authoritative and cleans temporary active files;
same-name/different-content collisions fail closed. Docker's runtime destination is
`/data/helper_releases`.

An existing active manifest that is unreadable, malformed, unsafe, or invalid
causes activation to fail before any new artifact is copied. The explicit
`--replace-corrupt-active-manifest` recovery flag is accepted only with
`--activate`. It preserves the old manifest as an immutable SHA-named backup,
retains every pre-existing artifact, and replaces the active authority only after
the new complete release set passes validation. This is a manual recovery tool,
not a normal publishing mode.

The build records the SHA-256 of the canonical effective backend origin, not the raw
origin. The backend recomputes that identity from its authoritative handoff origin
before status, release listing, or download. A mismatch is fail-closed and does not
fall back to the same incompatible v2 package or the DB catalog.

Manifest metadata and artifact verification are separately cached in-process.
Only the requested platform artifact is fingerprinted and hashed. Artifact paths
are opened component-by-component from the trusted package directory using
`dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`; an intermediate or final symlink is
rejected even when it points back inside the package directory. Per-artifact locks
allow different packages to hash concurrently, while concurrent readers of one
package share one validation. The verification cache is a bounded LRU.

Listings and downloads may reuse a prior hash only when the complete artifact
fingerprint matches an entry in the bounded verification cache. A cache miss or
changed fingerprint hashes the exact safely opened file description and compares
`fstat` before and after hashing. The request streams that same opened handle, so a
path replacement after verification cannot substitute different bytes. A bounded
retry handles a concurrently changing inode; an unstable package fails closed.
GET, HEAD, `Content-Length`, one RFC byte range, and safe `Content-Disposition`
filename handling are preserved. Audit records distinguish started, completed,
and interrupted downloads without recording query tokens.

The backend prefers package manifest v2. A platform gets at most one v2 primary
package. Legacy manifest or database releases remain a rollback fallback only when
the package-level primary is unavailable; they are not expanded from a v2 package.
No database migration is required.

## Inner installer manifest

Every ZIP contains `.elvern/manifest.json` and a non-executable
`.elvern/installer-manifest.tsv` with schema
`desktop-helper-installer-manifest-v2`. Each payload record contains:

- runtime ID
- relative path
- executable name
- byte size
- SHA-256

Every ZIP also contains `.elvern/tree-manifest.tsv`. It covers every regular
package file except itself, including the root installer, README, selectors,
bridges, runners, uninstallers, inner manifests, and payloads. Installers reject
unsafe paths, links, extra files, unsupported runtime overrides, missing payloads,
size mismatches, and hash mismatches before sourcing package code or executing the
staged Helper. `.DS_Store` is the only explicitly tolerated Finder extra.

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
The Linux installer, uninstaller, and selector use POSIX `/bin/sh`; the universal
package does not assume Bash exists on glibc or musl systems.

## Install and rollback

Windows installs per user under `%LocalAppData%`. macOS installs to
`~/Applications/Elvern VLC Opener.app`. Linux installs under
`~/.local/lib/elvern-vlc-opener` and writes a user desktop entry under
`~/.local/share/applications`.

All installers verify the full tree and selected payload, build a staged install,
run the staged `--version`, and only then replace the active install. If a final
check or protocol registration fails after replacement begins, the previous
installation, desktop entry, and per-user protocol state are restored. Reinstall
and upgrade are idempotent and do not require `sudo`, Python, or a separate .NET
Runtime.

Windows verifies the source tree, parses the exact TSV installer contract without
PowerShell 7-only APIs, copies the Helper and required uninstaller into staging,
and applies `Unblock-File` only to those verified staged files. It remains Windows
PowerShell 5.1 compatible. Registry state is exported and checked before
replacement; protocol and uninstall keys are restored and verified on failure.

Linux snapshots both user-level `mimeapps.list` locations, their existence and
modes, the prior desktop entry, and the prior default handler before modification.
Rollback restores those files byte-for-byte and verifies the effective handler; it
does not use `xdg-mime uninstall`.

All uninstallers take the same per-user exclusive lock as their installer. They
validate install ownership, stage same-filesystem backups, remove only exact
Elvern-owned registration, verify the result, and roll back on failure. A rollback
failure preserves recovery materials and returns a non-zero result.

Linux records only a safe previous handler basename. During uninstall, it restores
that handler only when Elvern is still the current default and the previous desktop
entry still exists. Without a valid previous handler it edits only the
`x-scheme-handler/elvern-vlc` entry in each user-level `[Default Applications]`
section. If the user selected a third-party handler after installing Elvern, that
new choice is not overwritten. Legacy installs without state remove only an active
Elvern mapping and never guess a previous handler.

macOS treats an exact Launch Services unregister failure as transactional failure
and restores/re-registers the App. Windows runs its uninstaller from a temporary
bootstrap when necessary, uses the same exclusive file lock, and removes protocol
or uninstall registry keys only when their values still identify the owned install.

## macOS trust boundary

The macOS installer preserves the AppleScript `open location` bridge because custom
URL events are not ordinary process arguments. After all App contents and
`Info.plist` values are final, it creates an ad-hoc structural signature and runs
`codesign --verify --deep --strict`.

Ad-hoc signing is not Developer ID signing and is not notarization. It cannot
guarantee acceptance under every Gatekeeper or MDM policy.

Quarantine removal is limited to:

- individual tree-manifest-listed files after the outer ZIP or tree-manifest hash
  and every listed file are verified by the Terminal fallback;
- the staged Helper App after its selected payload hash is verified;
- the final Helper App after the verified staged App is installed.

No command disables Gatekeeper, targets all of Downloads or Applications, uses
`sudo`, or pipes a network response into a shell.

The normal macOS and Linux installers use platform shell/system tools and have no
Python dependency. Python remains a release-build-host dependency for deterministic
manifest generation and package verification.

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
card. A coalesced page-resume event turns focus/pageshow/visible transitions into
one lightweight refresh; there is no healthy-state polling and no automatic
custom-protocol launch.

Third-party HTTP(S) VLC/store links open in a separate `noopener noreferrer` tab.
Helper ZIP downloads remain ordinary file links, and custom protocol checks still
require an explicit user action. Page-owned GET requests abort on `pagehide`;
Firefox lifecycle cancellation is normalized to `AbortError` rather than exposing a
raw browser `NetworkError`.

Connectivity failures are retained in a process-memory incident store with
monotonic failure and recovery identities. Health probes can only close failures
they observed; a failure that opens during an older in-flight probe schedules one
immediate follow-up. TanStack query recovery reads the store both on subscription
and immediately, preventing a recovery-before-error race, and claims one refetch
per exact query hash and recovery generation.

Legacy per-RID releases appear under low-emphasis `More options...` only when a v2
primary package is unavailable. Unknown browsers are not treated as Linux, and
iPad desktop-class user agents remain iPad.

## Rollback

To roll back server publication, restore the previous release manifest and package
files together. The backend will continue to parse legacy manifest or database
catalog records. To roll back a source deployment, revert this patch and rebuild the
frontend/backend; existing installed helpers remain compatible with the unchanged
handoff and verification endpoints.

`import-helper-releases` is a rollback-only catalog path for legacy
framework-dependent artifacts. It requires an explicit
`--runtime-requirement <major>.x`; standard v2 packages are never imported through
that database path.

## Validation matrix

Automated validation covers manifest contracts, lexical symlink rejection,
same-handle download verification, bounded/per-artifact hashing, Range and filename
headers, status schema, same-host/remote Linux behavior, frontend package
presentation, safe Terminal command generation, platform selectors, strict
installer parsing, transaction failure injection, exact Linux MIME rollback,
activation interruption, shared origin-normalization vectors, and VLC candidate
order.

Isolated Linux tests use a temporary HOME and fake desktop integration commands.
They do not establish remote Linux desktop compatibility. The following remain
real-device checks:

- Apple Silicon and Intel macOS 14+ install, Gatekeeper double-click, Terminal
  fallback, Apple Event protocol delivery, and Finder reveal.
- glibc x64/ARM64 and musl x64/ARM64 install and protocol registration.
- Windows x64 install, upgrade rollback, and protocol delivery.
- Safari, Chrome, and Firefox returning to the Install page after installation.
- Firefox 152.0.6 Ubuntu Snap over Tailscale HTTPS, including the external VLC tab,
  lifecycle cancellation, and Library/Detail/poster recovery chain.
