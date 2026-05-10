#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  PYTHON_BIN="python3"
fi

python_script_dir="$("$PYTHON_BIN" - <<'PY'
import sysconfig
print(sysconfig.get_path("scripts"))
PY
)"

PIP_AUDIT_BIN="${PIP_AUDIT_BIN:-$python_script_dir/pip-audit}"
BANDIT_BIN="${BANDIT_BIN:-$python_script_dir/bandit}"

run() {
  printf '\n==> %s\n' "$*"
  "$@"
}

run "$PYTHON_BIN" -m pip install --upgrade pip
run "$PYTHON_BIN" -m pip install -r backend/requirements-test.txt
run "$PYTHON_BIN" -m pytest
run dotnet build clients/desktop-vlc-opener/Elvern.VlcOpener.csproj --configuration Release

pushd frontend >/dev/null
run npm ci
run npm test
run npm run build
popd >/dev/null

run "$PYTHON_BIN" -m pip install pip-audit "bandit[toml]"
run "$PIP_AUDIT_BIN" -r backend/requirements.txt --strict
run "$PIP_AUDIT_BIN" -r backend/requirements-test.txt --strict
run "$BANDIT_BIN" -r backend/app -ll -ii --exclude backend/app/__pycache__

pushd frontend >/dev/null
run npm audit --audit-level=high
popd >/dev/null
