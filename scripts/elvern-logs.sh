#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

LINES="${1:-120}"
SERVICE_SCOPE="$(elvern_detect_service_scope)"

if [[ "${LINES}" =~ ^(--help|-h)$ ]]; then
  cat <<'EOF'
Usage: ./scripts/elvern-logs.sh [line-count]

Shows recent backend/frontend logs. It uses systemd journal logs when available
and readable, or falls back to the local launcher log files.
EOF
  exit 0
fi

if [[ -n "${SERVICE_SCOPE}" ]]; then
  if [[ "${SERVICE_SCOPE}" == "user" ]]; then
    exec journalctl --user -u "${ELVERN_BACKEND_UNIT}" -u "${ELVERN_FRONTEND_UNIT}" -n "${LINES}" --no-pager
  fi

  if journalctl -u "${ELVERN_BACKEND_UNIT}" -u "${ELVERN_FRONTEND_UNIT}" -n "${LINES}" --no-pager >/dev/null 2>&1; then
    exec journalctl -u "${ELVERN_BACKEND_UNIT}" -u "${ELVERN_FRONTEND_UNIT}" -n "${LINES}" --no-pager
  fi
fi

elvern_setup_runtime_dirs
printf '== %s ==\n' "${ELVERN_CONTROL_LOG}"
if [[ -f "${ELVERN_CONTROL_LOG}" ]]; then
  tail -n "${LINES}" "${ELVERN_CONTROL_LOG}"
else
  printf 'No control log yet.\n'
fi

for name in backend frontend; do
  log_file="$(elvern_log_file "${name}")"
  printf '\n== %s ==\n' "${log_file}"
  if [[ -f "${log_file}" ]]; then
    tail -n "${LINES}" "${log_file}"
  else
    printf 'No %s log yet.\n' "${name}"
  fi
done
