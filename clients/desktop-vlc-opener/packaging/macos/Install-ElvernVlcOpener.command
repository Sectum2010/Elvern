#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRIVATE_DIR="${SCRIPT_DIR}/.elvern"
MANIFEST_PATH="${PRIVATE_DIR}/manifest.json"
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
  if [[ -n "${STAGE_ROOT}" && -d "${STAGE_ROOT}" ]]; then
    rm -rf "${STAGE_ROOT}"
  fi
  if [[ ${INSTALL_SUCCEEDED} -eq 0 && ${REPLACEMENT_STARTED} -eq 1 ]]; then
    [[ -d "${DEST_APP}" ]] && rm -rf "${DEST_APP}"
    if [[ -n "${BACKUP_APP}" && -d "${BACKUP_APP}" ]]; then
      mv "${BACKUP_APP}" "${DEST_APP}" || true
    fi
  fi
  if [[ ${INSTALL_SUCCEEDED} -eq 1 && -n "${BACKUP_APP}" && -d "${BACKUP_APP}" ]]; then
    rm -rf "${BACKUP_APP}"
  fi
}
trap cleanup EXIT

fail() {
  show_error "$1"
  exit 1
}

[[ -f "${MANIFEST_PATH}" ]] || fail "The installer manifest is missing. Download a fresh Elvern package."
[[ -f "${SELECTORS}" ]] || fail "The platform selector is missing."
[[ -f "${APPLESCRIPT_SOURCE}" ]] || fail "The macOS URL bridge is missing."
[[ -f "${RUNNER_TEMPLATE}" ]] || fail "The helper runner is missing."
[[ -x "${OSACOMPILE}" ]] || fail "macOS could not find osacompile."
[[ -x "${PLISTBUDDY}" ]] || fail "macOS could not find PlistBuddy."
command -v python3 >/dev/null 2>&1 || fail "python3 is required to validate the signed installer manifest."
command -v shasum >/dev/null 2>&1 || fail "shasum is required to verify the helper payload."
command -v codesign >/dev/null 2>&1 || fail "codesign is required to build the local Helper App."

MACOS_VERSION="$(sw_vers -productVersion 2>/dev/null || true)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
[[ "${MACOS_MAJOR}" =~ ^[0-9]+$ ]] || fail "The macOS version could not be determined."
(( MACOS_MAJOR >= 14 )) || fail "macOS 14 or newer is required."

# shellcheck disable=SC1090
source "${SELECTORS}"
TRANSLATED="$(sysctl -in sysctl.proc_translated 2>/dev/null || printf '0')"
RUNTIME_ID="$(select_macos_runtime "${TRANSLATED}" "$(uname -m)")" || fail "This Mac CPU is not supported."

MANIFEST_OUTPUT="$(python3 - "${MANIFEST_PATH}" "${RUNTIME_ID}" <<'PY'
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
runtime_id = sys.argv[2]
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
if payload.get("schema_version") != "desktop-helper-installer-manifest-v1":
    raise SystemExit("Unsupported installer manifest schema")
if payload.get("deployment_mode") != "self_contained":
    raise SystemExit("Installer payload is not self-contained")
version = payload.get("helper_version")
if not isinstance(version, str) or not version.strip():
    raise SystemExit("Missing helper version")
matches = [entry for entry in payload.get("payloads", []) if entry.get("runtime_id") == runtime_id]
if len(matches) != 1:
    raise SystemExit(f"Missing unique payload for {runtime_id}")
entry = matches[0]
relative = pathlib.PurePosixPath(str(entry.get("relative_path", "")))
if relative.is_absolute() or ".." in relative.parts or not relative.parts:
    raise SystemExit("Unsafe payload path")
sha256 = str(entry.get("sha256", ""))
size = entry.get("size_bytes")
if len(sha256) != 64 or not all(character in "0123456789abcdef" for character in sha256.lower()):
    raise SystemExit("Invalid payload hash")
if not isinstance(size, int) or size <= 0:
    raise SystemExit("Invalid payload size")
print(version)
print(relative.as_posix())
print(sha256.lower())
print(size)
PY
)" || fail "The installer manifest is invalid."

[[ "$(printf '%s\n' "${MANIFEST_OUTPUT}" | wc -l | tr -d '[:space:]')" == "4" ]] || fail "The installer manifest did not return a valid payload."
HELPER_VERSION="$(printf '%s\n' "${MANIFEST_OUTPUT}" | sed -n '1p')"
PAYLOAD_RELATIVE_PATH="$(printf '%s\n' "${MANIFEST_OUTPUT}" | sed -n '2p')"
EXPECTED_SHA256="$(printf '%s\n' "${MANIFEST_OUTPUT}" | sed -n '3p')"
EXPECTED_SIZE="$(printf '%s\n' "${MANIFEST_OUTPUT}" | sed -n '4p')"
SOURCE_PAYLOAD="${PRIVATE_DIR}/${PAYLOAD_RELATIVE_PATH}"
[[ -f "${SOURCE_PAYLOAD}" ]] || fail "The ${RUNTIME_ID} payload is missing."
ACTUAL_SIZE="$(wc -c < "${SOURCE_PAYLOAD}" | tr -d '[:space:]')"
[[ "${ACTUAL_SIZE}" == "${EXPECTED_SIZE}" ]] || fail "The helper payload size does not match its manifest."
ACTUAL_SHA256="$(/usr/bin/shasum -a 256 "${SOURCE_PAYLOAD}" | awk '{print $1}')"
[[ "${ACTUAL_SHA256}" == "${EXPECTED_SHA256}" ]] || fail "The helper payload SHA-256 check failed."
chmod 755 "${SOURCE_PAYLOAD}"
"${SOURCE_PAYLOAD}" --version >/dev/null || fail "The selected helper payload did not pass its version check."

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

codesign --force --deep --sign - "${STAGED_APP}" >/dev/null || fail "The local App could not be structurally signed."
codesign --verify --deep --strict "${STAGED_APP}" >/dev/null 2>&1 || fail "The local App signature verification failed."
xattr -dr com.apple.quarantine "${STAGED_APP}" >/dev/null 2>&1 || true
"${APP_PAYLOAD_DIR}/Elvern.VlcOpener" --version >/dev/null || fail "The staged helper failed its version check."

REPLACEMENT_STARTED=1
if [[ -d "${DEST_APP}" ]]; then
  BACKUP_APP="${DEST_DIR}/.elvern-vlc-opener-backup.$$.app"
  mv "${DEST_APP}" "${BACKUP_APP}" || fail "The existing Helper App could not be staged for upgrade."
fi
mv "${STAGED_APP}" "${DEST_APP}" || fail "The new Helper App could not replace the existing installation."
xattr -dr com.apple.quarantine "${DEST_APP}" >/dev/null 2>&1 || true
if [[ -x "${LSREGISTER}" ]]; then
  "${LSREGISTER}" -f "${DEST_APP}" >/dev/null 2>&1 || true
fi
"${DEST_APP}/Contents/Resources/app/Elvern.VlcOpener" --version >/dev/null || fail "The installed helper failed its final version check."
touch "${DEST_APP}"
INSTALL_SUCCEEDED=1
open -R "${DEST_APP}" >/dev/null 2>&1 || true
echo "Installed ${APP_NAME} ${HELPER_VERSION} into ${DEST_APP}"
echo "The App uses a local ad-hoc structural signature; it is not Developer ID signed or notarized."
