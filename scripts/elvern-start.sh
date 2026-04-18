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
Usage: ./scripts/elvern-start.sh [--open-browser]

Starts Elvern if needed, preferring systemd services when installed and falling
back to safe local background processes otherwise.
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

BACKEND_HEALTH_URL="$(elvern_local_backend_url)/health"
FRONTEND_HEALTH_URL="$(elvern_local_frontend_url)/health"
BACKEND_PUBLIC_HEALTH_URL="$(elvern_backend_url)/health"
FRONTEND_PUBLIC_HEALTH_URL="$(elvern_public_app_url)/health"
FRONTEND_URL="$(elvern_public_app_launch_url)"
SERVICE_SCOPE="$(elvern_detect_service_scope)"

if [[ -z "${SERVICE_SCOPE}" ]]; then
  if elvern_process_needs_restart "backend"; then
    elvern_log_message INFO "Backend env changed since the last local launch. Restarting it with the current config."
    elvern_stop_local_process "backend"
  fi
  if elvern_process_needs_restart "frontend"; then
    elvern_log_message INFO "Frontend env changed since the last local launch. Restarting it with the current config."
    elvern_stop_local_process "frontend"
  fi
fi

if elvern_healthcheck "${BACKEND_HEALTH_URL}" && elvern_healthcheck "${FRONTEND_HEALTH_URL}"; then
  elvern_log_message INFO "Elvern is already running."
  if (( OPEN_BROWSER )); then
    if elvern_open_browser "${FRONTEND_URL}"; then
      elvern_gui_info "Elvern" "Elvern is already running and was opened in your browser."
    else
      elvern_log_message WARN "Could not open a browser automatically. Open ${FRONTEND_URL} manually."
    fi
  fi
  exit 0
fi

if [[ -n "${SERVICE_SCOPE}" ]]; then
  elvern_log_message INFO "Trying ${SERVICE_SCOPE} systemd services first."
  if elvern_systemctl_action "${SERVICE_SCOPE}" start "${ELVERN_BACKEND_UNIT}" "${ELVERN_FRONTEND_UNIT}"; then
    if elvern_wait_for_url "${BACKEND_HEALTH_URL}" "Backend API" 30 && elvern_wait_for_url "${FRONTEND_HEALTH_URL}" "Frontend server" 30; then
      elvern_log_message INFO "Elvern started through ${SERVICE_SCOPE} systemd."
      if (( OPEN_BROWSER )); then
        if elvern_open_browser "${FRONTEND_URL}"; then
          elvern_gui_info "Elvern" "Elvern started and was opened in your browser."
        else
          elvern_log_message WARN "Could not open a browser automatically. Open ${FRONTEND_URL} manually."
        fi
      fi
      exit 0
    fi
  else
    elvern_log_message WARN "Could not start ${SERVICE_SCOPE} systemd services. Falling back to local processes."
  fi
fi

if ! elvern_healthcheck "${BACKEND_HEALTH_URL}"; then
  elvern_start_local_process \
    "backend" \
    "${ELVERN_PROJECT_ROOT}" \
    "${ELVERN_PORT}" \
    "${ELVERN_PROJECT_ROOT}/.venv/bin/uvicorn" \
    backend.app.main:app \
    --host "${ELVERN_BIND_HOST}" \
    --port "${ELVERN_PORT}"
fi

elvern_wait_for_url "${BACKEND_HEALTH_URL}" "Backend API" 30

if ! elvern_healthcheck "${FRONTEND_HEALTH_URL}"; then
  elvern_start_local_process \
    "frontend" \
    "${ELVERN_PROJECT_ROOT}/frontend" \
    "${ELVERN_FRONTEND_PORT}" \
    node \
    server.mjs
fi

elvern_wait_for_url "${FRONTEND_HEALTH_URL}" "Frontend server" 30

elvern_log_message INFO "Elvern is ready at ${FRONTEND_URL}"
elvern_log_message INFO "Tailscale status: $(elvern_tailscale_status)"

if [[ -n "${ELVERN_PUBLIC_APP_ORIGIN:-}" ]] && ! elvern_healthcheck "${FRONTEND_PUBLIC_HEALTH_URL}"; then
  elvern_log_message WARN "Elvern is running locally, but the configured app URL is not reachable from this host: ${FRONTEND_PUBLIC_HEALTH_URL}"
fi

if [[ -n "${ELVERN_BACKEND_ORIGIN:-}" ]] && ! elvern_healthcheck "${BACKEND_PUBLIC_HEALTH_URL}"; then
  elvern_log_message WARN "Elvern is running locally, but the configured backend API URL is not reachable from this host: ${BACKEND_PUBLIC_HEALTH_URL}"
fi

if (( OPEN_BROWSER )); then
  if elvern_open_browser "${FRONTEND_URL}"; then
    elvern_gui_info "Elvern" "Elvern is ready and was opened in your browser."
  else
    elvern_log_message WARN "Could not open a browser automatically. Open ${FRONTEND_URL} manually."
  fi
fi
