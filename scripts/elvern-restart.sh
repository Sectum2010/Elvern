#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

OPEN_BROWSER=0

while (($# > 0)); do
  case "$1" in
    --open-browser)
      OPEN_BROWSER=1
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./scripts/elvern-restart.sh [--open-browser]

Restarts Elvern through systemd when installed, or restarts the local fallback
processes otherwise.
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

elvern_setup_runtime_dirs

if ! elvern_load_env; then
  elvern_gui_error "Elvern" "Missing deploy/env/elvern.env. Run ./scripts/setup-ubuntu.sh first."
  exit 1
fi

if ! elvern_validate_basic_env; then
  elvern_gui_error "Elvern" "The env file is incomplete. Fix deploy/env/elvern.env first."
  exit 1
fi

if ! elvern_runtime_preflight; then
  elvern_gui_error "Elvern" "Elvern is missing required runtime files. Run ./scripts/setup-ubuntu.sh first."
  exit 1
fi

SERVICE_SCOPE="$(elvern_detect_service_scope)"
BACKEND_HEALTH_URL="$(elvern_local_backend_url)/health"
FRONTEND_HEALTH_URL="$(elvern_local_frontend_url)/health"

if [[ -n "${SERVICE_SCOPE}" ]]; then
  elvern_log_message INFO "Restarting ${SERVICE_SCOPE} systemd services."
  if elvern_systemctl_action "${SERVICE_SCOPE}" restart "${ELVERN_BACKEND_UNIT}" "${ELVERN_FRONTEND_UNIT}"; then
    if elvern_wait_for_url "${BACKEND_HEALTH_URL}" "Backend API" 30 && elvern_wait_for_url "${FRONTEND_HEALTH_URL}" "Frontend server" 30; then
      if (( OPEN_BROWSER )); then
        exec "${ELVERN_PROJECT_ROOT}/scripts/elvern-start.sh" --open-browser
      fi
      exec "${ELVERN_PROJECT_ROOT}/scripts/elvern-start.sh"
    fi
  fi
  elvern_log_message WARN "Systemd restart failed or services did not recover in time. Falling back to local restart."
fi

"${ELVERN_PROJECT_ROOT}/scripts/elvern-stop.sh"
if (( OPEN_BROWSER )); then
  exec "${ELVERN_PROJECT_ROOT}/scripts/elvern-start.sh" --open-browser
fi
exec "${ELVERN_PROJECT_ROOT}/scripts/elvern-start.sh"
