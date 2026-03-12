#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/.run/genedu"
PID_DIR="${RUN_DIR}/pids"
TAGS="${TAGS:-orthanc,viewer,backend}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]
Options:
  --tags LIST   Comma-separated: orthanc,viewer,backend (default: ${TAGS})
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tags) TAGS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

has_tag() {
  [[ ",${TAGS}," == *",$1,"* ]]
}

stop_by_pidfile() {
  local name="$1"
  local pattern="${2:-}"
  local pid_file="${PID_DIR}/${name}.pid"
  if [[ ! -f "${pid_file}" ]]; then
    echo "[genedu] ${name} pidfile not found"
    if [[ -n "${pattern}" ]]; then
      echo "[genedu] trying pattern stop for ${name}: ${pattern}"
      pkill -TERM -f "${pattern}" 2>/dev/null || true
      sleep 1
      pkill -KILL -f "${pattern}" 2>/dev/null || true
    fi
    return 0
  fi

  local pid
  pid="$(cat "${pid_file}" || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    echo "[genedu] stopping ${name} pid=${pid}"
    local pgid
    pgid="$(ps -o pgid= -p "${pid}" 2>/dev/null | tr -d '[:space:]' || true)"
    [[ -n "${pgid}" ]] && kill -TERM -"${pgid}" 2>/dev/null || true
    pkill -TERM -P "${pid}" 2>/dev/null || true
    kill "${pid}" || true
    sleep 1
    if kill -0 "${pid}" 2>/dev/null; then
      echo "[genedu] force killing ${name} pid=${pid}"
      [[ -n "${pgid}" ]] && kill -KILL -"${pgid}" 2>/dev/null || true
      pkill -KILL -P "${pid}" 2>/dev/null || true
      kill -9 "${pid}" || true
    fi
  else
    echo "[genedu] ${name} already stopped"
  fi
  if [[ -n "${pattern}" ]]; then
    pkill -TERM -f "${pattern}" 2>/dev/null || true
    sleep 1
    pkill -KILL -f "${pattern}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
}

if has_tag backend; then
  stop_by_pidfile "backend" "uvicorn main:app"
fi

if has_tag viewer; then
  stop_by_pidfile "viewer" "webpack serve|lerna run dev:orthanc|yarn dev:orthanc"
fi

if has_tag orthanc; then
  echo "[genedu] stopping Orthanc"
  (cd "${ROOT_DIR}/Viewer" && yarn orthanc:down) || true
fi

echo "[genedu] done. tags=${TAGS}"
