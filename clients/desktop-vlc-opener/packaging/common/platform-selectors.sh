#!/usr/bin/env bash

select_macos_runtime() {
  local translated="${1:-0}"
  local machine="${2:-}"
  if [[ "${translated}" == "1" ]]; then
    printf '%s\n' "osx-arm64"
    return 0
  fi
  case "${machine}" in
    arm64)
      printf '%s\n' "osx-arm64"
      ;;
    x86_64)
      printf '%s\n' "osx-x64"
      ;;
    *)
      echo "Unsupported macOS CPU architecture: ${machine:-unknown}" >&2
      return 1
      ;;
  esac
}

select_linux_runtime() {
  local machine="${1:-}"
  local libc_family="${2:-}"
  local arch=""
  case "${machine}" in
    x86_64|amd64)
      arch="x64"
      ;;
    aarch64|arm64)
      arch="arm64"
      ;;
    *)
      echo "Unsupported Linux CPU architecture: ${machine:-unknown}" >&2
      return 1
      ;;
  esac
  case "${libc_family}" in
    glibc)
      printf 'linux-%s\n' "${arch}"
      ;;
    musl)
      printf 'linux-musl-%s\n' "${arch}"
      ;;
    *)
      echo "Could not determine whether this Linux system uses glibc or musl." >&2
      return 1
      ;;
  esac
}

detect_linux_libc() {
  if command -v getconf >/dev/null 2>&1 && getconf GNU_LIBC_VERSION >/dev/null 2>&1; then
    printf '%s\n' "glibc"
    return 0
  fi
  local ldd_output=""
  if command -v ldd >/dev/null 2>&1; then
    ldd_output="$(ldd --version 2>&1 || true)"
    if [[ "${ldd_output,,}" == *musl* ]]; then
      printf '%s\n' "musl"
      return 0
    fi
    if [[ "${ldd_output,,}" == *glibc* || "${ldd_output,,}" == *"gnu libc"* ]]; then
      printf '%s\n' "glibc"
      return 0
    fi
  fi
  if compgen -G '/lib/ld-musl-*.so.1' >/dev/null || compgen -G '/usr/lib/ld-musl-*.so.1' >/dev/null; then
    printf '%s\n' "musl"
    return 0
  fi
  echo "Could not determine whether this Linux system uses glibc or musl." >&2
  return 1
}
