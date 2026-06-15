#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  echo "Usage: $0 [--fresh]" >&2
}

FRESH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh)
      FRESH=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

TEMP_ROOT=""
HIDDEN_FRONTEND_DIST=""

restore_frontend_dist() {
  if [[ -n "$HIDDEN_FRONTEND_DIST" && -e "$HIDDEN_FRONTEND_DIST" ]]; then
    rm -rf "$ROOT_DIR/frontend/dist"
    mv "$HIDDEN_FRONTEND_DIST" "$ROOT_DIR/frontend/dist"
    HIDDEN_FRONTEND_DIST=""
  fi
}

cleanup() {
  local status=$?
  restore_frontend_dist
  if [[ -n "$TEMP_ROOT" ]]; then
    rm -rf "$TEMP_ROOT"
  fi
  return "$status"
}
trap cleanup EXIT

run() {
  printf '\n==> %s\n' "$*"
  "$@"
}

check_node_version() {
  local required_version="22.12.0"
  if ! command -v node >/dev/null 2>&1; then
    echo "Node.js is required for frontend CI. Install Node.js >= ${required_version}." >&2
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required for frontend CI. Install Node.js/npm >= ${required_version}." >&2
    exit 1
  fi
  local current_version
  current_version="$(node -p "process.versions.node")"
  if ! node -e '
const current = process.versions.node.split(".").map(Number);
const required = process.argv[1].split(".").map(Number);
for (let i = 0; i < required.length; i += 1) {
  if ((current[i] || 0) > required[i]) process.exit(0);
  if ((current[i] || 0) < required[i]) process.exit(1);
}
' "$required_version"; then
    echo "Node.js ${current_version} is below Elvern's frontend CI baseline. Install Node.js >= ${required_version}." >&2
    exit 1
  fi
  printf '\n==> Node.js %s satisfies frontend CI baseline >= %s\n' "$current_version" "$required_version"
}

check_node_version

if [[ "$FRESH" == "1" ]]; then
  TEMP_ROOT="$(mktemp -d)"
  BASE_PYTHON="${PYTHON:-python3}"
  run "$BASE_PYTHON" -m venv "$TEMP_ROOT/venv"
  PYTHON_BIN="$TEMP_ROOT/venv/bin/python"
else
  if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
  elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    PYTHON_BIN="python3"
  fi
fi

python_script_dir="$("$PYTHON_BIN" - <<'PY'
import sysconfig
print(sysconfig.get_path("scripts"))
PY
)"

PIP_AUDIT_BIN="${PIP_AUDIT_BIN:-$python_script_dir/pip-audit}"
BANDIT_BIN="${BANDIT_BIN:-$python_script_dir/bandit}"

hide_frontend_dist_for_backend_pytest() {
  if [[ "$FRESH" == "1" && -e "$ROOT_DIR/frontend/dist" ]]; then
    HIDDEN_FRONTEND_DIST="$TEMP_ROOT/frontend-dist-hidden"
    rm -rf "$HIDDEN_FRONTEND_DIST"
    mv "$ROOT_DIR/frontend/dist" "$HIDDEN_FRONTEND_DIST"
    printf '\n==> Fresh mode: hiding existing frontend/dist during backend pytest\n'
  fi
}

run "$PYTHON_BIN" -m pip install --upgrade pip
run "$PYTHON_BIN" -m pip install -r backend/requirements-test.txt

hide_frontend_dist_for_backend_pytest
run "$PYTHON_BIN" -m pytest
restore_frontend_dist

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
