#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_FILE="${PROJECT_DIR}/Elvern.VlcOpener.csproj"
PACKAGING_DIR="${PROJECT_DIR}/packaging"
METADATA_FILE="${PACKAGING_DIR}/helper-release.env"
ARTIFACTS_DIR="${PROJECT_DIR}/artifacts"
PUBLISH_DIR="${ARTIFACTS_DIR}/publish"
PACKAGES_DIR="${ARTIFACTS_DIR}/packages"
RELEASE_MANIFEST_FILE="${PACKAGES_DIR}/release-manifest.json"
COMMON_README="${PROJECT_DIR}/packaging/common/README.txt"
WINDOWS_INSTALLER_DIR="${PROJECT_DIR}/packaging/windows"
MACOS_INSTALLER_DIR="${PROJECT_DIR}/packaging/macos"
MACOS_BRIDGE_SOURCE="${MACOS_INSTALLER_DIR}/ElvernVlcOpener.applescript"
MACOS_RUNNER_TEMPLATE="${MACOS_INSTALLER_DIR}/run-helper.sh.template"

declare -a DEFAULT_RUNTIMES=("win-x64" "osx-arm64" "osx-x64")
declare -a RUNTIMES=()
PUBLISH_MODE="portable"
ZIP_OUTPUT=1
CUSTOM_RUNTIMES=0
MANIFEST_GENERATED_AT_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
declare -a MANIFEST_PACKAGE_RECORDS=()

if [[ ! -f "${METADATA_FILE}" ]]; then
  echo "Missing helper packaging metadata: ${METADATA_FILE}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${METADATA_FILE}"

for required_key in HELPER_VERSION HELPER_CHANNEL DOTNET_RUNTIME_MAJOR DOTNET_RUNTIME_DISPLAY PACKAGE_NAME_PREFIX; do
  if [[ -z "${!required_key:-}" ]]; then
    echo "Missing ${required_key} in ${METADATA_FILE}" >&2
    exit 1
  fi
done

usage() {
  cat <<'EOF'
Usage: ./scripts/publish-bundles.sh [options]

Build client-installable packages for Elvern VLC Opener.

Default behavior:
  - portable/framework-dependent publish
  - no RID-specific runtime-pack restore
  - packages for win-x64, osx-arm64, osx-x64

Options:
  --runtime <rid>       Build only the given package runtime. Repeatable.
  --portable            Force the default portable/framework-dependent mode.
  --self-contained      Attempt RID-specific self-contained publish instead.
  --no-zip              Leave package directories only; skip zip archives.
  --help                Show this help.

Examples:
  ./scripts/publish-bundles.sh
  ./scripts/publish-bundles.sh --runtime osx-arm64
  ./scripts/publish-bundles.sh --runtime win-x64 --runtime osx-arm64
  ./scripts/publish-bundles.sh --runtime win-x64 --self-contained
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --runtime" >&2
        exit 1
      fi
      if [[ ${CUSTOM_RUNTIMES} -eq 0 ]]; then
        RUNTIMES=()
        CUSTOM_RUNTIMES=1
      fi
      RUNTIMES+=("$2")
      shift 2
      ;;
    --portable)
      PUBLISH_MODE="portable"
      shift
      ;;
    --self-contained)
      PUBLISH_MODE="self-contained"
      shift
      ;;
    --no-zip)
      ZIP_OUTPUT=0
      shift
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

if [[ ${#RUNTIMES[@]} -eq 0 ]]; then
  RUNTIMES=("${DEFAULT_RUNTIMES[@]}")
fi

if [[ ! -f "${PROJECT_FILE}" ]]; then
  echo "Missing project file: ${PROJECT_FILE}" >&2
  exit 1
fi

VERSION="${HELPER_VERSION}"

mkdir -p "${PUBLISH_DIR}" "${PACKAGES_DIR}"

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "${value}"
}

resolve_platform_family() {
  local runtime="$1"
  case "${runtime}" in
    win-*)
      printf 'windows'
      ;;
    osx-*)
      printf 'macos'
      ;;
    linux-*)
      printf 'linux'
      ;;
    *)
      printf 'unknown'
      ;;
  esac
}

compute_sha256() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file_path}" | awk '{print $1}'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${file_path}" | awk '{print $1}'
    return
  fi
  echo "Missing sha256 tool (sha256sum or shasum)." >&2
  exit 1
}

register_release_package() {
  local runtime="$1"
  local artifact_path="$2"
  local package_name="$3"
  local artifact_kind="$4"
  local generated_at_utc="$5"
  local filename
  local relative_path
  local size_bytes
  local sha256
  local platform_family

  if [[ ! -f "${artifact_path}" ]]; then
    return
  fi

  filename="$(basename "${artifact_path}")"
  relative_path="${artifact_path#${PACKAGES_DIR}/}"
  size_bytes="$(wc -c < "${artifact_path}" | tr -d '[:space:]')"
  sha256="$(compute_sha256 "${artifact_path}")"
  platform_family="$(resolve_platform_family "${runtime}")"

  MANIFEST_PACKAGE_RECORDS+=("    {
      \"runtime\": \"$(json_escape "${runtime}")\",
      \"platform_family\": \"$(json_escape "${platform_family}")\",
      \"artifact_kind\": \"$(json_escape "${artifact_kind}")\",
      \"package_name\": \"$(json_escape "${package_name}")\",
      \"filename\": \"$(json_escape "${filename}")\",
      \"relative_path\": \"$(json_escape "${relative_path}")\",
      \"size_bytes\": ${size_bytes},
      \"sha256\": \"$(json_escape "${sha256}")\",
      \"generated_at_utc\": \"$(json_escape "${generated_at_utc}")\"
    }")
}

write_release_manifest() {
  {
    printf '{\n'
    printf '  "helper_version": "%s",\n' "$(json_escape "${HELPER_VERSION}")"
    printf '  "channel": "%s",\n' "$(json_escape "${HELPER_CHANNEL}")"
    printf '  "dotnet_runtime_major": "%s",\n' "$(json_escape "${DOTNET_RUNTIME_MAJOR}")"
    printf '  "dotnet_runtime_display": "%s",\n' "$(json_escape "${DOTNET_RUNTIME_DISPLAY}")"
    printf '  "package_name_prefix": "%s",\n' "$(json_escape "${PACKAGE_NAME_PREFIX}")"
    printf '  "publish_mode": "%s",\n' "$(json_escape "${PUBLISH_MODE}")"
    printf '  "generated_at_utc": "%s",\n' "$(json_escape "${MANIFEST_GENERATED_AT_UTC}")"
    printf '  "packages": ['
    if [[ ${#MANIFEST_PACKAGE_RECORDS[@]} -gt 0 ]]; then
      printf '\n'
      local index
      local last_index=$(( ${#MANIFEST_PACKAGE_RECORDS[@]} - 1 ))
      for index in "${!MANIFEST_PACKAGE_RECORDS[@]}"; do
        printf '%s' "${MANIFEST_PACKAGE_RECORDS[${index}]}"
        if [[ ${index} -lt ${last_index} ]]; then
          printf ',\n'
        else
          printf '\n'
        fi
      done
    fi
    printf '  ]\n'
    printf '}\n'
  } > "${RELEASE_MANIFEST_FILE}"
}

prepare_portable_publish() {
  local output_dir="${PUBLISH_DIR}/portable"
  rm -rf "${output_dir}"
  mkdir -p "${output_dir}"
  echo "Publishing portable/framework-dependent helper..." >&2
  dotnet restore "${PROJECT_FILE}" >&2
  dotnet publish "${PROJECT_FILE}" \
    -c Release \
    --no-restore \
    --self-contained false \
    -p:UseAppHost=false \
    -p:PublishSingleFile=false \
    -p:PublishTrimmed=false \
    -o "${output_dir}" >&2
  echo "${output_dir}"
}

prepare_self_contained_publish() {
  local runtime="$1"
  local output_dir="${PUBLISH_DIR}/${runtime}"
  rm -rf "${output_dir}"
  mkdir -p "${output_dir}"
  echo "Publishing self-contained helper for ${runtime}..." >&2
  dotnet publish "${PROJECT_FILE}" \
    -c Release \
    -r "${runtime}" \
    --self-contained true \
    -p:PublishSingleFile=true \
    -p:IncludeNativeLibrariesForSelfExtract=true \
    -p:PublishTrimmed=false \
    -o "${output_dir}" >&2
  echo "${output_dir}"
}

write_package_readme() {
  local package_dir="$1"
  local runtime="$2"
  local mode="$3"
  local readme_path="${package_dir}/README.txt"

  cp "${COMMON_README}" "${readme_path}"
  {
    echo
    echo "Package details:"
    echo "- Runtime target label: ${runtime}"
    echo "- Packaging mode: ${mode}"
    if [[ "${mode}" == "portable" ]]; then
      echo "- This package is framework-dependent."
      echo "- Install the ${DOTNET_RUNTIME_DISPLAY} on the client machine before first use."
    else
      echo "- This package is self-contained for ${runtime}."
    fi
  } >> "${readme_path}"
}

build_windows_package() {
  local runtime="$1"
  local source_publish_dir="$2"
  local mode="$3"
  local package_name="${PACKAGE_NAME_PREFIX}-${VERSION}-${runtime}"
  local package_dir="${PACKAGES_DIR}/${package_name}"
  local artifact_generated_at_utc=""

  rm -rf "${package_dir}"
  mkdir -p "${package_dir}/app"
  cp -R "${source_publish_dir}/." "${package_dir}/app/"
  cp "${WINDOWS_INSTALLER_DIR}/Install-ElvernVlcOpener.ps1" "${package_dir}/"
  cp "${WINDOWS_INSTALLER_DIR}/Install-ElvernVlcOpener.cmd" "${package_dir}/"
  cp "${WINDOWS_INSTALLER_DIR}/Uninstall-ElvernVlcOpener.ps1" "${package_dir}/"
  cp "${WINDOWS_INSTALLER_DIR}/Uninstall-ElvernVlcOpener.cmd" "${package_dir}/"
  write_package_readme "${package_dir}" "${runtime}" "${mode}"

  if [[ ${ZIP_OUTPUT} -eq 1 ]] && command -v zip >/dev/null 2>&1; then
    rm -f "${PACKAGES_DIR}/${package_name}.zip"
    (cd "${PACKAGES_DIR}" && zip -qry "${package_name}.zip" "${package_name}")
    artifact_generated_at_utc="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    register_release_package "${runtime}" "${PACKAGES_DIR}/${package_name}.zip" "${package_name}" "zip" "${artifact_generated_at_utc}"
  fi
}

build_macos_package() {
  local runtime="$1"
  local source_publish_dir="$2"
  local mode="$3"
  local package_name="${PACKAGE_NAME_PREFIX}-${VERSION}-${runtime}"
  local package_dir="${PACKAGES_DIR}/${package_name}"
  local artifact_generated_at_utc=""
  local app_dir="${package_dir}/Elvern VLC Opener.app"
  local contents_dir="${app_dir}/Contents"
  local macos_dir="${contents_dir}/MacOS"
  local resources_dir="${contents_dir}/Resources"
  local app_payload_dir="${resources_dir}/app"
  local launcher_path="${macos_dir}/Elvern VLC Opener"
  local plist_path="${contents_dir}/Info.plist"

  rm -rf "${package_dir}"
  mkdir -p "${macos_dir}" "${app_payload_dir}"
  cp -R "${source_publish_dir}/." "${app_payload_dir}/"
  sed "s/__VERSION__/${VERSION}/g" "${MACOS_INSTALLER_DIR}/Info.plist.template" > "${plist_path}"

  cat > "${launcher_path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTENTS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="${CONTENTS_DIR}/Resources/app"
SELF_CONTAINED_BIN="${APP_DIR}/Elvern.VlcOpener"
FRAMEWORK_DLL="${APP_DIR}/Elvern.VlcOpener.dll"
DOTNET_CMD=""
DOTNET_ROOT_DIR=""
declare -a CHECKED_DOTNET_PATHS=(
  "/usr/local/share/dotnet/dotnet"
  "/opt/homebrew/share/dotnet/dotnet"
  "/usr/local/bin/dotnet"
  "/opt/homebrew/bin/dotnet"
)

if [[ -x "${SELF_CONTAINED_BIN}" ]]; then
  exec "${SELF_CONTAINED_BIN}" "$@"
fi

if [[ -f "${FRAMEWORK_DLL}" ]]; then
  for candidate in "${CHECKED_DOTNET_PATHS[@]}"; do
    if [[ -x "${candidate}" ]]; then
      DOTNET_CMD="${candidate}"
      case "${candidate}" in
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

  if [[ -z "${DOTNET_CMD}" ]] && command -v dotnet >/dev/null 2>&1; then
    DOTNET_CMD="$(command -v dotnet)"
    case "${DOTNET_CMD}" in
      */share/dotnet/dotnet)
        DOTNET_ROOT_DIR="$(dirname "${DOTNET_CMD}")"
        ;;
      */bin/dotnet)
        DOTNET_ROOT_DIR="$(cd "$(dirname "${DOTNET_CMD}")/.." && pwd)"
        ;;
      *)
        DOTNET_ROOT_DIR="$(dirname "${DOTNET_CMD}")"
        ;;
    esac
  fi

  if [[ -z "${DOTNET_CMD}" ]]; then
    CHECKED_SUMMARY="$(printf '%s\n' "${CHECKED_DOTNET_PATHS[@]}")"
    if command -v osascript >/dev/null 2>&1; then
      osascript -e 'display alert "__DOTNET_RUNTIME_ALERT_TITLE__" message "Elvern VLC Opener could not find dotnet. Checked /usr/local/share/dotnet/dotnet, /opt/homebrew/share/dotnet/dotnet, /usr/local/bin/dotnet, /opt/homebrew/bin/dotnet, then PATH."' >/dev/null 2>&1 || true
    fi
    echo "Elvern VLC Opener could not find dotnet." >&2
    echo "Checked these paths:" >&2
    echo "${CHECKED_SUMMARY}" >&2
    echo "Checked PATH lookup: dotnet" >&2
    exit 1
  fi

  if [[ -n "${DOTNET_ROOT_DIR}" ]]; then
    export DOTNET_ROOT="${DOTNET_ROOT_DIR}"
  fi

  exec "${DOTNET_CMD}" "${FRAMEWORK_DLL}" "$@"
fi

echo "Missing Elvern VLC Opener payload." >&2
exit 1
EOF
  sed -i \
    -e "s/__DOTNET_RUNTIME_ALERT_TITLE__/.NET ${DOTNET_RUNTIME_MAJOR} Runtime Required/g" \
    "${launcher_path}"
  chmod +x "${launcher_path}"
  cp "${MACOS_INSTALLER_DIR}/Install-ElvernVlcOpener.command" "${package_dir}/"
  cp "${MACOS_INSTALLER_DIR}/Uninstall-ElvernVlcOpener.command" "${package_dir}/"
  cp "${MACOS_BRIDGE_SOURCE}" "${package_dir}/"
  cp "${MACOS_RUNNER_TEMPLATE}" "${package_dir}/"
  write_package_readme "${package_dir}" "${runtime}" "${mode}"

  if [[ ${ZIP_OUTPUT} -eq 1 ]] && command -v zip >/dev/null 2>&1; then
    rm -f "${PACKAGES_DIR}/${package_name}.zip"
    (cd "${PACKAGES_DIR}" && zip -qry "${package_name}.zip" "${package_name}")
    artifact_generated_at_utc="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    register_release_package "${runtime}" "${PACKAGES_DIR}/${package_name}.zip" "${package_name}" "zip" "${artifact_generated_at_utc}"
  fi
}

if [[ "${PUBLISH_MODE}" == "portable" ]]; then
  PORTABLE_PUBLISH_DIR="$(prepare_portable_publish)"
  for runtime in "${RUNTIMES[@]}"; do
    case "${runtime}" in
      win-*)
        build_windows_package "${runtime}" "${PORTABLE_PUBLISH_DIR}" "portable"
        ;;
      osx-*)
        build_macos_package "${runtime}" "${PORTABLE_PUBLISH_DIR}" "portable"
        ;;
      *)
        echo "Unsupported package runtime: ${runtime}" >&2
        exit 1
        ;;
    esac
  done
else
  echo "Self-contained publish remains available, but it may fail on the DGX host if RID runtime packs are unavailable."
  for runtime in "${RUNTIMES[@]}"; do
    SC_PUBLISH_DIR="$(prepare_self_contained_publish "${runtime}")"
    case "${runtime}" in
      win-*)
        build_windows_package "${runtime}" "${SC_PUBLISH_DIR}" "self-contained"
        ;;
      osx-*)
        build_macos_package "${runtime}" "${SC_PUBLISH_DIR}" "self-contained"
        ;;
      *)
        echo "Unsupported package runtime: ${runtime}" >&2
        exit 1
        ;;
    esac
  done
fi

write_release_manifest

echo
echo "Done."
echo "Publish output: ${PUBLISH_DIR}"
echo "Package output: ${PACKAGES_DIR}"
