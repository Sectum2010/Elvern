#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

if (($# > 0)) && [[ "$1" =~ ^(--help|-h)$ ]]; then
  cat <<'EOF'
Usage: ./scripts/elvern-stop.sh

Stops Elvern systemd services when installed, or stops the local fallback
processes that were started by the launcher scripts.
EOF
  exit 0
fi

elvern_setup_runtime_dirs
elvern_load_env >/dev/null 2>&1 || true

SERVICE_SCOPE="$(elvern_detect_service_scope)"

if [[ -n "${SERVICE_SCOPE}" ]] && (elvern_unit_active "${SERVICE_SCOPE}" "${ELVERN_BACKEND_UNIT}" || elvern_unit_active "${SERVICE_SCOPE}" "${ELVERN_FRONTEND_UNIT}"); then
  elvern_log_message INFO "Stopping ${SERVICE_SCOPE} systemd services."
  if ! elvern_systemctl_action "${SERVICE_SCOPE}" stop "${ELVERN_FRONTEND_UNIT}" "${ELVERN_BACKEND_UNIT}"; then
    elvern_log_message WARN "Could not stop ${SERVICE_SCOPE} systemd services automatically."
  fi
fi

elvern_stop_local_process "frontend"
elvern_stop_local_process "backend"

elvern_gui_info "Elvern" "Stop request finished. Run ./scripts/elvern-status.sh to confirm the final state if needed."
