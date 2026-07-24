#!/usr/bin/env bash
set -euo pipefail
umask 022

APP_NAME="Elvern VLC Opener.app"
DEST_DIR="${HOME}/Applications"
DEST_APP="${DEST_DIR}/${APP_NAME}"
LOCK_DIR="${DEST_DIR}/.elvern-vlc-opener-install.lock"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
PLISTBUDDY="/usr/libexec/PlistBuddy"
INFO_PLIST="${DEST_APP}/Contents/Info.plist"
STATE_PLIST="${DEST_APP}/Contents/Resources/install-state.plist"
BACKUP_APP=""
LOCK_HELD=0
UNREGISTERED=0
APP_MOVED=0
UNINSTALL_COMMITTED=0
ROLLBACK_FAILED=0
TRANSACTION_NONCE="$$-$(date -u +%Y%m%dT%H%M%SZ)"

fail() {
  echo "Elvern VLC Opener was not removed: $1" >&2
  exit 1
}

inject_failure() {
  local point="$1"
  if [[ "${ELVERN_UNINSTALL_TEST_MODE:-0}" == "1" \
    && "${ELVERN_UNINSTALL_TEST_FAIL_AT:-}" == "${point}" ]]; then
    fail "injected failure at ${point}."
  fi
}

cleanup() {
  local status=$?
  trap - EXIT
  if [[ ${UNINSTALL_COMMITTED} -eq 0 ]]; then
    if [[ ${APP_MOVED} -eq 1 ]]; then
      if [[ -e "${DEST_APP}" || ! -d "${BACKUP_APP}" ]]; then
        ROLLBACK_FAILED=1
      elif ! mv "${BACKUP_APP}" "${DEST_APP}"; then
        ROLLBACK_FAILED=1
      else
        APP_MOVED=0
      fi
    fi
    if [[ ${UNREGISTERED} -eq 1 ]]; then
      if ! "${LSREGISTER}" -f "${DEST_APP}" >/dev/null 2>&1; then
        ROLLBACK_FAILED=1
      fi
    fi
    if [[ -d "${DEST_APP}" ]]; then
      [[ -x "${DEST_APP}/Contents/Resources/app/Elvern.VlcOpener" ]] \
        || ROLLBACK_FAILED=1
      codesign --verify --deep --strict "${DEST_APP}" >/dev/null 2>&1 \
        || ROLLBACK_FAILED=1
    fi
  fi
  if [[ ${LOCK_HELD} -eq 1 ]]; then
    rm -f "${LOCK_DIR}/owner"
    rmdir "${LOCK_DIR}" 2>/dev/null || :
  fi
  if [[ ${ROLLBACK_FAILED} -ne 0 ]]; then
    echo "Elvern VLC Opener uninstall rollback could not be verified." >&2
    [[ -z "${BACKUP_APP}" ]] \
      || echo "Preserved App backup: ${BACKUP_APP}" >&2
    exit 1
  fi
  exit "${status}"
}
trap cleanup EXIT

mkdir -p "${DEST_DIR}"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  fail "another Helper install or uninstall may be running. Remove ${LOCK_DIR} manually only after confirming no transaction is active."
fi
LOCK_HELD=1
printf 'pid=%s\nstarted_at=%s\ntransaction_nonce=%s\n' \
  "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${TRANSACTION_NONCE}" \
  > "${LOCK_DIR}/owner"
chmod 644 "${LOCK_DIR}/owner"

if [[ ! -e "${DEST_APP}" ]]; then
  echo "${DEST_APP} is not installed."
  UNINSTALL_COMMITTED=1
  exit 0
fi
[[ -d "${DEST_APP}" && ! -L "${DEST_APP}" ]] \
  || fail "the installed Elvern App is not a safe directory."
[[ -f "${INFO_PLIST}" && ! -L "${INFO_PLIST}" ]] \
  || fail "the installed Elvern App identity is missing."
BUNDLE_ID="$("${PLISTBUDDY}" -c "Print :CFBundleIdentifier" "${INFO_PLIST}" 2>/dev/null || :)"
[[ "${BUNDLE_ID}" == "local.elvern.vlcopener" ]] \
  || fail "the installed App does not belong to Elvern."
if [[ -e "${STATE_PLIST}" ]]; then
  [[ -f "${STATE_PLIST}" && ! -L "${STATE_PLIST}" ]] \
    || fail "the installed ownership state is unsafe."
  STATE_SCHEMA="$("${PLISTBUDDY}" -c "Print :schema_version" "${STATE_PLIST}" 2>/dev/null || :)"
  STATE_HELPER_VERSION="$("${PLISTBUDDY}" -c "Print :helper_version" "${STATE_PLIST}" 2>/dev/null || :)"
  STATE_PRODUCT="$("${PLISTBUDDY}" -c "Print :product_id" "${STATE_PLIST}" 2>/dev/null || :)"
  STATE_PACKAGE_TARGET="$("${PLISTBUDDY}" -c "Print :package_target" "${STATE_PLIST}" 2>/dev/null || :)"
  STATE_NONCE="$("${PLISTBUDDY}" -c "Print :transaction_nonce" "${STATE_PLIST}" 2>/dev/null || :)"
  [[ "${STATE_SCHEMA}" == "elvern-desktop-helper-install-state-v1" \
    && "${STATE_PRODUCT}" == "local.elvern.vlcopener" \
    && "${STATE_PACKAGE_TARGET}" == "macos-dual-arch" \
    && "${STATE_HELPER_VERSION}" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ \
    && "${STATE_NONCE}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || fail "the installed ownership state does not belong to Elvern."
fi
[[ -x "${LSREGISTER}" ]] \
  || fail "Launch Services registration support is unavailable."

inject_failure "unregister"
"${LSREGISTER}" -u "${DEST_APP}" >/dev/null 2>&1 \
  || fail "Launch Services could not unregister the Elvern Helper App."
UNREGISTERED=1

BACKUP_APP="$(mktemp -d "${DEST_DIR}/.elvern-vlc-opener-uninstall-backup.XXXXXX")"
rmdir "${BACKUP_APP}" \
  || fail "the App backup path could not be prepared."
BACKUP_APP="${BACKUP_APP}.app"
inject_failure "backup_move"
mv "${DEST_APP}" "${BACKUP_APP}" \
  || fail "the Elvern Helper App could not be staged for removal."
APP_MOVED=1
[[ ! -e "${DEST_APP}" ]] \
  || fail "the active Elvern Helper App path is still present."
inject_failure "final_verification"
UNINSTALL_COMMITTED=1
if [[ "${ELVERN_UNINSTALL_TEST_MODE:-0}" == "1" \
  && "${ELVERN_UNINSTALL_TEST_FAIL_AT:-}" == "backup_delete" ]]; then
  echo "Warning: the committed App backup could not be removed: ${BACKUP_APP}" >&2
elif ! rm -rf "${BACKUP_APP}"; then
  echo "Warning: the committed App backup could not be removed: ${BACKUP_APP}" >&2
else
  APP_MOVED=0
  BACKUP_APP=""
fi
echo "Removed ${DEST_APP}"
