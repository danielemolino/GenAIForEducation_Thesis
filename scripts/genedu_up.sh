#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/.run/genedu"
LOG_DIR="${RUN_DIR}/logs"
PID_DIR="${RUN_DIR}/pids"
mkdir -p "${LOG_DIR}" "${PID_DIR}"

TAGS="${TAGS:-orthanc,viewer,backend}"
BACKEND_MODE="${BACKEND_MODE:-full}"
GENERATION_ENGINE="${GENERATION_ENGINE:-real}"
REMOTE_INFERENCE_URL="${REMOTE_INFERENCE_URL:-http://192.168.128.3:4450}"
GENEDU_PYTHON="${GENEDU_PYTHON:-${ROOT_DIR}/.venv/bin/python}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]
Options:
  --tags LIST              Comma-separated: orthanc,viewer,backend (default: ${TAGS})
  --backend-mode MODE      api-only|ct-only|xgem-only|full (default: ${BACKEND_MODE})
  --generation-engine ENG  real|asset (default: ${GENERATION_ENGINE})
  --remote-url URL         Remote inference URL (default: ${REMOTE_INFERENCE_URL})
  --python PATH            Python executable for backend (default: ${GENEDU_PYTHON})
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tags) TAGS="$2"; shift 2 ;;
    --backend-mode) BACKEND_MODE="$2"; shift 2 ;;
    --generation-engine) GENERATION_ENGINE="$2"; shift 2 ;;
    --remote-url) REMOTE_INFERENCE_URL="$2"; shift 2 ;;
    --python) GENEDU_PYTHON="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

has_tag() {
  [[ ",${TAGS}," == *",$1,"* ]]
}

start_bg() {
  local name="$1"
  local cmd="$2"
  local pid_file="${PID_DIR}/${name}.pid"
  local log_file="${LOG_DIR}/${name}.log"

  if [[ -f "${pid_file}" ]]; then
    local old_pid
    old_pid="$(cat "${pid_file}" || true)"
    if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
      echo "[genedu] ${name} already running (pid=${old_pid})"
      return 0
    fi
  fi

  echo "[genedu] starting ${name}"
  nohup bash -lc "${cmd}" >"${log_file}" 2>&1 &
  local new_pid=$!
  echo "${new_pid}" > "${pid_file}"
  echo "[genedu] ${name} started (pid=${new_pid}) log=${log_file}"
}

if has_tag orthanc; then
  echo "[genedu] ensuring Orthanc is up"
  (cd "${ROOT_DIR}/Viewer" && yarn orthanc:up)
fi

if has_tag viewer; then
  start_bg "viewer" "cd '${ROOT_DIR}/Viewer' && exec yarn dev:orthanc"
fi

if has_tag backend; then
  if [[ ! -x "${GENEDU_PYTHON}" ]]; then
    echo "[genedu] python not executable: ${GENEDU_PYTHON}"
    exit 1
  fi
  start_bg "backend" \
    "cd '${ROOT_DIR}/BackendModelli' && \
     BACKEND_MODE='${BACKEND_MODE}' GENERATION_ENGINE='${GENERATION_ENGINE}' REMOTE_INFERENCE_URL='${REMOTE_INFERENCE_URL}' \
     exec '${GENEDU_PYTHON}' -m uvicorn main:app --host 0.0.0.0 --port 8000 --access-log"
fi

echo "[genedu] done. tags=${TAGS}"
