#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/.run/gn03"
PID_DIR="${RUN_DIR}/pids"
SERVICES="${SERVICES:-gateway,ct,xray}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]
Options:
  --services LIST   Comma-separated: gateway,ct,xray (default: ${SERVICES})
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --services) SERVICES="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

has_service() {
  [[ ",${SERVICES}," == *",$1,"* ]]
}

stop_by_pidfile() {
  local name="$1"
  local pattern="${2:-}"
  local pid_file="${PID_DIR}/${name}.pid"
  if [[ ! -f "${pid_file}" ]]; then
    echo "[gn03] ${name} pidfile not found"
    if [[ -n "${pattern}" ]]; then
      echo "[gn03] trying pattern stop for ${name}: ${pattern}"
      pkill -TERM -f "${pattern}" 2>/dev/null || true
      sleep 1
      pkill -KILL -f "${pattern}" 2>/dev/null || true
    fi
    return 0
  fi

  local pid
  pid="$(cat "${pid_file}" || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    echo "[gn03] stopping ${name} pid=${pid}"
    local pgid
    pgid="$(ps -o pgid= -p "${pid}" 2>/dev/null | tr -d '[:space:]' || true)"
    [[ -n "${pgid}" ]] && kill -TERM -"${pgid}" 2>/dev/null || true
    pkill -TERM -P "${pid}" 2>/dev/null || true
    kill "${pid}" || true
    sleep 1
    if kill -0 "${pid}" 2>/dev/null; then
      echo "[gn03] force killing ${name} pid=${pid}"
      [[ -n "${pgid}" ]] && kill -KILL -"${pgid}" 2>/dev/null || true
      pkill -KILL -P "${pid}" 2>/dev/null || true
      kill -9 "${pid}" || true
    fi
  else
    echo "[gn03] ${name} already stopped"
  fi
  if [[ -n "${pattern}" ]]; then
    pkill -TERM -f "${pattern}" 2>/dev/null || true
    sleep 1
    pkill -KILL -f "${pattern}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
}

if has_service gateway; then
  stop_by_pidfile "gateway" "uvicorn remote_services.gateway.main:app"
fi

if has_service ct; then
  stop_by_pidfile "ct_worker" "uvicorn remote_services.workers.ct_worker:app"
fi

if has_service xray; then
  stop_by_pidfile "xray_worker" "uvicorn remote_services.workers.xray_worker:app"
fi

echo "[gn03] done. services=${SERVICES}"
