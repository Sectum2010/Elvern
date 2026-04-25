#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
python -m backend.app.cli backup-inspect "$@"
