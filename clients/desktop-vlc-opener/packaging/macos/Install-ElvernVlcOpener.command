#!/usr/bin/env bash
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRIVATE_DIR="${SCRIPT_DIR}/.elvern"
MANIFEST_TSV="${PRIVATE_DIR}/installer-manifest.tsv"
TREE_MANIFEST="${PRIVATE_DIR}/tree-manifest.tsv"
SELECTORS="${PRIVATE_DIR}/lib/platform-selectors.sh"
APPLESCRIPT_SOURCE="${PRIVATE_DIR}/bridge/ElvernVlcOpener.applescript"
RUNNER_TEMPLATE="${PRIVATE_DIR}/bridge/run-helper.sh.template"
UNINSTALL_SOURCE="${PRIVATE_DIR}/uninstall/Uninstall-ElvernVlcOpener.command"
DEST_DIR="${HOME}/Applications"
APP_NAME="Elvern VLC Opener.app"
DEST_APP="${DEST_DIR}/${APP_NAME}"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
OSACOMPILE="/usr/bin/osacompile"
PLISTBUDDY="/usr/libexec/PlistBuddy"
STAGE_ROOT=""
BACKUP_APP=""
FAILED_NEW_APP=""
LOCK_DIR=""
LOCK_HELD=0
INSTALL_NONCE="$$-$(date -u +%Y%m%dT%H%M%SZ)"
STAGING_CREATED=0
OLD_INSTALL_EXISTED=0
OLD_INSTALL_BACKED_UP=0
NEW_INSTALL_PLACED=0
OLD_REGISTRATION_CAPTURED=0
REGISTRATION_MODIFIED=0
FINAL_VALIDATION_PASSED=0
INSTALL_COMMITTED=0

inject_failure() {
  local point="$1"
  if [[ "${ELVERN_INSTALL_TEST_MODE:-0}" == "1" && "${ELVERN_INSTALL_TEST_FAIL_AT:-}" == "${point}" ]]; then
    fail "Injected failure at ${point}."
  fi
}

prepare_backup_target() {
  local prefix="$1"
  local reserved=""
  local candidate=""
  local attempt=0
  [[ -d "${DEST_DIR}" && ! -L "${DEST_DIR}" \
    && "${DEST_DIR}" != *$'\t'* \
    && "${DEST_DIR}" != *$'\r'* \
    && "${DEST_DIR}" != *$'\n'* ]] \
    || fail "The App backup parent is unsafe."
  while [[ ${attempt} -lt 8 ]]; do
    attempt=$((attempt + 1))
    reserved="$(mktemp -d "${DEST_DIR}/${prefix}.XXXXXX")" \
      || fail "A unique App backup path could not be reserved."
    [[ "$(dirname "${reserved}")" == "${DEST_DIR}" && ! -L "${reserved}" ]] \
      || fail "The App backup parent is unsafe."
    rmdir "${reserved}" || fail "A unique App backup path could not be prepared."
    candidate="${reserved}.app"
    if [[ "${ELVERN_INSTALL_TEST_MODE:-0}" == "1" \
      && "${ELVERN_INSTALL_TEST_BACKUP_COLLISIONS:-0}" -ge "${attempt}" ]]; then
      continue
    fi
    if [[ ! -e "${candidate}" && ! -L "${candidate}" \
      && "$(dirname "${candidate}")" == "${DEST_DIR}" \
      && "${candidate}" != *$'\t'* \
      && "${candidate}" != *$'\r'* \
      && "${candidate}" != *$'\n'* ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  fail "A unique safe App backup path could not be prepared."
}

show_error() {
  local message="$1"
  echo "Elvern VLC Opener was not installed: ${message}" >&2
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display alert \"Elvern VLC Opener\" message \"${message//\"/\\\"}\"" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  local rollback_failed=0
  if [[ ${INSTALL_COMMITTED} -eq 0 ]]; then
    if [[ ${NEW_INSTALL_PLACED} -eq 1 && -d "${DEST_APP}" ]]; then
      if [[ ${REGISTRATION_MODIFIED} -eq 1 ]]; then
        if ! "${LSREGISTER}" -u "${DEST_APP}" >/dev/null 2>&1; then
          rollback_failed=1
          FAILED_NEW_APP="${STAGE_ROOT}/${APP_NAME}"
          if ! mv "${DEST_APP}" "${FAILED_NEW_APP}"; then
            FAILED_NEW_APP="${DEST_APP}"
          fi
        fi
      fi
      if [[ -d "${DEST_APP}" && "${FAILED_NEW_APP}" != "${DEST_APP}" ]]; then
        rm -rf "${DEST_APP}" || rollback_failed=1
      fi
    fi
    if [[ ${OLD_INSTALL_BACKED_UP} -eq 1 ]]; then
      if [[ -d "${DEST_APP}" || ! -d "${BACKUP_APP}" ]]; then
        rollback_failed=1
      else
        /bin/cp -a "${BACKUP_APP}" "${DEST_APP}" || rollback_failed=1
        if [[ ${rollback_failed} -eq 0 ]]; then
          "${LSREGISTER}" -f "${DEST_APP}" >/dev/null 2>&1 || rollback_failed=1
          "${DEST_APP}/Contents/Resources/app/Elvern.VlcOpener" --version >/dev/null 2>&1 \
            || rollback_failed=1
        fi
      fi
    fi
  fi
  if [[
    -n "${STAGE_ROOT}"
    && -d "${STAGE_ROOT}"
    && ( ${INSTALL_COMMITTED} -eq 1 || ${rollback_failed} -eq 0 )
  ]]; then
    rm -rf "${STAGE_ROOT}"
  fi
  if [[
    ( ${INSTALL_COMMITTED} -eq 1 || ${rollback_failed} -eq 0 )
    && ${OLD_INSTALL_BACKED_UP} -eq 1
    && -d "${BACKUP_APP}"
  ]]; then
    rm -rf "${BACKUP_APP}"
  fi
  if [[ ${LOCK_HELD} -eq 1 && -d "${LOCK_DIR}" ]]; then
    rm -f "${LOCK_DIR}/owner"
    rmdir "${LOCK_DIR}"
  fi
  if [[ ${rollback_failed} -ne 0 ]]; then
    echo "Elvern VLC Opener rollback could not be verified." >&2
    [[ -n "${BACKUP_APP}" ]] \
      && echo "Preserved previous App backup when available: ${BACKUP_APP}" >&2
    [[ -n "${STAGE_ROOT}" ]] \
      && echo "Preserved rollback workspace: ${STAGE_ROOT}" >&2
    [[ -n "${FAILED_NEW_APP}" ]] \
      && echo "Preserved the failed newly registered App: ${FAILED_NEW_APP}" >&2
    echo "Repair only the listed Elvern App registration before retrying." >&2
    return 1
  fi
  return 0
}
trap cleanup EXIT

fail() {
  show_error "$1"
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
  return 0
}

verify_package_tree() {
  [[ -f "${TREE_MANIFEST}" && ! -L "${TREE_MANIFEST}" ]] || fail "The installer tree manifest is missing."
  if find "${SCRIPT_DIR}" -type l -print -quit | grep -q .; then
    fail "The installer package contains an unsafe link."
  fi
  local expected actual case_seen path size digest file_class extra full actual_size actual_digest header row_count=0 lower_path
  IFS= read -r header < "${TREE_MANIFEST}" || fail "The installer tree manifest is empty."
  [[ "${header}" == $'path\tsize_bytes\tsha256\tfile_class' ]] \
    || fail "The installer tree manifest header is invalid."
  expected="$(mktemp)"
  actual="$(mktemp)"
  case_seen="$(mktemp)"
  while IFS=$'\t' read -r path size digest file_class extra; do
    [[ -n "${path}${size}${digest}${file_class}${extra:-}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "The installer tree manifest contains an empty row."; }
    [[ -z "${extra:-}" ]] || { rm -f "${expected}" "${actual}"; fail "The installer tree manifest has an invalid row."; }
    safe_relative_path "${path}" || { rm -f "${expected}" "${actual}"; fail "The installer tree manifest contains an unsafe path."; }
    [[ "${size}" =~ ^[0-9]+$ && "${size}" -le 2147483648 && "${digest}" =~ ^[0-9a-f]{64}$ && "${file_class}" =~ ^(data|executable)$ ]] \
      || { rm -f "${expected}" "${actual}"; fail "The installer tree manifest contains invalid metadata."; }
    lower_path="$(printf '%s' "${path}" | tr '[:upper:]' '[:lower:]')"
    if grep -Fqx "${path}" "${expected}" || grep -Fqx "${lower_path}" "${case_seen}"; then
      rm -f "${expected}" "${actual}" "${case_seen}"
      fail "The installer tree manifest contains a duplicate or case-colliding path."
    fi
    row_count=$((row_count + 1))
    full="${SCRIPT_DIR}/${path}"
    [[ -f "${full}" && ! -L "${full}" ]] || { rm -f "${expected}" "${actual}"; fail "An installer file is missing or unsafe."; }
    actual_size="$(wc -c < "${full}" | tr -d '[:space:]')"
    actual_digest="$(/usr/bin/shasum -a 256 "${full}" | /usr/bin/awk '{print $1}')"
    [[ "${actual_size}" == "${size}" && "${actual_digest}" == "${digest}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "An installer file failed integrity verification."; }
    printf '%s\n' "${path}" >> "${expected}"
    printf '%s\n' "${lower_path}" >> "${case_seen}"
  done < <(tail -n +2 "${TREE_MANIFEST}")
  [[ ${row_count} -gt 0 ]] || { rm -f "${expected}" "${actual}" "${case_seen}"; fail "The installer tree manifest is empty."; }
  while IFS= read -r full; do
    path="${full#"${SCRIPT_DIR}/"}"
    [[ "${path}" == ".elvern/tree-manifest.tsv" || "${path##*/}" == ".DS_Store" ]] && continue
    printf '%s\n' "${path}" >> "${actual}"
  done < <(find "${SCRIPT_DIR}" -type f -print)
  LC_ALL=C sort -o "${expected}" "${expected}"
  LC_ALL=C sort -o "${actual}" "${actual}"
  cmp -s "${expected}" "${actual}" || { rm -f "${expected}" "${actual}" "${case_seen}"; fail "The installer package contains a missing or unexpected file."; }
  rm -f "${expected}" "${actual}" "${case_seen}"
}

verify_quarantine_cleared() {
  local root="$1"
  local entry
  while IFS= read -r entry; do
    if xattr -p com.apple.quarantine "${entry}" >/dev/null 2>&1; then
      fail "macOS quarantine is still present on the verified Helper App."
    fi
  done < <(find "${root}" -print)
}

[[ -x "${OSACOMPILE}" ]] || fail "macOS could not find osacompile."
[[ -x "${PLISTBUDDY}" ]] || fail "macOS could not find PlistBuddy."
[[ -x "${LSREGISTER}" ]] || fail "macOS could not find Launch Services registration."
command -v shasum >/dev/null 2>&1 || fail "shasum is required to verify the installer."
command -v codesign >/dev/null 2>&1 || fail "codesign is required to build the local Helper App."
command -v xattr >/dev/null 2>&1 || fail "xattr is required to prepare the local Helper App."
verify_package_tree

[[ -f "${MANIFEST_TSV}" && ! -L "${MANIFEST_TSV}" ]] || fail "The verified installer manifest is missing."
[[ -f "${SELECTORS}" && ! -L "${SELECTORS}" ]] || fail "The verified platform selector is missing."
[[ -f "${APPLESCRIPT_SOURCE}" && ! -L "${APPLESCRIPT_SOURCE}" ]] || fail "The verified macOS URL bridge is missing."
[[ -f "${RUNNER_TEMPLATE}" && ! -L "${RUNNER_TEMPLATE}" ]] || fail "The verified Helper runner is missing."
[[ -f "${UNINSTALL_SOURCE}" && ! -L "${UNINSTALL_SOURCE}" ]] || fail "The verified Helper uninstaller is missing."

MACOS_VERSION="$(sw_vers -productVersion 2>/dev/null || true)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
[[ "${MACOS_MAJOR}" =~ ^[0-9]+$ ]] || fail "The macOS version could not be determined."
(( MACOS_MAJOR >= 14 )) || fail "macOS 14 or newer is required."

# The selector was covered by the verified package tree before execution.
# shellcheck disable=SC1090
source "${SELECTORS}"
TRANSLATED="$(sysctl -in sysctl.proc_translated 2>/dev/null || printf '0')"
RUNTIME_ID="$(select_macos_runtime "${TRANSLATED}" "$(uname -m)")" || fail "This Mac CPU is not supported."

SCHEMA=""
HELPER_VERSION=""
TARGET_FRAMEWORK=""
RUNTIME_FAMILY=""
DEPLOYMENT_MODE=""
PACKAGE_TARGET=""
BOUND_ORIGIN_SHA256=""
PAYLOAD_RELATIVE_PATH=""
EXPECTED_SHA256=""
EXPECTED_SIZE=""
PAYLOAD_EXECUTABLE=""
SCHEMA_COUNT=0
HELPER_VERSION_COUNT=0
TARGET_FRAMEWORK_COUNT=0
RUNTIME_FAMILY_COUNT=0
DEPLOYMENT_MODE_COUNT=0
PACKAGE_TARGET_COUNT=0
BOUND_ORIGIN_SHA256_COUNT=0
RID_SEEN_FILE="$(mktemp)"
while IFS=$'\t' read -r kind field value fourth fifth sixth extra; do
  [[ -z "${extra:-}" ]] || fail "The verified installer manifest has an invalid row."
  if [[ "${kind}" == "meta" ]]; then
    [[ -z "${fourth}${fifth}${sixth}" ]] || fail "The verified installer manifest has an invalid metadata row."
    case "${field}" in
      schema_version|helper_version|target_framework|runtime_family|deployment_mode|package_target|bound_origin_sha256) ;;
      *) fail "The verified installer manifest contains unknown metadata." ;;
    esac
    case "${field}" in
      schema_version) SCHEMA_COUNT=$((SCHEMA_COUNT + 1)); [[ ${SCHEMA_COUNT} -eq 1 ]] || fail "The verified installer manifest repeats mandatory metadata."; SCHEMA="${value}" ;;
      helper_version) HELPER_VERSION_COUNT=$((HELPER_VERSION_COUNT + 1)); [[ ${HELPER_VERSION_COUNT} -eq 1 ]] || fail "The verified installer manifest repeats mandatory metadata."; HELPER_VERSION="${value}" ;;
      target_framework) TARGET_FRAMEWORK_COUNT=$((TARGET_FRAMEWORK_COUNT + 1)); [[ ${TARGET_FRAMEWORK_COUNT} -eq 1 ]] || fail "The verified installer manifest repeats mandatory metadata."; TARGET_FRAMEWORK="${value}" ;;
      runtime_family) RUNTIME_FAMILY_COUNT=$((RUNTIME_FAMILY_COUNT + 1)); [[ ${RUNTIME_FAMILY_COUNT} -eq 1 ]] || fail "The verified installer manifest repeats mandatory metadata."; RUNTIME_FAMILY="${value}" ;;
      deployment_mode) DEPLOYMENT_MODE_COUNT=$((DEPLOYMENT_MODE_COUNT + 1)); [[ ${DEPLOYMENT_MODE_COUNT} -eq 1 ]] || fail "The verified installer manifest repeats mandatory metadata."; DEPLOYMENT_MODE="${value}" ;;
      package_target) PACKAGE_TARGET_COUNT=$((PACKAGE_TARGET_COUNT + 1)); [[ ${PACKAGE_TARGET_COUNT} -eq 1 ]] || fail "The verified installer manifest repeats mandatory metadata."; PACKAGE_TARGET="${value}" ;;
      bound_origin_sha256) BOUND_ORIGIN_SHA256_COUNT=$((BOUND_ORIGIN_SHA256_COUNT + 1)); [[ ${BOUND_ORIGIN_SHA256_COUNT} -eq 1 ]] || fail "The verified installer manifest repeats mandatory metadata."; BOUND_ORIGIN_SHA256="${value}" ;;
    esac
  elif [[ "${kind}" == "payload" ]]; then
    [[ -n "${field}${value}${fourth}${fifth}${sixth}" ]] || fail "The verified installer manifest has an invalid payload row."
    grep -Fqx "${field}" "${RID_SEEN_FILE}" && fail "The verified installer manifest repeats a runtime."
    printf '%s\n' "${field}" >> "${RID_SEEN_FILE}"
    if [[ "${field}" == "${RUNTIME_ID}" ]]; then
      PAYLOAD_RELATIVE_PATH="${value}"
      EXPECTED_SHA256="${fourth}"
      EXPECTED_SIZE="${fifth}"
      PAYLOAD_EXECUTABLE="${sixth}"
    fi
  else
    fail "The verified installer manifest contains an unknown row."
  fi
done < "${MANIFEST_TSV}"
rm -f "${RID_SEEN_FILE}"
[[ "${SCHEMA}" == "desktop-helper-installer-manifest-v2" ]] || fail "The verified installer manifest schema is unsupported."
[[
  "${SCHEMA_COUNT}" -eq 1
  && "${HELPER_VERSION_COUNT}" -eq 1
  && "${TARGET_FRAMEWORK_COUNT}" -eq 1
  && "${RUNTIME_FAMILY_COUNT}" -eq 1
  && "${DEPLOYMENT_MODE_COUNT}" -eq 1
  && "${PACKAGE_TARGET_COUNT}" -eq 1
  && "${BOUND_ORIGIN_SHA256_COUNT}" -eq 1
]] || fail "The verified installer manifest is missing mandatory metadata."
[[
  "${TARGET_FRAMEWORK}" == "net10.0"
  && "${RUNTIME_FAMILY}" == "10.0"
  && "${BOUND_ORIGIN_SHA256}" =~ ^[0-9a-f]{64}$
  && "${DEPLOYMENT_MODE}" == "self_contained"
  && "${PACKAGE_TARGET}" == "macos-dual-arch"
]] \
  || fail "This is not the standard self-contained macOS package."
[[ "${HELPER_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "The verified installer manifest has an invalid version."
safe_relative_path "${PAYLOAD_RELATIVE_PATH}" || fail "The selected payload path is unsafe."
[[ "${EXPECTED_SHA256}" =~ ^[0-9a-f]{64}$ && "${EXPECTED_SIZE}" =~ ^[0-9]+$ && "${EXPECTED_SIZE}" -le 2147483648 ]] \
  || fail "The selected payload metadata is invalid."
[[ "${PAYLOAD_EXECUTABLE}" == "Elvern.VlcOpener" ]] || fail "The selected payload executable is invalid."
SOURCE_PAYLOAD="${PRIVATE_DIR}/${PAYLOAD_RELATIVE_PATH}"
[[ -f "${SOURCE_PAYLOAD}" && ! -L "${SOURCE_PAYLOAD}" ]] || fail "The ${RUNTIME_ID} payload is missing."
[[ "$(wc -c < "${SOURCE_PAYLOAD}" | tr -d '[:space:]')" == "${EXPECTED_SIZE}" ]] || fail "The Helper payload size check failed."
[[ "$(/usr/bin/shasum -a 256 "${SOURCE_PAYLOAD}" | /usr/bin/awk '{print $1}')" == "${EXPECTED_SHA256}" ]] \
  || fail "The Helper payload SHA-256 check failed."

mkdir -p "${DEST_DIR}"
LOCK_DIR="${DEST_DIR}/.elvern-vlc-opener-install.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  fail "Another Helper install or uninstall may be running. Remove ${LOCK_DIR} manually only after confirming no transaction is active."
fi
LOCK_HELD=1
printf 'pid=%s\nstarted_at=%s\ntransaction_nonce=%s\n' \
  "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${INSTALL_NONCE}" \
  > "${LOCK_DIR}/owner"
chmod 644 "${LOCK_DIR}/owner"
STAGE_ROOT="$(mktemp -d "${DEST_DIR}/.elvern-vlc-opener-stage.XXXXXX")"
STAGING_CREATED=1
inject_failure "staging_created"
STAGED_APP="${STAGE_ROOT}/${APP_NAME}"
"${OSACOMPILE}" -o "${STAGED_APP}" "${APPLESCRIPT_SOURCE}" || fail "The local URL bridge could not be created."
RESOURCES_DIR="${STAGED_APP}/Contents/Resources"
APP_PAYLOAD_DIR="${RESOURCES_DIR}/app"
mkdir -p "${APP_PAYLOAD_DIR}"
cp "${SOURCE_PAYLOAD}" "${APP_PAYLOAD_DIR}/Elvern.VlcOpener"
chmod 755 "${APP_PAYLOAD_DIR}/Elvern.VlcOpener"
cp "${RUNNER_TEMPLATE}" "${RESOURCES_DIR}/run-helper.sh"
chmod 755 "${RESOURCES_DIR}/run-helper.sh"
cp "${UNINSTALL_SOURCE}" "${RESOURCES_DIR}/Uninstall-ElvernVlcOpener.command"
chmod 755 "${RESOURCES_DIR}/Uninstall-ElvernVlcOpener.command"
INFO_PLIST="${STAGED_APP}/Contents/Info.plist"

plist_set() {
  local key="$1"
  local type="$2"
  local value="$3"
  "${PLISTBUDDY}" -c "Set :${key} ${value}" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :${key} ${type} ${value}" "${INFO_PLIST}"
}
plist_set "CFBundleIdentifier" string "local.elvern.vlcopener"
plist_set "CFBundleName" string "Elvern VLC Opener"
plist_set "CFBundleDisplayName" string "Elvern VLC Opener"
plist_set "CFBundleShortVersionString" string "${HELPER_VERSION}"
plist_set "CFBundleVersion" string "${HELPER_VERSION}"
plist_set "LSMinimumSystemVersion" string "14.0"
"${PLISTBUDDY}" -c "Delete :CFBundleURLTypes" "${INFO_PLIST}" >/dev/null 2>&1 || true
"${PLISTBUDDY}" -c "Add :CFBundleURLTypes array" "${INFO_PLIST}"
"${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0 dict" "${INFO_PLIST}"
"${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLName string Elvern VLC Opener" "${INFO_PLIST}"
"${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "${INFO_PLIST}"
"${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string elvern-vlc" "${INFO_PLIST}"

INSTALL_STATE="${RESOURCES_DIR}/install-state.plist"
cat > "${INSTALL_STATE}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>schema_version</key>
  <string>elvern-desktop-helper-install-state-v1</string>
  <key>helper_version</key>
  <string>${HELPER_VERSION}</string>
  <key>product_id</key>
  <string>local.elvern.vlcopener</string>
  <key>package_target</key>
  <string>${PACKAGE_TARGET}</string>
  <key>transaction_nonce</key>
  <string>${INSTALL_NONCE}</string>
</dict>
</plist>
EOF
chmod 644 "${INSTALL_STATE}"

codesign --force --sign - "${APP_PAYLOAD_DIR}/Elvern.VlcOpener" >/dev/null \
  || fail "The staged Helper executable could not be structurally signed."
codesign --force --sign - "${STAGED_APP}" >/dev/null \
  || fail "The local App could not be structurally signed."
codesign --verify --deep --strict "${STAGED_APP}" >/dev/null 2>&1 \
  || fail "The local App signature verification failed."
xattr -dr com.apple.quarantine "${STAGED_APP}" \
  || fail "macOS quarantine could not be removed from the verified staged Helper App."
verify_quarantine_cleared "${STAGED_APP}"
"${APP_PAYLOAD_DIR}/Elvern.VlcOpener" --version >/dev/null \
  || fail "The staged Helper failed its version check."

OLD_REGISTRATION_CAPTURED=1
if [[ -d "${DEST_APP}" ]]; then
  OLD_INSTALL_EXISTED=1
  BACKUP_APP="$(prepare_backup_target ".elvern-vlc-opener-backup")"
  inject_failure "backup_target_prepared"
  inject_failure "first_backup_move"
  mv "${DEST_APP}" "${BACKUP_APP}" || fail "The existing Helper App could not be staged for upgrade."
  OLD_INSTALL_BACKED_UP=1
fi
inject_failure "new_placement"
mv "${STAGED_APP}" "${DEST_APP}" || fail "The new Helper App could not replace the existing installation."
NEW_INSTALL_PLACED=1
chmod 755 "${DEST_APP}" "${DEST_APP}/Contents" "${DEST_APP}/Contents/Resources" "${DEST_APP}/Contents/Resources/app" \
  || fail "The installed Helper directory permissions could not be secured."
chmod 755 "${DEST_APP}/Contents/Resources/app/Elvern.VlcOpener" "${DEST_APP}/Contents/Resources/run-helper.sh" \
  || fail "The installed Helper executable permissions could not be secured."
[[ -f "${DEST_APP}/Contents/Resources/Uninstall-ElvernVlcOpener.command" \
  && ! -L "${DEST_APP}/Contents/Resources/Uninstall-ElvernVlcOpener.command" \
  && -x "${DEST_APP}/Contents/Resources/Uninstall-ElvernVlcOpener.command" ]] \
  || fail "The installed Helper uninstaller is missing or unsafe."
xattr -dr com.apple.quarantine "${DEST_APP}" \
  || fail "macOS quarantine could not be removed from the verified installed Helper App."
verify_quarantine_cleared "${DEST_APP}"
inject_failure "registration"
"${LSREGISTER}" -f "${DEST_APP}" >/dev/null 2>&1 \
  || fail "Launch Services could not register the installed Helper App."
REGISTRATION_MODIFIED=1
inject_failure "registration_validation"
codesign --verify --deep --strict "${DEST_APP}" >/dev/null 2>&1 \
  || fail "The installed App signature verification failed."
inject_failure "final_binary_validation"
"${DEST_APP}/Contents/Resources/app/Elvern.VlcOpener" --version >/dev/null \
  || fail "The installed Helper failed its final version check."
FINAL_VALIDATION_PASSED=1
touch "${DEST_APP}"
INSTALL_COMMITTED=1
if ! open -R "${DEST_APP}" >/dev/null 2>&1; then
  echo "Warning: Finder could not reveal the installed Helper App at ${DEST_APP}." >&2
fi
echo "Installed ${APP_NAME} ${HELPER_VERSION} into ${DEST_APP}"
echo "The App uses a local ad-hoc structural signature; it is not Developer ID signed or notarized."
