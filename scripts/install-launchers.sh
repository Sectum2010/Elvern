#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

INSTALL_DESKTOP_ICONS=1

while (($# > 0)); do
  case "$1" in
    --no-desktop-icons)
      INSTALL_DESKTOP_ICONS=0
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./scripts/install-launchers.sh [--no-desktop-icons]

Installs Elvern desktop entries into ~/.local/share/applications and optionally
copies them onto ~/Desktop for double-click launching.
EOF
      exit 0
      ;;
    *)
      elvern_log_message ERROR "Unknown argument: $1"
      exit 1
      ;;
  esac
  shift
done

APP_DIR="${HOME}/.local/share/applications"
DESKTOP_DIR="${HOME}/Desktop"
ICON_PATH="${ELVERN_PROJECT_ROOT}/frontend/public/icons/icon-512.png"

mkdir -p "${APP_DIR}"

render_desktop_file() {
  local template_file="$1"
  local target_file="$2"

  sed \
    -e "s|__PROJECT_ROOT__|${ELVERN_PROJECT_ROOT}|g" \
    -e "s|__ICON_PATH__|${ICON_PATH}|g" \
    "${template_file}" >"${target_file}"
}

render_desktop_file "${ELVERN_PROJECT_ROOT}/deploy/linux/elvern.desktop" "${APP_DIR}/elvern.desktop"
render_desktop_file "${ELVERN_PROJECT_ROOT}/deploy/linux/elvern-control.desktop" "${APP_DIR}/elvern-control.desktop"
rm -f "${APP_DIR}/elvern-player.desktop"

chmod 644 "${APP_DIR}/elvern.desktop" "${APP_DIR}/elvern-control.desktop"

if [[ -x "${ELVERN_PROJECT_ROOT}/clients/desktop-vlc-opener/scripts/register-protocol-linux.sh" ]]; then
  "${ELVERN_PROJECT_ROOT}/clients/desktop-vlc-opener/scripts/register-protocol-linux.sh" "${ELVERN_PROJECT_ROOT}/scripts/elvern-vlc-opener.sh" >/dev/null 2>&1 || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${APP_DIR}" >/dev/null 2>&1 || true
fi

if (( INSTALL_DESKTOP_ICONS )) && [[ -d "${DESKTOP_DIR}" ]]; then
  install -m 0755 "${APP_DIR}/elvern.desktop" "${DESKTOP_DIR}/Elvern.desktop"
  install -m 0755 "${APP_DIR}/elvern-control.desktop" "${DESKTOP_DIR}/Elvern Control.desktop"
  rm -f "${DESKTOP_DIR}/Elvern Player.desktop"
fi

printf 'Installed Elvern launchers into %s\n' "${APP_DIR}"
if (( INSTALL_DESKTOP_ICONS )) && [[ -d "${DESKTOP_DIR}" ]]; then
  printf 'Desktop launchers were also copied into %s\n' "${DESKTOP_DIR}"
fi
