#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/elvern-common.sh"

show_status_window() {
  local output
  output="$("${SCRIPT_DIR}/elvern-status.sh")"

  if [[ -n "${DISPLAY:-}" ]] && command -v zenity >/dev/null 2>&1; then
    local tmp_file
    tmp_file="$(mktemp)"
    printf '%s\n' "${output}" >"${tmp_file}"
    zenity --text-info --title="Elvern Status" --width=720 --height=420 --filename="${tmp_file}" >/dev/null 2>&1 || true
    rm -f "${tmp_file}"
    return
  fi

  printf '%s\n' "${output}"
}


show_logs_window() {
  local output
  output="$("${SCRIPT_DIR}/elvern-logs.sh" 80)"

  if [[ -n "${DISPLAY:-}" ]] && command -v zenity >/dev/null 2>&1; then
    local tmp_file
    tmp_file="$(mktemp)"
    printf '%s\n' "${output}" >"${tmp_file}"
    zenity --text-info --title="Elvern Logs" --width=900 --height=600 --filename="${tmp_file}" >/dev/null 2>&1 || true
    rm -f "${tmp_file}"
    return
  fi

  printf '%s\n' "${output}"
}


open_elvern() {
  "${SCRIPT_DIR}/elvern-start.sh" --open-browser
}


run_menu() {
  if [[ -n "${DISPLAY:-}" ]] && command -v zenity >/dev/null 2>&1; then
    local choice
    choice="$(
      zenity --list \
        --title="Elvern Control" \
        --text="Choose an action" \
        --column="Action" \
        "Start Elvern" \
        "Open Elvern" \
        "Restart Elvern" \
        "Stop Elvern" \
        "Show Status" \
        "View Recent Logs" \
        --height=360 \
        --width=420
    )"

    case "${choice}" in
      "Start Elvern"|"Open Elvern")
        open_elvern
        ;;
      "Restart Elvern")
        "${SCRIPT_DIR}/elvern-restart.sh" --open-browser
        ;;
      "Stop Elvern")
        "${SCRIPT_DIR}/elvern-stop.sh"
        ;;
      "Show Status")
        show_status_window
        ;;
      "View Recent Logs")
        show_logs_window
        ;;
      *)
        ;;
    esac
    return
  fi

  if [[ ! -t 0 && -n "${DISPLAY:-}" ]] && command -v x-terminal-emulator >/dev/null 2>&1; then
    exec x-terminal-emulator -e bash -lc "\"${SCRIPT_DIR}/elvern-control.sh\" --menu-shell; printf '\nPress Enter to close... '; read -r _"
  fi

  cat <<'EOF'
Elvern Control
1) Start Elvern
2) Open Elvern
3) Restart Elvern
4) Stop Elvern
5) Show status
6) View recent logs
q) Quit
EOF
  read -r -p "> " choice
  case "${choice}" in
    1|2)
      open_elvern
      ;;
    3)
      "${SCRIPT_DIR}/elvern-restart.sh" --open-browser
      ;;
    4)
      "${SCRIPT_DIR}/elvern-stop.sh"
      ;;
    5)
      show_status_window
      ;;
    6)
      show_logs_window
      ;;
    *)
      ;;
  esac
}


case "${1:-menu}" in
  start|open)
    exec "${SCRIPT_DIR}/elvern-start.sh" --open-browser
    ;;
  stop)
    exec "${SCRIPT_DIR}/elvern-stop.sh"
    ;;
  restart)
    exec "${SCRIPT_DIR}/elvern-restart.sh" --open-browser
    ;;
  status)
    exec "${SCRIPT_DIR}/elvern-status.sh"
    ;;
  logs)
    exec "${SCRIPT_DIR}/elvern-logs.sh"
    ;;
  menu|--menu|--menu-shell)
    run_menu
    ;;
  --help|-h)
    cat <<'EOF'
Usage: ./scripts/elvern-control.sh [start|stop|restart|status|logs|menu]

Runs the simple local Elvern control tool. With no argument it opens the menu.
EOF
    ;;
  *)
    elvern_log_message ERROR "Unknown action: $1"
    exit 1
    ;;
esac
