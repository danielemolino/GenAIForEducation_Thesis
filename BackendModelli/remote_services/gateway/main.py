from __future__ import annotations

import json
import os
from urllib import request as urlrequest
from urllib import error as urlerror

from fastapi import FastAPI
from fastapi.responses import JSONResponse


app = FastAPI()

CT_WORKER_URL = (os.getenv("CT_WORKER_URL") or "http://127.0.0.1:8101").strip().rstrip("/")
XRAY_WORKER_URL = (os.getenv("XRAY_WORKER_URL") or "http://127.0.0.1:8102").strip().rstrip("/")
WORKER_TIMEOUT_SECONDS = float(os.getenv("WORKER_TIMEOUT_SECONDS", "300"))


def _post_json(url: str, payload: dict, timeout: float = WORKER_TIMEOUT_SECONDS) -> dict:
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, timeout: float = 3.0) -> dict:
    req = urlrequest.Request(url, method="GET")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _health_for_worker(worker_url: str) -> tuple[bool, str]:
    try:
        data = _get_json(f"{worker_url}/health")
        ok = str(data.get("status", "")).lower() == "ok"
        return ok, "ok" if ok else "unhealthy"
    except Exception as exc:
        return False, str(exc)


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}


@app.get("/health/ct")
def health_ct():
    ok, reason = _health_for_worker(CT_WORKER_URL)
    status = "ok" if ok else "down"
    return {"status": status, "service": "ct", "workerUrl": CT_WORKER_URL, "reason": reason}


@app.get("/health/xray")
def health_xray():
    ok, reason = _health_for_worker(XRAY_WORKER_URL)
    status = "ok" if ok else "down"
    return {"status": status, "service": "xray", "workerUrl": XRAY_WORKER_URL, "reason": reason}


@app.post("/infer/ct")
def infer_ct(payload: dict):
    try:
        data = _post_json(f"{CT_WORKER_URL}/infer/ct", payload)
        return data
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return JSONResponse(status_code=502, content={"ok": False, "error": f"CT worker HTTP {exc.code}: {detail}"})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"ok": False, "error": f"CT worker failed: {exc}"})


@app.post("/infer/xray")
def infer_xray(payload: dict):
    try:
        data = _post_json(f"{XRAY_WORKER_URL}/infer/xray", payload)
        return data
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return JSONResponse(status_code=502, content={"ok": False, "error": f"XRay worker HTTP {exc.code}: {detail}"})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"ok": False, "error": f"XRay worker failed: {exc}"})
