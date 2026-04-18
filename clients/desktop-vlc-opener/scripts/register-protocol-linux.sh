#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LAUNCHER_PATH="${1:-${PROJECT_ROOT}/scripts/elvern-vlc-opener.sh}"
DESKTOP_FILE="${HOME}/.local/share/applications/elvern-vlc-opener.desktop"

mkdir -p "$(dirname "${DESKTOP_FILE}")"

cat >"${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Name=Elvern VLC Opener
Exec=${LAUNCHER_PATH} %u
Terminal=false
MimeType=x-scheme-handler/elvern-vlc;
Categories=AudioVideo;Video;
EOF

chmod 644 "${DESKTOP_FILE}"
xdg-mime default elvern-vlc-opener.desktop x-scheme-handler/elvern-vlc

echo "Registered elvern-vlc:// for ${LAUNCHER_PATH}"
