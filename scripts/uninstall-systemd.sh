#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

SCOPE="system"

while (($# > 0)); do
  case "$1" in
    --scope)
      shift
      SCOPE="${1:-}"
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./scripts/uninstall-systemd.sh [--scope system|user]

Stops and removes Elvern systemd unit files from the chosen scope.
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

if [[ "${SCOPE}" != "system" && "${SCOPE}" != "user" ]]; then
  elvern_log_message ERROR "--scope must be either 'system' or 'user'."
  exit 1
fi

if [[ "${SCOPE}" == "user" ]]; then
  systemctl --user disable --now "${ELVERN_FRONTEND_UNIT}" "${ELVERN_BACKEND_UNIT}" >/dev/null 2>&1 || true
  rm -f "${HOME}/.config/systemd/user/${ELVERN_BACKEND_UNIT}" "${HOME}/.config/systemd/user/${ELVERN_FRONTEND_UNIT}"
  systemctl --user daemon-reload
  printf 'Removed user systemd units.\n'
  exit 0
fi

sudo systemctl disable --now "${ELVERN_FRONTEND_UNIT}" "${ELVERN_BACKEND_UNIT}" >/dev/null 2>&1 || true
sudo rm -f "/etc/systemd/system/${ELVERN_BACKEND_UNIT}" "/etc/systemd/system/${ELVERN_FRONTEND_UNIT}"
sudo systemctl daemon-reload
printf 'Removed system systemd units.\n'
