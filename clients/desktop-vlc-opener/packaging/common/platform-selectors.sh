#!/bin/sh

elvern_path_has_control_characters() {
  elvern_path_value=$1
  elvern_tab=$(printf '\t')
  elvern_cr=$(printf '\r')
  elvern_lf='
'
  case "${elvern_path_value}" in
    *"${elvern_tab}"*|*"${elvern_cr}"*|*"${elvern_lf}"*) return 0 ;;
  esac
  elvern_printable_path=$(LC_ALL=C printf '%s' "${elvern_path_value}" | tr -d '\001-\010\013\014\016-\037\177')
  [ "${elvern_printable_path}" != "${elvern_path_value}" ]
}

elvern_safe_absolute_path_value() {
  elvern_path_value=${1:-}
  [ -n "${elvern_path_value}" ] || return 1
  case "${elvern_path_value}" in
    /*) ;;
    *) return 1 ;;
  esac
  elvern_path_has_control_characters "${elvern_path_value}" && return 1
  case "/${elvern_path_value#/}/" in
    */../*|*/./*) return 1 ;;
  esac
  return 0
}

select_macos_runtime() {
  selector_translated="${1:-0}"
  selector_machine="${2:-}"
  if [ "${selector_translated}" = "1" ]; then
    printf '%s\n' "osx-arm64"
    return 0
  fi
  case "${selector_machine}" in
    arm64)
      printf '%s\n' "osx-arm64"
      ;;
    x86_64)
      printf '%s\n' "osx-x64"
      ;;
    *)
      echo "Unsupported macOS CPU architecture: ${selector_machine:-unknown}" >&2
      return 1
      ;;
  esac
}

select_linux_runtime() {
  selector_machine="${1:-}"
  selector_libc_family="${2:-}"
  selector_arch=""
  case "${selector_machine}" in
    x86_64|amd64)
      selector_arch="x64"
      ;;
    aarch64|arm64)
      selector_arch="arm64"
      ;;
    *)
      echo "Unsupported Linux CPU architecture: ${selector_machine:-unknown}" >&2
      return 1
      ;;
  esac
  case "${selector_libc_family}" in
    glibc)
      printf 'linux-%s\n' "${selector_arch}"
      ;;
    musl)
      printf 'linux-musl-%s\n' "${selector_arch}"
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
  selector_ldd_output=""
  if command -v ldd >/dev/null 2>&1; then
    selector_ldd_output="$(ldd --version 2>&1 || :)"
    selector_ldd_output="$(printf '%s' "${selector_ldd_output}" | tr '[:upper:]' '[:lower:]')"
    case "${selector_ldd_output}" in
      *musl*)
        printf '%s\n' "musl"
        return 0
        ;;
      *glibc*|*"gnu libc"*)
        printf '%s\n' "glibc"
        return 0
        ;;
    esac
  fi
  for selector_loader in /lib/ld-musl-*.so.1 /usr/lib/ld-musl-*.so.1; do
    if [ -e "${selector_loader}" ]; then
      printf '%s\n' "musl"
      return 0
    fi
  done
  echo "Could not determine whether this Linux system uses glibc or musl." >&2
  return 1
}
