#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/.run/gn03"
LOG_DIR="${RUN_DIR}/logs"
PID_DIR="${RUN_DIR}/pids"
mkdir -p "${LOG_DIR}" "${PID_DIR}"

SERVICES="${SERVICES:-gateway,ct}"
GN03_PYTHON="${GN03_PYTHON:-python}"
CT_GPU="${CT_GPU:-1}"
XRAY_GPU="${XRAY_GPU:-0}"

CT_PORT="${CT_PORT:-8002}"
GATEWAY_PORT="${GATEWAY_PORT:-8001}"
XRAY_PORT="${XRAY_PORT:-8003}"

CT_WORKER_URL="${CT_WORKER_URL:-http://127.0.0.1:${CT_PORT}}"
XRAY_WORKER_URL="${XRAY_WORKER_URL:-http://127.0.0.1:8000}"
XRAY_HEALTH_PATH="${XRAY_HEALTH_PATH:-/healthz}"
XRAY_INFER_PATH="${XRAY_INFER_PATH:-/generate}"
XRAY_LEGACY_TASK="${XRAY_LEGACY_TASK:-T->F}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]
Options:
  --services LIST      Comma-separated: gateway,ct,xray (default: ${SERVICES})
  --python PATH        Python executable (default: ${GN03_PYTHON})
  --ct-gpu ID          GPU id for CT worker CUDA_VISIBLE_DEVICES (default: ${CT_GPU})
  --xray-gpu ID        GPU id for XRay worker CUDA_VISIBLE_DEVICES (default: ${XRAY_GPU})
  --ct-port PORT       CT worker port (default: ${CT_PORT})
  --gateway-port PORT  Gateway port (default: ${GATEWAY_PORT})
  --xray-port PORT     XRay worker port if xray service enabled (default: ${XRAY_PORT})
  --xray-url URL       External XRay worker URL (default: ${XRAY_WORKER_URL})
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --services) SERVICES="$2"; shift 2 ;;
    --python) GN03_PYTHON="$2"; shift 2 ;;
    --ct-gpu) CT_GPU="$2"; shift 2 ;;
    --xray-gpu) XRAY_GPU="$2"; shift 2 ;;
    --ct-port) CT_PORT="$2"; shift 2 ;;
    --gateway-port) GATEWAY_PORT="$2"; shift 2 ;;
    --xray-port) XRAY_PORT="$2"; shift 2 ;;
    --xray-url) XRAY_WORKER_URL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

has_service() {
  [[ ",${SERVICES}," == *",$1,"* ]]
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
      echo "[gn03] ${name} already running (pid=${old_pid})"
      return 0
    fi
  fi

  echo "[gn03] starting ${name}"
  nohup bash -lc "${cmd}" >"${log_file}" 2>&1 &
  local new_pid=$!
  echo "${new_pid}" > "${pid_file}"
  echo "[gn03] ${name} started (pid=${new_pid}) log=${log_file}"
}

if ! command -v "${GN03_PYTHON}" >/dev/null 2>&1; then
  echo "[gn03] python not found: ${GN03_PYTHON}"
  exit 1
fi

CT_WORKER_URL="http://127.0.0.1:${CT_PORT}"
if has_service ct; then
  start_bg "ct_worker" \
    "cd '${ROOT_DIR}/BackendModelli' && \
     CUDA_VISIBLE_DEVICES='${CT_GPU}' exec '${GN03_PYTHON}' -m uvicorn remote_services.workers.ct_worker:app --host 0.0.0.0 --port '${CT_PORT}' --access-log"
fi

if has_service xray; then
  XRAY_WORKER_URL="http://127.0.0.1:${XRAY_PORT}"
  start_bg "xray_worker" \
    "cd '${ROOT_DIR}/BackendModelli' && \
     CUDA_VISIBLE_DEVICES='${XRAY_GPU}' exec '${GN03_PYTHON}' -m uvicorn remote_services.workers.xray_worker:app --host 0.0.0.0 --port '${XRAY_PORT}' --access-log"
fi

if has_service gateway; then
  start_bg "gateway" \
    "cd '${ROOT_DIR}/BackendModelli' && \
     CT_WORKER_URL='${CT_WORKER_URL}' XRAY_WORKER_URL='${XRAY_WORKER_URL}' \
     XRAY_HEALTH_PATH='${XRAY_HEALTH_PATH}' XRAY_INFER_PATH='${XRAY_INFER_PATH}' XRAY_LEGACY_TASK='${XRAY_LEGACY_TASK}' \
     exec '${GN03_PYTHON}' -m uvicorn remote_services.gateway.main:app --host 0.0.0.0 --port '${GATEWAY_PORT}' --access-log"
fi

echo "[gn03] done. services=${SERVICES}"
