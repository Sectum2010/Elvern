#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

INSTALL_PACKAGES=0
INSTALL_SYSTEMD=0
SYSTEMD_SCOPE="system"
ENABLE_NOW=0
BUILD_DESKTOP_HELPER=1

while (($# > 0)); do
  case "$1" in
    --install-packages)
      INSTALL_PACKAGES=1
      ;;
    --install-systemd)
      INSTALL_SYSTEMD=1
      ;;
    --scope)
      shift
      SYSTEMD_SCOPE="${1:-}"
      ;;
    --enable-now)
      ENABLE_NOW=1
      ;;
    --skip-helper-build)
      BUILD_DESKTOP_HELPER=0
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./scripts/setup-ubuntu.sh [--install-packages] [--install-systemd] [--scope system|user] [--enable-now] [--skip-helper-build]

Prepares Elvern for normal Ubuntu desktop use:
- checks/install host dependencies
- creates the Python virtualenv and installs backend deps
- installs frontend deps and builds the production frontend
- builds the optional desktop VLC helper when dotnet is available
- creates launcher desktop entries
- optionally installs systemd units
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

if [[ "${SYSTEMD_SCOPE}" != "system" && "${SYSTEMD_SCOPE}" != "user" ]]; then
  elvern_log_message ERROR "--scope must be either 'system' or 'user'."
  exit 1
fi

if (( INSTALL_PACKAGES )); then
  sudo apt update
  sudo apt install -y \
    python3-venv \
    python3-pip \
    ffmpeg \
    nodejs \
    npm \
    curl \
    dotnet-sdk-8.0 \
    vlc \
    libvlc5 \
    libvlc-dev \
    vlc-plugin-base \
    desktop-file-utils
fi

MISSING=()
for command_name in python3 npm node ffmpeg; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    MISSING+=("${command_name}")
  fi
done

if ((${#MISSING[@]} > 0)); then
  elvern_log_message ERROR "Missing required host commands: ${MISSING[*]}"
  elvern_log_message ERROR "Install them manually or rerun ./scripts/setup-ubuntu.sh --install-packages"
  exit 1
fi

cd "${ELVERN_PROJECT_ROOT}"

mkdir -p backend/data "${ELVERN_RUNTIME_DIR}" "${ELVERN_PID_DIR}" "${ELVERN_LOG_DIR}"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt

cd "${ELVERN_PROJECT_ROOT}/frontend"
npm install
npm run build

cd "${ELVERN_PROJECT_ROOT}"

if [[ ! -f deploy/env/elvern.env ]]; then
  cp deploy/env/.env.example deploy/env/elvern.env
  elvern_log_message INFO "Created deploy/env/elvern.env from deploy/env/.env.example"
fi

elvern_load_env >/dev/null 2>&1 || true

mkdir -p "$(dirname "${ELVERN_DB_PATH:-${ELVERN_PROJECT_ROOT}/backend/data/elvern.db}")"
mkdir -p "${ELVERN_TRANSCODE_DIR:-${ELVERN_PROJECT_ROOT}/backend/data/transcodes}"

if (( BUILD_DESKTOP_HELPER )); then
  if command -v dotnet >/dev/null 2>&1; then
    cd "${ELVERN_PROJECT_ROOT}/clients/desktop-vlc-opener"
    dotnet build
    cd "${ELVERN_PROJECT_ROOT}"
  else
    elvern_log_message WARN "dotnet is not installed. Skipping optional desktop VLC helper build."
  fi
fi

"${ELVERN_PROJECT_ROOT}/scripts/install-launchers.sh"

if (( INSTALL_SYSTEMD )); then
  INSTALL_ARGS=(--scope "${SYSTEMD_SCOPE}")
  if (( ENABLE_NOW )); then
    INSTALL_ARGS+=(--enable-now)
  fi
  "${ELVERN_PROJECT_ROOT}/scripts/install-systemd.sh" "${INSTALL_ARGS[@]}"
fi

printf '\nSetup complete.\n'
printf 'Daily use:\n'
printf '  1) Double-click Elvern from the app menu or Desktop.\n'
printf '  2) Or run: ./scripts/elvern-start.sh --open-browser\n'
printf '\nManagement:\n'
printf '  - ./scripts/elvern-control.sh\n'
printf '  - ./scripts/elvern-status.sh\n'
printf '  - ./scripts/elvern-restart.sh --open-browser\n'
printf '\nBefore first launch, edit deploy/env/elvern.env if you have not done that yet.\n'
