#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_SOURCE_DIR="${PROJECT_ROOT}/clients/desktop-vlc-opener/artifacts/packages"

cd "$PROJECT_ROOT"

if [[ ! -f deploy/env/elvern.env ]]; then
  echo "Missing deploy/env/elvern.env" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Run scripts/bootstrap.sh first." >&2
  exit 1
fi

set -a
. deploy/env/elvern.env
set +a

. .venv/bin/activate

declare -a sources=()
if [[ "$#" -gt 0 ]]; then
  sources=("$@")
else
  if [[ ! -d "$DEFAULT_SOURCE_DIR" ]]; then
    echo "Missing helper package directory: $DEFAULT_SOURCE_DIR" >&2
    exit 1
  fi

  while IFS= read -r path; do
    sources+=("$path")
  done < <(find "$DEFAULT_SOURCE_DIR" -maxdepth 1 \( -type f -name 'elvern-vlc-opener-*.zip' -o -type d -name 'elvern-vlc-opener-*' \) | sort)
fi

if [[ "${#sources[@]}" -eq 0 ]]; then
  echo "No helper release artifacts found to import." >&2
  exit 1
fi

python -m backend.app.cli import-helper-releases "${sources[@]}"
