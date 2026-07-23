#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRIVATE_DIR="${SCRIPT_DIR}/.elvern"
MANIFEST_PATH="${PRIVATE_DIR}/manifest.json"
SELECTORS="${PRIVATE_DIR}/lib/platform-selectors.sh"
INSTALL_PARENT="${HOME}/.local/lib"
INSTALL_DIR="${INSTALL_PARENT}/elvern-vlc-opener"
DESKTOP_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${DESKTOP_DIR}/elvern-vlc-opener.desktop"
RUNTIME_OVERRIDE=""
STAGE_DIR=""
BACKUP_DIR=""
INSTALL_SUCCEEDED=0
REPLACEMENT_STARTED=0

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

cleanup() {
  [[ -n "${STAGE_DIR}" && -d "${STAGE_DIR}" ]] && rm -rf "${STAGE_DIR}"
  if [[ ${INSTALL_SUCCEEDED} -eq 0 && ${REPLACEMENT_STARTED} -eq 1 ]]; then
    [[ -d "${INSTALL_DIR}" ]] && rm -rf "${INSTALL_DIR}"
    if [[ -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]]; then
      mv "${BACKUP_DIR}" "${INSTALL_DIR}" || true
    fi
  fi
  [[ ${INSTALL_SUCCEEDED} -eq 1 && -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]] && rm -rf "${BACKUP_DIR}"
}
trap cleanup EXIT

fail() {
  echo "Elvern VLC Opener was not installed: $1" >&2
  exit 1
}

[[ -f "${MANIFEST_PATH}" ]] || fail "the installer manifest is missing."
[[ -f "${SELECTORS}" ]] || fail "the platform selector is missing."
command -v python3 >/dev/null 2>&1 || fail "python3 is required to validate the installer manifest."
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required to verify the payload."
command -v xdg-mime >/dev/null 2>&1 || fail "xdg-mime is required to register elvern-vlc://."

# shellcheck disable=SC1090
source "${SELECTORS}"
if [[ -n "${RUNTIME_OVERRIDE}" ]]; then
  RUNTIME_ID="${RUNTIME_OVERRIDE}"
else
  LIBC_FAMILY="$(detect_linux_libc)" || fail "the system libc could not be identified."
  RUNTIME_ID="$(select_linux_runtime "$(uname -m)" "${LIBC_FAMILY}")" || fail "the CPU or libc is unsupported."
fi

mapfile -t MANIFEST_VALUES < <(python3 - "${MANIFEST_PATH}" "${RUNTIME_ID}" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
runtime_id = sys.argv[2]
if payload.get("schema_version") != "desktop-helper-installer-manifest-v1":
    raise SystemExit("Unsupported installer manifest")
if payload.get("deployment_mode") != "self_contained":
    raise SystemExit("Payload is not self-contained")
matches = [entry for entry in payload.get("payloads", []) if entry.get("runtime_id") == runtime_id]
if len(matches) != 1:
    raise SystemExit("Runtime override is not in the package allowlist")
entry = matches[0]
relative = pathlib.PurePosixPath(str(entry.get("relative_path", "")))
if relative.is_absolute() or ".." in relative.parts or not relative.parts:
    raise SystemExit("Unsafe payload path")
sha256 = str(entry.get("sha256", ""))
size = entry.get("size_bytes")
if len(sha256) != 64 or not all(c in "0123456789abcdef" for c in sha256.lower()):
    raise SystemExit("Invalid payload hash")
if not isinstance(size, int) or size <= 0:
    raise SystemExit("Invalid payload size")
print(payload["helper_version"])
print(relative.as_posix())
print(sha256.lower())
print(size)
PY
) || fail "the installer manifest is invalid or does not support ${RUNTIME_ID}."
[[ ${#MANIFEST_VALUES[@]} -eq 4 ]] || fail "the installer manifest did not return a valid payload."
HELPER_VERSION="${MANIFEST_VALUES[0]}"
PAYLOAD="${PRIVATE_DIR}/${MANIFEST_VALUES[1]}"
EXPECTED_SHA="${MANIFEST_VALUES[2]}"
EXPECTED_SIZE="${MANIFEST_VALUES[3]}"
[[ -f "${PAYLOAD}" ]] || fail "the ${RUNTIME_ID} payload is missing."
[[ "$(wc -c < "${PAYLOAD}" | tr -d '[:space:]')" == "${EXPECTED_SIZE}" ]] || fail "the payload size check failed."
[[ "$(sha256sum "${PAYLOAD}" | awk '{print $1}')" == "${EXPECTED_SHA}" ]] || fail "the payload SHA-256 check failed."
chmod 755 "${PAYLOAD}"
"${PAYLOAD}" --version >/dev/null || fail "the selected payload failed its version check."

mkdir -p "${INSTALL_PARENT}" "${DESKTOP_DIR}"
STAGE_DIR="$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-stage.XXXXXX")"
cp "${PAYLOAD}" "${STAGE_DIR}/Elvern.VlcOpener"
chmod 755 "${STAGE_DIR}/Elvern.VlcOpener"
cp "${PRIVATE_DIR}/uninstall/Uninstall-ElvernVlcOpener.sh" "${STAGE_DIR}/Uninstall-ElvernVlcOpener.sh"
chmod 755 "${STAGE_DIR}/Uninstall-ElvernVlcOpener.sh"
"${STAGE_DIR}/Elvern.VlcOpener" --version >/dev/null || fail "the staged payload failed its version check."

REPLACEMENT_STARTED=1
if [[ -d "${INSTALL_DIR}" ]]; then
  BACKUP_DIR="${INSTALL_PARENT}/.elvern-vlc-opener-backup.$$"
  mv "${INSTALL_DIR}" "${BACKUP_DIR}" || fail "the existing installation could not be staged for upgrade."
fi
mv "${STAGE_DIR}" "${INSTALL_DIR}" || fail "the new helper could not replace the existing installation."
STAGE_DIR=""

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
xdg-mime default elvern-vlc-opener.desktop x-scheme-handler/elvern-vlc || fail "xdg-mime could not register the protocol handler."
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
fi
"${INSTALL_DIR}/Elvern.VlcOpener" --version >/dev/null || fail "the installed helper failed its final version check."
INSTALL_SUCCEEDED=1
echo "Installed Elvern VLC Opener ${HELPER_VERSION} for ${RUNTIME_ID}."
echo "Registered elvern-vlc:// without administrator privileges."
