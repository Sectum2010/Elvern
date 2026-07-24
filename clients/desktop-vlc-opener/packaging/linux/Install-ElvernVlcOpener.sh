#!/usr/bin/env bash
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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
LOCK_DIR=""
STAGING_CREATED=0
OLD_INSTALL_EXISTED=0
OLD_INSTALL_BACKED_UP=0
NEW_INSTALL_PLACED=0
OLD_REGISTRATION_CAPTURED=0
REGISTRATION_MODIFIED=0
FINAL_VALIDATION_PASSED=0
INSTALL_COMMITTED=0
MIME_PATHS=("${MIME_CONFIG_FILE}" "${MIME_DATA_FILE}")
MIME_EXISTED=()
MIME_MODES=()
MIME_BACKUPS=()

inject_failure() {
  local point="$1"
  if [[ "${ELVERN_INSTALL_TEST_MODE:-0}" == "1" && "${ELVERN_INSTALL_TEST_FAIL_AT:-}" == "${point}" ]]; then
    fail "injected failure at ${point}."
  fi
}

usage() {
  echo "Usage: ./Install-ElvernVlcOpener.sh [--runtime <supported-rid>]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)
      [[ $# -ge 2 ]] || { echo "Missing value for --runtime." >&2; exit 1; }
      RUNTIME_OVERRIDE="$2"
      shift 2
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

cleanup() {
  local rollback_failed=0 index target temp_path restored_default
  if [[ ${INSTALL_COMMITTED} -eq 0 ]]; then
    if [[ ${NEW_INSTALL_PLACED} -eq 1 && -e "${INSTALL_DIR}" ]]; then
      rm -rf "${INSTALL_DIR}" || rollback_failed=1
    fi
    if [[ ${OLD_INSTALL_BACKED_UP} -eq 1 ]]; then
      if [[
        "${ELVERN_INSTALL_TEST_MODE:-0}" == "1"
        && "${ELVERN_INSTALL_TEST_FAIL_ROLLBACK:-0}" == "1"
      ]]; then
        rollback_failed=1
      elif [[ -e "${INSTALL_DIR}" || ! -d "${BACKUP_DIR}" ]]; then
        rollback_failed=1
      else
        cp -a "${BACKUP_DIR}" "${INSTALL_DIR}" || rollback_failed=1
      fi
    fi
    if [[ ${REGISTRATION_MODIFIED} -eq 1 && ${OLD_REGISTRATION_CAPTURED} -eq 1 ]]; then
      if [[ -n "${PREVIOUS_PROTOCOL_DEFAULT}" ]]; then
        xdg-mime default "${PREVIOUS_PROTOCOL_DEFAULT}" x-scheme-handler/elvern-vlc \
          >/dev/null 2>&1 || rollback_failed=1
      fi
      for index in "${!MIME_PATHS[@]}"; do
        target="${MIME_PATHS[$index]}"
        if [[ "${MIME_EXISTED[$index]}" == "1" ]]; then
          mkdir -p "$(dirname "${target}")" || rollback_failed=1
          temp_path="$(mktemp "$(dirname "${target}")/.elvern-mime-restore.XXXXXX")" || {
            rollback_failed=1
            continue
          }
          if ! cp "${MIME_BACKUPS[$index]}" "${temp_path}" \
            || ! chmod "${MIME_MODES[$index]}" "${temp_path}" \
            || ! mv "${temp_path}" "${target}"; then
            rm -f "${temp_path}"
            rollback_failed=1
          fi
        elif [[ -e "${target}" ]]; then
          rm -f "${target}" || rollback_failed=1
        fi
      done
      if [[ -n "${DESKTOP_BACKUP}" ]]; then
        temp_path="$(mktemp "${DESKTOP_DIR}/.elvern-desktop-restore.XXXXXX")" || rollback_failed=1
        if [[ -n "${temp_path:-}" ]]; then
          cp "${DESKTOP_BACKUP}" "${temp_path}" || rollback_failed=1
          chmod "$(stat -c '%a' "${DESKTOP_BACKUP}")" "${temp_path}" || rollback_failed=1
          mv "${temp_path}" "${DESKTOP_FILE}" || rollback_failed=1
        fi
      elif [[ -e "${DESKTOP_FILE}" ]]; then
        rm -f "${DESKTOP_FILE}" || rollback_failed=1
      fi
      if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
      fi
      restored_default="$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null || true)"
      if [[ -n "${PREVIOUS_PROTOCOL_DEFAULT}" ]]; then
        [[ "${restored_default}" == "${PREVIOUS_PROTOCOL_DEFAULT}" ]] || rollback_failed=1
      else
        [[ "${restored_default}" != "elvern-vlc-opener.desktop" ]] || rollback_failed=1
      fi
    fi
  fi
  [[ -n "${STAGE_DIR}" && -d "${STAGE_DIR}" ]] && rm -rf "${STAGE_DIR}"
  if [[
    ( ${INSTALL_COMMITTED} -eq 1 || ${rollback_failed} -eq 0 )
    && ${OLD_INSTALL_BACKED_UP} -eq 1
    && -d "${BACKUP_DIR}"
  ]]; then
    rm -rf "${BACKUP_DIR}"
  fi
  if [[
    -n "${TRANSACTION_DIR}"
    && -d "${TRANSACTION_DIR}"
    && ( ${INSTALL_COMMITTED} -eq 1 || ${rollback_failed} -eq 0 )
  ]]; then
    rm -rf "${TRANSACTION_DIR}"
  fi
  if [[ -n "${LOCK_DIR}" && -d "${LOCK_DIR}" ]]; then
    rm -f "${LOCK_DIR}/owner"
    rmdir "${LOCK_DIR}"
  fi
  if [[ ${rollback_failed} -ne 0 ]]; then
    echo "Elvern VLC Opener rollback could not be verified." >&2
    [[ -n "${BACKUP_DIR}" ]] \
      && echo "Preserved previous installation backup: ${BACKUP_DIR}" >&2
    [[ -n "${TRANSACTION_DIR}" ]] \
      && echo "Preserved MIME and desktop registration backups: ${TRANSACTION_DIR}" >&2
    echo "Repair only the preserved user-level Elvern registration before retrying." >&2
    return 1
  fi
  return 0
}
trap cleanup EXIT

fail() {
  echo "Elvern VLC Opener was not installed: $1" >&2
  exit 1
}

safe_relative_path() {
  local value="$1"
  [[
    -n "${value}"
    && "${value}" != /*
    && "${value}" != *\\*
    && "${value}" != *$'\r'*
    && "${value}" != *$'\n'*
    && "${value}" != *$'\t'*
  ]] || return 1
  case "/${value}/" in
    */../*|*/./*) return 1 ;;
  esac
}

verify_package_tree() {
  [[ -f "${TREE_MANIFEST}" && ! -L "${TREE_MANIFEST}" ]] || fail "the installer tree manifest is missing."
  if find "${SCRIPT_DIR}" -type l -print -quit | grep -q .; then
    fail "the installer package contains an unsafe link."
  fi
  local expected actual path size digest file_class extra full header row_count=0 lower_path
  local -A seen_paths=()
  local -A seen_case_paths=()
  IFS= read -r header < "${TREE_MANIFEST}" || fail "the installer tree manifest is empty."
  [[ "${header}" == $'path\tsize_bytes\tsha256\tfile_class' ]] \
    || fail "the installer tree manifest header is invalid."
  expected="$(mktemp)"
  actual="$(mktemp)"
  while IFS=$'\t' read -r path size digest file_class extra; do
    [[ -n "${path}${size}${digest}${file_class}${extra:-}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest contains an empty row."; }
    [[ -z "${extra:-}" ]] || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest has an invalid row."; }
    safe_relative_path "${path}" || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest contains an unsafe path."; }
    [[ "${size}" =~ ^[0-9]+$ && "${size}" -le 2147483648 && "${digest}" =~ ^[0-9a-f]{64}$ && "${file_class}" =~ ^(data|executable)$ ]] \
      || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest contains invalid metadata."; }
    lower_path="${path,,}"
    [[ -z "${seen_paths[${path}]+x}" && -z "${seen_case_paths[${lower_path}]+x}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest contains a duplicate or case-colliding path."; }
    seen_paths["${path}"]=1
    seen_case_paths["${lower_path}"]=1
    row_count=$((row_count + 1))
    full="${SCRIPT_DIR}/${path}"
    [[ -f "${full}" && ! -L "${full}" ]] || { rm -f "${expected}" "${actual}"; fail "an installer file is missing or unsafe."; }
    [[ "$(wc -c < "${full}" | tr -d '[:space:]')" == "${size}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "an installer file size check failed."; }
    [[ "$(sha256sum "${full}" | awk '{print $1}')" == "${digest}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "an installer file SHA-256 check failed."; }
    printf '%s\n' "${path}" >> "${expected}"
  done < <(tail -n +2 "${TREE_MANIFEST}")
  [[ ${row_count} -gt 0 ]] || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest is empty."; }
  while IFS= read -r full; do
    path="${full#"${SCRIPT_DIR}/"}"
    [[ "${path}" == ".elvern/tree-manifest.tsv" || "${path##*/}" == ".DS_Store" ]] && continue
    printf '%s\n' "${path}" >> "${actual}"
  done < <(find "${SCRIPT_DIR}" -type f -print)
  LC_ALL=C sort -o "${expected}" "${expected}"
  LC_ALL=C sort -o "${actual}" "${actual}"
  cmp -s "${expected}" "${actual}" || { rm -f "${expected}" "${actual}"; fail "the installer package contains a missing or unexpected file."; }
  rm -f "${expected}" "${actual}"
}

command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required to verify the installer."
command -v xdg-mime >/dev/null 2>&1 || fail "xdg-mime is required to register elvern-vlc://."
verify_package_tree
[[ -f "${MANIFEST_TSV}" && ! -L "${MANIFEST_TSV}" ]] || fail "the verified installer manifest is missing."
[[ -f "${SELECTORS}" && ! -L "${SELECTORS}" ]] || fail "the verified platform selector is missing."
[[ -f "${UNINSTALL_SOURCE}" && ! -L "${UNINSTALL_SOURCE}" ]] || fail "the verified uninstaller is missing."

# The selector was covered by the verified package tree before execution.
# shellcheck disable=SC1090
source "${SELECTORS}"
if [[ -n "${RUNTIME_OVERRIDE}" ]]; then
  RUNTIME_ID="${RUNTIME_OVERRIDE}"
else
  LIBC_FAMILY="$(detect_linux_libc)" || fail "the system libc could not be identified."
  RUNTIME_ID="$(select_linux_runtime "$(uname -m)" "${LIBC_FAMILY}")" \
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
declare -A META_COUNTS=()
declare -A RID_COUNTS=()
while IFS=$'\t' read -r kind field value fourth fifth sixth extra; do
  [[ -z "${extra:-}" ]] || fail "the verified installer manifest has an invalid row."
  if [[ "${kind}" == "meta" ]]; then
    [[ -z "${fourth}${fifth}${sixth}" ]] || fail "the verified installer manifest has an invalid metadata row."
    case "${field}" in
      schema_version|helper_version|target_framework|runtime_family|deployment_mode|package_target|bound_origin_sha256) ;;
      *) fail "the verified installer manifest contains unknown metadata." ;;
    esac
    META_COUNTS["${field}"]=$(( ${META_COUNTS["${field}"]:-0} + 1 ))
    [[ ${META_COUNTS["${field}"]} -eq 1 ]] || fail "the verified installer manifest repeats mandatory metadata."
    case "${field}" in
      schema_version) SCHEMA="${value}" ;;
      helper_version) HELPER_VERSION="${value}" ;;
      target_framework) TARGET_FRAMEWORK="${value}" ;;
      runtime_family) RUNTIME_FAMILY="${value}" ;;
      deployment_mode) DEPLOYMENT_MODE="${value}" ;;
      package_target) PACKAGE_TARGET="${value}" ;;
      bound_origin_sha256) BOUND_ORIGIN_SHA256="${value}" ;;
    esac
  elif [[ "${kind}" == "payload" ]]; then
    [[ -n "${field}${value}${fourth}${fifth}${sixth}" ]] || fail "the verified installer manifest has an invalid payload row."
    RID_COUNTS["${field}"]=$(( ${RID_COUNTS["${field}"]:-0} + 1 ))
    [[ ${RID_COUNTS["${field}"]} -eq 1 ]] || fail "the verified installer manifest repeats a runtime."
    if [[ "${field}" == "${RUNTIME_ID}" ]]; then
      PAYLOAD_RELATIVE_PATH="${value}"
      EXPECTED_SHA="${fourth}"
      EXPECTED_SIZE="${fifth}"
      PAYLOAD_EXECUTABLE="${sixth}"
    fi
  else
    fail "the verified installer manifest contains an unknown row."
  fi
done < "${MANIFEST_TSV}"
[[ "${SCHEMA}" == "desktop-helper-installer-manifest-v2" ]] || fail "the verified installer manifest schema is unsupported."
for mandatory_meta in schema_version helper_version target_framework runtime_family deployment_mode package_target bound_origin_sha256; do
  [[ "${META_COUNTS[${mandatory_meta}]:-0}" -eq 1 ]] || fail "the verified installer manifest is missing mandatory metadata."
done
[[
  "${TARGET_FRAMEWORK}" == "net10.0"
  && "${RUNTIME_FAMILY}" == "10.0"
  && "${BOUND_ORIGIN_SHA256}" =~ ^[0-9a-f]{64}$
  && "${DEPLOYMENT_MODE}" == "self_contained"
  && "${PACKAGE_TARGET}" == "linux-universal"
]] \
  || fail "this is not the standard self-contained Linux package."
[[ "${HELPER_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "the verified installer manifest has an invalid version."
[[ -n "${PAYLOAD_RELATIVE_PATH}" ]] || fail "the selected runtime is not in the package allowlist."
safe_relative_path "${PAYLOAD_RELATIVE_PATH}" || fail "the selected payload path is unsafe."
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{64}$ && "${EXPECTED_SIZE}" =~ ^[0-9]+$ && "${EXPECTED_SIZE}" -le 2147483648 ]] \
  || fail "the selected payload metadata is invalid."
[[ "${PAYLOAD_EXECUTABLE}" == "Elvern.VlcOpener" ]] || fail "the selected payload executable is invalid."
PAYLOAD="${PRIVATE_DIR}/${PAYLOAD_RELATIVE_PATH}"
[[ -f "${PAYLOAD}" && ! -L "${PAYLOAD}" ]] || fail "the ${RUNTIME_ID} payload is missing."
[[ "$(wc -c < "${PAYLOAD}" | tr -d '[:space:]')" == "${EXPECTED_SIZE}" ]] || fail "the payload size check failed."
[[ "$(sha256sum "${PAYLOAD}" | awk '{print $1}')" == "${EXPECTED_SHA}" ]] || fail "the payload SHA-256 check failed."

mkdir -p "${INSTALL_PARENT}" "${DESKTOP_DIR}" \
  || fail "the user-level installation directories could not be created."
LOCK_DIR="${INSTALL_PARENT}/.elvern-vlc-opener-install.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  fail "another Helper install may be running. Remove ${LOCK_DIR} manually only after confirming no installer is active."
fi
printf 'pid=%s\nstarted_at=%s\n' "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${LOCK_DIR}/owner"
chmod 644 "${LOCK_DIR}/owner"
TRANSACTION_DIR="$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-transaction.XXXXXX")" \
  || fail "the install transaction directory could not be created."
STAGE_DIR="$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-stage.XXXXXX")" \
  || fail "a staged installation directory could not be created."
STAGING_CREATED=1
inject_failure "staging_created"
cp "${PAYLOAD}" "${STAGE_DIR}/Elvern.VlcOpener"
chmod 755 "${STAGE_DIR}/Elvern.VlcOpener"
cp "${UNINSTALL_SOURCE}" "${STAGE_DIR}/Uninstall-ElvernVlcOpener.sh"
chmod 755 "${STAGE_DIR}/Uninstall-ElvernVlcOpener.sh"
"${STAGE_DIR}/Elvern.VlcOpener" --version >/dev/null \
  || fail "the staged payload failed its version check."

PREVIOUS_PROTOCOL_DEFAULT="$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null || true)"
for index in "${!MIME_PATHS[@]}"; do
  target="${MIME_PATHS[$index]}"
  if [[ -f "${target}" && ! -L "${target}" ]]; then
    MIME_EXISTED[$index]=1
    MIME_MODES[$index]="$(stat -c '%a' "${target}")"
    MIME_BACKUPS[$index]="${TRANSACTION_DIR}/mimeapps-${index}"
    cp -p "${target}" "${MIME_BACKUPS[$index]}" \
      || fail "an existing user MIME registration could not be backed up."
  elif [[ -e "${target}" ]]; then
    fail "a user MIME registration path is not a regular file."
  else
    MIME_EXISTED[$index]=0
    MIME_MODES[$index]=""
    MIME_BACKUPS[$index]=""
  fi
done
if [[ -f "${DESKTOP_FILE}" ]]; then
  [[ ! -L "${DESKTOP_FILE}" ]] || fail "the existing Elvern desktop entry is unsafe."
  DESKTOP_BACKUP="${TRANSACTION_DIR}/elvern-vlc-opener.desktop"
  cp -p "${DESKTOP_FILE}" "${DESKTOP_BACKUP}" \
    || fail "the existing Elvern desktop entry could not be backed up."
fi
OLD_REGISTRATION_CAPTURED=1
if [[ -d "${INSTALL_DIR}" ]]; then
  OLD_INSTALL_EXISTED=1
  BACKUP_DIR="$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-backup.XXXXXX")" \
    || fail "a unique backup path could not be reserved."
  rmdir "${BACKUP_DIR}" || fail "the unique backup path could not be prepared."
  inject_failure "first_backup_move"
  mv "${INSTALL_DIR}" "${BACKUP_DIR}" || fail "the existing installation could not be staged for upgrade."
  OLD_INSTALL_BACKED_UP=1
fi
inject_failure "new_placement"
mv "${STAGE_DIR}" "${INSTALL_DIR}" || fail "the new Helper could not replace the existing installation."
STAGE_DIR=""
NEW_INSTALL_PLACED=1

DESKTOP_TEMP="$(mktemp "${DESKTOP_DIR}/.elvern-vlc-opener.desktop.XXXXXX")"
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
REGISTRATION_MODIFIED=1
inject_failure "registration"
xdg-mime default elvern-vlc-opener.desktop x-scheme-handler/elvern-vlc \
  || fail "xdg-mime could not register the protocol handler."
inject_failure "registration_validation"
[[ "$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null || true)" == "elvern-vlc-opener.desktop" ]] \
  || fail "the registered protocol handler could not be verified."
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
fi
inject_failure "final_binary_validation"
"${INSTALL_DIR}/Elvern.VlcOpener" --version >/dev/null \
  || fail "the installed Helper failed its final version check."
FINAL_VALIDATION_PASSED=1
INSTALL_COMMITTED=1
echo "Installed Elvern VLC Opener ${HELPER_VERSION} for ${RUNTIME_ID}."
echo "Registered elvern-vlc:// without administrator privileges."
