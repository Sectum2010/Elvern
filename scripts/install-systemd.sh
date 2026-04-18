#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

SCOPE="system"
ENABLE_NOW=0

while (($# > 0)); do
  case "$1" in
    --scope)
      shift
      SCOPE="${1:-}"
      ;;
    --enable-now)
      ENABLE_NOW=1
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./scripts/install-systemd.sh [--scope system|user] [--enable-now]

Installs or updates Elvern systemd units using the current project root and user.
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

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

BACKEND_UNIT_PATH="${TMP_DIR}/${ELVERN_BACKEND_UNIT}"
FRONTEND_UNIT_PATH="${TMP_DIR}/${ELVERN_FRONTEND_UNIT}"

SYSTEMD_USER_DIRECTIVE=""
SYSTEMD_GROUP_DIRECTIVE=""
WANTED_BY="multi-user.target"

if [[ "${SCOPE}" == "user" ]]; then
  WANTED_BY="default.target"
else
  SYSTEMD_USER_DIRECTIVE="User=${USER}"
  SYSTEMD_GROUP_DIRECTIVE="Group=${USER}"
fi

cat >"${BACKEND_UNIT_PATH}" <<EOF
[Unit]
Description=Elvern backend API
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
${SYSTEMD_USER_DIRECTIVE}
${SYSTEMD_GROUP_DIRECTIVE}
WorkingDirectory=${ELVERN_PROJECT_ROOT}
EnvironmentFile=${ELVERN_PROJECT_ROOT}/deploy/env/elvern.env
ExecStart=${ELVERN_PROJECT_ROOT}/.venv/bin/uvicorn backend.app.main:app --host \${ELVERN_BIND_HOST} --port \${ELVERN_PORT}
Restart=always
RestartSec=5
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=${WANTED_BY}
EOF

cat >"${FRONTEND_UNIT_PATH}" <<EOF
[Unit]
Description=Elvern frontend PWA server
After=network-online.target ${ELVERN_BACKEND_UNIT}
Wants=network-online.target ${ELVERN_BACKEND_UNIT}

[Service]
Type=simple
${SYSTEMD_USER_DIRECTIVE}
${SYSTEMD_GROUP_DIRECTIVE}
WorkingDirectory=${ELVERN_PROJECT_ROOT}/frontend
EnvironmentFile=${ELVERN_PROJECT_ROOT}/deploy/env/elvern.env
Environment=NODE_ENV=production
ExecStart=/usr/bin/node ${ELVERN_PROJECT_ROOT}/frontend/server.mjs
Restart=always
RestartSec=5
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=${WANTED_BY}
EOF

if [[ "${SCOPE}" == "user" ]]; then
  INSTALL_DIR="${HOME}/.config/systemd/user"
  mkdir -p "${INSTALL_DIR}"
  install -m 0644 "${BACKEND_UNIT_PATH}" "${INSTALL_DIR}/${ELVERN_BACKEND_UNIT}"
  install -m 0644 "${FRONTEND_UNIT_PATH}" "${INSTALL_DIR}/${ELVERN_FRONTEND_UNIT}"
  systemctl --user daemon-reload
  systemctl --user enable "${ELVERN_BACKEND_UNIT}" "${ELVERN_FRONTEND_UNIT}"
  if (( ENABLE_NOW )); then
    systemctl --user restart "${ELVERN_BACKEND_UNIT}" "${ELVERN_FRONTEND_UNIT}"
  fi
  printf 'Installed user systemd units into %s\n' "${INSTALL_DIR}"
  printf 'For boot-start before login, run once: sudo loginctl enable-linger %s\n' "${USER}"
  exit 0
fi

sudo install -m 0644 "${BACKEND_UNIT_PATH}" "/etc/systemd/system/${ELVERN_BACKEND_UNIT}"
sudo install -m 0644 "${FRONTEND_UNIT_PATH}" "/etc/systemd/system/${ELVERN_FRONTEND_UNIT}"
sudo systemctl daemon-reload
sudo systemctl enable "${ELVERN_BACKEND_UNIT}" "${ELVERN_FRONTEND_UNIT}"
if (( ENABLE_NOW )); then
  sudo systemctl restart "${ELVERN_BACKEND_UNIT}" "${ELVERN_FRONTEND_UNIT}"
fi
printf 'Installed system systemd units into /etc/systemd/system\n'
