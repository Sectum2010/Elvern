#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOME}/.local/lib/elvern-vlc-opener"
DESKTOP_FILE="${HOME}/.local/share/applications/elvern-vlc-opener.desktop"
rm -f "${DESKTOP_FILE}"
rm -rf "${INSTALL_DIR}"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${HOME}/.local/share/applications" >/dev/null 2>&1 || true
fi
echo "Removed Elvern VLC Opener from this user account."
