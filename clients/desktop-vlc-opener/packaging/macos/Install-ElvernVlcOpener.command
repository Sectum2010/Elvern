#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
METADATA_FILE="${SCRIPT_DIR}/../helper-release.env"
APP_NAME="Elvern VLC Opener.app"
SOURCE_APP="${SCRIPT_DIR}/${APP_NAME}"
DEST_DIR="${HOME}/Applications"
DEST_APP="${DEST_DIR}/${APP_NAME}"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
OSACOMPILE="/usr/bin/osacompile"
PLISTBUDDY="/usr/libexec/PlistBuddy"
APPLESCRIPT_SOURCE="${SCRIPT_DIR}/ElvernVlcOpener.applescript"
RUNNER_TEMPLATE="${SCRIPT_DIR}/run-helper.sh.template"
SOURCE_PAYLOAD_DIR="${SOURCE_APP}/Contents/Resources/app"
DEST_PAYLOAD_DIR="${DEST_APP}/Contents/Resources/app"
DEST_RUNNER="${DEST_APP}/Contents/Resources/run-helper.sh"
INFO_PLIST="${DEST_APP}/Contents/Info.plist"
DOTNET_FOUND=0

if [[ ! -f "${METADATA_FILE}" ]]; then
  echo "Missing helper packaging metadata: ${METADATA_FILE}."
  exit 1
fi

# shellcheck disable=SC1090
source "${METADATA_FILE}"

for required_key in HELPER_VERSION HELPER_CHANNEL DOTNET_RUNTIME_MAJOR DOTNET_RUNTIME_DISPLAY PACKAGE_NAME_PREFIX; do
  if [[ -z "${!required_key:-}" ]]; then
    echo "Missing ${required_key} in ${METADATA_FILE}."
    exit 1
  fi
done

for dotnet_candidate in \
  /usr/local/share/dotnet/dotnet \
  /opt/homebrew/share/dotnet/dotnet \
  /usr/local/bin/dotnet \
  /opt/homebrew/bin/dotnet
do
  if [[ -x "${dotnet_candidate}" ]]; then
    DOTNET_FOUND=1
    break
  fi
done

if [[ ${DOTNET_FOUND} -eq 0 ]] && command -v dotnet >/dev/null 2>&1; then
  DOTNET_FOUND=1
fi

if [[ ! -d "${SOURCE_APP}" ]]; then
  echo "Missing ${APP_NAME} next to this installer."
  exit 1
fi

if [[ ! -d "${SOURCE_PAYLOAD_DIR}" ]]; then
  echo "Missing packaged helper payload inside ${APP_NAME}."
  exit 1
fi

if [[ ! -f "${APPLESCRIPT_SOURCE}" ]]; then
  echo "Missing ElvernVlcOpener.applescript next to this installer."
  exit 1
fi

if [[ ! -f "${RUNNER_TEMPLATE}" ]]; then
  echo "Missing run-helper.sh.template next to this installer."
  exit 1
fi

if [[ ! -x "${OSACOMPILE}" ]]; then
  echo "macOS install requires ${OSACOMPILE} so the app can receive URL-open events."
  exit 1
fi

mkdir -p "${DEST_DIR}"
rm -rf "${DEST_APP}"

"${OSACOMPILE}" -o "${DEST_APP}" "${APPLESCRIPT_SOURCE}"
mkdir -p "${DEST_PAYLOAD_DIR}"

if command -v ditto >/dev/null 2>&1; then
  ditto "${SOURCE_PAYLOAD_DIR}" "${DEST_PAYLOAD_DIR}"
else
  cp -R "${SOURCE_PAYLOAD_DIR}" "${DEST_PAYLOAD_DIR}"
fi

cp "${RUNNER_TEMPLATE}" "${DEST_RUNNER}"
chmod 755 "${DEST_RUNNER}"

if [[ -x "${PLISTBUDDY}" ]]; then
  "${PLISTBUDDY}" -c "Set :CFBundleIdentifier local.elvern.vlcopener" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleIdentifier string local.elvern.vlcopener" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Set :CFBundleName Elvern VLC Opener" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleName string Elvern VLC Opener" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Set :CFBundleDisplayName Elvern VLC Opener" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleDisplayName string Elvern VLC Opener" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Set :CFBundleShortVersionString ${HELPER_VERSION}" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleShortVersionString string ${HELPER_VERSION}" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Set :CFBundleVersion ${HELPER_VERSION}" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleVersion string ${HELPER_VERSION}" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Set :LSMinimumSystemVersion 12.0" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :LSMinimumSystemVersion string 12.0" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Delete :CFBundleURLTypes" "${INFO_PLIST}" >/dev/null 2>&1 || true
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes array" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0 dict" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLName string Elvern VLC Opener" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string elvern-vlc" "${INFO_PLIST}"
fi

xattr -dr com.apple.quarantine "${DEST_APP}" >/dev/null 2>&1 || true

if [[ -x "${LSREGISTER}" ]]; then
  "${LSREGISTER}" -f "${DEST_APP}" >/dev/null 2>&1 || true
fi

if [[ -f "${DEST_APP}/Contents/Resources/app/Elvern.VlcOpener.dll" ]] && [[ ! -x "${DEST_APP}/Contents/Resources/app/Elvern.VlcOpener" ]]; then
  if [[ ${DOTNET_FOUND} -eq 0 ]]; then
    echo
    echo "Note: this package is framework-dependent."
    echo "Elvern VLC Opener checked:"
    echo "  /usr/local/share/dotnet/dotnet"
    echo "  /opt/homebrew/share/dotnet/dotnet"
    echo "  /usr/local/bin/dotnet"
    echo "  /opt/homebrew/bin/dotnet"
    echo "  PATH lookup for dotnet"
    echo "Install the ${DOTNET_RUNTIME_DISPLAY} on this Mac before using Elvern VLC Opener."
  fi
fi

touch "${DEST_APP}"
echo "Installed ${APP_NAME} into ${DEST_APP}"
echo "You can now click Open in VLC from Elvern on this Mac."
