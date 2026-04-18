#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

if (($# > 0)) && [[ "$1" =~ ^(--help|-h)$ ]]; then
  cat <<'EOF'
Usage: ./scripts/elvern-status.sh

Shows the current Elvern runtime mode, health, and launcher-related checks.
EOF
  exit 0
fi

elvern_setup_runtime_dirs
ENV_PRESENT="no"

if elvern_load_env; then
  ENV_PRESENT="yes"
fi

BACKEND_URL="$(elvern_backend_url)"
BACKEND_LOCAL_URL="$(elvern_local_backend_url)"
FRONTEND_URL="$(elvern_frontend_url)"
FRONTEND_LOCAL_URL="$(elvern_local_frontend_url)"
BACKEND_HEALTH="down"
FRONTEND_HEALTH="down"
BACKEND_PUBLIC_HEALTH="down"
FRONTEND_PUBLIC_HEALTH="down"
SERVICE_SCOPE="$(elvern_detect_service_scope)"
BACKEND_MODE="stopped"
FRONTEND_MODE="stopped"

if elvern_healthcheck "${BACKEND_LOCAL_URL}/health"; then
  BACKEND_HEALTH="ok"
fi

if elvern_healthcheck "${FRONTEND_LOCAL_URL}/health"; then
  FRONTEND_HEALTH="ok"
fi

if elvern_healthcheck "${BACKEND_URL}/health"; then
  BACKEND_PUBLIC_HEALTH="ok"
fi

if elvern_healthcheck "${FRONTEND_URL}/health"; then
  FRONTEND_PUBLIC_HEALTH="ok"
fi

if [[ -n "${SERVICE_SCOPE}" ]] && elvern_unit_active "${SERVICE_SCOPE}" "${ELVERN_BACKEND_UNIT}"; then
  BACKEND_MODE="systemd (${SERVICE_SCOPE})"
elif elvern_local_process_running "backend"; then
  BACKEND_MODE="local launcher"
elif elvern_port_listening "${ELVERN_PORT:-8000}"; then
  BACKEND_MODE="port busy"
fi

if [[ -n "${SERVICE_SCOPE}" ]] && elvern_unit_active "${SERVICE_SCOPE}" "${ELVERN_FRONTEND_UNIT}"; then
  FRONTEND_MODE="systemd (${SERVICE_SCOPE})"
elif elvern_local_process_running "frontend"; then
  FRONTEND_MODE="local launcher"
elif elvern_port_listening "${ELVERN_FRONTEND_PORT:-4173}"; then
  FRONTEND_MODE="port busy"
fi

printf 'Elvern status\n'
printf '=============\n'
printf 'Project root: %s\n' "${ELVERN_PROJECT_ROOT}"
printf 'Env file:     %s (%s)\n' "$(elvern_env_file)" "${ENV_PRESENT}"
printf 'Backend:      %s, local-health=%s, public-health=%s, local=%s, public=%s\n' "${BACKEND_MODE}" "${BACKEND_HEALTH}" "${BACKEND_PUBLIC_HEALTH}" "${BACKEND_LOCAL_URL}" "${BACKEND_URL}"
printf 'Frontend:     %s, local-health=%s, public-health=%s, local=%s, public=%s\n' "${FRONTEND_MODE}" "${FRONTEND_HEALTH}" "${FRONTEND_PUBLIC_HEALTH}" "${FRONTEND_LOCAL_URL}" "${FRONTEND_URL}"
printf 'ffmpeg:       %s\n' "$(elvern_resolve_command "${ELVERN_FFMPEG_PATH:-ffmpeg}" 2>/dev/null || printf 'missing')"
printf 'ffprobe:      %s\n' "$(elvern_resolve_command "${ELVERN_FFPROBE_PATH:-ffprobe}" 2>/dev/null || printf 'missing')"
printf 'Tailscale:    %s\n' "$(elvern_tailscale_status)"

if [[ "${ENV_PRESENT}" == "yes" ]]; then
  if elvern_validate_basic_env >/dev/null 2>&1; then
    printf 'Config:       looks ready\n'
  else
    printf 'Config:       needs attention\n'
  fi
fi

if [[ -n "${SERVICE_SCOPE}" ]]; then
  printf 'Systemd:      %s units detected\n' "${SERVICE_SCOPE}"
  printf 'Enabled:      backend=%s frontend=%s\n' \
    "$(elvern_unit_enabled "${SERVICE_SCOPE}" "${ELVERN_BACKEND_UNIT}" && printf yes || printf no)" \
    "$(elvern_unit_enabled "${SERVICE_SCOPE}" "${ELVERN_FRONTEND_UNIT}" && printf yes || printf no)"
else
  printf 'Systemd:      no Elvern units detected\n'
fi
