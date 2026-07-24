#!/bin/sh
set -eu

INSTALL_DIR="${HOME}/.local/lib/elvern-vlc-opener"
XDG_DATA_ROOT="${XDG_DATA_HOME:-${HOME}/.local/share}"
DESKTOP_DIR="${XDG_DATA_ROOT}/applications"
DESKTOP_FILE="${DESKTOP_DIR}/elvern-vlc-opener.desktop"
rm -f "${DESKTOP_FILE}"
rm -rf "${INSTALL_DIR}"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || :
fi
echo "Removed Elvern VLC Opener from this user account."
