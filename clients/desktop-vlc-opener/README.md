# Elvern VLC Opener

`Elvern VLC Opener` is a lightweight desktop helper for installed VLC. It does not
play media itself. Instead it resolves a short-lived Elvern handoff and launches
the user’s installed VLC app with the mapped direct source or backend fallback URL.

## Packaging on the Elvern host

Build distributable client bundles from the Elvern repo:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
./scripts/publish-bundles.sh
```

That produces:

- published binaries under `clients/desktop-vlc-opener/artifacts/publish/`
- distributable client packages under `clients/desktop-vlc-opener/artifacts/packages/`

Default packaging mode:

- portable/framework-dependent
- no RID-specific self-contained publish by default
- practical for building Windows/macOS packages from the DGX Linux host

Default package targets:

- `win-x64`
- `osx-arm64`
- `osx-x64`

Example package build:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
./scripts/publish-bundles.sh --runtime win-x64 --runtime osx-arm64
```

If you explicitly want to try the older RID-specific self-contained path, it is still available:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
./scripts/publish-bundles.sh --runtime osx-arm64 --self-contained
```

That self-contained path may fail on the DGX host when cross-runtime packs are unavailable. The portable mode is the normal recommended path.

## Windows install

Use the generated Windows package on the Windows client machine:

1. Unzip `elvern-vlc-opener-<version>-win-x64.zip`
2. Double-click `Install-ElvernVlcOpener.cmd`
3. Make sure the `.NET 8 Runtime` is installed on that Windows machine
4. Keep VLC installed normally
5. Open Elvern in the browser and click `Open in VLC`

The installer copies the helper into `%LocalAppData%\Programs\Elvern VLC Opener\`
and registers `elvern-vlc://` for the current user. Portable packages register
`dotnet <installed app dll>` as the protocol target; self-contained packages register
the packaged `.exe`. On Windows, the helper receives the custom protocol URL as a normal
command-line argument via `%1`, so there is no macOS-style Apple Event bridge here.
The installer now resolves `dotnet.exe` from standard absolute paths first:

- `%ProgramFiles%\dotnet\dotnet.exe`
- `%ProgramFiles(x86)%\dotnet\dotnet.exe`

and only falls back to `PATH` lookup if needed.

## macOS install

Use the generated macOS package on the Mac client machine:

1. Unzip `elvern-vlc-opener-<version>-osx-arm64.zip` or `...-osx-x64.zip`
2. Double-click `Install-ElvernVlcOpener.command`
3. Make sure the `.NET 8 Runtime` is installed on that Mac
4. Keep VLC installed in `/Applications` or `~/Applications`
5. Open Elvern in the browser and click `Open in VLC`

The installer copies `Elvern VLC Opener.app` into `~/Applications` and registers
`elvern-vlc://` locally on that Mac. Portable packages run the helper inside the app
bundle via `dotnet Elvern.VlcOpener.dll`; self-contained packages run the bundled binary directly.
The macOS install flow now compiles a local AppleScript bridge with `osacompile`
so browser `elvern-vlc://...` clicks arrive via `open location` events instead of relying
on argv delivery. That bridge forwards the URL into the embedded helper runner, which checks
these absolute dotnet paths first before falling back to `PATH`: `/usr/local/share/dotnet/dotnet`,
`/opt/homebrew/share/dotnet/dotnet`, `/usr/local/bin/dotnet`, and `/opt/homebrew/bin/dotnet`.

## Temporary manual testing path

If you need to test before packaging is in your normal workflow, this is still acceptable:

- copy `clients/desktop-vlc-opener` to the Windows or macOS machine
- build it locally there with `dotnet build`
- register the protocol locally there with the existing dev scripts:

Windows:

```powershell
cd C:\path\to\desktop-vlc-opener
dotnet build
.\scripts\register-protocol-windows.ps1
```

The manual Windows path also requires `.NET 8` on that Windows machine.

macOS:

```bash
cd /path/to/desktop-vlc-opener
dotnet build
./scripts/register-protocol-macos.sh
```

That manual path is for testing only. The normal long-term flow should use the
packaged client bundles, not a copied repo checkout.

## Daily use after install

1. Open Elvern in the browser.
2. Click `Open in VLC`.
3. The registered `elvern-vlc://` helper resolves the handoff and opens VLC.

Local test from a terminal:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
dotnet run -- "elvern-vlc://play?api=http%3A%2F%2Fexample-private-host%3A8000&handoff=HANDOFF_ID&token=ACCESS_TOKEN"
```

The helper logs to:

- Linux: `~/.local/state/elvern-vlc-opener/opener.log`
- macOS: `~/Library/Logs/ElvernVlcOpener/opener.log`
- Windows: `%LocalAppData%\\ElvernVlcOpener\\opener.log`
