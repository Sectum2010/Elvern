#!/bin/sh
set -eu
umask 022

INSTALL_PARENT="${HOME}/.local/lib"
INSTALL_DIR="${INSTALL_PARENT}/elvern-vlc-opener"
LOCK_DIR="${INSTALL_PARENT}/.elvern-vlc-opener-install.lock"
XDG_CONFIG_ROOT="${XDG_CONFIG_HOME:-${HOME}/.config}"
XDG_DATA_ROOT="${XDG_DATA_HOME:-${HOME}/.local/share}"
XDG_DATA_SEARCH_PATH="${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
DESKTOP_DIR="${XDG_DATA_ROOT}/applications"
DESKTOP_FILE="${DESKTOP_DIR}/elvern-vlc-opener.desktop"
MIME_CONFIG_FILE="${XDG_CONFIG_ROOT}/mimeapps.list"
MIME_DATA_FILE="${DESKTOP_DIR}/mimeapps.list"
STATE_FILE="${INSTALL_DIR}/install-state.tsv"
TRANSACTION_DIR=""
INSTALL_BACKUP=""
DESKTOP_BACKUP=""
MIME_STATE_FILE=""
LOCK_HELD=0
INSTALL_MOVED=0
DESKTOP_REMOVED=0
MIME_MODIFIED=0
UNINSTALL_COMMITTED=0
ROLLBACK_FAILED=0
CURRENT_DEFAULT=""
PREVIOUS_DEFAULT=""
TAB=$(printf '\t')
CR=$(printf '\r')
LF='
'
TRANSACTION_NONCE="$$-$(date -u +%Y%m%dT%H%M%SZ)"

fail() {
  echo "Elvern VLC Opener was not removed: $1" >&2
  exit 1
}

inject_failure() {
  point=$1
  if [ "${ELVERN_UNINSTALL_TEST_MODE:-0}" = "1" ] \
    && [ "${ELVERN_UNINSTALL_TEST_FAIL_AT:-}" = "${point}" ]; then
    fail "injected failure at ${point}."
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || fail "$1 is required to remove Elvern VLC Opener."
}

query_default_handler() {
  output=$(mktemp "${TMPDIR:-/tmp}/elvern-xdg-query.XXXXXX") \
    || fail "a temporary protocol query file could not be created."
  if xdg-mime query default x-scheme-handler/elvern-vlc > "${output}" 2>/dev/null; then
    value=$(cat "${output}")
    rm -f "${output}"
  else
    rm -f "${output}"
    fail "the current protocol handler could not be queried safely."
  fi
  if [ -n "${value}" ] && ! safe_desktop_basename "${value}"; then
    fail "the current protocol handler is unsafe."
  fi
  printf '%s\n' "${value}"
}

desktop_handler_exists() {
  handler=$1
  safe_desktop_basename "${handler}" || return 1
  if [ -f "${XDG_DATA_ROOT}/applications/${handler}" ] \
    && [ ! -L "${XDG_DATA_ROOT}/applications/${handler}" ]; then
    return 0
  fi
  old_ifs=${IFS}
  IFS=:
  for data_root in ${XDG_DATA_SEARCH_PATH}; do
    IFS=${old_ifs}
    [ -n "${data_root}" ] || data_root="${HOME}/.local/share"
    case "${data_root}" in
      /*) ;;
      *) return 1 ;;
    esac
    candidate="${data_root}/applications/${handler}"
    if [ -f "${candidate}" ] && [ ! -L "${candidate}" ]; then
      return 0
    fi
    IFS=:
  done
  IFS=${old_ifs}
  return 1
}

mime_file_has_elvern_token() {
  target=$1
  [ -f "${target}" ] && [ ! -L "${target}" ] || return 1
  awk '
    BEGIN { in_default = 0; found = 0 }
    /^\[/ {
      in_default = ($0 == "[Default Applications]")
      next
    }
    in_default && /^x-scheme-handler\/elvern-vlc=/ {
      value = substr($0, index($0, "=") + 1)
      count = split(value, tokens, ";")
      for (i = 1; i <= count; i++) {
        if (tokens[i] == "elvern-vlc-opener.desktop") {
          found = 1
        }
      }
    }
    END { exit(found ? 0 : 1) }
  ' "${target}"
}

safe_desktop_basename() {
  case "$1" in
    ""|.desktop|*[!A-Za-z0-9._-]*)
      return 1
      ;;
    *.desktop)
      return 0
      ;;
  esac
  return 1
}

backup_regular_file() {
  target=$1
  label=$2
  if [ -f "${target}" ] && [ ! -L "${target}" ]; then
    mode=$(stat -c '%a' "${target}") \
      || fail "the ${label} mode could not be read."
    backup="${TRANSACTION_DIR}/${label}.backup"
    cp -p "${target}" "${backup}" \
      || fail "the ${label} could not be backed up."
    printf '%s\t1\t%s\t%s\n' "${target}" "${mode}" "${backup}" \
      >> "${MIME_STATE_FILE}"
  elif [ -e "${target}" ] || [ -L "${target}" ]; then
    fail "the ${label} is not a safe regular file."
  else
    printf '%s\t0\t-\t-\n' "${target}" >> "${MIME_STATE_FILE}"
  fi
}

restore_backed_up_files() {
  [ -f "${MIME_STATE_FILE}" ] || return 1
  while IFS="${TAB}" read -r target existed mode backup extra; do
    [ -z "${extra:-}" ] || return 1
    if [ "${existed}" = "1" ]; then
      mkdir -p "$(dirname -- "${target}")" || return 1
      temp=$(mktemp "$(dirname -- "${target}")/.elvern-restore.XXXXXX") \
        || return 1
      if ! cp "${backup}" "${temp}" \
        || ! chmod "${mode}" "${temp}" \
        || ! mv "${temp}" "${target}"; then
        rm -f "${temp}"
        return 1
      fi
    elif [ -e "${target}" ] || [ -L "${target}" ]; then
      rm -f "${target}" || return 1
    fi
  done < "${MIME_STATE_FILE}"
}

remove_elvern_default_from_file() {
  target=$1
  [ -e "${target}" ] || return 0
  [ -f "${target}" ] && [ ! -L "${target}" ] \
    || fail "a user MIME registration path is unsafe."
  mode=$(stat -c '%a' "${target}") \
    || fail "a user MIME registration mode could not be read."
  temp=$(mktemp "$(dirname -- "${target}")/.elvern-mime-uninstall.XXXXXX") \
    || fail "a user MIME registration update could not be staged."
  if ! awk '
    BEGIN { in_default = 0 }
    /^\[/ {
      in_default = ($0 == "[Default Applications]")
      print
      next
    }
    in_default && /^x-scheme-handler\/elvern-vlc=/ {
      key = "x-scheme-handler/elvern-vlc="
      value = substr($0, length(key) + 1)
      count = split(value, tokens, ";")
      output = ""
      for (i = 1; i <= count; i++) {
        if (tokens[i] != "" && tokens[i] != "elvern-vlc-opener.desktop") {
          output = output tokens[i] ";"
        }
      }
      if (output != "") {
        print key output
      }
      next
    }
    { print }
  ' "${target}" > "${temp}" \
    || ! chmod "${mode}" "${temp}" \
    || ! mv "${temp}" "${target}"; then
    rm -f "${temp}"
    fail "the Elvern MIME registration could not be removed."
  fi
}

read_installed_state() {
  [ -e "${STATE_FILE}" ] || return 0
  [ -f "${STATE_FILE}" ] && [ ! -L "${STATE_FILE}" ] \
    || fail "the installed ownership state is unsafe."
  schema=""
  helper_version=""
  product=""
  package_target=""
  state_nonce=""
  seen=""
  state_count=0
  while IFS="${TAB}" read -r key value extra; do
    [ -z "${extra:-}" ] || fail "the installed ownership state is invalid."
    case "${key}" in
      schema_version|helper_version|product_id|package_target|transaction_nonce|previous_protocol_default) ;;
      *) fail "the installed ownership state contains an unknown field." ;;
    esac
    case "
${seen}
" in
      *"
${key}
"*) fail "the installed ownership state repeats a field." ;;
    esac
    seen="${seen}
${key}"
    state_count=$((state_count + 1))
    case "${key}" in
      schema_version) schema=${value} ;;
      helper_version) helper_version=${value} ;;
      product_id) product=${value} ;;
      package_target) package_target=${value} ;;
      transaction_nonce) state_nonce=${value} ;;
      previous_protocol_default) PREVIOUS_DEFAULT=${value} ;;
    esac
  done < "${STATE_FILE}"
  [ "${state_count}" -eq 6 ] \
    && [ "${schema}" = "elvern-desktop-helper-install-state-v1" ] \
    && [ "${product}" = "elvern-vlc-opener" ] \
    && [ "${package_target}" = "linux-universal" ] \
    || fail "the installed ownership state does not belong to Elvern."
  printf '%s\n' "${helper_version}" \
    | grep -E '^[A-Za-z0-9][A-Za-z0-9._+-]*$' >/dev/null 2>&1 \
    || fail "the installed ownership state has an invalid Helper version."
  printf '%s\n' "${state_nonce}" \
    | grep -E '^[A-Za-z0-9][A-Za-z0-9._-]*$' >/dev/null 2>&1 \
    || fail "the installed ownership state has an invalid transaction nonce."
  if [ -n "${PREVIOUS_DEFAULT}" ] && ! safe_desktop_basename "${PREVIOUS_DEFAULT}"; then
    fail "the previous protocol handler in installed state is unsafe."
  fi
}

cleanup() {
  status=$?
  trap - 0
  if [ "${UNINSTALL_COMMITTED}" -eq 0 ] \
    && { [ "${INSTALL_MOVED}" -eq 1 ] \
      || [ "${DESKTOP_REMOVED}" -eq 1 ] \
      || [ "${MIME_MODIFIED}" -eq 1 ]; }; then
    if [ -n "${CURRENT_DEFAULT}" ]; then
      xdg-mime default "${CURRENT_DEFAULT}" x-scheme-handler/elvern-vlc \
        >/dev/null 2>&1 || ROLLBACK_FAILED=1
    fi
    restore_backed_up_files || ROLLBACK_FAILED=1
    if [ "${INSTALL_MOVED}" -eq 1 ]; then
      if [ -e "${INSTALL_DIR}" ] || [ ! -d "${INSTALL_BACKUP}" ]; then
        ROLLBACK_FAILED=1
      elif ! mv "${INSTALL_BACKUP}" "${INSTALL_DIR}"; then
        ROLLBACK_FAILED=1
      else
        INSTALL_MOVED=0
      fi
    fi
    if restored=$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null) \
      && { [ -z "${restored}" ] || safe_desktop_basename "${restored}"; }; then
      [ "${restored}" = "${CURRENT_DEFAULT}" ] || ROLLBACK_FAILED=1
    else
      ROLLBACK_FAILED=1
    fi
  fi

  if [ "${UNINSTALL_COMMITTED}" -eq 1 ]; then
    [ -z "${INSTALL_BACKUP}" ] || [ ! -d "${INSTALL_BACKUP}" ] \
      || rm -rf "${INSTALL_BACKUP}"
    [ -z "${TRANSACTION_DIR}" ] || [ ! -d "${TRANSACTION_DIR}" ] \
      || rm -rf "${TRANSACTION_DIR}"
  elif [ "${ROLLBACK_FAILED}" -eq 0 ]; then
    [ -z "${TRANSACTION_DIR}" ] || [ ! -d "${TRANSACTION_DIR}" ] \
      || rm -rf "${TRANSACTION_DIR}"
  fi
  if [ "${LOCK_HELD}" -eq 1 ]; then
    rm -f "${LOCK_DIR}/owner"
    rmdir "${LOCK_DIR}" 2>/dev/null || :
  fi
  if [ "${ROLLBACK_FAILED}" -ne 0 ]; then
    echo "Elvern VLC Opener uninstall rollback could not be verified." >&2
    [ -z "${INSTALL_BACKUP}" ] \
      || echo "Preserved installation backup: ${INSTALL_BACKUP}" >&2
    [ -z "${TRANSACTION_DIR}" ] \
      || echo "Preserved registration backups: ${TRANSACTION_DIR}" >&2
    exit 1
  fi
  exit "${status}"
}
trap cleanup 0

for command in \
  awk cat chmod cp date dirname grep mkdir mktemp mv rm rmdir stat xdg-mime
do
  require_command "${command}"
done

mkdir -p "${INSTALL_PARENT}" "${DESKTOP_DIR}" "${XDG_CONFIG_ROOT}" \
  || fail "the user-level directories are unavailable."
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  fail "another Helper install or uninstall may be running. Remove ${LOCK_DIR} manually only after confirming no transaction is active."
fi
LOCK_HELD=1
printf 'pid=%s\nstarted_at=%s\ntransaction_nonce=%s\n' \
  "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${TRANSACTION_NONCE}" \
  > "${LOCK_DIR}/owner"
chmod 644 "${LOCK_DIR}/owner"

CURRENT_DEFAULT=$(query_default_handler)
STALE_MAPPING=0
if mime_file_has_elvern_token "${MIME_CONFIG_FILE}" \
  || mime_file_has_elvern_token "${MIME_DATA_FILE}"; then
  STALE_MAPPING=1
fi
if [ ! -e "${INSTALL_DIR}" ] && [ ! -e "${DESKTOP_FILE}" ] \
  && [ "${STALE_MAPPING}" -eq 0 ] \
  && [ "${CURRENT_DEFAULT}" != "elvern-vlc-opener.desktop" ]; then
  echo "Elvern VLC Opener is not installed for this user."
  UNINSTALL_COMMITTED=1
  exit 0
fi
[ ! -L "${INSTALL_DIR}" ] \
  || fail "the installed Elvern path is an unsafe link."
if [ -e "${INSTALL_DIR}" ]; then
  [ -d "${INSTALL_DIR}" ] \
    || fail "the installed Elvern path is not a directory."
  read_installed_state
fi
[ ! -L "${DESKTOP_FILE}" ] \
  || fail "the Elvern desktop entry is an unsafe link."

TRANSACTION_DIR=$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-uninstall.XXXXXX") \
  || fail "the uninstall transaction could not be created."
MIME_STATE_FILE="${TRANSACTION_DIR}/file-state.tsv"
: > "${MIME_STATE_FILE}"
backup_regular_file "${MIME_CONFIG_FILE}" "mime-config"
if [ "${MIME_DATA_FILE}" != "${MIME_CONFIG_FILE}" ]; then
  backup_regular_file "${MIME_DATA_FILE}" "mime-data"
fi
if [ -f "${DESKTOP_FILE}" ]; then
  DESKTOP_BACKUP="${TRANSACTION_DIR}/desktop-entry.backup"
  cp -p "${DESKTOP_FILE}" "${DESKTOP_BACKUP}" \
    || fail "the Elvern desktop entry could not be backed up."
  printf '%s\t1\t%s\t%s\n' \
    "${DESKTOP_FILE}" "$(stat -c '%a' "${DESKTOP_FILE}")" "${DESKTOP_BACKUP}" \
    >> "${MIME_STATE_FILE}"
elif [ -e "${DESKTOP_FILE}" ]; then
  fail "the Elvern desktop entry is not a regular file."
else
  printf '%s\t0\t-\t-\n' "${DESKTOP_FILE}" >> "${MIME_STATE_FILE}"
fi

if [ -d "${INSTALL_DIR}" ]; then
  INSTALL_BACKUP=$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-uninstall-backup.XXXXXX") \
    || fail "the installation backup path could not be reserved."
  rmdir "${INSTALL_BACKUP}" \
    || fail "the installation backup path could not be prepared."
  inject_failure "install_backup_move"
  mv "${INSTALL_DIR}" "${INSTALL_BACKUP}" \
    || fail "the installed Helper could not be staged for removal."
  INSTALL_MOVED=1
fi

remove_elvern_default_from_file "${MIME_CONFIG_FILE}"
if [ "${MIME_DATA_FILE}" != "${MIME_CONFIG_FILE}" ]; then
  remove_elvern_default_from_file "${MIME_DATA_FILE}"
fi
MIME_MODIFIED=1
if [ "${CURRENT_DEFAULT}" = "elvern-vlc-opener.desktop" ] \
  && [ -n "${PREVIOUS_DEFAULT}" ]; then
  if desktop_handler_exists "${PREVIOUS_DEFAULT}"; then
    xdg-mime default "${PREVIOUS_DEFAULT}" x-scheme-handler/elvern-vlc \
      || fail "the previous protocol handler could not be restored."
  else
    echo "Warning: the previous protocol handler is no longer installed." >&2
  fi
fi
inject_failure "mime_update"

if [ -e "${DESKTOP_FILE}" ]; then
  inject_failure "desktop_delete"
  rm -f "${DESKTOP_FILE}" \
    || fail "the Elvern desktop entry could not be removed."
  DESKTOP_REMOVED=1
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || :
fi
inject_failure "default_validation"
AFTER_DEFAULT=$(query_default_handler)
if [ "${CURRENT_DEFAULT}" = "elvern-vlc-opener.desktop" ]; then
  [ "${AFTER_DEFAULT}" != "elvern-vlc-opener.desktop" ] \
    || fail "Elvern is still the default protocol handler."
  if [ -n "${PREVIOUS_DEFAULT}" ] \
    && desktop_handler_exists "${PREVIOUS_DEFAULT}"; then
    [ "${AFTER_DEFAULT}" = "${PREVIOUS_DEFAULT}" ] \
      || fail "the previous protocol handler restore could not be verified."
  fi
elif [ "${AFTER_DEFAULT}" != "${CURRENT_DEFAULT}" ]; then
  fail "the current third-party protocol handler changed unexpectedly."
fi

UNINSTALL_COMMITTED=1
echo "Removed Elvern VLC Opener from this user account."
