#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PROJECT_ROOT"

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt

cd frontend
npm install
npm run build

cd "$PROJECT_ROOT"

if [[ ! -f deploy/env/elvern.env ]]; then
  cp deploy/env/.env.example deploy/env/elvern.env
  echo "Created deploy/env/elvern.env from deploy/env/.env.example"
fi

echo
echo "Bootstrap complete."
echo "Next steps:"
echo "  1) For the launcher-first Ubuntu workflow, prefer:"
echo "     ./scripts/setup-ubuntu.sh"
echo "  2) Edit deploy/env/elvern.env"
echo "  3) Generate a password hash with:"
echo "     .venv/bin/python -m backend.app.cli hash-password \"your-password\""
echo "  4) Rebuild the frontend after frontend changes with:"
echo "     (cd frontend && npm run build)"
