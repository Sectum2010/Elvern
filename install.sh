#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/elvern-common.sh"

INSTALL_PACKAGES=0
INSTALL_PACKAGES_SET=0
UNATTENDED=0
DRY_RUN=0
ENABLE_NOW=0
ENABLE_NOW_SET=0
FORCE=0
SYSTEMD_SCOPE="system"

MEDIA_ROOT=""
APP_ORIGIN=""
BACKEND_ORIGIN=""
ADMIN_USERNAME=""
ADMIN_PASSWORD=""
ADMIN_PASSWORD_HASH=""
BOOTSTRAP_PASSWORD=""
SESSION_SECRET=""

ENV_BOOTSTRAPPED=0
ENV_RESET=0

EXAMPLE_ENV_FILE="${ELVERN_PROJECT_ROOT}/deploy/env/.env.example"
EXAMPLE_DB_PATH="/opt/elvern/backend/data/elvern.db"
EXAMPLE_TRANSCODE_DIR="/opt/elvern/backend/data/transcodes"
EXAMPLE_HELPER_RELEASES_DIR="/opt/elvern/backend/data/helper_releases"
EXAMPLE_LIBRARY_ROOT_LINUX="/srv/media/movies"
EXAMPLE_SESSION_SECRET="replace-with-a-random-64-char-hex-string"


usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Friendly Linux host installer for the current Elvern Ubuntu/systemd path.
This is a thin wrapper around the existing setup and systemd scripts. It:
- bootstraps deploy/env/elvern.env from deploy/env/.env.example when needed
- prompts for or accepts the main runtime values
- prefers storing ELVERN_ADMIN_PASSWORD_HASH instead of plaintext
- runs scripts/setup-ubuntu.sh and reuses the existing systemd install path

Options:
  --help                    Show this help text
  --dry-run                 Print planned changes without writing files or running setup
  --unattended              Require flags or existing env values instead of prompting
  --install-packages        Let setup-ubuntu.sh install host packages with apt
  --media-root PATH         Set ELVERN_MEDIA_ROOT
  --app-origin URL          Set ELVERN_PUBLIC_APP_ORIGIN
  --backend-origin URL      Set ELVERN_BACKEND_ORIGIN
  --admin-username NAME     Set ELVERN_ADMIN_USERNAME
  --admin-password VALUE    Hash this password and write ELVERN_ADMIN_PASSWORD_HASH
  --admin-password-hash HASH
                            Write ELVERN_ADMIN_PASSWORD_HASH directly
  --bootstrap-password VALUE
                            Write ELVERN_ADMIN_BOOTSTRAP_PASSWORD directly
  --session-secret VALUE    Set ELVERN_SESSION_SECRET
  --scope system|user       Pass the systemd scope through to install-systemd.sh
  --enable-now              Restart the installed systemd units after install
  --force                   Replace an existing env file from the example first

Examples:
  ./install.sh
  ./install.sh --install-packages --enable-now
  ./install.sh --unattended --media-root /srv/media/movies \
    --app-origin https://media.tailnet.ts.net \
    --backend-origin http://media-host:8000 \
    --admin-username admin \
    --admin-password 'replace-me' \
    --session-secret 'replace-with-a-long-random-secret' \
    --install-packages --enable-now
EOF
}


log_info() {
  elvern_log_message INFO "$@"
}


log_warn() {
  elvern_log_message WARN "$@"
}


log_error() {
  elvern_log_message ERROR "$@"
}


run_cmd() {
  if (( DRY_RUN )); then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi

  "$@"
}


require_linux() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    log_error "install.sh only supports the Linux host install flow."
    exit 1
  fi
}


require_interactive_or_unattended() {
  if (( !UNATTENDED )) && [[ ! -t 0 ]]; then
    log_error "No interactive terminal detected. Re-run with --unattended and explicit flags."
    exit 1
  fi
}


normalize_origin() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit

value = sys.argv[1].strip()
parts = urlsplit(value)
if parts.scheme not in {"http", "https"} or not parts.hostname:
    raise SystemExit(1)
if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username or parts.password:
    raise SystemExit(1)
port = f":{parts.port}" if parts.port else ""
print(f"{parts.scheme}://{parts.hostname}{port}")
PY
}


looks_like_password_hash() {
  python3 - "$1" <<'PY'
import sys
from backend.app.security import looks_like_password_hash

value = sys.argv[1]
raise SystemExit(0 if looks_like_password_hash(value) else 1)
PY
}


generate_password_hash() {
  ELVERN_INSTALL_PASSWORD="$1" python3 - <<'PY'
from backend.app.security import hash_password
import os

password = os.environ["ELVERN_INSTALL_PASSWORD"]
print(hash_password(password))
PY
}


generate_session_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
    return 0
  fi

  python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
}


env_set() {
  local name="$1"
  local value="$2"
  local escaped_value
  local temp_file
  local found=0

  escaped_value="$(printf '%q' "${value}")"

  if (( DRY_RUN )); then
    if [[ "${name}" == "ELVERN_ADMIN_PASSWORD_HASH" || "${name}" == "ELVERN_ADMIN_BOOTSTRAP_PASSWORD" || "${name}" == "ELVERN_SESSION_SECRET" ]]; then
      log_info "Would set ${name} in $(elvern_env_file) (value hidden)."
    else
      log_info "Would set ${name}=${value} in $(elvern_env_file)."
    fi
    return 0
  fi

  temp_file="$(mktemp)"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ "${line}" == "${name}="* ]]; then
      printf '%s=%s\n' "${name}" "${escaped_value}" >>"${temp_file}"
      found=1
    else
      printf '%s\n' "${line}" >>"${temp_file}"
    fi
  done <"$(elvern_env_file)"

  if (( !found )); then
    printf '%s=%s\n' "${name}" "${escaped_value}" >>"${temp_file}"
  fi

  mv "${temp_file}" "$(elvern_env_file)"
}


copy_example_env_if_needed() {
  local env_file
  env_file="$(elvern_env_file)"

  mkdir -p "$(dirname "${env_file}")"

  if [[ -f "${env_file}" ]]; then
    if (( FORCE )); then
      if (( DRY_RUN )); then
        log_info "Would back up and replace ${env_file} from ${EXAMPLE_ENV_FILE}."
      else
        local backup_path
        backup_path="${env_file}.bak.$(date +%Y%m%d%H%M%S)"
        cp "${env_file}" "${backup_path}"
        cp "${EXAMPLE_ENV_FILE}" "${env_file}"
        log_info "Backed up ${env_file} to ${backup_path} and reset it from ${EXAMPLE_ENV_FILE}."
      fi
      ENV_BOOTSTRAPPED=1
      ENV_RESET=1
    fi
    return 0
  fi

  if (( DRY_RUN )); then
    log_info "Would create ${env_file} from ${EXAMPLE_ENV_FILE}."
  else
    cp "${EXAMPLE_ENV_FILE}" "${env_file}"
    log_info "Created ${env_file} from ${EXAMPLE_ENV_FILE}."
  fi
  ENV_BOOTSTRAPPED=1
}


load_current_env_values() {
  ELVERN_MEDIA_ROOT=""
  ELVERN_PUBLIC_APP_ORIGIN=""
  ELVERN_BACKEND_ORIGIN=""
  ELVERN_ADMIN_USERNAME=""
  ELVERN_ADMIN_PASSWORD_HASH=""
  ELVERN_ADMIN_BOOTSTRAP_PASSWORD=""
  ELVERN_SESSION_SECRET=""
  ELVERN_DB_PATH=""
  ELVERN_TRANSCODE_DIR=""
  ELVERN_HELPER_RELEASES_DIR=""
  ELVERN_LIBRARY_ROOT_LINUX=""
  ELVERN_COOKIE_SECURE=""

  if [[ -f "$(elvern_env_file)" ]]; then
    elvern_load_env >/dev/null 2>&1 || true
  fi

  CURRENT_MEDIA_ROOT="${ELVERN_MEDIA_ROOT:-}"
  CURRENT_APP_ORIGIN="${ELVERN_PUBLIC_APP_ORIGIN:-}"
  CURRENT_BACKEND_ORIGIN="${ELVERN_BACKEND_ORIGIN:-}"
  CURRENT_ADMIN_USERNAME="${ELVERN_ADMIN_USERNAME:-admin}"
  CURRENT_ADMIN_PASSWORD_HASH="${ELVERN_ADMIN_PASSWORD_HASH:-}"
  CURRENT_BOOTSTRAP_PASSWORD="${ELVERN_ADMIN_BOOTSTRAP_PASSWORD:-}"
  CURRENT_SESSION_SECRET="${ELVERN_SESSION_SECRET:-}"
  CURRENT_DB_PATH="${ELVERN_DB_PATH:-}"
  CURRENT_TRANSCODE_DIR="${ELVERN_TRANSCODE_DIR:-}"
  CURRENT_HELPER_RELEASES_DIR="${ELVERN_HELPER_RELEASES_DIR:-}"
  CURRENT_LIBRARY_ROOT_LINUX="${ELVERN_LIBRARY_ROOT_LINUX:-}"
  CURRENT_COOKIE_SECURE="${ELVERN_COOKIE_SECURE:-}"

  if [[ "${CURRENT_SESSION_SECRET}" == "${EXAMPLE_SESSION_SECRET}" ]]; then
    CURRENT_SESSION_SECRET=""
  fi
}


prompt_text() {
  local prompt_label="$1"
  local default_value="$2"
  local result=""

  if (( UNATTENDED )); then
    printf '%s\n' "${default_value}"
    return 0
  fi

  if [[ -n "${default_value}" ]]; then
    read -r -p "${prompt_label} [${default_value}]: " result
    printf '%s\n' "${result:-${default_value}}"
    return 0
  fi

  read -r -p "${prompt_label}: " result
  printf '%s\n' "${result}"
}


prompt_yes_no() {
  local prompt_label="$1"
  local default_value="$2"
  local reply=""

  if (( UNATTENDED )); then
    printf '%s\n' "${default_value}"
    return 0
  fi

  if [[ "${default_value}" == "yes" ]]; then
    read -r -p "${prompt_label} [Y/n]: " reply
    reply="${reply:-y}"
  else
    read -r -p "${prompt_label} [y/N]: " reply
    reply="${reply:-n}"
  fi

  case "${reply,,}" in
    y|yes) printf 'yes\n' ;;
    *) printf 'no\n' ;;
  esac
}


prompt_password_twice() {
  local prompt_label="$1"
  local password_one=""
  local password_two=""

  while true; do
    read -rs -p "${prompt_label}: " password_one
    printf '\n'
    read -rs -p "Confirm ${prompt_label,,}: " password_two
    printf '\n'

    if [[ -z "${password_one}" ]]; then
      log_error "${prompt_label} must not be empty."
      continue
    fi

    if [[ "${password_one}" != "${password_two}" ]]; then
      log_error "${prompt_label} values did not match. Try again."
      continue
    fi

    printf '%s\n' "${password_one}"
    return 0
  done
}


validate_required_media_root() {
  if [[ -z "${MEDIA_ROOT}" ]]; then
    log_error "ELVERN_MEDIA_ROOT is required."
    exit 1
  fi

  if [[ ! -d "${MEDIA_ROOT}" ]]; then
    log_error "ELVERN_MEDIA_ROOT does not exist: ${MEDIA_ROOT}"
    exit 1
  fi
}


validate_required_origin() {
  local label="$1"
  local value="$2"
  if [[ -z "${value}" ]]; then
    log_error "${label} is required."
    exit 1
  fi

  if ! normalize_origin "${value}" >/dev/null 2>&1; then
    log_error "${label} must be a valid http(s) origin like http://host:4173 or https://host."
    exit 1
  fi
}


validate_required_session_secret() {
  if [[ -z "${SESSION_SECRET}" || ${#SESSION_SECRET} -lt 32 ]]; then
    log_error "ELVERN_SESSION_SECRET must be at least 32 characters."
    exit 1
  fi
}


validate_credential_flags() {
  local selected=0

  [[ -n "${ADMIN_PASSWORD}" ]] && selected=$((selected + 1))
  [[ -n "${ADMIN_PASSWORD_HASH}" ]] && selected=$((selected + 1))
  [[ -n "${BOOTSTRAP_PASSWORD}" ]] && selected=$((selected + 1))

  if (( selected > 1 )); then
    log_error "Use only one of --admin-password, --admin-password-hash, or --bootstrap-password."
    exit 1
  fi

  if [[ -n "${ADMIN_PASSWORD_HASH}" ]] && ! looks_like_password_hash "${ADMIN_PASSWORD_HASH}"; then
    log_error "--admin-password-hash is not in the expected pbkdf2 format."
    exit 1
  fi
}


collect_values() {
  local guessed_host=""

  guessed_host="$(hostname -f 2>/dev/null || hostname 2>/dev/null || printf 'localhost')"

  if [[ -z "${MEDIA_ROOT}" ]]; then
    MEDIA_ROOT="${CURRENT_MEDIA_ROOT}"
    while [[ -z "${MEDIA_ROOT}" ]]; do
      MEDIA_ROOT="$(prompt_text "Media root" "${CURRENT_MEDIA_ROOT}")"
      if [[ -z "${MEDIA_ROOT}" ]] && (( UNATTENDED )); then
        break
      fi
    done
  fi

  if [[ -z "${APP_ORIGIN}" ]]; then
    if (( UNATTENDED )); then
      APP_ORIGIN="${CURRENT_APP_ORIGIN}"
    else
      APP_ORIGIN="$(prompt_text "Public app origin" "${CURRENT_APP_ORIGIN:-http://${guessed_host}:4173}")"
    fi
  fi

  if [[ -z "${BACKEND_ORIGIN}" ]]; then
    if (( UNATTENDED )); then
      BACKEND_ORIGIN="${CURRENT_BACKEND_ORIGIN}"
    else
      BACKEND_ORIGIN="$(prompt_text "Backend API origin" "${CURRENT_BACKEND_ORIGIN:-http://${guessed_host}:8000}")"
    fi
  fi

  if [[ -z "${ADMIN_USERNAME}" ]]; then
    ADMIN_USERNAME="${CURRENT_ADMIN_USERNAME:-admin}"
    if (( !UNATTENDED )); then
      ADMIN_USERNAME="$(prompt_text "Admin username" "${ADMIN_USERNAME}")"
    fi
  fi

  if [[ -z "${SESSION_SECRET}" ]]; then
    if [[ -n "${CURRENT_SESSION_SECRET}" ]]; then
      SESSION_SECRET="${CURRENT_SESSION_SECRET}"
    else
      SESSION_SECRET="$(generate_session_secret)"
      log_info "Generated a new ELVERN_SESSION_SECRET automatically."
      if (( !UNATTENDED )); then
        local secret_choice
        secret_choice="$(prompt_yes_no "Use the generated session secret" "yes")"
        if [[ "${secret_choice}" != "yes" ]]; then
          SESSION_SECRET="$(prompt_text "Session secret" "${SESSION_SECRET}")"
        fi
      fi
    fi
  fi

  if [[ -z "${ADMIN_PASSWORD}" && -z "${ADMIN_PASSWORD_HASH}" && -z "${BOOTSTRAP_PASSWORD}" ]]; then
    if [[ -n "${CURRENT_ADMIN_PASSWORD_HASH}" ]]; then
      CREDENTIAL_MODE="existing hash"
      FINAL_ADMIN_PASSWORD_HASH="${CURRENT_ADMIN_PASSWORD_HASH}"
      FINAL_BOOTSTRAP_PASSWORD=""
      return 0
    fi

    if [[ -n "${CURRENT_BOOTSTRAP_PASSWORD}" ]]; then
      CREDENTIAL_MODE="existing bootstrap password"
      FINAL_ADMIN_PASSWORD_HASH=""
      FINAL_BOOTSTRAP_PASSWORD="${CURRENT_BOOTSTRAP_PASSWORD}"
      return 0
    fi

    if (( UNATTENDED )); then
      log_error "Provide --admin-password, --admin-password-hash, or --bootstrap-password in unattended mode."
      exit 1
    fi

    local use_hash
    use_hash="$(prompt_yes_no "Store a hashed admin password in the env file" "yes")"
    if [[ "${use_hash}" == "yes" ]]; then
      ADMIN_PASSWORD="$(prompt_password_twice "Admin password")"
    else
      BOOTSTRAP_PASSWORD="$(prompt_password_twice "Bootstrap admin password")"
    fi
  fi

  if [[ -n "${ADMIN_PASSWORD_HASH}" ]]; then
    CREDENTIAL_MODE="provided hash"
    FINAL_ADMIN_PASSWORD_HASH="${ADMIN_PASSWORD_HASH}"
    FINAL_BOOTSTRAP_PASSWORD=""
    return 0
  fi

  if [[ -n "${ADMIN_PASSWORD}" ]]; then
    CREDENTIAL_MODE="generated hash from provided password"
    FINAL_ADMIN_PASSWORD_HASH="$(generate_password_hash "${ADMIN_PASSWORD}")"
    FINAL_BOOTSTRAP_PASSWORD=""
    return 0
  fi

  CREDENTIAL_MODE="bootstrap password"
  FINAL_ADMIN_PASSWORD_HASH=""
  FINAL_BOOTSTRAP_PASSWORD="${BOOTSTRAP_PASSWORD}"
}


normalize_and_validate_values() {
  validate_required_media_root
  validate_required_origin "ELVERN_PUBLIC_APP_ORIGIN" "${APP_ORIGIN}"
  validate_required_origin "ELVERN_BACKEND_ORIGIN" "${BACKEND_ORIGIN}"
  validate_required_session_secret

  APP_ORIGIN="$(normalize_origin "${APP_ORIGIN}")"
  BACKEND_ORIGIN="$(normalize_origin "${BACKEND_ORIGIN}")"

  if [[ -z "${ADMIN_USERNAME}" ]]; then
    log_error "ELVERN_ADMIN_USERNAME is required."
    exit 1
  fi
}


summarize_plan() {
  printf '\nElvern install plan\n'
  printf '===================\n'
  printf 'Env file:        %s\n' "$(elvern_env_file)"
  if (( ENV_RESET )); then
    printf 'Env bootstrap:   reset from example (--force)\n'
  elif (( ENV_BOOTSTRAPPED )); then
    printf 'Env bootstrap:   create from example\n'
  else
    printf 'Env bootstrap:   update existing file in place\n'
  fi
  printf 'Media root:      %s\n' "${MEDIA_ROOT}"
  printf 'App origin:      %s\n' "${APP_ORIGIN}"
  printf 'Backend origin:  %s\n' "${BACKEND_ORIGIN}"
  printf 'Admin username:  %s\n' "${ADMIN_USERNAME}"
  printf 'Admin auth:      %s\n' "${CREDENTIAL_MODE}"
  if [[ "${CREDENTIAL_MODE}" == "generated hash from provided password" || "${CREDENTIAL_MODE}" == "provided hash" || "${CREDENTIAL_MODE}" == "existing hash" ]]; then
    printf 'Password storage: ELVERN_ADMIN_PASSWORD_HASH\n'
  else
    printf 'Password storage: ELVERN_ADMIN_BOOTSTRAP_PASSWORD\n'
  fi
  printf 'Systemd scope:   %s\n' "${SYSTEMD_SCOPE}"
  printf 'Install pkgs:    %s\n' "$([[ ${INSTALL_PACKAGES} -eq 1 ]] && printf yes || printf no)"
  printf 'Enable now:      %s\n' "$([[ ${ENABLE_NOW} -eq 1 ]] && printf yes || printf no)"
  printf 'Dry run:         %s\n' "$([[ ${DRY_RUN} -eq 1 ]] && printf yes || printf no)"
}


confirm_plan_if_interactive() {
  if (( UNATTENDED )); then
    return 0
  fi

  local proceed
  proceed="$(prompt_yes_no "Proceed with install" "yes")"
  if [[ "${proceed}" != "yes" ]]; then
    log_warn "Install cancelled."
    exit 1
  fi
}


write_env_values() {
  local desired_db_path="${CURRENT_DB_PATH}"
  local desired_transcode_dir="${CURRENT_TRANSCODE_DIR}"
  local desired_helper_releases_dir="${CURRENT_HELPER_RELEASES_DIR}"
  local desired_library_root_linux="${CURRENT_LIBRARY_ROOT_LINUX}"
  local desired_cookie_secure="${CURRENT_COOKIE_SECURE:-true}"

  if (( ENV_BOOTSTRAPPED )) || [[ -z "${desired_db_path}" || "${desired_db_path}" == "${EXAMPLE_DB_PATH}" ]]; then
    desired_db_path="${ELVERN_PROJECT_ROOT}/backend/data/elvern.db"
  fi

  if (( ENV_BOOTSTRAPPED )) || [[ -z "${desired_transcode_dir}" || "${desired_transcode_dir}" == "${EXAMPLE_TRANSCODE_DIR}" ]]; then
    desired_transcode_dir="${ELVERN_PROJECT_ROOT}/backend/data/transcodes"
  fi

  if (( ENV_BOOTSTRAPPED )) || [[ -z "${desired_helper_releases_dir}" || "${desired_helper_releases_dir}" == "${EXAMPLE_HELPER_RELEASES_DIR}" ]]; then
    desired_helper_releases_dir="${ELVERN_PROJECT_ROOT}/backend/data/helper_releases"
  fi

  if (( ENV_BOOTSTRAPPED )) || [[ -z "${desired_library_root_linux}" || "${desired_library_root_linux}" == "${EXAMPLE_LIBRARY_ROOT_LINUX}" || "${desired_library_root_linux}" == "${CURRENT_MEDIA_ROOT}" ]]; then
    desired_library_root_linux="${MEDIA_ROOT}"
  fi

  if [[ "${APP_ORIGIN}" == http://* ]]; then
    desired_cookie_secure="false"
  elif [[ -z "${desired_cookie_secure}" ]]; then
    desired_cookie_secure="true"
  fi

  env_set "ELVERN_MEDIA_ROOT" "${MEDIA_ROOT}"
  env_set "ELVERN_PUBLIC_APP_ORIGIN" "${APP_ORIGIN}"
  env_set "ELVERN_BACKEND_ORIGIN" "${BACKEND_ORIGIN}"
  env_set "ELVERN_ADMIN_USERNAME" "${ADMIN_USERNAME}"
  env_set "ELVERN_SESSION_SECRET" "${SESSION_SECRET}"
  env_set "ELVERN_ADMIN_PASSWORD_HASH" "${FINAL_ADMIN_PASSWORD_HASH}"
  env_set "ELVERN_ADMIN_BOOTSTRAP_PASSWORD" "${FINAL_BOOTSTRAP_PASSWORD}"
  env_set "ELVERN_DB_PATH" "${desired_db_path}"
  env_set "ELVERN_TRANSCODE_DIR" "${desired_transcode_dir}"
  env_set "ELVERN_HELPER_RELEASES_DIR" "${desired_helper_releases_dir}"
  env_set "ELVERN_LIBRARY_ROOT_LINUX" "${desired_library_root_linux}"
  env_set "ELVERN_COOKIE_SECURE" "${desired_cookie_secure}"
}


run_setup() {
  local setup_args=()

  if (( INSTALL_PACKAGES )); then
    setup_args+=(--install-packages)
  fi
  setup_args+=(--install-systemd --scope "${SYSTEMD_SCOPE}")
  if (( ENABLE_NOW )); then
    setup_args+=(--enable-now)
  fi

  run_cmd "${ELVERN_PROJECT_ROOT}/scripts/setup-ubuntu.sh" "${setup_args[@]}"
}


post_install_validation() {
  if (( DRY_RUN )); then
    log_info "Would run a post-install config and runtime validation step."
    return 0
  fi

  if ! elvern_load_env; then
    log_error "Post-install validation could not load $(elvern_env_file)."
    exit 1
  fi

  if ! elvern_validate_basic_env; then
    log_error "Post-install validation found env issues. Fix $(elvern_env_file) and rerun ./install.sh."
    exit 1
  fi

  if ! elvern_runtime_preflight; then
    log_error "Post-install validation found missing runtime files. Re-run ./scripts/setup-ubuntu.sh."
    exit 1
  fi

  if (( ENABLE_NOW )); then
    if ! elvern_wait_for_url "$(elvern_local_backend_url)/health" "Backend API" 30; then
      exit 1
    fi
    if ! elvern_wait_for_url "$(elvern_local_frontend_url)/health" "Frontend server" 30; then
      exit 1
    fi
    log_info "Smoke check passed: backend and frontend health endpoints are responding."
    return 0
  fi

  "${ELVERN_PROJECT_ROOT}/scripts/elvern-status.sh"
}


while (($# > 0)); do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --unattended)
      UNATTENDED=1
      ;;
    --install-packages)
      INSTALL_PACKAGES=1
      INSTALL_PACKAGES_SET=1
      ;;
    --media-root)
      shift
      MEDIA_ROOT="${1:-}"
      ;;
    --app-origin)
      shift
      APP_ORIGIN="${1:-}"
      ;;
    --backend-origin)
      shift
      BACKEND_ORIGIN="${1:-}"
      ;;
    --admin-username)
      shift
      ADMIN_USERNAME="${1:-}"
      ;;
    --admin-password)
      shift
      ADMIN_PASSWORD="${1:-}"
      ;;
    --admin-password-hash)
      shift
      ADMIN_PASSWORD_HASH="${1:-}"
      ;;
    --bootstrap-password)
      shift
      BOOTSTRAP_PASSWORD="${1:-}"
      ;;
    --session-secret)
      shift
      SESSION_SECRET="${1:-}"
      ;;
    --scope)
      shift
      SYSTEMD_SCOPE="${1:-}"
      ;;
    --enable-now)
      ENABLE_NOW=1
      ENABLE_NOW_SET=1
      ;;
    --force)
      FORCE=1
      ;;
    *)
      log_error "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
  shift
done

if [[ "${SYSTEMD_SCOPE}" != "system" && "${SYSTEMD_SCOPE}" != "user" ]]; then
  log_error "--scope must be either 'system' or 'user'."
  exit 1
fi

require_linux
require_interactive_or_unattended
validate_credential_flags
copy_example_env_if_needed
load_current_env_values

if (( !UNATTENDED )) && (( !INSTALL_PACKAGES_SET )); then
  if [[ "$(prompt_yes_no "Install required Ubuntu packages with apt" "yes")" == "yes" ]]; then
    INSTALL_PACKAGES=1
  fi
fi

if (( !UNATTENDED )) && (( !ENABLE_NOW_SET )); then
  if [[ "$(prompt_yes_no "Enable and start Elvern systemd services now" "yes")" == "yes" ]]; then
    ENABLE_NOW=1
  fi
fi

collect_values
normalize_and_validate_values
summarize_plan
confirm_plan_if_interactive
write_env_values
run_setup
post_install_validation

printf '\nInstall complete.\n'
printf 'Primary daily-use command: ./scripts/elvern-start.sh --open-browser\n'
printf 'Status command: ./scripts/elvern-status.sh\n'
