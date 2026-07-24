#!/bin/sh
set -eu
umask 022

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PRIVATE_DIR="${SCRIPT_DIR}/.elvern"
MANIFEST_TSV="${PRIVATE_DIR}/installer-manifest.tsv"
TREE_MANIFEST="${PRIVATE_DIR}/tree-manifest.tsv"
SELECTORS="${PRIVATE_DIR}/lib/platform-selectors.sh"
UNINSTALL_SOURCE="${PRIVATE_DIR}/uninstall/Uninstall-ElvernVlcOpener.sh"
INSTALL_PARENT="${HOME}/.local/lib"
INSTALL_DIR="${INSTALL_PARENT}/elvern-vlc-opener"
XDG_CONFIG_ROOT="${XDG_CONFIG_HOME:-${HOME}/.config}"
XDG_DATA_ROOT="${XDG_DATA_HOME:-${HOME}/.local/share}"
DESKTOP_DIR="${XDG_DATA_ROOT}/applications"
DESKTOP_FILE="${DESKTOP_DIR}/elvern-vlc-opener.desktop"
MIME_CONFIG_FILE="${XDG_CONFIG_ROOT}/mimeapps.list"
MIME_DATA_FILE="${DESKTOP_DIR}/mimeapps.list"
RUNTIME_OVERRIDE=""
STAGE_DIR=""
BACKUP_DIR=""
DESKTOP_BACKUP=""
PREVIOUS_PROTOCOL_DEFAULT=""
TRANSACTION_DIR=""
MIME_STATE_FILE=""
LOCK_DIR=""
LOCK_HELD=0
VERIFY_EXPECTED=""
VERIFY_ACTUAL=""
VERIFY_SEEN=""
VERIFY_CASE_SEEN=""
MANIFEST_META_SEEN=""
MANIFEST_RID_SEEN=""
DESKTOP_TEMP=""
STAGING_CREATED=0
OLD_INSTALL_BACKED_UP=0
NEW_INSTALL_PLACED=0
OLD_REGISTRATION_CAPTURED=0
REGISTRATION_MODIFIED=0
INSTALL_COMMITTED=0
INSTALL_NONCE="$$-$(date -u +%Y%m%dT%H%M%SZ)"
TAB=$(printf '\t')
CR=$(printf '\r')
LF='
'

fail() {
  echo "Elvern VLC Opener was not installed: $1" >&2
  exit 1
}

inject_failure() {
  inject_point=$1
  if [ "${ELVERN_INSTALL_TEST_MODE:-0}" = "1" ] \
    && [ "${ELVERN_INSTALL_TEST_FAIL_AT:-}" = "${inject_point}" ]; then
    fail "injected failure at ${inject_point}."
  fi
}

usage() {
  echo "Usage: ./Install-ElvernVlcOpener.sh [--runtime <supported-rid>]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --runtime)
      [ "$#" -ge 2 ] || {
        echo "Missing value for --runtime." >&2
        exit 1
      }
      RUNTIME_OVERRIDE=$2
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

safe_relative_path() {
  safe_value=$1
  case "${safe_value}" in
    ""|/*|*\\*|*"${TAB}"*|*"${CR}"*|*"${LF}"*)
      return 1
      ;;
  esac
  case "/${safe_value}/" in
    */../*|*/./*)
      return 1
      ;;
  esac
  return 0
}

valid_sha256() {
  valid_digest=$1
  [ "${#valid_digest}" -eq 64 ] || return 1
  case "${valid_digest}" in
    *[!0-9a-f]*)
      return 1
      ;;
  esac
  return 0
}

valid_unsigned_integer() {
  valid_number=$1
  case "${valid_number}" in
    ""|*[!0-9]*)
      return 1
      ;;
  esac
  [ "${valid_number}" -le 2147483648 ] 2>/dev/null
}

require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || fail "$1 is required to install Elvern VLC Opener."
}

cleanup() {
  original_status=$?
  trap - 0
  rollback_failed=0

  if [ "${INSTALL_COMMITTED}" -eq 0 ]; then
    if [ "${NEW_INSTALL_PLACED}" -eq 1 ] && [ -e "${INSTALL_DIR}" ]; then
      rm -rf "${INSTALL_DIR}" || rollback_failed=1
    fi
    if [ "${OLD_INSTALL_BACKED_UP}" -eq 1 ]; then
      if [ "${ELVERN_INSTALL_TEST_MODE:-0}" = "1" ] \
        && [ "${ELVERN_INSTALL_TEST_FAIL_ROLLBACK:-0}" = "1" ]; then
        rollback_failed=1
      elif [ -e "${INSTALL_DIR}" ] || [ ! -d "${BACKUP_DIR}" ]; then
        rollback_failed=1
      else
        cp -a "${BACKUP_DIR}" "${INSTALL_DIR}" || rollback_failed=1
      fi
    fi

    if [ "${REGISTRATION_MODIFIED}" -eq 1 ] \
      && [ "${OLD_REGISTRATION_CAPTURED}" -eq 1 ]; then
      if [ -n "${PREVIOUS_PROTOCOL_DEFAULT}" ]; then
        xdg-mime default "${PREVIOUS_PROTOCOL_DEFAULT}" x-scheme-handler/elvern-vlc \
          >/dev/null 2>&1 || rollback_failed=1
      fi

      if [ -n "${MIME_STATE_FILE}" ] && [ -f "${MIME_STATE_FILE}" ]; then
        while IFS="${TAB}" read -r restore_target restore_existed restore_mode restore_backup restore_extra; do
          [ -z "${restore_extra:-}" ] || {
            rollback_failed=1
            continue
          }
          if [ "${restore_existed}" = "1" ]; then
            mkdir -p "$(dirname -- "${restore_target}")" || {
              rollback_failed=1
              continue
            }
            restore_temp=$(mktemp "$(dirname -- "${restore_target}")/.elvern-mime-restore.XXXXXX") || {
              rollback_failed=1
              continue
            }
            if ! cp "${restore_backup}" "${restore_temp}" \
              || ! chmod "${restore_mode}" "${restore_temp}" \
              || ! mv "${restore_temp}" "${restore_target}"; then
              rm -f "${restore_temp}"
              rollback_failed=1
            fi
          elif [ -e "${restore_target}" ] || [ -L "${restore_target}" ]; then
            rm -f "${restore_target}" || rollback_failed=1
          fi
        done < "${MIME_STATE_FILE}"
      else
        rollback_failed=1
      fi

      if [ -n "${DESKTOP_BACKUP}" ]; then
        restore_temp=$(mktemp "${DESKTOP_DIR}/.elvern-desktop-restore.XXXXXX") \
          || rollback_failed=1
        if [ -n "${restore_temp:-}" ]; then
          cp "${DESKTOP_BACKUP}" "${restore_temp}" || rollback_failed=1
          chmod "$(stat -c '%a' "${DESKTOP_BACKUP}")" "${restore_temp}" \
            || rollback_failed=1
          mv "${restore_temp}" "${DESKTOP_FILE}" || rollback_failed=1
        fi
      elif [ -e "${DESKTOP_FILE}" ] || [ -L "${DESKTOP_FILE}" ]; then
        rm -f "${DESKTOP_FILE}" || rollback_failed=1
      fi

      if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || :
      fi
      restored_default=$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null || :)
      if [ -n "${PREVIOUS_PROTOCOL_DEFAULT}" ]; then
        [ "${restored_default}" = "${PREVIOUS_PROTOCOL_DEFAULT}" ] \
          || rollback_failed=1
      else
        [ "${restored_default}" != "elvern-vlc-opener.desktop" ] \
          || rollback_failed=1
      fi
    fi
  fi

  [ -z "${DESKTOP_TEMP}" ] || rm -f "${DESKTOP_TEMP}"
  [ -z "${STAGE_DIR}" ] || [ ! -d "${STAGE_DIR}" ] || rm -rf "${STAGE_DIR}"
  rm -f \
    "${VERIFY_EXPECTED:-}" \
    "${VERIFY_ACTUAL:-}" \
    "${VERIFY_SEEN:-}" \
    "${VERIFY_CASE_SEEN:-}" \
    "${MANIFEST_META_SEEN:-}" \
    "${MANIFEST_RID_SEEN:-}"

  if { [ "${INSTALL_COMMITTED}" -eq 1 ] || [ "${rollback_failed}" -eq 0 ]; } \
    && [ "${OLD_INSTALL_BACKED_UP}" -eq 1 ] \
    && [ -d "${BACKUP_DIR}" ]; then
    rm -rf "${BACKUP_DIR}"
  fi
  if [ -n "${TRANSACTION_DIR}" ] \
    && [ -d "${TRANSACTION_DIR}" ] \
    && { [ "${INSTALL_COMMITTED}" -eq 1 ] || [ "${rollback_failed}" -eq 0 ]; }; then
    rm -rf "${TRANSACTION_DIR}"
  fi
  if [ "${LOCK_HELD}" -eq 1 ] && [ -d "${LOCK_DIR}" ]; then
    rm -f "${LOCK_DIR}/owner"
    rmdir "${LOCK_DIR}" 2>/dev/null || :
  fi

  if [ "${rollback_failed}" -ne 0 ]; then
    echo "Elvern VLC Opener rollback could not be verified." >&2
    [ -z "${BACKUP_DIR}" ] \
      || echo "Preserved previous installation backup: ${BACKUP_DIR}" >&2
    [ -z "${TRANSACTION_DIR}" ] \
      || echo "Preserved MIME and desktop registration backups: ${TRANSACTION_DIR}" >&2
    echo "Repair only the preserved user-level Elvern registration before retrying." >&2
    exit 1
  fi
  exit "${original_status}"
}
trap cleanup 0

verify_package_tree() {
  [ -f "${TREE_MANIFEST}" ] && [ ! -L "${TREE_MANIFEST}" ] \
    || fail "the installer tree manifest is missing."
  if [ -n "$(find "${SCRIPT_DIR}" -type l -print -quit)" ]; then
    fail "the installer package contains an unsafe link."
  fi

  IFS= read -r tree_header < "${TREE_MANIFEST}" \
    || fail "the installer tree manifest is empty."
  [ "${tree_header}" = "path${TAB}size_bytes${TAB}sha256${TAB}file_class" ] \
    || fail "the installer tree manifest header is invalid."

  VERIFY_EXPECTED=$(mktemp "${TMPDIR:-/tmp}/elvern-tree-expected.XXXXXX") \
    || fail "a temporary verification file could not be created."
  VERIFY_ACTUAL=$(mktemp "${TMPDIR:-/tmp}/elvern-tree-actual.XXXXXX") \
    || fail "a temporary verification file could not be created."
  VERIFY_SEEN=$(mktemp "${TMPDIR:-/tmp}/elvern-tree-seen.XXXXXX") \
    || fail "a temporary verification file could not be created."
  VERIFY_CASE_SEEN=$(mktemp "${TMPDIR:-/tmp}/elvern-tree-case-seen.XXXXXX") \
    || fail "a temporary verification file could not be created."

  if ! sed '1d' "${TREE_MANIFEST}" | while IFS="${TAB}" read -r tree_path tree_size tree_digest tree_class tree_extra; do
    [ -n "${tree_path}${tree_size}${tree_digest}${tree_class}${tree_extra:-}" ] \
      || fail "the installer tree manifest contains an empty row."
    [ -z "${tree_extra:-}" ] \
      || fail "the installer tree manifest has an invalid row."
    safe_relative_path "${tree_path}" \
      || fail "the installer tree manifest contains an unsafe path."
    valid_unsigned_integer "${tree_size}" \
      || fail "the installer tree manifest contains invalid metadata."
    valid_sha256 "${tree_digest}" \
      || fail "the installer tree manifest contains invalid metadata."
    case "${tree_class}" in
      data|executable) ;;
      *) fail "the installer tree manifest contains invalid metadata." ;;
    esac
    tree_lower=$(printf '%s' "${tree_path}" | tr '[:upper:]' '[:lower:]')
    if grep -F -x -e "${tree_path}" "${VERIFY_SEEN}" >/dev/null 2>&1 \
      || grep -F -x -e "${tree_lower}" "${VERIFY_CASE_SEEN}" >/dev/null 2>&1; then
      fail "the installer tree manifest contains a duplicate or case-colliding path."
    fi
    printf '%s\n' "${tree_path}" >> "${VERIFY_SEEN}"
    printf '%s\n' "${tree_lower}" >> "${VERIFY_CASE_SEEN}"
    tree_full="${SCRIPT_DIR}/${tree_path}"
    [ -f "${tree_full}" ] && [ ! -L "${tree_full}" ] \
      || fail "an installer file is missing or unsafe."
    tree_actual_size=$(wc -c < "${tree_full}" | tr -d '[:space:]')
    [ "${tree_actual_size}" = "${tree_size}" ] \
      || fail "an installer file size check failed."
    tree_actual_digest=$(sha256sum "${tree_full}" | awk '{print $1}')
    [ "${tree_actual_digest}" = "${tree_digest}" ] \
      || fail "an installer file SHA-256 check failed."
    printf '%s\n' "${tree_path}" >> "${VERIFY_EXPECTED}"
  done; then
    fail "the installer tree manifest could not be verified."
  fi
  [ -s "${VERIFY_EXPECTED}" ] \
    || fail "the installer tree manifest is empty."

  find "${SCRIPT_DIR}" -type f -print | while IFS= read -r tree_full; do
    tree_path=${tree_full#"${SCRIPT_DIR}/"}
    case "${tree_path}" in
      .elvern/tree-manifest.tsv|*/.DS_Store)
        continue
        ;;
    esac
    printf '%s\n' "${tree_path}" >> "${VERIFY_ACTUAL}"
  done
  LC_ALL=C sort -o "${VERIFY_EXPECTED}" "${VERIFY_EXPECTED}"
  LC_ALL=C sort -o "${VERIFY_ACTUAL}" "${VERIFY_ACTUAL}"
  cmp -s "${VERIFY_EXPECTED}" "${VERIFY_ACTUAL}" \
    || fail "the installer package contains a missing or unexpected file."
}

backup_mime_path() {
  mime_target=$1
  mime_index=$2
  case "${mime_target}" in
    *"${TAB}"*|*"${CR}"*|*"${LF}"*)
      fail "a user MIME registration path contains unsupported control characters."
      ;;
  esac
  if [ -f "${mime_target}" ] && [ ! -L "${mime_target}" ]; then
    mime_mode=$(stat -c '%a' "${mime_target}")
    mime_backup="${TRANSACTION_DIR}/mimeapps-${mime_index}"
    cp -p "${mime_target}" "${mime_backup}" \
      || fail "an existing user MIME registration could not be backed up."
    printf '%s\t1\t%s\t%s\n' \
      "${mime_target}" "${mime_mode}" "${mime_backup}" >> "${MIME_STATE_FILE}"
  elif [ -e "${mime_target}" ] || [ -L "${mime_target}" ]; then
    fail "a user MIME registration path is not a regular file."
  else
    printf '%s\t0\t-\t-\n' "${mime_target}" >> "${MIME_STATE_FILE}"
  fi
}

for required_command in \
  sha256sum xdg-mime mktemp stat cp mv chmod mkdir rm dirname find grep sed cat \
  sort cmp wc tr awk uname date
do
  require_command "${required_command}"
done

verify_package_tree
[ -f "${MANIFEST_TSV}" ] && [ ! -L "${MANIFEST_TSV}" ] \
  || fail "the verified installer manifest is missing."
[ -f "${SELECTORS}" ] && [ ! -L "${SELECTORS}" ] \
  || fail "the verified platform selector is missing."
[ -f "${UNINSTALL_SOURCE}" ] && [ ! -L "${UNINSTALL_SOURCE}" ] \
  || fail "the verified uninstaller is missing."

# The POSIX selector was covered by the verified package tree before execution.
# shellcheck disable=SC1090
. "${SELECTORS}"
if [ -n "${RUNTIME_OVERRIDE}" ]; then
  RUNTIME_ID=${RUNTIME_OVERRIDE}
else
  LIBC_FAMILY=$(detect_linux_libc) \
    || fail "the system libc could not be identified."
  RUNTIME_ID=$(select_linux_runtime "$(uname -m)" "${LIBC_FAMILY}") \
    || fail "the CPU or libc is unsupported."
fi

SCHEMA=""
HELPER_VERSION=""
TARGET_FRAMEWORK=""
RUNTIME_FAMILY=""
DEPLOYMENT_MODE=""
PACKAGE_TARGET=""
BOUND_ORIGIN_SHA256=""
PAYLOAD_RELATIVE_PATH=""
EXPECTED_SHA=""
EXPECTED_SIZE=""
PAYLOAD_EXECUTABLE=""
MANIFEST_META_SEEN=$(mktemp "${TMPDIR:-/tmp}/elvern-meta-seen.XXXXXX") \
  || fail "a temporary manifest verification file could not be created."
MANIFEST_RID_SEEN=$(mktemp "${TMPDIR:-/tmp}/elvern-rid-seen.XXXXXX") \
  || fail "a temporary manifest verification file could not be created."

while IFS="${TAB}" read -r manifest_kind manifest_field manifest_value manifest_fourth manifest_fifth manifest_sixth manifest_extra; do
  [ -z "${manifest_extra:-}" ] \
    || fail "the verified installer manifest has an invalid row."
  case "${manifest_kind}" in
    meta)
      [ -z "${manifest_fourth}${manifest_fifth}${manifest_sixth}" ] \
        || fail "the verified installer manifest has an invalid metadata row."
      case "${manifest_field}" in
        schema_version|helper_version|target_framework|runtime_family|deployment_mode|package_target|bound_origin_sha256) ;;
        *) fail "the verified installer manifest contains unknown metadata." ;;
      esac
      if grep -F -x -e "${manifest_field}" "${MANIFEST_META_SEEN}" >/dev/null 2>&1; then
        fail "the verified installer manifest repeats mandatory metadata."
      fi
      printf '%s\n' "${manifest_field}" >> "${MANIFEST_META_SEEN}"
      case "${manifest_field}" in
        schema_version) SCHEMA=${manifest_value} ;;
        helper_version) HELPER_VERSION=${manifest_value} ;;
        target_framework) TARGET_FRAMEWORK=${manifest_value} ;;
        runtime_family) RUNTIME_FAMILY=${manifest_value} ;;
        deployment_mode) DEPLOYMENT_MODE=${manifest_value} ;;
        package_target) PACKAGE_TARGET=${manifest_value} ;;
        bound_origin_sha256) BOUND_ORIGIN_SHA256=${manifest_value} ;;
      esac
      ;;
    payload)
      [ -n "${manifest_field}${manifest_value}${manifest_fourth}${manifest_fifth}${manifest_sixth}" ] \
        || fail "the verified installer manifest has an invalid payload row."
      if grep -F -x -e "${manifest_field}" "${MANIFEST_RID_SEEN}" >/dev/null 2>&1; then
        fail "the verified installer manifest repeats a runtime."
      fi
      printf '%s\n' "${manifest_field}" >> "${MANIFEST_RID_SEEN}"
      safe_relative_path "${manifest_value}" \
        || fail "the verified installer manifest contains an unsafe payload path."
      valid_sha256 "${manifest_fourth}" \
        || fail "the verified installer manifest contains invalid payload metadata."
      valid_unsigned_integer "${manifest_fifth}" \
        || fail "the verified installer manifest contains invalid payload metadata."
      [ "${manifest_sixth}" = "Elvern.VlcOpener" ] \
        || fail "the verified installer manifest contains invalid payload metadata."
      if [ "${manifest_field}" = "${RUNTIME_ID}" ]; then
        PAYLOAD_RELATIVE_PATH=${manifest_value}
        EXPECTED_SHA=${manifest_fourth}
        EXPECTED_SIZE=${manifest_fifth}
        PAYLOAD_EXECUTABLE=${manifest_sixth}
      fi
      ;;
    *)
      fail "the verified installer manifest contains an unknown row."
      ;;
  esac
done < "${MANIFEST_TSV}"

for mandatory_meta in \
  schema_version helper_version target_framework runtime_family deployment_mode \
  package_target bound_origin_sha256
do
  grep -F -x -e "${mandatory_meta}" "${MANIFEST_META_SEEN}" >/dev/null 2>&1 \
    || fail "the verified installer manifest is missing mandatory metadata."
done
[ "${SCHEMA}" = "desktop-helper-installer-manifest-v2" ] \
  || fail "the verified installer manifest schema is unsupported."
[ "${TARGET_FRAMEWORK}" = "net10.0" ] \
  && [ "${RUNTIME_FAMILY}" = "10.0" ] \
  && [ "${DEPLOYMENT_MODE}" = "self_contained" ] \
  && [ "${PACKAGE_TARGET}" = "linux-universal" ] \
  && valid_sha256 "${BOUND_ORIGIN_SHA256}" \
  || fail "this is not the standard self-contained Linux package."
printf '%s\n' "${HELPER_VERSION}" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' >/dev/null 2>&1 \
  || fail "the verified installer manifest has an invalid version."
[ -n "${PAYLOAD_RELATIVE_PATH}" ] \
  || fail "the selected runtime is not in the package allowlist."
[ "${PAYLOAD_EXECUTABLE}" = "Elvern.VlcOpener" ] \
  || fail "the selected payload executable is invalid."

PAYLOAD="${PRIVATE_DIR}/${PAYLOAD_RELATIVE_PATH}"
[ -f "${PAYLOAD}" ] && [ ! -L "${PAYLOAD}" ] \
  || fail "the ${RUNTIME_ID} payload is missing."
[ "$(wc -c < "${PAYLOAD}" | tr -d '[:space:]')" = "${EXPECTED_SIZE}" ] \
  || fail "the payload size check failed."
[ "$(sha256sum "${PAYLOAD}" | awk '{print $1}')" = "${EXPECTED_SHA}" ] \
  || fail "the payload SHA-256 check failed."

mkdir -p "${INSTALL_PARENT}" "${DESKTOP_DIR}" "${XDG_CONFIG_ROOT}" \
  || fail "the user-level installation directories could not be created."
LOCK_DIR="${INSTALL_PARENT}/.elvern-vlc-opener-install.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  fail "another Helper install or uninstall may be running. Remove ${LOCK_DIR} manually only after confirming no transaction is active."
fi
LOCK_HELD=1
printf 'pid=%s\nstarted_at=%s\ntransaction_nonce=%s\n' \
  "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${INSTALL_NONCE}" \
  > "${LOCK_DIR}/owner"
chmod 644 "${LOCK_DIR}/owner"
TRANSACTION_DIR=$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-transaction.XXXXXX") \
  || fail "the install transaction directory could not be created."
MIME_STATE_FILE="${TRANSACTION_DIR}/mime-state.tsv"
: > "${MIME_STATE_FILE}"
STAGE_DIR=$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-stage.XXXXXX") \
  || fail "a staged installation directory could not be created."
STAGING_CREATED=1
inject_failure "staging_created"
cp "${PAYLOAD}" "${STAGE_DIR}/Elvern.VlcOpener"
chmod 755 "${STAGE_DIR}/Elvern.VlcOpener"
cp "${UNINSTALL_SOURCE}" "${STAGE_DIR}/Uninstall-ElvernVlcOpener.sh"
chmod 755 "${STAGE_DIR}/Uninstall-ElvernVlcOpener.sh"
"${STAGE_DIR}/Elvern.VlcOpener" --version >/dev/null \
  || fail "the staged payload failed its version check."

PREVIOUS_PROTOCOL_DEFAULT=$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null || :)
SAFE_PREVIOUS_PROTOCOL_DEFAULT=""
if [ -n "${PREVIOUS_PROTOCOL_DEFAULT}" ] \
  && printf '%s\n' "${PREVIOUS_PROTOCOL_DEFAULT}" \
    | grep -E '^[A-Za-z0-9._-]+\.desktop$' >/dev/null 2>&1; then
  SAFE_PREVIOUS_PROTOCOL_DEFAULT=${PREVIOUS_PROTOCOL_DEFAULT}
fi
cat > "${STAGE_DIR}/install-state.tsv" <<EOF
schema_version	elvern-desktop-helper-install-state-v1
helper_version	${HELPER_VERSION}
product_id	elvern-vlc-opener
package_target	${PACKAGE_TARGET}
transaction_nonce	${INSTALL_NONCE}
previous_protocol_default	${SAFE_PREVIOUS_PROTOCOL_DEFAULT}
EOF
chmod 644 "${STAGE_DIR}/install-state.tsv"
backup_mime_path "${MIME_CONFIG_FILE}" 0
backup_mime_path "${MIME_DATA_FILE}" 1
if [ -f "${DESKTOP_FILE}" ]; then
  [ ! -L "${DESKTOP_FILE}" ] \
    || fail "the existing Elvern desktop entry is unsafe."
  DESKTOP_BACKUP="${TRANSACTION_DIR}/elvern-vlc-opener.desktop"
  cp -p "${DESKTOP_FILE}" "${DESKTOP_BACKUP}" \
    || fail "the existing Elvern desktop entry could not be backed up."
elif [ -e "${DESKTOP_FILE}" ] || [ -L "${DESKTOP_FILE}" ]; then
  fail "the existing Elvern desktop entry is not a regular file."
fi
OLD_REGISTRATION_CAPTURED=1

if [ -d "${INSTALL_DIR}" ]; then
  BACKUP_DIR=$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-backup.XXXXXX") \
    || fail "a unique backup path could not be reserved."
  rmdir "${BACKUP_DIR}" \
    || fail "the unique backup path could not be prepared."
  inject_failure "first_backup_move"
  mv "${INSTALL_DIR}" "${BACKUP_DIR}" \
    || fail "the existing installation could not be staged for upgrade."
  OLD_INSTALL_BACKED_UP=1
elif [ -e "${INSTALL_DIR}" ] || [ -L "${INSTALL_DIR}" ]; then
  fail "the existing installation path is not a directory."
fi

inject_failure "new_placement"
mv "${STAGE_DIR}" "${INSTALL_DIR}" \
  || fail "the new Helper could not replace the existing installation."
STAGE_DIR=""
NEW_INSTALL_PLACED=1

DESKTOP_TEMP=$(mktemp "${DESKTOP_DIR}/.elvern-vlc-opener.desktop.XXXXXX") \
  || fail "the desktop entry could not be staged."
cat > "${DESKTOP_TEMP}" <<EOF
[Desktop Entry]
Type=Application
Name=Elvern VLC Opener
Exec="${INSTALL_DIR}/Elvern.VlcOpener" %u
Terminal=false
NoDisplay=true
MimeType=x-scheme-handler/elvern-vlc;
Categories=AudioVideo;Video;
EOF
chmod 644 "${DESKTOP_TEMP}"
mv "${DESKTOP_TEMP}" "${DESKTOP_FILE}"
DESKTOP_TEMP=""
REGISTRATION_MODIFIED=1
inject_failure "registration"
xdg-mime default elvern-vlc-opener.desktop x-scheme-handler/elvern-vlc \
  || fail "xdg-mime could not register the protocol handler."
inject_failure "registration_validation"
[ "$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null || :)" = "elvern-vlc-opener.desktop" ] \
  || fail "the registered protocol handler could not be verified."
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || :
fi
inject_failure "final_binary_validation"
"${INSTALL_DIR}/Elvern.VlcOpener" --version >/dev/null \
  || fail "the installed Helper failed its final version check."
INSTALL_COMMITTED=1
echo "Installed Elvern VLC Opener ${HELPER_VERSION} for ${RUNTIME_ID}."
echo "Registered elvern-vlc:// without administrator privileges."
