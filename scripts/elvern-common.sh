#!/usr/bin/env bash

ELVERN_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ELVERN_DEFAULT_ENV_FILE="${ELVERN_PROJECT_ROOT}/deploy/env/elvern.env"
ELVERN_RUNTIME_DIR="${ELVERN_PROJECT_ROOT}/.runtime"
ELVERN_PID_DIR="${ELVERN_RUNTIME_DIR}/pids"
ELVERN_LOG_DIR="${ELVERN_RUNTIME_DIR}/logs"
ELVERN_STATE_DIR="${ELVERN_RUNTIME_DIR}/state"
ELVERN_CONTROL_LOG="${ELVERN_LOG_DIR}/control.log"
ELVERN_BACKEND_UNIT="elvern-backend.service"
ELVERN_FRONTEND_UNIT="elvern-frontend.service"


elvern_setup_runtime_dirs() {
  mkdir -p "${ELVERN_PID_DIR}" "${ELVERN_LOG_DIR}" "${ELVERN_STATE_DIR}"
}


elvern_timestamp() {
  date +"%Y-%m-%d %H:%M:%S"
}


elvern_log_message() {
  local level="$1"
  shift
  local message="$*"

  elvern_setup_runtime_dirs
  printf '[%s] [%s] %s\n' "$(elvern_timestamp)" "${level}" "${message}" >>"${ELVERN_CONTROL_LOG}"

  case "${level}" in
    ERROR|WARN)
      printf '[%s] %s\n' "${level}" "${message}" >&2
      ;;
    *)
      printf '%s\n' "${message}"
      ;;
  esac
}


elvern_gui_info() {
  local title="$1"
  local body="$2"

  if [[ -n "${DISPLAY:-}" ]] && command -v notify-send >/dev/null 2>&1; then
    notify-send "${title}" "${body}" >/dev/null 2>&1 || true
    return
  fi

  if [[ -n "${DISPLAY:-}" ]] && command -v zenity >/dev/null 2>&1; then
    zenity --info --title="${title}" --text="${body}" >/dev/null 2>&1 || true
  fi
}


elvern_gui_error() {
  local title="$1"
  local body="$2"

  if [[ -n "${DISPLAY:-}" ]] && command -v zenity >/dev/null 2>&1; then
    zenity --error --title="${title}" --text="${body}" >/dev/null 2>&1 || true
    return
  fi

  if [[ -n "${DISPLAY:-}" ]] && command -v notify-send >/dev/null 2>&1; then
    notify-send --urgency=critical "${title}" "${body}" >/dev/null 2>&1 || true
  fi
}


elvern_env_file() {
  printf '%s\n' "${ELVERN_ENV_FILE:-${ELVERN_DEFAULT_ENV_FILE}}"
}


elvern_load_env() {
  local env_file
  env_file="$(elvern_env_file)"

  if [[ ! -f "${env_file}" ]]; then
    return 1
  fi

  set -a
  # shellcheck disable=SC1090
  source "${env_file}"
  set +a

  export ELVERN_ENV_FILE="${env_file}"
  : "${ELVERN_BIND_HOST:=127.0.0.1}"
  : "${ELVERN_PORT:=8000}"
  : "${ELVERN_FRONTEND_HOST:=127.0.0.1}"
  : "${ELVERN_FRONTEND_PORT:=4173}"
  : "${ELVERN_PUBLIC_APP_ORIGIN:=}"
  : "${ELVERN_BACKEND_ORIGIN:=}"
  : "${ELVERN_FFMPEG_PATH:=ffmpeg}"
  : "${ELVERN_FFPROBE_PATH:=ffprobe}"
  : "${ELVERN_TRANSCODE_ENABLED:=true}"
  : "${ELVERN_DB_PATH:=${ELVERN_PROJECT_ROOT}/backend/data/elvern.db}"
  : "${ELVERN_TRANSCODE_DIR:=${ELVERN_PROJECT_ROOT}/backend/data/transcodes}"
}


elvern_validate_basic_env() {
  local valid=0

  if [[ -z "${ELVERN_MEDIA_ROOT:-}" ]]; then
    elvern_log_message ERROR "ELVERN_MEDIA_ROOT is empty in $(elvern_env_file)."
    valid=1
  elif [[ ! -d "${ELVERN_MEDIA_ROOT}" ]]; then
    elvern_log_message ERROR "ELVERN_MEDIA_ROOT does not exist: ${ELVERN_MEDIA_ROOT}"
    valid=1
  fi

  if [[ -z "${ELVERN_SESSION_SECRET:-}" || ${#ELVERN_SESSION_SECRET} -lt 32 ]]; then
    elvern_log_message ERROR "ELVERN_SESSION_SECRET must be set to at least 32 characters."
    valid=1
  fi

  if [[ -z "${ELVERN_ADMIN_PASSWORD_HASH:-}" && -z "${ELVERN_ADMIN_BOOTSTRAP_PASSWORD:-}" ]]; then
    elvern_log_message ERROR "Set ELVERN_ADMIN_PASSWORD_HASH or ELVERN_ADMIN_BOOTSTRAP_PASSWORD in $(elvern_env_file)."
    valid=1
  fi

  return "${valid}"
}


elvern_command_exists() {
  command -v "$1" >/dev/null 2>&1
}


elvern_resolve_command() {
  local candidate="$1"

  if [[ -z "${candidate}" ]]; then
    return 1
  fi

  if [[ "${candidate}" == */* ]]; then
    [[ -x "${candidate}" ]] || return 1
    printf '%s\n' "${candidate}"
    return 0
  fi

  command -v "${candidate}" 2>/dev/null
}


elvern_local_frontend_url() {
  local host="${ELVERN_FRONTEND_HOST:-127.0.0.1}"
  if [[ -z "${host}" || "${host}" == "0.0.0.0" || "${host}" == "::" || "${host}" == "[::]" ]]; then
    host="127.0.0.1"
  fi
  printf 'http://%s:%s\n' "${host}" "${ELVERN_FRONTEND_PORT:-4173}"
}


elvern_public_app_url() {
  if [[ -n "${ELVERN_PUBLIC_APP_ORIGIN:-}" ]]; then
    printf '%s\n' "${ELVERN_PUBLIC_APP_ORIGIN%/}"
    return 0
  fi
  elvern_local_frontend_url
}


elvern_public_app_launch_url() {
  local base_url
  base_url="$(elvern_public_app_url)"
  printf '%s/library\n' "${base_url%/}"
}


elvern_frontend_url() {
  elvern_public_app_url
}


elvern_local_backend_url() {
  local host="${ELVERN_BIND_HOST:-127.0.0.1}"
  if [[ -z "${host}" || "${host}" == "0.0.0.0" || "${host}" == "::" || "${host}" == "[::]" ]]; then
    host="127.0.0.1"
  fi
  printf 'http://%s:%s\n' "${host}" "${ELVERN_PORT:-8000}"
}


elvern_backend_url() {
  if [[ -n "${ELVERN_BACKEND_ORIGIN:-}" ]]; then
    printf '%s\n' "${ELVERN_BACKEND_ORIGIN%/}"
    return 0
  fi
  elvern_local_backend_url
}


elvern_open_browser() {
  local url="$1"
  local default_browser=""

  if command -v xdg-settings >/dev/null 2>&1; then
    default_browser="$(xdg-settings get default-web-browser 2>/dev/null || true)"
  fi

  if [[ "${default_browser}" == firefox* ]] && command -v firefox >/dev/null 2>&1; then
    firefox --new-tab "${url}" >/dev/null 2>&1 &
    return 0
  fi

  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${url}" >/dev/null 2>&1 &
    return 0
  fi

  if command -v gio >/dev/null 2>&1; then
    gio open "${url}" >/dev/null 2>&1 &
    return 0
  fi

  return 1
}


elvern_healthcheck() {
  local url="$1"

  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "${url}" >/dev/null 2>&1
    return $?
  fi

  python3 - "${url}" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url, timeout=2) as response:
    if response.status < 200 or response.status >= 400:
        raise SystemExit(1)
PY
}


elvern_wait_for_url() {
  local url="$1"
  local label="$2"
  local timeout="${3:-30}"
  local elapsed=0

  while (( elapsed < timeout )); do
    if elvern_healthcheck "${url}"; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  elvern_log_message ERROR "${label} did not become ready within ${timeout} seconds."
  return 1
}


elvern_port_listening() {
  local port="$1"
  ss -ltnH "( sport = :${port} )" 2>/dev/null | grep -q .
}


elvern_pid_file() {
  printf '%s/%s.pid\n' "${ELVERN_PID_DIR}" "$1"
}


elvern_state_file() {
  printf '%s/%s.state\n' "${ELVERN_STATE_DIR}" "$1"
}


elvern_log_file() {
  printf '%s/%s.log\n' "${ELVERN_LOG_DIR}" "$1"
}


elvern_env_signature() {
  local env_file
  env_file="$(elvern_env_file)"

  if [[ ! -f "${env_file}" ]]; then
    printf 'missing-env\n'
    return 0
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${env_file}" | awk '{print $1}'
    return 0
  fi

  cksum "${env_file}" | awk '{print $1 ":" $2}'
}


elvern_write_process_state() {
  local name="$1"
  cat >"$(elvern_state_file "${name}")" <<EOF
env_signature=$(elvern_env_signature)
bind_host=${ELVERN_BIND_HOST:-}
port=${ELVERN_PORT:-}
frontend_host=${ELVERN_FRONTEND_HOST:-}
frontend_port=${ELVERN_FRONTEND_PORT:-}
public_app_origin=${ELVERN_PUBLIC_APP_ORIGIN:-}
backend_origin=${ELVERN_BACKEND_ORIGIN:-}
EOF
}


elvern_process_state_matches() {
  local name="$1"
  local state_file
  state_file="$(elvern_state_file "${name}")"

  if [[ ! -f "${state_file}" ]]; then
    return 1
  fi

  local current_signature
  current_signature="$(elvern_env_signature)"
  grep -q "^env_signature=${current_signature}\$" "${state_file}"
}


elvern_process_needs_restart() {
  local name="$1"
  elvern_local_process_running "${name}" && ! elvern_process_state_matches "${name}"
}


elvern_pid_running() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}


elvern_local_process_running() {
  local pid_file
  pid_file="$(elvern_pid_file "$1")"

  if [[ ! -f "${pid_file}" ]]; then
    return 1
  fi

  elvern_pid_running "$(cat "${pid_file}")"
}


elvern_stop_local_process() {
  local name="$1"
  local pid_file
  local pid

  pid_file="$(elvern_pid_file "${name}")"
  if [[ ! -f "${pid_file}" ]]; then
    return 0
  fi

  pid="$(cat "${pid_file}")"
  if ! elvern_pid_running "${pid}"; then
    rm -f "${pid_file}"
    rm -f "$(elvern_state_file "${name}")"
    return 0
  fi

  kill "${pid}" >/dev/null 2>&1 || true
  for _ in $(seq 1 15); do
    if ! elvern_pid_running "${pid}"; then
      rm -f "${pid_file}"
      rm -f "$(elvern_state_file "${name}")"
      elvern_log_message INFO "Stopped local ${name} process."
      return 0
    fi
    sleep 1
  done

  kill -9 "${pid}" >/dev/null 2>&1 || true
  rm -f "${pid_file}"
  rm -f "$(elvern_state_file "${name}")"
  elvern_log_message WARN "Force-stopped local ${name} process."
}


elvern_start_local_process() {
  local name="$1"
  local workdir="$2"
  local port="$3"
  shift 3

  local pid_file
  local log_file
  local pid

  pid_file="$(elvern_pid_file "${name}")"
  log_file="$(elvern_log_file "${name}")"

  if elvern_local_process_running "${name}"; then
    elvern_log_message INFO "Local ${name} process is already running."
    return 0
  fi

  if elvern_port_listening "${port}"; then
    elvern_log_message ERROR "Port ${port} is already in use by another process. Elvern will not start a duplicate ${name}."
    return 1
  fi

  (
    cd "${workdir}"
    nohup "$@" >>"${log_file}" 2>&1 &
    echo $! >"${pid_file}"
  )

  pid="$(cat "${pid_file}")"
  elvern_write_process_state "${name}"
  elvern_log_message INFO "Started local ${name} process (pid ${pid})."
}


elvern_unit_exists() {
  local scope="$1"
  local unit="$2"
  local load_state=""

  if [[ "${scope}" == "user" ]]; then
    load_state="$(systemctl --user show -p LoadState --value "${unit}" 2>/dev/null || true)"
  else
    load_state="$(systemctl show -p LoadState --value "${unit}" 2>/dev/null || true)"
  fi

  [[ -n "${load_state}" && "${load_state}" != "not-found" ]]
}


elvern_unit_active() {
  local scope="$1"
  local unit="$2"

  if [[ "${scope}" == "user" ]]; then
    systemctl --user is-active --quiet "${unit}" 2>/dev/null
  else
    systemctl is-active --quiet "${unit}" 2>/dev/null
  fi
}


elvern_unit_enabled() {
  local scope="$1"
  local unit="$2"

  if [[ "${scope}" == "user" ]]; then
    systemctl --user is-enabled --quiet "${unit}" 2>/dev/null
  else
    systemctl is-enabled --quiet "${unit}" 2>/dev/null
  fi
}


elvern_detect_service_scope() {
  if elvern_unit_active "user" "${ELVERN_BACKEND_UNIT}" || elvern_unit_active "user" "${ELVERN_FRONTEND_UNIT}"; then
    printf 'user\n'
    return
  fi

  if elvern_unit_active "system" "${ELVERN_BACKEND_UNIT}" || elvern_unit_active "system" "${ELVERN_FRONTEND_UNIT}"; then
    printf 'system\n'
    return
  fi

  if elvern_unit_exists "user" "${ELVERN_BACKEND_UNIT}" || elvern_unit_exists "user" "${ELVERN_FRONTEND_UNIT}"; then
    printf 'user\n'
    return
  fi

  if elvern_unit_exists "system" "${ELVERN_BACKEND_UNIT}" || elvern_unit_exists "system" "${ELVERN_FRONTEND_UNIT}"; then
    printf 'system\n'
    return
  fi

  printf '\n'
}


elvern_systemctl_action() {
  local scope="$1"
  local action="$2"
  shift 2

  if [[ "${scope}" == "user" ]]; then
    systemctl --user "${action}" "$@"
    return $?
  fi

  if systemctl "${action}" "$@" >/dev/null 2>&1; then
    return 0
  fi

  if command -v pkexec >/dev/null 2>&1; then
    pkexec /usr/bin/systemctl "${action}" "$@"
    return $?
  fi

  if [[ -t 1 ]] && command -v sudo >/dev/null 2>&1; then
    sudo systemctl "${action}" "$@"
    return $?
  fi

  return 1
}


elvern_tailscale_status() {
  if command -v tailscale >/dev/null 2>&1; then
    if tailscale status >/dev/null 2>&1; then
      printf 'connected\n'
    else
      printf 'installed, not connected\n'
    fi
    return
  fi

  if systemctl is-active --quiet tailscaled.service; then
    printf 'service active\n'
  else
    printf 'not detected\n'
  fi
}


elvern_runtime_preflight() {
  local failed=0

  if [[ ! -f "$(elvern_env_file)" ]]; then
    elvern_log_message ERROR "Missing $(elvern_env_file). Run ./scripts/setup-ubuntu.sh first."
    failed=1
  fi

  if [[ ! -x "${ELVERN_PROJECT_ROOT}/.venv/bin/uvicorn" ]]; then
    elvern_log_message ERROR "Missing ${ELVERN_PROJECT_ROOT}/.venv/bin/uvicorn. Run ./scripts/setup-ubuntu.sh first."
    failed=1
  fi

  if ! command -v node >/dev/null 2>&1; then
    elvern_log_message ERROR "Node.js is not installed or not on PATH."
    failed=1
  fi

  if [[ ! -f "${ELVERN_PROJECT_ROOT}/frontend/dist/index.html" ]]; then
    elvern_log_message ERROR "Missing frontend/dist/index.html. Run ./scripts/setup-ubuntu.sh first."
    failed=1
  fi

  if ! elvern_resolve_command "${ELVERN_FFPROBE_PATH:-ffprobe}" >/dev/null 2>&1; then
    elvern_log_message WARN "ffprobe is not available. Library metadata scans will fail until it is installed."
  fi

  if [[ "${ELVERN_TRANSCODE_ENABLED:-true}" == "true" ]] && ! elvern_resolve_command "${ELVERN_FFMPEG_PATH:-ffmpeg}" >/dev/null 2>&1; then
    elvern_log_message WARN "ffmpeg is not available. Direct play will still work, but HLS fallback will not."
  fi

  return "${failed}"
}
