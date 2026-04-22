#!/usr/bin/env bash
set -euo pipefail

: "${ELVERN_BIND_HOST:=0.0.0.0}"
: "${ELVERN_PORT:=8000}"
: "${ELVERN_FRONTEND_HOST:=0.0.0.0}"
: "${ELVERN_FRONTEND_PORT:=4173}"

if [[ -n "${ELVERN_DB_PATH:-}" ]]; then
  mkdir -p "$(dirname "${ELVERN_DB_PATH}")"
fi

if [[ -n "${ELVERN_TRANSCODE_DIR:-}" ]]; then
  mkdir -p "${ELVERN_TRANSCODE_DIR}"
fi

if [[ -n "${ELVERN_HELPER_RELEASES_DIR:-}" ]]; then
  mkdir -p "${ELVERN_HELPER_RELEASES_DIR}"
fi

uvicorn backend.app.main:app --host "${ELVERN_BIND_HOST}" --port "${ELVERN_PORT}" &
backend_pid=$!

node frontend/server.mjs &
frontend_pid=$!

shutdown() {
  kill "${backend_pid}" "${frontend_pid}" 2>/dev/null || true
}

trap shutdown TERM INT

wait -n "${backend_pid}" "${frontend_pid}"
exit_code=$?

shutdown
wait "${backend_pid}" "${frontend_pid}" 2>/dev/null || true

exit "${exit_code}"
