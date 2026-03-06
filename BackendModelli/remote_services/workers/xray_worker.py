from __future__ import annotations

from pathlib import Path
import base64
import io
import importlib
from datetime import datetime
import os
import sys
import threading

import numpy as np
import torch
from fastapi import FastAPI
from fastapi.responses import JSONResponse

try:
    import tifffile
except Exception:
    tifffile = None


app = FastAPI()

OUTPUT_ROOT = Path(__file__).resolve().parent / "runs_xray"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

_state_lock = threading.Lock()
_model_lock = threading.Lock()
_is_warm = False
_xgem_model = None


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


def _resolve_xgem_root() -> Path:
    env_root = os.environ.get("XGEM_API_ROOT")
    if not env_root:
        raise RuntimeError("XGEM_API_ROOT is not configured")
    root = Path(env_root).resolve()
    if not root.exists():
        raise RuntimeError(f"XGEM_API_ROOT does not exist: {root}")
    return root


def _load_model_once():
    global _xgem_model
    with _model_lock:
        if _xgem_model is not None:
            return _xgem_model

        xgem_root = _resolve_xgem_root()
        os.chdir(str(xgem_root))
        if str(xgem_root) not in sys.path:
            sys.path.insert(0, str(xgem_root))

        model_path = os.environ.get("XGEM_MODEL_PATH")
        weights_dir = os.environ.get("XGEM_WEIGHTS_DIR")
        if model_path:
            os.environ["XGEM_MODEL_PATH"] = model_path
        if weights_dir:
            os.environ["XGEM_WEIGHTS_DIR"] = weights_dir

        device = os.environ.get("XGEM_DEVICE")
        if not device:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device.lower() == "cpu":
            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)

        from inference.model_loader import load_model

        _xgem_model = load_model(device=device)
        _set_warm(True)
        return _xgem_model


def _run_report_to_frontal(model, report: str, output_dir: Path, steps: int) -> np.ndarray:
    if tifffile is None:
        raise RuntimeError("tifffile is required")

    helper = importlib.import_module("xgem_report_to_frontal")
    run_fn = getattr(helper, "_run_report_to_frontal")
    run_fn(model=model, report=report, output_dir=output_dir, steps=max(1, int(steps)))

    out_file = output_dir / "output_frontal.tiff"
    if not out_file.exists():
        raise RuntimeError("XRay output not found: output_frontal.tiff")

    arr = tifffile.imread(str(out_file)).astype(np.float32)
    if arr.ndim > 2:
        arr = np.squeeze(arr)
    return arr


@app.get("/health")
def health():
    return {"status": "ok", "service": "xray-worker", "modelWarm": _get_warm()}


@app.post("/warmup")
def warmup():
    try:
        _load_model_once()
        return {"ok": True, "modelWarm": _get_warm()}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/infer/xray")
def infer_xray(payload: dict):
    work_dir = OUTPUT_ROOT / datetime.now().strftime("%Y%m%d%H%M%S%f")
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        model = _load_model_once()
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise RuntimeError("prompt is required")
        steps = int(payload.get("xgemSteps") or os.environ.get("XGEM_STEPS") or 50)
        pixels = _run_report_to_frontal(model=model, report=prompt, output_dir=work_dir, steps=steps)
        return {
            "ok": True,
            "pixels_npy_b64": _encode_numpy_to_base64(pixels.astype(np.float32)),
            "shape": list(pixels.shape),
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})
