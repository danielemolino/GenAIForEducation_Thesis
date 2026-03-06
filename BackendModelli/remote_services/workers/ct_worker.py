from __future__ import annotations

from pathlib import Path
import base64
import io
import importlib
from datetime import datetime
import threading

import numpy as np
from fastapi import FastAPI
from fastapi.responses import JSONResponse


app = FastAPI()

OUTPUT_ROOT = Path(__file__).resolve().parent / "runs_ct"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

_state_lock = threading.Lock()
_is_warm = False


def _import_main_text2ct():
    try:
        return importlib.import_module("main_text2ct")
    except Exception:
        return importlib.import_module("BackendModelli.main_text2ct")


def _encode_numpy_to_base64(arr: np.ndarray) -> str:
    buff = io.BytesIO()
    np.save(buff, arr, allow_pickle=False)
    return base64.b64encode(buff.getvalue()).decode("ascii")


def _set_warm(value: bool) -> None:
    global _is_warm
    with _state_lock:
        _is_warm = value


def _get_warm() -> bool:
    with _state_lock:
        return _is_warm


@app.get("/health")
def health():
    return {"status": "ok", "service": "ct-worker", "modelWarm": _get_warm()}


@app.post("/warmup")
def warmup():
    # Warmup can be performed by calling a cheap CT inference payload from the orchestrator.
    return {"ok": True, "modelWarm": _get_warm()}


@app.post("/infer/ct")
def infer_ct(payload: dict):
    work_dir = OUTPUT_ROOT / datetime.now().strftime("%Y%m%d%H%M%S%f")
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        module = _import_main_text2ct()
        fn = getattr(module, "_generate_ct_volume_with_text2ct")
        volume, spacing = fn(payload, work_dir)
        volume = volume.astype(np.int16, copy=False)
        _set_warm(True)
        return {
            "ok": True,
            "volume_npy_b64": _encode_numpy_to_base64(volume),
            "spacing": [float(spacing[0]), float(spacing[1]), float(spacing[2])],
            "shape": list(volume.shape),
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
