#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/elvern-common.sh"

print_scope() {
  local scope="$1"
  printf '%s systemd\n' "${scope}"
  printf '%s\n' '-------------'
  printf 'backend: exists=%s enabled=%s active=%s\n' \
    "$(elvern_unit_exists "${scope}" "${ELVERN_BACKEND_UNIT}" && printf yes || printf no)" \
    "$(elvern_unit_enabled "${scope}" "${ELVERN_BACKEND_UNIT}" && printf yes || printf no)" \
    "$(elvern_unit_active "${scope}" "${ELVERN_BACKEND_UNIT}" && printf yes || printf no)"
  printf 'frontend: exists=%s enabled=%s active=%s\n' \
    "$(elvern_unit_exists "${scope}" "${ELVERN_FRONTEND_UNIT}" && printf yes || printf no)" \
    "$(elvern_unit_enabled "${scope}" "${ELVERN_FRONTEND_UNIT}" && printf yes || printf no)" \
    "$(elvern_unit_active "${scope}" "${ELVERN_FRONTEND_UNIT}" && printf yes || printf no)"
}

print_scope "user"
printf '\n'
print_scope "system"
