from __future__ import annotations

import json
import os
import uuid
import base64
from pathlib import Path
import tempfile
import zipfile
from urllib import request as urlrequest
from urllib import error as urlerror

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import numpy as np

try:
    import tifffile
except Exception:
    tifffile = None

try:
    from PIL import Image
except Exception:
    Image = None


app = FastAPI()

CT_WORKER_URL = (os.getenv("CT_WORKER_URL") or "http://127.0.0.1:8002").strip().rstrip("/")
XRAY_WORKER_URL = (os.getenv("XRAY_WORKER_URL") or "http://127.0.0.1:8000").strip().rstrip("/")
XRAY_HEALTH_PATH = (os.getenv("XRAY_HEALTH_PATH") or "/healthz").strip()
XRAY_INFER_PATH = (os.getenv("XRAY_INFER_PATH") or "/generate").strip()
XRAY_LEGACY_TASK = (os.getenv("XRAY_LEGACY_TASK") or "T->F").strip()
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


def _health_for_worker(worker_url: str, path: str = "/health") -> tuple[bool, str]:
    try:
        data = _get_json(f"{worker_url}{path}")
        ok = str(data.get("status", "")).lower() == "ok"
        if not ok and path == "/healthz":
            # Legacy services may return {"ok": true} or a generic JSON.
            ok = bool(data.get("ok")) or bool(data)
        return ok, "ok" if ok else "unhealthy"
    except Exception as exc:
        return False, str(exc)


def _build_multipart_form(fields: dict[str, str], file_fields: dict[str, tuple[str, str, bytes]] | None = None) -> tuple[bytes, str]:
    boundary = f"----xgem-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append((value or "").encode("utf-8"))
        chunks.append(b"\r\n")
    for name, (filename, content_type, content_bytes) in (file_fields or {}).items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        chunks.append(content_bytes)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def _post_xray_legacy(payload: dict) -> dict:
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("prompt is required for XRay legacy inference")
    task = (payload.get("xgemTask") or XRAY_LEGACY_TASK).strip()
    fields = {"task": task}
    file_fields = {"text_file": ("report.txt", "text/plain", prompt.encode("utf-8"))}
    body, boundary = _build_multipart_form(fields, file_fields=file_fields)
    req = urlrequest.Request(
        f"{XRAY_WORKER_URL}{XRAY_INFER_PATH}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlrequest.urlopen(req, timeout=WORKER_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8"))
        if "application/zip" in content_type or raw[:2] == b"PK":
            return {"ok": True, "zip_bytes_b64": base64.b64encode(raw).decode("ascii")}
        raise RuntimeError(f"Unsupported legacy response content type: {content_type or 'unknown'}")


def _encode_npy_b64(arr: np.ndarray) -> str:
    import io

    buff = io.BytesIO()
    np.save(buff, arr, allow_pickle=False)
    return base64.b64encode(buff.getvalue()).decode("ascii")


def _load_pixels_from_path(path_str: str) -> np.ndarray:
    path = Path(path_str)
    if not path.exists():
        raise RuntimeError(f"Legacy XRay output path does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        if tifffile is None:
            raise RuntimeError("tifffile is required to read legacy tiff output")
        arr = tifffile.imread(str(path))
        return np.squeeze(arr).astype(np.float32)
    if suffix in {".png", ".jpg", ".jpeg"}:
        if Image is None:
            raise RuntimeError("Pillow is required to read legacy image output")
        arr = np.array(Image.open(path).convert("L"), dtype=np.float32)
        return arr
    raise RuntimeError(f"Unsupported legacy XRay output format: {suffix}")


def _normalize_xray_response(data: dict) -> dict:
    if data.get("ok") and data.get("pixels_npy_b64"):
        return data

    if data.get("ok") and data.get("zip_bytes_b64"):
        if tifffile is None and Image is None:
            raise RuntimeError("Need tifffile or Pillow to decode legacy zip output")
        zip_bytes = base64.b64decode(data["zip_bytes_b64"].encode("ascii"))
        with tempfile.TemporaryDirectory(prefix="xgem_zip_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            zip_path = tmp_dir / "output.zip"
            zip_path.write_bytes(zip_bytes)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_dir)
            # Prefer medical image outputs first.
            candidates = list(tmp_dir.rglob("*.tiff")) + list(tmp_dir.rglob("*.tif"))
            candidates += list(tmp_dir.rglob("*.png")) + list(tmp_dir.rglob("*.jpg")) + list(tmp_dir.rglob("*.jpeg"))
            if not candidates:
                raise RuntimeError("Legacy zip does not contain a supported image output")
            pixels = _load_pixels_from_path(str(candidates[0]))
            return {"ok": True, "pixels_npy_b64": _encode_npy_b64(pixels), "shape": list(pixels.shape)}

    # Common legacy fields that may contain output image path.
    candidate_keys = [
        "output_path",
        "output_file",
        "frontal_path",
        "path",
        "result_path",
    ]
    for key in candidate_keys:
        if isinstance(data.get(key), str) and data[key]:
            pixels = _load_pixels_from_path(data[key])
            return {"ok": True, "pixels_npy_b64": _encode_npy_b64(pixels), "shape": list(pixels.shape)}

    # Legacy nested result shape.
    result = data.get("result")
    if isinstance(result, dict):
        for key in candidate_keys:
            if isinstance(result.get(key), str) and result[key]:
                pixels = _load_pixels_from_path(result[key])
                return {"ok": True, "pixels_npy_b64": _encode_npy_b64(pixels), "shape": list(pixels.shape)}

    raise RuntimeError(
        "Unsupported XRay legacy response format; expected pixels_npy_b64 or output path fields"
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}


@app.get("/health/ct")
def health_ct():
    ok, reason = _health_for_worker(CT_WORKER_URL, "/health")
    status = "ok" if ok else "down"
    return {"status": status, "service": "ct", "workerUrl": CT_WORKER_URL, "reason": reason}


@app.get("/health/xray")
def health_xray():
    ok, reason = _health_for_worker(XRAY_WORKER_URL, XRAY_HEALTH_PATH)
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
        if XRAY_INFER_PATH == "/infer/xray":
            data = _post_json(f"{XRAY_WORKER_URL}/infer/xray", payload)
        else:
            data = _normalize_xray_response(_post_xray_legacy(payload))
        return data
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return JSONResponse(status_code=502, content={"ok": False, "error": f"XRay worker HTTP {exc.code}: {detail}"})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"ok": False, "error": f"XRay worker failed: {exc}"})
