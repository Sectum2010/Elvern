#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_FILE="${PROJECT_DIR}/Elvern.VlcOpener.csproj"
PROPS_FILE="${PROJECT_DIR}/Directory.Build.props"
PACKAGING_DIR="${PROJECT_DIR}/packaging"
METADATA_FILE="${PACKAGING_DIR}/helper-release.env"
ARTIFACTS_DIR="${PROJECT_DIR}/artifacts"
PACKAGES_DIR="${ARTIFACTS_DIR}/packages"
COMMON_README="${PACKAGING_DIR}/common/README.txt"
SELECTORS="${PACKAGING_DIR}/common/platform-selectors.sh"
PUBLISH_MODE="self-contained"
NUGET_SOURCE="${ELVERN_DOTNET_NUGET_SOURCE:-https://api.nuget.org/v3/index.json}"
GENERATED_AT_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
declare -a TARGETS=("windows" "macos" "linux")
declare -a WINDOWS_RIDS=("win-x64")
declare -a MACOS_RIDS=("osx-arm64" "osx-x64")
declare -a LINUX_RIDS=("linux-x64" "linux-arm64" "linux-musl-x64" "linux-musl-arm64")

usage() {
  cat <<'EOF'
Usage: ./scripts/publish-bundles.sh [--platform windows|macos|linux]

Standard publishing is always self-contained and non-trimmed. With no options,
the script builds Windows x64, macOS dual-architecture, and Linux universal packages.
Repeat --platform to build a selected set. A failed RID aborts the entire publish.
EOF
}

if [[ $# -gt 0 ]]; then
  TARGETS=()
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      [[ $# -ge 2 ]] || { echo "Missing value for --platform." >&2; exit 1; }
      case "$2" in
        windows|macos|linux) TARGETS+=("$2") ;;
        *) echo "Unsupported platform target: $2" >&2; exit 1 ;;
      esac
      shift 2
      ;;
    --windows) TARGETS+=("windows"); shift ;;
    --macos) TARGETS+=("macos"); shift ;;
    --linux) TARGETS+=("linux"); shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done
[[ ${#TARGETS[@]} -gt 0 ]] || { echo "At least one platform target is required." >&2; exit 1; }

for required_file in "${PROJECT_FILE}" "${PROPS_FILE}" "${METADATA_FILE}" "${COMMON_README}" "${SELECTORS}"; do
  [[ -f "${required_file}" ]] || { echo "Missing required file: ${required_file}" >&2; exit 1; }
done
command -v python3 >/dev/null 2>&1 || { echo "python3 is required for deterministic manifest generation." >&2; exit 1; }
command -v zip >/dev/null 2>&1 || { echo "zip is required to create helper packages." >&2; exit 1; }
command -v dotnet >/dev/null 2>&1 || { echo "dotnet SDK 10 is required to publish helper packages." >&2; exit 1; }
dotnet --list-sdks | awk '{print $1}' | grep -Eq '^10\.' || {
  echo ".NET SDK 10 is required to publish self-contained helper packages." >&2
  exit 1
}

# shellcheck disable=SC1090
source "${METADATA_FILE}"
for required_key in HELPER_CHANNEL PACKAGE_NAME_PREFIX MACOS_MINIMUM_VERSION; do
  [[ -n "${!required_key:-}" ]] || { echo "Missing ${required_key} in ${METADATA_FILE}." >&2; exit 1; }
done
if [[ -z "${ELVERN_BACKEND_ORIGIN:-}" || ! "${ELVERN_BACKEND_ORIGIN}" =~ ^https?://[^/]+/?$ ]]; then
  echo "Set ELVERN_BACKEND_ORIGIN to the exact absolute http(s) backend origin before publishing." >&2
  exit 1
fi
ELVERN_BACKEND_ORIGIN="${ELVERN_BACKEND_ORIGIN%/}"

mapfile -t BUILD_PROPERTIES < <(python3 - "${PROPS_FILE}" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
values = {child.tag: (child.text or "").strip() for group in root.findall("PropertyGroup") for child in group}
version = values.get("ElvernHelperVersion", "")
framework = values.get("TargetFramework", "")
if not version or not framework:
    raise SystemExit("Directory.Build.props must define ElvernHelperVersion and TargetFramework")
print(version)
print(framework)
PY
)
[[ ${#BUILD_PROPERTIES[@]} -eq 2 ]] || { echo "Could not read helper build properties." >&2; exit 1; }
HELPER_VERSION="${BUILD_PROPERTIES[0]}"
TARGET_FRAMEWORK="${BUILD_PROPERTIES[1]}"
[[ "${TARGET_FRAMEWORK}" == "net10.0" ]] || { echo "Standard helper publishing requires net10.0, found ${TARGET_FRAMEWORK}." >&2; exit 1; }
RUNTIME_FAMILY="10.0"

mkdir -p "${ARTIFACTS_DIR}" "${PACKAGES_DIR}"
WORK_DIR="$(mktemp -d "${ARTIFACTS_DIR}/.package-build.XXXXXX")"
trap 'rm -rf "${WORK_DIR}"' EXIT
PUBLISH_ROOT="${WORK_DIR}/publish"
PACKAGE_ROOT="${WORK_DIR}/packages"
RECORDS_FILE="${WORK_DIR}/package-records.jsonl"
mkdir -p "${PUBLISH_ROOT}" "${PACKAGE_ROOT}"
: > "${RECORDS_FILE}"

compute_sha256() {
  sha256sum "$1" | awk '{print $1}'
}

publish_rid() {
  local rid="$1"
  local output_dir="${PUBLISH_ROOT}/${rid}"
  mkdir -p "${output_dir}"
  echo "Publishing self-contained ${rid}..."
  dotnet publish "${PROJECT_FILE}" \
    --configuration Release \
    --runtime "${rid}" \
    --self-contained true \
    -p:PublishSingleFile=true \
    -p:IncludeNativeLibrariesForSelfExtract=true \
    -p:PublishTrimmed=false \
    -p:DebugType=None \
    -p:ElvernAllowedOrigin="${ELVERN_BACKEND_ORIGIN}" \
    --source "${NUGET_SOURCE}" \
    --output "${output_dir}"
  local executable="Elvern.VlcOpener"
  [[ "${rid}" == win-* ]] && executable="Elvern.VlcOpener.exe"
  [[ -f "${output_dir}/${executable}" ]] || { echo "Publish for ${rid} did not create ${executable}." >&2; exit 1; }
  [[ ! -f "${output_dir}/Elvern.VlcOpener.dll" ]] || { echo "Publish for ${rid} unexpectedly produced a loose framework DLL." >&2; exit 1; }
}

copy_payloads() {
  local private_dir="$1"
  shift
  local rid executable
  for rid in "$@"; do
    executable="Elvern.VlcOpener"
    [[ "${rid}" == win-* ]] && executable="Elvern.VlcOpener.exe"
    mkdir -p "${private_dir}/payloads/${rid}"
    cp "${PUBLISH_ROOT}/${rid}/${executable}" "${private_dir}/payloads/${rid}/${executable}"
    chmod 755 "${private_dir}/payloads/${rid}/${executable}" 2>/dev/null || true
  done
}

write_inner_manifest() {
  local manifest_path="$1"
  local package_target="$2"
  local private_dir="$3"
  shift 3
  python3 - "${manifest_path}" "${HELPER_VERSION}" "${package_target}" "${private_dir}" "$@" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
version = sys.argv[2]
package_target = sys.argv[3]
private_dir = pathlib.Path(sys.argv[4])
rids = sys.argv[5:]
payloads = []
for rid in rids:
    executable = "Elvern.VlcOpener.exe" if rid.startswith("win-") else "Elvern.VlcOpener"
    relative = pathlib.PurePosixPath("payloads") / rid / executable
    path = private_dir / pathlib.Path(relative)
    data = path.read_bytes()
    payloads.append({
        "runtime_id": rid,
        "relative_path": relative.as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "executable_name": executable,
    })
manifest = {
    "schema_version": "desktop-helper-installer-manifest-v1",
    "helper_version": version,
    "target_framework": "net10.0",
    "runtime_family": "10.0",
    "deployment_mode": "self_contained",
    "external_runtime_required": False,
    "package_target": package_target,
    "payloads": payloads,
}
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY
}

write_package_readme() {
  local path="$1"
  local detail="$2"
  cp "${COMMON_README}" "${path}"
  {
    echo
    echo "Package version: ${HELPER_VERSION}"
    echo "${detail}"
    echo "The helper is bound to: ${ELVERN_BACKEND_ORIGIN}"
  } >> "${path}"
}

register_package() {
  local package_target="$1"
  local platform="$2"
  local filename="$3"
  local package_root_name="$4"
  local installer_entrypoint="$5"
  local minimum_os_version="$6"
  shift 6
  local artifact_path="${PACKAGE_ROOT}/${filename}"
  local inner_manifest="${PACKAGE_ROOT}/${package_root_name}/.elvern/manifest.json"
  python3 - "${RECORDS_FILE}" "${package_target}" "${platform}" "${filename}" "${package_root_name}" "${installer_entrypoint}" "${minimum_os_version}" "${artifact_path}" "${inner_manifest}" "${GENERATED_AT_UTC}" "$@" <<'PY'
import hashlib
import json
import pathlib
import sys

records_path = pathlib.Path(sys.argv[1])
package_target, platform, filename, package_root, installer, minimum_os = sys.argv[2:8]
artifact = pathlib.Path(sys.argv[8])
inner_manifest = pathlib.Path(sys.argv[9])
generated_at = sys.argv[10]
rids = sys.argv[11:]
artifact_data = artifact.read_bytes()
manifest_data = inner_manifest.read_bytes()
record = {
    "package_target": package_target,
    "platform": platform,
    "artifact_kind": "zip",
    "filename": filename,
    "relative_path": filename,
    "package_root": package_root,
    "installer_entrypoint": installer,
    "supported_runtime_ids": rids,
    "external_runtime_required": False,
    "size_bytes": len(artifact_data),
    "sha256": hashlib.sha256(artifact_data).hexdigest(),
    "installer_manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
    "generated_at_utc": generated_at,
}
if minimum_os:
    record["minimum_os_version"] = minimum_os
with records_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, separators=(",", ":")) + "\n")
PY
  echo "Package: ${filename}"
  echo "  target: ${package_target}"
  echo "  RIDs: $*"
  echo "  compressed size: $(wc -c < "${artifact_path}" | tr -d '[:space:]') bytes"
  echo "  SHA-256: $(compute_sha256 "${artifact_path}")"
}

zip_package() {
  local package_root_name="$1"
  local filename="$2"
  (cd "${PACKAGE_ROOT}" && zip -qry "${filename}" "${package_root_name}")
}

build_windows() {
  local rid
  for rid in "${WINDOWS_RIDS[@]}"; do publish_rid "${rid}"; done
  local root_name="Elvern VLC Opener Windows Installer"
  local root="${PACKAGE_ROOT}/${root_name}"
  local private="${root}/.elvern"
  local filename="${PACKAGE_NAME_PREFIX}-${HELPER_VERSION}-windows-x64.zip"
  mkdir -p "${private}/uninstall"
  cp "${PACKAGING_DIR}/windows/Install-ElvernVlcOpener.cmd" "${root}/"
  cp "${PACKAGING_DIR}/windows/Install-ElvernVlcOpener.ps1" "${private}/"
  cp "${PACKAGING_DIR}/windows/Uninstall-ElvernVlcOpener.ps1" "${private}/uninstall/"
  write_package_readme "${root}/README.txt" "Includes the self-contained Windows x64 helper."
  copy_payloads "${private}" "${WINDOWS_RIDS[@]}"
  write_inner_manifest "${private}/manifest.json" "windows-x64" "${private}" "${WINDOWS_RIDS[@]}"
  zip_package "${root_name}" "${filename}"
  register_package "windows-x64" "windows" "${filename}" "${root_name}" "Install-ElvernVlcOpener.cmd" "" "${WINDOWS_RIDS[@]}"
}

build_macos() {
  local rid
  for rid in "${MACOS_RIDS[@]}"; do publish_rid "${rid}"; done
  local root_name="Elvern VLC Opener Installer"
  local root="${PACKAGE_ROOT}/${root_name}"
  local private="${root}/.elvern"
  local filename="${PACKAGE_NAME_PREFIX}-${HELPER_VERSION}-macos-dual-arch.zip"
  mkdir -p "${private}/bridge" "${private}/uninstall" "${private}/lib"
  cp "${PACKAGING_DIR}/macos/Install-ElvernVlcOpener.command" "${root}/"
  chmod 755 "${root}/Install-ElvernVlcOpener.command"
  cp "${PACKAGING_DIR}/macos/ElvernVlcOpener.applescript" "${private}/bridge/"
  cp "${PACKAGING_DIR}/macos/run-helper.sh.template" "${private}/bridge/"
  cp "${PACKAGING_DIR}/macos/Uninstall-ElvernVlcOpener.command" "${private}/uninstall/"
  cp "${SELECTORS}" "${private}/lib/"
  write_package_readme "${root}/README.txt" "Includes Apple Silicon and Intel payloads. The installer selects locally and requires macOS ${MACOS_MINIMUM_VERSION} or newer."
  copy_payloads "${private}" "${MACOS_RIDS[@]}"
  write_inner_manifest "${private}/manifest.json" "macos-dual-arch" "${private}" "${MACOS_RIDS[@]}"
  zip_package "${root_name}" "${filename}"
  register_package "macos-dual-arch" "mac" "${filename}" "${root_name}" "Install-ElvernVlcOpener.command" "${MACOS_MINIMUM_VERSION}" "${MACOS_RIDS[@]}"
}

build_linux() {
  local rid
  for rid in "${LINUX_RIDS[@]}"; do publish_rid "${rid}"; done
  local root_name="Elvern VLC Opener Linux Installer"
  local root="${PACKAGE_ROOT}/${root_name}"
  local private="${root}/.elvern"
  local filename="${PACKAGE_NAME_PREFIX}-${HELPER_VERSION}-linux-universal.zip"
  mkdir -p "${private}/uninstall" "${private}/lib"
  cp "${PACKAGING_DIR}/linux/Install-ElvernVlcOpener.sh" "${root}/"
  chmod 755 "${root}/Install-ElvernVlcOpener.sh"
  cp "${PACKAGING_DIR}/linux/Uninstall-ElvernVlcOpener.sh" "${private}/uninstall/"
  cp "${SELECTORS}" "${private}/lib/"
  write_package_readme "${root}/README.txt" "Includes x64 and ARM64 payloads for glibc and musl. The installer selects locally. Flatpak VLC is not supported in this release."
  copy_payloads "${private}" "${LINUX_RIDS[@]}"
  write_inner_manifest "${private}/manifest.json" "linux-universal" "${private}" "${LINUX_RIDS[@]}"
  zip_package "${root_name}" "${filename}"
  register_package "linux-universal" "linux" "${filename}" "${root_name}" "Install-ElvernVlcOpener.sh" "" "${LINUX_RIDS[@]}"
}

for target in "${TARGETS[@]}"; do
  case "${target}" in
    windows) build_windows ;;
    macos) build_macos ;;
    linux) build_linux ;;
  esac
done

python3 - "${RECORDS_FILE}" "${PACKAGE_ROOT}/release-manifest.json" "${HELPER_VERSION}" "${HELPER_CHANNEL}" "${TARGET_FRAMEWORK}" "${RUNTIME_FAMILY}" "${GENERATED_AT_UTC}" <<'PY'
import json
import pathlib
import sys

records_path, output_path = map(pathlib.Path, sys.argv[1:3])
records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
manifest = {
    "schema_version": "desktop-helper-release-manifest-v2",
    "helper_version": sys.argv[3],
    "channel": sys.argv[4],
    "target_framework": sys.argv[5],
    "runtime_family": sys.argv[6],
    "deployment_mode": "self_contained",
    "generated_at_utc": sys.argv[7],
    "packages": records,
}
output_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

# Only publish finished ZIPs, then replace the manifest after every requested RID succeeds.
find "${PACKAGE_ROOT}" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
for path in "${PACKAGE_ROOT}"/*.zip; do
  name="$(basename "${path}")"
  rm -f "${PACKAGES_DIR:?}/${name}"
  mv "${path}" "${PACKAGES_DIR}/${name}"
done
mv "${PACKAGE_ROOT}/release-manifest.json" "${PACKAGES_DIR}/release-manifest.json"

echo
echo "Desktop helper packages published successfully."
echo "Helper version: ${HELPER_VERSION}"
echo "Target framework: ${TARGET_FRAMEWORK}"
echo "Release manifest: ${PACKAGES_DIR}/release-manifest.json"
