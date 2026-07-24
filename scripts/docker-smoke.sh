#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${PROJECT_ROOT}/tmp"
WORK_DIR="$(mktemp -d "${PROJECT_ROOT}/tmp/docker-smoke.XXXXXX")"
RUN_ID="$$-$(date -u +%Y%m%d%H%M%S)"
IMAGE="elvern-docker-smoke:${RUN_ID}"
CONTAINER="elvern-docker-smoke-${RUN_ID}"
LOG_FILE="${WORK_DIR}/container.log"

cleanup() {
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  if [[ "${WORK_DIR}" == "${PROJECT_ROOT}"/tmp/docker-smoke.* ]] \
      && docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    docker run --rm \
      --volume "${WORK_DIR}:/elvern-smoke-cleanup" \
      --entrypoint /bin/sh \
      "${IMAGE}" \
      -c 'find /elvern-smoke-cleanup -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +' \
      >/dev/null 2>&1 || true
  fi
  docker image rm -f "${IMAGE}" >/dev/null 2>&1 || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

for command in docker curl; do
  command -v "${command}" >/dev/null 2>&1 || {
    echo "${command} is required for the Docker smoke test." >&2
    exit 1
  }
done

mkdir -p \
  "${WORK_DIR}/media" \
  "${WORK_DIR}/data" \
  "${WORK_DIR}/transcodes" \
  "${WORK_DIR}/helper_releases"

docker build --tag "${IMAGE}" "${PROJECT_ROOT}"
docker run --rm --entrypoint python "${IMAGE}" \
  -c "import backend.app.main; from elvern_shared.desktop_helper_package_contract import PACKAGE_NAME_PREFIX; print(PACKAGE_NAME_PREFIX)"

docker run --detach \
  --name "${CONTAINER}" \
  --publish 127.0.0.1::8000 \
  --publish 127.0.0.1::4173 \
  --volume "${WORK_DIR}/media:/smoke/media:ro" \
  --volume "${WORK_DIR}/data:/smoke/data" \
  --volume "${WORK_DIR}/transcodes:/smoke/transcodes" \
  --volume "${WORK_DIR}/helper_releases:/data/helper_releases" \
  --env ELVERN_MEDIA_ROOT=/smoke/media \
  --env ELVERN_DB_PATH=/smoke/data/elvern.db \
  --env ELVERN_TRANSCODE_DIR=/smoke/transcodes \
  --env ELVERN_HELPER_RELEASES_DIR=/data/helper_releases \
  --env ELVERN_BIND_HOST=0.0.0.0 \
  --env ELVERN_FRONTEND_HOST=0.0.0.0 \
  --env ELVERN_ADMIN_BOOTSTRAP_PASSWORD=synthetic-docker-smoke-password \
  --env ELVERN_SESSION_SECRET=synthetic-docker-smoke-session-secret-32chars \
  --env ELVERN_SCAN_ON_STARTUP=false \
  --env ELVERN_COOKIE_SECURE=false \
  --env ELVERN_PUBLIC_APP_ORIGIN=http://127.0.0.1:4173 \
  --env ELVERN_BACKEND_ORIGIN=http://127.0.0.1:8000 \
  "${IMAGE}" >/dev/null

backend_port="$(docker port "${CONTAINER}" 8000/tcp | awk -F: 'NR == 1 { print $NF }')"
frontend_port="$(docker port "${CONTAINER}" 4173/tcp | awk -F: 'NR == 1 { print $NF }')"
[[ "${backend_port}" =~ ^[0-9]+$ && "${frontend_port}" =~ ^[0-9]+$ ]] || {
  echo "Docker did not allocate smoke-test ports." >&2
  exit 1
}

healthy=0
for _attempt in $(seq 1 60); do
  if ! docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null \
    | grep -qx true; then
    break
  fi
  if curl --fail --silent \
      "http://127.0.0.1:${backend_port}/health" >/dev/null \
    && curl --fail --silent \
      "http://127.0.0.1:${frontend_port}/_elvern/frontend-health" >/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ ${healthy} -ne 1 ]]; then
  docker logs --tail 100 "${CONTAINER}" >"${LOG_FILE}" 2>&1 || true
  echo "Docker smoke health checks failed. Last container log lines:" >&2
  tail -100 "${LOG_FILE}" >&2
  exit 1
fi

[[ -z "$(find "${WORK_DIR}/helper_releases" -mindepth 1 -print -quit)" ]] || {
  echo "The empty Helper release mount was unexpectedly modified." >&2
  exit 1
}

echo "Docker smoke passed: shared import, Backend /health, and frontend health."
