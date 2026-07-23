#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRIVATE_DIR="${SCRIPT_DIR}/.elvern"
MANIFEST_TSV="${PRIVATE_DIR}/installer-manifest.tsv"
TREE_MANIFEST="${PRIVATE_DIR}/tree-manifest.tsv"
SELECTORS="${PRIVATE_DIR}/lib/platform-selectors.sh"
UNINSTALL_SOURCE="${PRIVATE_DIR}/uninstall/Uninstall-ElvernVlcOpener.sh"
INSTALL_PARENT="${HOME}/.local/lib"
INSTALL_DIR="${INSTALL_PARENT}/elvern-vlc-opener"
DESKTOP_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${DESKTOP_DIR}/elvern-vlc-opener.desktop"
RUNTIME_OVERRIDE=""
STAGE_DIR=""
BACKUP_DIR=""
DESKTOP_BACKUP=""
PREVIOUS_PROTOCOL_DEFAULT=""
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
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

cleanup() {
  [[ -n "${STAGE_DIR}" && -d "${STAGE_DIR}" ]] && rm -rf "${STAGE_DIR}"
  if [[ ${INSTALL_SUCCEEDED} -eq 0 && ${REPLACEMENT_STARTED} -eq 1 ]]; then
    [[ -d "${INSTALL_DIR}" ]] && rm -rf "${INSTALL_DIR}"
    if [[ -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]]; then
      mv "${BACKUP_DIR}" "${INSTALL_DIR}" || true
    fi
    if [[ -n "${PREVIOUS_PROTOCOL_DEFAULT}" ]]; then
      xdg-mime default "${PREVIOUS_PROTOCOL_DEFAULT}" x-scheme-handler/elvern-vlc >/dev/null 2>&1 || true
    elif [[ -f "${DESKTOP_FILE}" ]]; then
      xdg-mime uninstall --mode user "${DESKTOP_FILE}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${DESKTOP_BACKUP}" && -f "${DESKTOP_BACKUP}" ]]; then
      mv "${DESKTOP_BACKUP}" "${DESKTOP_FILE}" || true
    else
      rm -f "${DESKTOP_FILE}"
    fi
  fi
  [[ ${INSTALL_SUCCEEDED} -eq 1 && -n "${BACKUP_DIR}" && -d "${BACKUP_DIR}" ]] && rm -rf "${BACKUP_DIR}"
  [[ -n "${DESKTOP_BACKUP}" && -f "${DESKTOP_BACKUP}" ]] && rm -f "${DESKTOP_BACKUP}"
  return 0
}
trap cleanup EXIT

fail() {
  echo "Elvern VLC Opener was not installed: $1" >&2
  exit 1
}

safe_relative_path() {
  local value="$1"
  [[ -n "${value}" && "${value}" != /* && "${value}" != *\\* ]] || return 1
  case "/${value}/" in
    */../*|*/./*) return 1 ;;
  esac
}

verify_package_tree() {
  [[ -f "${TREE_MANIFEST}" && ! -L "${TREE_MANIFEST}" ]] || fail "the installer tree manifest is missing."
  if find "${SCRIPT_DIR}" -type l -print -quit | grep -q .; then
    fail "the installer package contains an unsafe link."
  fi
  local expected actual path size digest file_class extra full
  expected="$(mktemp)"
  actual="$(mktemp)"
  while IFS=$'\t' read -r path size digest file_class extra; do
    [[ "${path}" == "path" ]] && continue
    [[ -z "${extra:-}" ]] || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest has an invalid row."; }
    safe_relative_path "${path}" || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest contains an unsafe path."; }
    [[ "${size}" =~ ^[0-9]+$ && "${digest}" =~ ^[0-9a-f]{64}$ && "${file_class}" =~ ^(data|executable)$ ]] \
      || { rm -f "${expected}" "${actual}"; fail "the installer tree manifest contains invalid metadata."; }
    full="${SCRIPT_DIR}/${path}"
    [[ -f "${full}" && ! -L "${full}" ]] || { rm -f "${expected}" "${actual}"; fail "an installer file is missing or unsafe."; }
    [[ "$(wc -c < "${full}" | tr -d '[:space:]')" == "${size}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "an installer file size check failed."; }
    [[ "$(sha256sum "${full}" | awk '{print $1}')" == "${digest}" ]] \
      || { rm -f "${expected}" "${actual}"; fail "an installer file SHA-256 check failed."; }
    printf '%s\n' "${path}" >> "${expected}"
  done < "${TREE_MANIFEST}"
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
DEPLOYMENT_MODE=""
PACKAGE_TARGET=""
PAYLOAD_RELATIVE_PATH=""
EXPECTED_SHA=""
EXPECTED_SIZE=""
PAYLOAD_EXECUTABLE=""
while IFS=$'\t' read -r kind field value fourth fifth sixth extra; do
  [[ -z "${extra:-}" ]] || fail "the verified installer manifest has an invalid row."
  if [[ "${kind}" == "meta" ]]; then
    case "${field}" in
      schema_version) SCHEMA="${value}" ;;
      helper_version) HELPER_VERSION="${value}" ;;
      deployment_mode) DEPLOYMENT_MODE="${value}" ;;
      package_target) PACKAGE_TARGET="${value}" ;;
    esac
  elif [[ "${kind}" == "payload" && "${field}" == "${RUNTIME_ID}" ]]; then
    [[ -z "${PAYLOAD_RELATIVE_PATH}" ]] || fail "the runtime is duplicated in the verified allowlist."
    PAYLOAD_RELATIVE_PATH="${value}"
    EXPECTED_SHA="${fourth}"
    EXPECTED_SIZE="${fifth}"
    PAYLOAD_EXECUTABLE="${sixth}"
  fi
done < "${MANIFEST_TSV}"
[[ "${SCHEMA}" == "desktop-helper-installer-manifest-v2" ]] || fail "the verified installer manifest schema is unsupported."
[[ "${DEPLOYMENT_MODE}" == "self_contained" && "${PACKAGE_TARGET}" == "linux-universal" ]] \
  || fail "this is not the standard self-contained Linux package."
[[ "${HELPER_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "the verified installer manifest has an invalid version."
[[ -n "${PAYLOAD_RELATIVE_PATH}" ]] || fail "the selected runtime is not in the package allowlist."
safe_relative_path "${PAYLOAD_RELATIVE_PATH}" || fail "the selected payload path is unsafe."
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{64}$ && "${EXPECTED_SIZE}" =~ ^[0-9]+$ ]] \
  || fail "the selected payload metadata is invalid."
[[ "${PAYLOAD_EXECUTABLE}" == "Elvern.VlcOpener" ]] || fail "the selected payload executable is invalid."
PAYLOAD="${PRIVATE_DIR}/${PAYLOAD_RELATIVE_PATH}"
[[ -f "${PAYLOAD}" && ! -L "${PAYLOAD}" ]] || fail "the ${RUNTIME_ID} payload is missing."
[[ "$(wc -c < "${PAYLOAD}" | tr -d '[:space:]')" == "${EXPECTED_SIZE}" ]] || fail "the payload size check failed."
[[ "$(sha256sum "${PAYLOAD}" | awk '{print $1}')" == "${EXPECTED_SHA}" ]] || fail "the payload SHA-256 check failed."

mkdir -p "${INSTALL_PARENT}" "${DESKTOP_DIR}" \
  || fail "the user-level installation directories could not be created."
STAGE_DIR="$(mktemp -d "${INSTALL_PARENT}/.elvern-vlc-opener-stage.XXXXXX")" \
  || fail "a staged installation directory could not be created."
cp "${PAYLOAD}" "${STAGE_DIR}/Elvern.VlcOpener"
chmod 755 "${STAGE_DIR}/Elvern.VlcOpener"
cp "${UNINSTALL_SOURCE}" "${STAGE_DIR}/Uninstall-ElvernVlcOpener.sh"
chmod 755 "${STAGE_DIR}/Uninstall-ElvernVlcOpener.sh"
"${STAGE_DIR}/Elvern.VlcOpener" --version >/dev/null \
  || fail "the staged payload failed its version check."

PREVIOUS_PROTOCOL_DEFAULT="$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null || true)"
if [[ -f "${DESKTOP_FILE}" ]]; then
  DESKTOP_BACKUP="${DESKTOP_DIR}/.elvern-vlc-opener.desktop.backup.$$"
  cp "${DESKTOP_FILE}" "${DESKTOP_BACKUP}"
fi
REPLACEMENT_STARTED=1
if [[ -d "${INSTALL_DIR}" ]]; then
  BACKUP_DIR="${INSTALL_PARENT}/.elvern-vlc-opener-backup.$$"
  mv "${INSTALL_DIR}" "${BACKUP_DIR}" || fail "the existing installation could not be staged for upgrade."
fi
mv "${STAGE_DIR}" "${INSTALL_DIR}" || fail "the new Helper could not replace the existing installation."
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
xdg-mime default elvern-vlc-opener.desktop x-scheme-handler/elvern-vlc \
  || fail "xdg-mime could not register the protocol handler."
[[ "$(xdg-mime query default x-scheme-handler/elvern-vlc 2>/dev/null || true)" == "elvern-vlc-opener.desktop" ]] \
  || fail "the registered protocol handler could not be verified."
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
fi
"${INSTALL_DIR}/Elvern.VlcOpener" --version >/dev/null \
  || fail "the installed Helper failed its final version check."
INSTALL_SUCCEEDED=1
echo "Installed Elvern VLC Opener ${HELPER_VERSION} for ${RUNTIME_ID}."
echo "Registered elvern-vlc:// without administrator privileges."
