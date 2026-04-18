#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Elvern VLC Opener.app"
DEST_APP="${HOME}/Applications/${APP_NAME}"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

if [[ -d "${DEST_APP}" ]]; then
  if [[ -x "${LSREGISTER}" ]]; then
    "${LSREGISTER}" -u "${DEST_APP}" >/dev/null 2>&1 || true
  fi
  rm -rf "${DEST_APP}"
  echo "Removed ${DEST_APP}"
else
  echo "${DEST_APP} is not installed."
fi
