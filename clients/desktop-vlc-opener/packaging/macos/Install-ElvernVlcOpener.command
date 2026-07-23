#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRIVATE_DIR="${SCRIPT_DIR}/.elvern"
MANIFEST_TSV="${PRIVATE_DIR}/installer-manifest.tsv"
TREE_MANIFEST="${PRIVATE_DIR}/tree-manifest.tsv"
SELECTORS="${PRIVATE_DIR}/lib/platform-selectors.sh"
APPLESCRIPT_SOURCE="${PRIVATE_DIR}/bridge/ElvernVlcOpener.applescript"
RUNNER_TEMPLATE="${PRIVATE_DIR}/bridge/run-helper.sh.template"
DEST_DIR="${HOME}/Applications"
APP_NAME="Elvern VLC Opener.app"
DEST_APP="${DEST_DIR}/${APP_NAME}"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
OSACOMPILE="/usr/bin/osacompile"
PLISTBUDDY="/usr/libexec/PlistBuddy"
STAGE_ROOT=""
BACKUP_APP=""
INSTALL_SUCCEEDED=0
REPLACEMENT_STARTED=0

show_error() {
  local message="$1"
  echo "Elvern VLC Opener was not installed: ${message}" >&2
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display alert \"Elvern VLC Opener\" message \"${message//\"/\\\"}\"" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  [[ -n "${STAGE_ROOT}" && -d "${STAGE_ROOT}" ]] && rm -rf "${STAGE_ROOT}"
  if [[ ${INSTALL_SUCCEEDED} -eq 0 && ${REPLACEMENT_STARTED} -eq 1 ]]; then
    [[ -d "${DEST_APP}" ]] && rm -rf "${DEST_APP}"
    if [[ -n "${BACKUP_APP}" && -d "${BACKUP_APP}" ]]; then
      mv "${BACKUP_APP}" "${DEST_APP}" || true
      "${LSREGISTER}" -f "${DEST_APP}" >/dev/null 2>&1 || true
    fi
  fi
  if [[ ${INSTALL_SUCCEEDED} -eq 1 && -n "${BACKUP_APP}" && -d "${BACKUP_APP}" ]]; then
    rm -rf "${BACKUP_APP}"
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
  [[ -n "${value}" && "${value}" != /* && "${value}" != *\\* ]] || return 1
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
  local expected actual path size digest file_class extra full actual_size actual_digest
  expected="$(mktemp)"
  actual="$(mktemp)"
  while IFS=$'\t' read -r path size digest file_class extra; do
    [[ "${path}" == "path" ]] && continue
    [[ -z "${extra:-}" ]] || { rm -f "${expected}" "${actual}"; fail "The installer tree manifest has an invalid row."; }
    safe_relative_path "${path}" || { rm -f "${expected}" "${actual}"; fail "The installer tree manifest contains an unsafe path."; }
    [[ "${size}" =~ ^[0-9]+$ && "${digest}" =~ ^[0-9a-f]{64}$ && "${file_class}" =~ ^(data|executable)$ ]] \
      || { rm -f "${expected}" "${actual}"; fail "The installer tree manifest contains invalid metadata."; }
    full="${SCRIPT_DIR}/${path}"
    [[ -f "${full}" && ! -L "${full}" ]] || { rm -f "${expected}" "${actual}"; fail "An installer file is missing or unsafe."; }
    actual_size="$(wc -c < "${full}" | tr -d '[:space:]')"
    actual_digest="$(/usr/bin/shasum -a 256 "${full}" | /usr/bin/awk '{print $1}')"
    [[ "${actual_size}" == "${size}" && "${actual_digest}" == "${digest}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "An installer file failed integrity verification."; }
    printf '%s\n' "${path}" >> "${expected}"
  done < "${TREE_MANIFEST}"
  while IFS= read -r full; do
    path="${full#"${SCRIPT_DIR}/"}"
    [[ "${path}" == ".elvern/tree-manifest.tsv" || "${path##*/}" == ".DS_Store" ]] && continue
    printf '%s\n' "${path}" >> "${actual}"
  done < <(find "${SCRIPT_DIR}" -type f -print)
  LC_ALL=C sort -o "${expected}" "${expected}"
  LC_ALL=C sort -o "${actual}" "${actual}"
  cmp -s "${expected}" "${actual}" || { rm -f "${expected}" "${actual}"; fail "The installer package contains a missing or unexpected file."; }
  rm -f "${expected}" "${actual}"
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
DEPLOYMENT_MODE=""
PACKAGE_TARGET=""
PAYLOAD_RELATIVE_PATH=""
EXPECTED_SHA256=""
EXPECTED_SIZE=""
PAYLOAD_EXECUTABLE=""
while IFS=$'\t' read -r kind field value fourth fifth sixth extra; do
  [[ -z "${extra:-}" ]] || fail "The verified installer manifest has an invalid row."
  if [[ "${kind}" == "meta" ]]; then
    case "${field}" in
      schema_version) SCHEMA="${value}" ;;
      helper_version) HELPER_VERSION="${value}" ;;
      deployment_mode) DEPLOYMENT_MODE="${value}" ;;
      package_target) PACKAGE_TARGET="${value}" ;;
    esac
  elif [[ "${kind}" == "payload" && "${field}" == "${RUNTIME_ID}" ]]; then
    [[ -z "${PAYLOAD_RELATIVE_PATH}" ]] || fail "The verified installer manifest repeats this Mac payload."
    PAYLOAD_RELATIVE_PATH="${value}"
    EXPECTED_SHA256="${fourth}"
    EXPECTED_SIZE="${fifth}"
    PAYLOAD_EXECUTABLE="${sixth}"
  fi
done < "${MANIFEST_TSV}"
[[ "${SCHEMA}" == "desktop-helper-installer-manifest-v2" ]] || fail "The verified installer manifest schema is unsupported."
[[ "${DEPLOYMENT_MODE}" == "self_contained" && "${PACKAGE_TARGET}" == "macos-dual-arch" ]] \
  || fail "This is not the standard self-contained macOS package."
[[ "${HELPER_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "The verified installer manifest has an invalid version."
safe_relative_path "${PAYLOAD_RELATIVE_PATH}" || fail "The selected payload path is unsafe."
[[ "${EXPECTED_SHA256}" =~ ^[0-9a-f]{64}$ && "${EXPECTED_SIZE}" =~ ^[0-9]+$ ]] \
  || fail "The selected payload metadata is invalid."
[[ "${PAYLOAD_EXECUTABLE}" == "Elvern.VlcOpener" ]] || fail "The selected payload executable is invalid."
SOURCE_PAYLOAD="${PRIVATE_DIR}/${PAYLOAD_RELATIVE_PATH}"
[[ -f "${SOURCE_PAYLOAD}" && ! -L "${SOURCE_PAYLOAD}" ]] || fail "The ${RUNTIME_ID} payload is missing."
[[ "$(wc -c < "${SOURCE_PAYLOAD}" | tr -d '[:space:]')" == "${EXPECTED_SIZE}" ]] || fail "The Helper payload size check failed."
[[ "$(/usr/bin/shasum -a 256 "${SOURCE_PAYLOAD}" | /usr/bin/awk '{print $1}')" == "${EXPECTED_SHA256}" ]] \
  || fail "The Helper payload SHA-256 check failed."

mkdir -p "${DEST_DIR}"
STAGE_ROOT="$(mktemp -d "${DEST_DIR}/.elvern-vlc-opener-stage.XXXXXX")"
STAGED_APP="${STAGE_ROOT}/${APP_NAME}"
"${OSACOMPILE}" -o "${STAGED_APP}" "${APPLESCRIPT_SOURCE}" || fail "The local URL bridge could not be created."
RESOURCES_DIR="${STAGED_APP}/Contents/Resources"
APP_PAYLOAD_DIR="${RESOURCES_DIR}/app"
mkdir -p "${APP_PAYLOAD_DIR}"
cp "${SOURCE_PAYLOAD}" "${APP_PAYLOAD_DIR}/Elvern.VlcOpener"
chmod 755 "${APP_PAYLOAD_DIR}/Elvern.VlcOpener"
cp "${RUNNER_TEMPLATE}" "${RESOURCES_DIR}/run-helper.sh"
chmod 755 "${RESOURCES_DIR}/run-helper.sh"
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

REPLACEMENT_STARTED=1
if [[ -d "${DEST_APP}" ]]; then
  BACKUP_APP="${DEST_DIR}/.elvern-vlc-opener-backup.$$.app"
  mv "${DEST_APP}" "${BACKUP_APP}" || fail "The existing Helper App could not be staged for upgrade."
fi
mv "${STAGED_APP}" "${DEST_APP}" || fail "The new Helper App could not replace the existing installation."
xattr -dr com.apple.quarantine "${DEST_APP}" \
  || fail "macOS quarantine could not be removed from the verified installed Helper App."
verify_quarantine_cleared "${DEST_APP}"
"${LSREGISTER}" -f "${DEST_APP}" >/dev/null 2>&1 \
  || fail "Launch Services could not register the installed Helper App."
"${DEST_APP}/Contents/Resources/app/Elvern.VlcOpener" --version >/dev/null \
  || fail "The installed Helper failed its final version check."
touch "${DEST_APP}"
open -R "${DEST_APP}" >/dev/null 2>&1 || fail "Finder could not reveal the installed Helper App."
INSTALL_SUCCEEDED=1
echo "Installed ${APP_NAME} ${HELPER_VERSION} into ${DEST_APP}"
echo "The App uses a local ad-hoc structural signature; it is not Developer ID signed or notarized."
