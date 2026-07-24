#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PROJECT_DIR}/../.." && pwd)"
PROJECT_FILE="${PROJECT_DIR}/Elvern.VlcOpener.csproj"
PROPS_FILE="${PROJECT_DIR}/Directory.Build.props"
PACKAGING_DIR="${PROJECT_DIR}/packaging"
METADATA_FILE="${PACKAGING_DIR}/helper-release.env"
ARTIFACTS_DIR="${PROJECT_DIR}/artifacts"
ACTIVE_DIR=""
ACTIVE_DIR_CLI=""
STAGING_ROOT="${ARTIFACTS_DIR}/staging"
COMMON_README="${PACKAGING_DIR}/common/README.txt"
SELECTORS="${PACKAGING_DIR}/common/platform-selectors.sh"
ORIGIN_NORMALIZER="${SCRIPT_DIR}/normalize-origin.py"
PACKAGE_VALIDATOR="${SCRIPT_DIR}/validate-package.py"
PACKAGE_CONTRACT="${REPO_ROOT}/elvern_shared/desktop_helper_package_contract.py"
NUGET_SOURCE="${ELVERN_DOTNET_NUGET_SOURCE:-https://api.nuget.org/v3/index.json}"
GENERATED_AT_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
PUBLISH_MODE="self-contained"
ACTIVATE=0
ALLOW_PARTIAL_ACTIVATE=0
REPLACE_CORRUPT_ACTIVE_MANIFEST=0
TARGETS_EXPLICIT=0
ACTIVATION_LOCK_DIR=""
ACTIVE_TEMP_FILES=()
ACTIVE_CREATED_ARTIFACTS=()
ACTIVATION_COMMITTED=0
declare -A OLD_ACTIVE_REFERENCES=()
declare -a TARGETS=("windows" "macos" "linux")
declare -a WINDOWS_RIDS=()
declare -a MACOS_RIDS=()
declare -a LINUX_RIDS=()

usage() {
  cat <<'EOF'
Usage: ./scripts/publish-bundles.sh [options]

Options:
  --platform windows|macos|linux  Build one selected platform (repeatable).
  --windows | --macos | --linux  Shorthand platform selectors.
  --activate                     Verify all standard packages, then atomically
                                 publish their immutable artifacts and manifest.
  --active-dir PATH              Absolute Backend runtime release directory.
                                 Required by --activate unless
                                 ELVERN_HELPER_RELEASES_DIR is set.
  --allow-partial-activate       Dangerous recovery option. Allows --activate
                                 with an incomplete platform set.
  --replace-corrupt-active-manifest
                                 Dangerous recovery option. With --activate,
                                 preserve and replace an invalid active manifest.

Without --activate, every build remains under artifacts/staging and cannot
affect the active release manifest. Standard builds are self-contained,
single-file, non-trimmed .NET 10 packages.
EOF
}

select_target() {
  if [[ ${TARGETS_EXPLICIT} -eq 0 ]]; then
    TARGETS=()
    TARGETS_EXPLICIT=1
  fi
  TARGETS+=("$1")
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      [[ $# -ge 2 ]] || { echo "Missing value for --platform." >&2; exit 1; }
      case "$2" in
        windows|macos|linux) select_target "$2" ;;
        *) echo "Unsupported platform target: $2" >&2; exit 1 ;;
      esac
      shift 2
      ;;
    --windows) select_target "windows"; shift ;;
    --macos) select_target "macos"; shift ;;
    --linux) select_target "linux"; shift ;;
    --activate) ACTIVATE=1; shift ;;
    --active-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for --active-dir." >&2; exit 1; }
      ACTIVE_DIR_CLI="$2"
      shift 2
      ;;
    --allow-partial-activate) ALLOW_PARTIAL_ACTIVATE=1; shift ;;
    --replace-corrupt-active-manifest)
      REPLACE_CORRUPT_ACTIVE_MANIFEST=1
      shift
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done
[[ ${#TARGETS[@]} -gt 0 ]] || { echo "At least one platform target is required." >&2; exit 1; }
if [[ ${REPLACE_CORRUPT_ACTIVE_MANIFEST} -eq 1 && ${ACTIVATE} -ne 1 ]]; then
  echo "--replace-corrupt-active-manifest is only valid with --activate." >&2
  exit 1
fi
mapfile -t TARGETS < <(printf '%s\n' "${TARGETS[@]}" | awk '!seen[$0]++')

if [[ ${ACTIVATE} -eq 1 ]]; then
  ACTIVE_DIR="${ACTIVE_DIR_CLI:-${ELVERN_HELPER_RELEASES_DIR:-}}"
  [[ -n "${ACTIVE_DIR}" ]] || {
    echo "--activate requires --active-dir or ELVERN_HELPER_RELEASES_DIR." >&2
    exit 1
  }
  ACTIVE_DIR="$(
    python3 - "${ACTIVE_DIR}" "${STAGING_ROOT}" "${PACKAGING_DIR}" <<'PY'
import os
import pathlib
import sys

raw_active, raw_staging, raw_packaging = sys.argv[1:]
if any(ord(character) < 32 or ord(character) == 127 for character in raw_active):
    raise SystemExit("Active release directory contains unsafe control characters.")
active = pathlib.Path(raw_active)
if not active.is_absolute():
    raise SystemExit("Active release directory must be an absolute path.")
active = pathlib.Path(os.path.abspath(active))
staging = pathlib.Path(os.path.abspath(raw_staging))
packaging = pathlib.Path(os.path.abspath(raw_packaging))
if active == staging or staging in active.parents:
    raise SystemExit("Active release directory cannot be inside staging.")
if active == packaging or packaging in active.parents:
    raise SystemExit("Active release directory cannot be inside package sources.")
cursor = pathlib.Path(active.anchor)
for component in active.parts[1:]:
    cursor /= component
    if cursor.is_symlink():
        raise SystemExit("Active release directory cannot contain a symlink.")
print(active)
PY
  )"
fi

for required_file in "${PROJECT_FILE}" "${PROPS_FILE}" "${METADATA_FILE}" "${COMMON_README}" "${SELECTORS}" "${ORIGIN_NORMALIZER}" "${PACKAGE_VALIDATOR}" "${PACKAGE_CONTRACT}"; do
  [[ -f "${required_file}" ]] || { echo "Missing required file: ${required_file}" >&2; exit 1; }
done
command -v python3 >/dev/null 2>&1 || { echo "python3 is required on the release build host." >&2; exit 1; }
command -v zip >/dev/null 2>&1 || { echo "zip is required to create helper packages." >&2; exit 1; }
command -v dotnet >/dev/null 2>&1 || { echo ".NET SDK 10 is required to publish helper packages." >&2; exit 1; }
dotnet --list-sdks | awk '{print $1}' | grep -Eq '^10\.' || {
  echo ".NET SDK 10 is required to publish self-contained helper packages." >&2
  exit 1
}

# shellcheck disable=SC1090
source "${METADATA_FILE}"
for required_key in HELPER_CHANNEL MACOS_MINIMUM_VERSION; do
  [[ -n "${!required_key:-}" ]] || { echo "Missing ${required_key} in ${METADATA_FILE}." >&2; exit 1; }
done
PACKAGE_CONTRACT_JSON="$(
  PYTHONPATH="${REPO_ROOT}" python3 -m \
    elvern_shared.desktop_helper_package_contract --json
)"
PACKAGE_NAME_PREFIX="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["prefix"])' \
    <<<"${PACKAGE_CONTRACT_JSON}"
)"
[[ -n "${PACKAGE_NAME_PREFIX}" ]] || {
  echo "Shared desktop helper package prefix is unavailable." >&2
  exit 1
}
mapfile -t WINDOWS_RIDS < <(
  python3 -c 'import json,sys; print(*json.load(sys.stdin)["packages"]["windows-x64"]["rids"], sep="\n")' \
    <<<"${PACKAGE_CONTRACT_JSON}"
)
mapfile -t MACOS_RIDS < <(
  python3 -c 'import json,sys; print(*json.load(sys.stdin)["packages"]["macos-dual-arch"]["rids"], sep="\n")' \
    <<<"${PACKAGE_CONTRACT_JSON}"
)
mapfile -t LINUX_RIDS < <(
  python3 -c 'import json,sys; print(*json.load(sys.stdin)["packages"]["linux-universal"]["rids"], sep="\n")' \
    <<<"${PACKAGE_CONTRACT_JSON}"
)
[[ ${#WINDOWS_RIDS[@]} -gt 0 && ${#MACOS_RIDS[@]} -gt 0 && ${#LINUX_RIDS[@]} -gt 0 ]] || {
  echo "Shared desktop helper runtime contracts are unavailable." >&2
  exit 1
}

mapfile -t ORIGIN_PROPERTIES < <(
  python3 "${ORIGIN_NORMALIZER}" "${ELVERN_BACKEND_ORIGIN:-}"
)
[[ ${#ORIGIN_PROPERTIES[@]} -eq 2 ]] || { echo "Could not normalize ELVERN_BACKEND_ORIGIN." >&2; exit 1; }
ELVERN_BACKEND_ORIGIN="${ORIGIN_PROPERTIES[0]}"
BOUND_ORIGIN_SHA256="${ORIGIN_PROPERTIES[1]}"

mapfile -t BUILD_PROPERTIES < <(python3 - "${PROPS_FILE}" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
values = {child.tag: (child.text or "").strip() for group in root.findall("PropertyGroup") for child in group}
print(values.get("ElvernHelperVersion", ""))
print(values.get("TargetFramework", ""))
PY
)
HELPER_VERSION="${BUILD_PROPERTIES[0]:-}"
TARGET_FRAMEWORK="${BUILD_PROPERTIES[1]:-}"
[[ -n "${HELPER_VERSION}" && "${TARGET_FRAMEWORK}" == "net10.0" ]] || {
  echo "Directory.Build.props must define a helper version and TargetFramework net10.0." >&2
  exit 1
}
RUNTIME_FAMILY="10.0"
BUILD_ID="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(12))
PY
)"
STAGING_DIR="${STAGING_ROOT}/${BUILD_ID}"
WORK_DIR="${STAGING_DIR}/.work"
OUTPUT_DIR="${STAGING_DIR}/output"
PUBLISH_ROOT="${WORK_DIR}/publish"
PACKAGE_ROOT="${WORK_DIR}/packages"
RECORDS_FILE="${WORK_DIR}/package-records.jsonl"
mkdir -p "${PUBLISH_ROOT}" "${PACKAGE_ROOT}" "${OUTPUT_DIR}"
if [[ ${ACTIVATE} -eq 1 ]]; then
  mkdir -p "${ACTIVE_DIR}"
  [[ ! -L "${ACTIVE_DIR}" ]] || {
    echo "Active release directory cannot be a symlink." >&2
    exit 1
  }
fi
: > "${RECORDS_FILE}"
cleanup() {
  local status=$?
  local created active_path staged_path
  if [[ ${ACTIVATION_COMMITTED} -eq 0 ]]; then
    for created in "${ACTIVE_CREATED_ARTIFACTS[@]}"; do
      active_path="${ACTIVE_DIR}/${created}"
      staged_path="${FINAL_BUILD_DIR:-}/${created}"
      if [[ -n "${OLD_ACTIVE_REFERENCES[${created}]+x}" ]]; then
        continue
      fi
      if [[
        -f "${active_path}"
        && -f "${staged_path}"
        && "$(compute_sha256 "${active_path}")" == "$(compute_sha256 "${staged_path}")"
      ]]; then
        if [[
          "${ELVERN_PUBLISH_TEST_MODE:-0}" == "1"
          && "${ELVERN_PUBLISH_TEST_FAIL_AT:-}" == "orphan_cleanup"
        ]]; then
          echo "Could not remove transaction-created orphan artifact: ${active_path}" >&2
          continue
        fi
        if ! rm -f "${active_path}"; then
          echo "Could not remove transaction-created orphan artifact: ${active_path}" >&2
        fi
      elif [[ -e "${active_path}" ]]; then
        echo "Preserved an unproven active artifact after failed activation: ${active_path}" >&2
      fi
    done
  fi
  rm -rf "${WORK_DIR}"
  local active_temp
  for active_temp in "${ACTIVE_TEMP_FILES[@]}"; do
    [[ -n "${active_temp}" ]] && rm -f "${active_temp}"
  done
  if [[ -n "${ACTIVATION_LOCK_DIR}" ]]; then
    rm -f "${ACTIVATION_LOCK_DIR}/owner"
    rmdir "${ACTIVATION_LOCK_DIR}" 2>/dev/null || true
  fi
  return "${status}"
}
trap cleanup EXIT

compute_sha256() {
  sha256sum "$1" | awk '{print $1}'
}

fsync_directory() {
  python3 - "$1" <<'PY'
import os
import sys
descriptor = os.open(sys.argv[1], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

inject_activation_failure() {
  local point="$1"
  if [[ "${ELVERN_PUBLISH_TEST_MODE:-0}" == "1" && "${ELVERN_PUBLISH_TEST_FAIL_AT:-}" == "${point}" ]]; then
    echo "Injected activation failure at ${point}." >&2
    return 1
  fi
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

write_inner_manifests() {
  local private_dir="$1"
  local package_target="$2"
  shift 2
  python3 - "${private_dir}" "${HELPER_VERSION}" "${package_target}" "${BOUND_ORIGIN_SHA256}" "$@" <<'PY'
import hashlib
import json
import pathlib
import sys

def sha_size(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()

private_dir = pathlib.Path(sys.argv[1])
version, package_target, origin_hash = sys.argv[2:5]
rids = sys.argv[5:]
payloads = []
tsv_lines = [
    "meta\tschema_version\tdesktop-helper-installer-manifest-v2",
    f"meta\thelper_version\t{version}",
    "meta\ttarget_framework\tnet10.0",
    "meta\truntime_family\t10.0",
    "meta\tdeployment_mode\tself_contained",
    f"meta\tpackage_target\t{package_target}",
    f"meta\tbound_origin_sha256\t{origin_hash}",
]
for rid in rids:
    executable = "Elvern.VlcOpener.exe" if rid.startswith("win-") else "Elvern.VlcOpener"
    relative = pathlib.PurePosixPath("payloads") / rid / executable
    path = private_dir / pathlib.Path(relative)
    size, digest = sha_size(path)
    record = {
        "runtime_id": rid,
        "relative_path": relative.as_posix(),
        "sha256": digest,
        "size_bytes": size,
        "executable_name": executable,
    }
    payloads.append(record)
    tsv_lines.append(
        "\t".join((
            "payload",
            record["runtime_id"],
            record["relative_path"],
            record["sha256"],
            str(record["size_bytes"]),
            record["executable_name"],
        ))
    )
manifest = {
    "schema_version": "desktop-helper-installer-manifest-v2",
    "helper_version": version,
    "target_framework": "net10.0",
    "runtime_family": "10.0",
    "deployment_mode": "self_contained",
    "external_runtime_required": False,
    "package_target": package_target,
    "bound_origin_sha256": origin_hash,
    "payloads": payloads,
}
(private_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
(private_dir / "installer-manifest.tsv").write_text("\n".join(tsv_lines) + "\n", encoding="utf-8")
PY
}

write_tree_manifest() {
  local root="$1"
  python3 - "${root}" <<'PY'
import hashlib
import pathlib
import stat
import sys

def sha_size(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()

root = pathlib.Path(sys.argv[1])
tree_path = root / ".elvern" / "tree-manifest.tsv"
lines = ["path\tsize_bytes\tsha256\tfile_class"]
for path in sorted(root.rglob("*")):
    if path == tree_path:
        continue
    if path.is_symlink():
        raise SystemExit(f"Package tree contains an unsupported entry: {path.relative_to(root)}")
    if path.is_dir():
        continue
    if not path.is_file():
        raise SystemExit(f"Package tree contains an unsupported entry: {path.relative_to(root)}")
    relative = path.relative_to(root).as_posix()
    if relative.startswith("/") or ".." in pathlib.PurePosixPath(relative).parts or "\t" in relative:
        raise SystemExit("Package tree contains an unsafe path")
    size, digest = sha_size(path)
    mode = path.stat().st_mode
    file_class = "executable" if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) else "data"
    lines.append(f"{relative}\t{size}\t{digest}\t{file_class}")
tree_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
tree_path.chmod(0o644)
PY
}

normalize_package_modes() {
  local root="$1"
  find "${root}" -type d -exec chmod 0755 {} +
  find "${root}" -type f -exec chmod 0644 {} +
  if [[ -d "${root}/.elvern/payloads" ]]; then
    find "${root}/.elvern/payloads" -type f -exec chmod 0755 {} +
  fi
}

write_package_readme() {
  local path="$1"
  local detail="$2"
  cp "${COMMON_README}" "${path}"
  {
    echo
    echo "Package version: ${HELPER_VERSION}"
    echo "${detail}"
    echo "Runtime included. Integrity-verified and bound to this Elvern server origin by hash."
  } >> "${path}"
}

zip_package() {
  local package_root_name="$1"
  local target_slug="$2"
  local provisional="${PACKAGE_NAME_PREFIX}-${HELPER_VERSION}-${target_slug}.zip"
  (cd "${PACKAGE_ROOT}" && zip -qry "${provisional}" "${package_root_name}")
  local digest
  digest="$(compute_sha256 "${PACKAGE_ROOT}/${provisional}")"
  local immutable
  immutable="$(
    PYTHONPATH="${REPO_ROOT}" python3 "${PACKAGE_CONTRACT}" \
      "${HELPER_VERSION}" \
      "${target_slug}" \
      "${digest}"
  )"
  mv "${PACKAGE_ROOT}/${provisional}" "${OUTPUT_DIR}/${immutable}"
  printf '%s\n' "${immutable}"
}

register_package() {
  local package_target="$1"
  local platform="$2"
  local filename="$3"
  local package_root_name="$4"
  local installer_entrypoint="$5"
  local minimum_os_version="$6"
  shift 6
  local artifact_path="${OUTPUT_DIR}/${filename}"
  local inner_manifest="${PACKAGE_ROOT}/${package_root_name}/.elvern/manifest.json"
  local tree_manifest="${PACKAGE_ROOT}/${package_root_name}/.elvern/tree-manifest.tsv"
  python3 - "${RECORDS_FILE}" "${package_target}" "${platform}" "${filename}" "${package_root_name}" "${installer_entrypoint}" "${minimum_os_version}" "${artifact_path}" "${inner_manifest}" "${tree_manifest}" "${GENERATED_AT_UTC}" "${BOUND_ORIGIN_SHA256}" "$@" <<'PY'
import hashlib
import json
import pathlib
import sys

def sha_size(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()

records_path = pathlib.Path(sys.argv[1])
package_target, platform, filename, package_root, installer, minimum_os = sys.argv[2:8]
artifact, inner_manifest, tree_manifest = map(pathlib.Path, sys.argv[8:11])
generated_at, origin_hash = sys.argv[11:13]
rids = sys.argv[13:]
artifact_size, artifact_digest = sha_size(artifact)
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
    "size_bytes": artifact_size,
    "sha256": artifact_digest,
    "installer_manifest_sha256": sha_size(inner_manifest)[1],
    "installer_tree_manifest_path": ".elvern/tree-manifest.tsv",
    "installer_tree_manifest_sha256": sha_size(tree_manifest)[1],
    "bound_origin_sha256": origin_hash,
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

build_windows() {
  local rid
  for rid in "${WINDOWS_RIDS[@]}"; do publish_rid "${rid}"; done
  local root_name="Elvern VLC Opener Windows Installer"
  local root="${PACKAGE_ROOT}/${root_name}"
  local private="${root}/.elvern"
  mkdir -p "${private}/uninstall"
  cp "${PACKAGING_DIR}/windows/Install-ElvernVlcOpener.cmd" "${root}/"
  cp "${PACKAGING_DIR}/windows/Install-ElvernVlcOpener.ps1" "${private}/"
  cp "${PACKAGING_DIR}/windows/Uninstall-ElvernVlcOpener.ps1" "${private}/uninstall/"
  write_package_readme "${root}/README.txt" "Includes the self-contained Windows x64 helper."
  copy_payloads "${private}" "${WINDOWS_RIDS[@]}"
  write_inner_manifests "${private}" "windows-x64" "${WINDOWS_RIDS[@]}"
  normalize_package_modes "${root}"
  write_tree_manifest "${root}"
  local filename
  filename="$(zip_package "${root_name}" "windows-x64")"
  register_package "windows-x64" "windows" "${filename}" "${root_name}" "Install-ElvernVlcOpener.cmd" "" "${WINDOWS_RIDS[@]}"
}

build_macos() {
  local rid
  for rid in "${MACOS_RIDS[@]}"; do publish_rid "${rid}"; done
  local root_name="Elvern VLC Opener Installer"
  local root="${PACKAGE_ROOT}/${root_name}"
  local private="${root}/.elvern"
  mkdir -p "${private}/bridge" "${private}/uninstall" "${private}/lib"
  cp "${PACKAGING_DIR}/macos/Install-ElvernVlcOpener.command" "${root}/"
  chmod 755 "${root}/Install-ElvernVlcOpener.command"
  cp "${PACKAGING_DIR}/macos/ElvernVlcOpener.applescript" "${private}/bridge/"
  cp "${PACKAGING_DIR}/macos/run-helper.sh.template" "${private}/bridge/"
  cp "${PACKAGING_DIR}/macos/Uninstall-ElvernVlcOpener.command" "${private}/uninstall/"
  cp "${SELECTORS}" "${private}/lib/"
  write_package_readme "${root}/README.txt" "Includes Apple Silicon and Intel payloads. The installer selects locally and requires macOS ${MACOS_MINIMUM_VERSION} or newer."
  copy_payloads "${private}" "${MACOS_RIDS[@]}"
  write_inner_manifests "${private}" "macos-dual-arch" "${MACOS_RIDS[@]}"
  normalize_package_modes "${root}"
  chmod 0755 "${root}/Install-ElvernVlcOpener.command" \
    "${private}/uninstall/Uninstall-ElvernVlcOpener.command" \
    "${private}/lib/platform-selectors.sh"
  write_tree_manifest "${root}"
  local filename
  filename="$(zip_package "${root_name}" "macos-dual-arch")"
  register_package "macos-dual-arch" "mac" "${filename}" "${root_name}" "Install-ElvernVlcOpener.command" "${MACOS_MINIMUM_VERSION}" "${MACOS_RIDS[@]}"
}

build_linux() {
  local rid
  for rid in "${LINUX_RIDS[@]}"; do publish_rid "${rid}"; done
  local root_name="Elvern VLC Opener Linux Installer"
  local root="${PACKAGE_ROOT}/${root_name}"
  local private="${root}/.elvern"
  mkdir -p "${private}/uninstall" "${private}/lib"
  cp "${PACKAGING_DIR}/linux/Install-ElvernVlcOpener.sh" "${root}/"
  chmod 755 "${root}/Install-ElvernVlcOpener.sh"
  cp "${PACKAGING_DIR}/linux/Uninstall-ElvernVlcOpener.sh" "${private}/uninstall/"
  cp "${SELECTORS}" "${private}/lib/"
  write_package_readme "${root}/README.txt" "Includes x64 and ARM64 payloads for glibc and musl. The installer selects locally. Flatpak VLC is not supported."
  copy_payloads "${private}" "${LINUX_RIDS[@]}"
  write_inner_manifests "${private}" "linux-universal" "${LINUX_RIDS[@]}"
  normalize_package_modes "${root}"
  chmod 0755 "${root}/Install-ElvernVlcOpener.sh" \
    "${private}/uninstall/Uninstall-ElvernVlcOpener.sh" \
    "${private}/lib/platform-selectors.sh"
  write_tree_manifest "${root}"
  local filename
  filename="$(zip_package "${root_name}" "linux-universal")"
  register_package "linux-universal" "linux" "${filename}" "${root_name}" "Install-ElvernVlcOpener.sh" "" "${LINUX_RIDS[@]}"
}

for target in "${TARGETS[@]}"; do
  case "${target}" in
    windows) build_windows ;;
    macos) build_macos ;;
    linux) build_linux ;;
  esac
done

python3 - "${RECORDS_FILE}" "${OUTPUT_DIR}/release-manifest.json" "${HELPER_VERSION}" "${HELPER_CHANNEL}" "${TARGET_FRAMEWORK}" "${RUNTIME_FAMILY}" "${GENERATED_AT_UTC}" "${BOUND_ORIGIN_SHA256}" <<'PY'
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
    "bound_origin_sha256": sys.argv[8],
    "packages": records,
}
output_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

VALIDATOR_ARGS=(
  --manifest "${OUTPUT_DIR}/release-manifest.json"
  --artifacts-dir "${OUTPUT_DIR}"
  --package-name-prefix "${PACKAGE_NAME_PREFIX}"
  --expected-origin-sha256 "${BOUND_ORIGIN_SHA256}"
)
for target in "${TARGETS[@]}"; do
  case "${target}" in
    windows) VALIDATOR_ARGS+=(--expected-package-target "windows-x64") ;;
    macos) VALIDATOR_ARGS+=(--expected-package-target "macos-dual-arch") ;;
    linux) VALIDATOR_ARGS+=(--expected-package-target "linux-universal") ;;
  esac
done
python3 "${PACKAGE_VALIDATOR}" "${VALIDATOR_ARGS[@]}"

python3 - "${OUTPUT_DIR}/release-manifest.json" "${OUTPUT_DIR}/build-report.json" "${OUTPUT_DIR}/build-report.md" "${BUILD_ID}" <<'PY'
import json
import pathlib
import sys

manifest_path, json_path, markdown_path = map(pathlib.Path, sys.argv[1:4])
manifest = json.loads(manifest_path.read_text())
report = {
    "build_id": sys.argv[4],
    "status": "verified_staging",
    "helper_version": manifest["helper_version"],
    "target_framework": manifest["target_framework"],
    "deployment_mode": manifest["deployment_mode"],
    "bound_origin_sha256": manifest["bound_origin_sha256"],
    "packages": manifest["packages"],
}
json_path.write_text(json.dumps(report, indent=2) + "\n")
lines = [
    "# Desktop Helper Staging Build",
    "",
    f"- Build ID: `{report['build_id']}`",
    f"- Helper version: `{report['helper_version']}`",
    f"- Target framework: `{report['target_framework']}`",
    "- Deployment: self-contained",
    f"- Origin identity: `{report['bound_origin_sha256']}`",
    "",
    "## Packages",
]
for package in report["packages"]:
    lines.extend([
        "",
        f"- `{package['package_target']}`: `{package['filename']}`",
        f"  - RIDs: {', '.join(package['supported_runtime_ids'])}",
        f"  - Size: {package['size_bytes']} bytes",
        f"  - SHA-256: `{package['sha256']}`",
    ])
markdown_path.write_text("\n".join(lines) + "\n")
PY

FINAL_BUILD_DIR="${OUTPUT_DIR}"

activate_release() {
  if [[ ${#TARGETS[@]} -ne 3 && ${ALLOW_PARTIAL_ACTIVATE} -ne 1 ]]; then
    echo "Activation requires Windows, macOS, and Linux. Use --allow-partial-activate only for explicit emergency rollback work." >&2
    exit 1
  fi
  if [[ ${ALLOW_PARTIAL_ACTIVATE} -eq 1 ]]; then
    echo "WARNING: activating an incomplete desktop helper release set." >&2
  fi
  local lock_dir="${ACTIVE_DIR}/.activation.lock"
  if ! mkdir "${lock_dir}" 2>/dev/null; then
    echo "Another desktop helper activation is already running, or a stale lock remains at ${lock_dir}." >&2
    [[ -f "${lock_dir}/owner" ]] && sed -n '1,3p' "${lock_dir}/owner" >&2
    echo "Do not remove the lock until confirming the recorded process is no longer active." >&2
    exit 1
  fi
  ACTIVATION_LOCK_DIR="${lock_dir}"
  printf 'pid=%s\nstarted_at=%s\nbuild_id=%s\n' \
    "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${BUILD_ID}" > "${lock_dir}/owner"
  chmod 644 "${lock_dir}/owner"
  local active_manifest="${ACTIVE_DIR}/release-manifest.json"
  local active_manifest_valid=0
  if [[ -e "${active_manifest}" || -L "${active_manifest}" ]]; then
    if python3 "${PACKAGE_VALIDATOR}" \
      --manifest "${active_manifest}" \
      --artifacts-dir "${ACTIVE_DIR}" \
      --package-name-prefix "${PACKAGE_NAME_PREFIX}" \
      >/dev/null 2>&1; then
      active_manifest_valid=1
    elif [[ ${REPLACE_CORRUPT_ACTIVE_MANIFEST} -ne 1 ]]; then
      echo "Active desktop helper manifest is invalid; activation was not attempted." >&2
      exit 1
    else
      [[ -f "${active_manifest}" && ! -L "${active_manifest}" ]] || {
        echo "Active desktop helper manifest is unsafe and cannot be preserved for recovery." >&2
        exit 1
      }
      local corrupt_digest corrupt_backup
      corrupt_digest="$(compute_sha256 "${active_manifest}")"
      corrupt_backup="${ACTIVE_DIR}/release-manifest.corrupt-${corrupt_digest:0:12}.json"
      echo "WARNING: replacing an invalid active desktop helper manifest." >&2
      echo "The invalid authority will be preserved as ${corrupt_backup}." >&2
      if [[ -e "${corrupt_backup}" ]]; then
        [[ -f "${corrupt_backup}" && ! -L "${corrupt_backup}" ]] || {
          echo "Corrupt manifest backup path is unsafe: ${corrupt_backup}" >&2
          exit 1
        }
        [[ "$(compute_sha256 "${corrupt_backup}")" == "${corrupt_digest}" ]] || {
          echo "Corrupt manifest backup collision: ${corrupt_backup}" >&2
          exit 1
        }
      else
        cp "${active_manifest}" "${corrupt_backup}"
        chmod 0444 "${corrupt_backup}"
        python3 - "${corrupt_backup}" <<'PY'
import os
import sys
with open(sys.argv[1], "rb") as handle:
    os.fsync(handle.fileno())
PY
        fsync_directory "${ACTIVE_DIR}"
      fi
    fi
  fi
  if [[ ${active_manifest_valid} -eq 1 ]]; then
    while IFS= read -r old_filename; do
      [[ -n "${old_filename}" ]] && OLD_ACTIVE_REFERENCES["${old_filename}"]=1
    done < <(python3 - "${active_manifest}" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for package in payload.get("packages", []):
    filename = package.get("filename") if isinstance(package, dict) else None
    if (
        isinstance(filename, str)
        and filename
        and pathlib.PurePosixPath(filename).name == filename
    ):
        print(filename)
PY
)
  fi
  python3 "${PACKAGE_VALIDATOR}" "${VALIDATOR_ARGS[@]}"
  python3 - "${FINAL_BUILD_DIR}/release-manifest.json" "${BOUND_ORIGIN_SHA256}" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = sys.argv[2]
if manifest.get("bound_origin_sha256") != expected:
    raise SystemExit("Release manifest origin changed before activation")
if any(package.get("bound_origin_sha256") != expected for package in manifest.get("packages", [])):
    raise SystemExit("Package origin changed before activation")
PY
  local package
  for package in "${FINAL_BUILD_DIR}"/*.zip; do
    local name temp_path
    name="$(basename "${package}")"
    if [[ -e "${ACTIVE_DIR}/${name}" ]]; then
      [[ -f "${ACTIVE_DIR}/${name}" ]] || {
        echo "Active artifact path is not a regular file: ${name}" >&2
        exit 1
      }
      [[ "$(compute_sha256 "${ACTIVE_DIR}/${name}")" == "$(compute_sha256 "${package}")" ]] || {
        echo "Immutable active artifact collision: ${name}" >&2
        exit 1
      }
      chmod 0444 "${ACTIVE_DIR}/${name}"
      continue
    fi
    temp_path="${ACTIVE_DIR}/.${name}.new.${BUILD_ID}"
    ACTIVE_TEMP_FILES+=("${temp_path}")
    cp "${package}" "${temp_path}"
    chmod 0444 "${temp_path}"
    python3 - "${temp_path}" <<'PY'
import os
import sys
with open(sys.argv[1], "rb") as handle:
    os.fsync(handle.fileno())
PY
    mv "${temp_path}" "${ACTIVE_DIR}/${name}"
    ACTIVE_CREATED_ARTIFACTS+=("${name}")
    inject_activation_failure "artifact_copy"
    if [[
      "${ELVERN_PUBLISH_TEST_MODE:-0}" == "1"
      && "${ELVERN_PUBLISH_TEST_FAIL_AT:-}" == "orphan_cleanup"
    ]]; then
      echo "Injected activation failure before orphan cleanup." >&2
      return 1
    fi
  done
  fsync_directory "${ACTIVE_DIR}"
  local manifest_temp="${ACTIVE_DIR}/.release-manifest.json.new.${BUILD_ID}"
  ACTIVE_TEMP_FILES+=("${manifest_temp}")
  cp "${FINAL_BUILD_DIR}/release-manifest.json" "${manifest_temp}"
  chmod 0444 "${manifest_temp}"
  python3 - "${manifest_temp}" <<'PY'
import os
import sys
with open(sys.argv[1], "rb") as handle:
    os.fsync(handle.fileno())
PY
  inject_activation_failure "manifest_rename"
  mv "${manifest_temp}" "${ACTIVE_DIR}/release-manifest.json"
  ACTIVATION_COMMITTED=1
  fsync_directory "${ACTIVE_DIR}"
  rm -f "${lock_dir}/owner"
  rmdir "${lock_dir}"
  ACTIVATION_LOCK_DIR=""
  echo "Activated verified desktop helper release manifest."
}

if [[ ${ACTIVATE} -eq 1 ]]; then
  activate_release
else
  echo "Build verified and retained in staging history; active releases were not changed."
fi

echo
echo "Desktop helper package build completed."
echo "Build ID: ${BUILD_ID}"
echo "Helper version: ${HELPER_VERSION}"
echo "Target framework: ${TARGET_FRAMEWORK}"
echo "Verified build: ${FINAL_BUILD_DIR}"
echo "Staging directory: ${STAGING_DIR}"
echo "Selected active directory: ${ACTIVE_DIR:-not selected}"
echo "Activation performed: $([[ ${ACTIVATE} -eq 1 ]] && echo yes || echo no)"
