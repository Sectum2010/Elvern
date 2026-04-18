#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

HELPER_DLL=""
for candidate in \
  "${ELVERN_PROJECT_ROOT}/clients/desktop-vlc-opener/bin/Debug/net8.0/Elvern.VlcOpener.dll" \
  "${ELVERN_PROJECT_ROOT}/clients/desktop-vlc-opener/bin/Release/net8.0/Elvern.VlcOpener.dll"
do
  if [[ -f "${candidate}" ]]; then
    HELPER_DLL="${candidate}"
    break
  fi
done

if [[ -n "${HELPER_DLL}" ]] && command -v dotnet >/dev/null 2>&1; then
  exec dotnet "${HELPER_DLL}" "$@"
fi

if command -v dotnet >/dev/null 2>&1; then
  exec dotnet run --project "${ELVERN_PROJECT_ROOT}/clients/desktop-vlc-opener/Elvern.VlcOpener.csproj" -- "$@"
fi

elvern_gui_error "Elvern VLC Opener" "The VLC opener helper is not built yet. Build clients/desktop-vlc-opener first."
elvern_log_message ERROR "Elvern VLC Opener is not built yet. Build clients/desktop-vlc-opener first."
exit 1
