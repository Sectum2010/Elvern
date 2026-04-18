#!/usr/bin/env bash
set -euo pipefail

HELPER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER_DLL="${1:-${HELPER_ROOT}/bin/Debug/net8.0/Elvern.VlcOpener.dll}"
APP_DIR="${HOME}/Applications/Elvern VLC Opener.app"
CONTENTS_DIR="${APP_DIR}/Contents"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
INFO_PLIST="${CONTENTS_DIR}/Info.plist"
RUNNER="${RESOURCES_DIR}/run-helper.sh"
OSACOMPILE="/usr/bin/osacompile"
PLISTBUDDY="/usr/libexec/PlistBuddy"
TMP_SOURCE="$(mktemp)"
TMP_RUNNER="$(mktemp)"

cleanup() {
  rm -f "${TMP_SOURCE}" "${TMP_RUNNER}"
}
trap cleanup EXIT

cat >"${TMP_SOURCE}" <<'EOF'
on run argv
  if (count of argv) is 0 then
    return
  end if
  my forwardURL(item 1 of argv)
end run

on open location this_URL
  my forwardURL(this_URL)
end open location

on forwardURL(this_URL)
  set appPath to POSIX path of (path to me)
  set runnerPath to quoted form of (appPath & "Contents/Resources/run-helper.sh")
  set quotedURL to quoted form of this_URL
  do shell script "/bin/bash " & runnerPath & " " & quotedURL & " >/dev/null 2>&1 &"
end forwardURL
EOF

cat >"${TMP_RUNNER}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="\${HOME}/Library/Logs/ElvernVlcOpener"
LOG_FILE="\${LOG_DIR}/opener.log"
mkdir -p "\${LOG_DIR}"
exec >>"\${LOG_FILE}" 2>&1

DOTNET_CMD=""
DOTNET_ROOT_DIR=""
declare -a CHECKED_DOTNET_PATHS=(
  "/usr/local/share/dotnet/dotnet"
  "/opt/homebrew/share/dotnet/dotnet"
  "/usr/local/bin/dotnet"
  "/opt/homebrew/bin/dotnet"
)

for candidate in "\${CHECKED_DOTNET_PATHS[@]}"; do
  if [[ -x "\${candidate}" ]]; then
    DOTNET_CMD="\${candidate}"
    case "\${candidate}" in
      /usr/local/share/dotnet/dotnet)
        DOTNET_ROOT_DIR="/usr/local/share/dotnet"
        ;;
      /opt/homebrew/share/dotnet/dotnet)
        DOTNET_ROOT_DIR="/opt/homebrew/share/dotnet"
        ;;
      /usr/local/bin/dotnet)
        DOTNET_ROOT_DIR="/usr/local/share/dotnet"
        ;;
      /opt/homebrew/bin/dotnet)
        DOTNET_ROOT_DIR="/opt/homebrew/share/dotnet"
        ;;
    esac
    break
  fi
done

if [[ -z "\${DOTNET_CMD}" ]] && command -v dotnet >/dev/null 2>&1; then
  DOTNET_CMD="\$(command -v dotnet)"
  case "\${DOTNET_CMD}" in
    */share/dotnet/dotnet)
      DOTNET_ROOT_DIR="\$(dirname "\${DOTNET_CMD}")"
      ;;
    */bin/dotnet)
      DOTNET_ROOT_DIR="\$(cd "\$(dirname "\${DOTNET_CMD}")/.." && pwd)"
      ;;
    *)
      DOTNET_ROOT_DIR="\$(dirname "\${DOTNET_CMD}")"
      ;;
  esac
fi

if [[ -z "\${DOTNET_CMD}" ]]; then
  echo "Could not find dotnet for dev protocol registration."
  exit 1
fi

if [[ -n "\${DOTNET_ROOT_DIR}" ]]; then
  export DOTNET_ROOT="\${DOTNET_ROOT_DIR}"
fi

exec "\${DOTNET_CMD}" "${HELPER_DLL}" "\$@"
EOF

"${OSACOMPILE}" -o "${APP_DIR}" "${TMP_SOURCE}"
mkdir -p "${RESOURCES_DIR}"
cp "${TMP_RUNNER}" "${RUNNER}"
chmod 755 "${RUNNER}"

if [[ -x "${PLISTBUDDY}" ]]; then
  "${PLISTBUDDY}" -c "Set :CFBundleIdentifier local.elvern.vlcopener" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleIdentifier string local.elvern.vlcopener" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Set :CFBundleName Elvern VLC Opener" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleName string Elvern VLC Opener" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Set :CFBundleDisplayName Elvern VLC Opener" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleDisplayName string Elvern VLC Opener" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Set :CFBundleShortVersionString 1.0" "${INFO_PLIST}" >/dev/null 2>&1 || \
    "${PLISTBUDDY}" -c "Add :CFBundleShortVersionString string 1.0" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Delete :CFBundleURLTypes" "${INFO_PLIST}" >/dev/null 2>&1 || true
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes array" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0 dict" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLName string Elvern VLC Opener" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "${INFO_PLIST}"
  "${PLISTBUDDY}" -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string elvern-vlc" "${INFO_PLIST}"
fi

if [[ -x "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister" ]]; then
  "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister" -f "${APP_DIR}" >/dev/null 2>&1 || true
fi

touch "${APP_DIR}"
echo "Registered elvern-vlc:// via ${APP_DIR}"
