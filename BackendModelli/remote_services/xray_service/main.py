from __future__ import annotations

from pathlib import Path
import base64
import io
import importlib
from datetime import datetime

import numpy as np
from fastapi import FastAPI
from fastapi.responses import JSONResponse


app = FastAPI()

OUTPUT_ROOT = Path(__file__).resolve().parent / "runs"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def _import_main_xgem():
    try:
        return importlib.import_module("main_xgem")
    except Exception:
        return importlib.import_module("BackendModelli.main_xgem")


def _encode_numpy_to_base64(arr: np.ndarray) -> str:
    buff = io.BytesIO()
    np.save(buff, arr, allow_pickle=False)
    return base64.b64encode(buff.getvalue()).decode("ascii")


@app.get("/health")
def health():
    return {"status": "ok", "service": "xray"}


@app.post("/infer/xray")
def infer_xray(payload: dict):
    work_dir = OUTPUT_ROOT / datetime.now().strftime("%Y%m%d%H%M%S%f")
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        module = _import_main_xgem()
        fn = getattr(module, "_generate_xray_pixels_with_xgem")
        pixels = fn(payload, work_dir)
        pixels = pixels.astype(np.float32, copy=False)
        return {
            "ok": True,
            "pixels_npy_b64": _encode_numpy_to_base64(pixels),
            "shape": list(pixels.shape),
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
